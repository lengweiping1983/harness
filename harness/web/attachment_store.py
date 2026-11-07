"""附件落盘：内容寻址仓库。

领域模型层（:mod:`harness.web.attachments`）只回答"这个字节块能不能收、
它是什么类型"，回答完就丢掉了字节本身。这一层负责把它**可靠地**存下来，
并在需要时原样取回。

存储布局
--------
::

    <root>/<sha256 前 2 位>/<sha256>            # 内容
    <root>/<sha256 前 2 位>/<sha256>.meta.json  # 展示名与媒体类型

按哈希前两位分桶是为了避免单目录几十万文件 —— ext4/NTFS 上万级目录项会让
``scandir`` 和目录查找都明显变慢。两级结构把它压回每个目录约 1/256 的量级。

内容寻址顺带解决三件事：**天然去重**（同一张图传一百次只有一份）、
**完整性可校验**（回读时重新 sha256 就能发现磁盘损坏）、
**id 不可伪造**（改一个字节，id 就对不上了）。

并发与原子性
------------
多个请求可能同时写同一个哈希（同一张图被两个会话同时引用）。因此：

* 先写同目录中独占创建的临时文件 再 :func:`os.replace` —— ``replace`` 在
  POSIX 和 Windows 上都是原子的，读者要么看到旧文件要么看到完整新文件，
  永远读不到半个文件。
* 目标已存在时校验字节和当前元数据再复用；损坏原件明确报错，不覆盖或补造。

id 校验
-------
``attachment_id`` 来自 URL，所以它是**不可信输入**。:func:`is_attachment_id`
只放行恰好 64 位小写十六进制，其余一律拒绝 —— 这条规则挡住了
``../../etc/passwd``、``..\\windows`` 以及超长文件名。
"""

from __future__ import annotations

from harness.i18n import tr
from ..core.fsatomic import atomic_write

import json
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import HarnessError
from .attachments import Attachment, AttachmentRejected, MAX_ATTACHMENT_BYTES, admit, content_id

#: 存储根目录名，挂在 HARNESS_HOME 下。
STORE_DIRNAME = "attachments"

#: ``attachment_id`` 的唯一合法形态。
_ID_RE = re.compile(r"^[0-9a-f]{64}$")

#: 侧车元数据后缀。
META_SUFFIX = ".meta.json"


def is_attachment_id(value: object) -> bool:
    """是否为合法附件 id（恰好 64 位小写十六进制）。"""
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    """仓库里的一份附件：元数据 + 内容路径。"""

    attachment: Attachment
    path: Path

    @property
    def meta_path(self) -> Path:
        return self.path.with_name(self.path.name + META_SUFFIX)


class AttachmentStorageError(HarnessError):
    """附件原件或当前元数据无效，不修补、不覆盖。"""

    code = 'attachment/invalid-storage'


def _invalid() -> AttachmentStorageError:
    return AttachmentStorageError(tr('ui.collaboration.error.attachment/invalid-storage'))


def _metadata(raw: object, attachment_id: str, data: bytes) -> Attachment:
    """只接受当前完整侧车格式，名称和类型可安全放入路径及响应头。"""
    size = len(data)
    if (not isinstance(raw, dict) or set(raw) != {'attachmentId', 'name', 'mediaType', 'size', 'isImage'}
            or raw['attachmentId'] != attachment_id
            or not isinstance(raw['name'], str) or not 1 <= len(raw['name']) <= 128
            or raw['name'] in {'.', '..'} or any(char in raw['name'] for char in '/\\')
            or any(ord(char) < 32 or ord(char) == 127 for char in raw['name'])
            or not isinstance(raw['mediaType'], str) or re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', raw['mediaType']) is None
            or type(raw['size']) is not int or raw['size'] != size or not 0 < size <= MAX_ATTACHMENT_BYTES
            or type(raw['isImage']) is not bool or raw['isImage'] != raw['mediaType'].startswith('image/')):
        raise _invalid()
    try:
        expected = admit(data, name=raw['name'], declared_type=raw['mediaType'])
    except AttachmentRejected:
        raise _invalid() from None
    if expected.media_type != raw['mediaType'] or expected.is_image != raw['isImage']:
        raise _invalid()
    return Attachment(attachment_id, raw['name'], raw['mediaType'], size, raw['isImage'])


class AttachmentStore:
    """内容寻址的附件仓库。

    线程安全：Web 层是 ``ThreadingHTTPServer``，多个上传请求会并发打进来。
    真正的文件级正确性靠 ``os.replace`` 的原子性保证，这里这把锁只是防止
    两个线程同时对同一个哈希做"写临时文件 → replace"的冗余功，以及让
    元数据写入和数据写入保持可见顺序。
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 路径

    def bucket_dir(self, attachment_id: str) -> Path:
        """某个 id 所在的桶目录。"""
        if not is_attachment_id(attachment_id):
            raise ValueError(tr('python.attachment_store.8faca54e76b2', p0=f'{attachment_id!r}'))
        return self.root / attachment_id[:2]

    def path_for(self, attachment_id: str) -> Path:
        """内容文件路径（**不检查存在性**）。"""
        return self.bucket_dir(attachment_id) / attachment_id

    # ---------------------------------------------------------------- 写入

    def put(self, data: bytes, attachment: Attachment) -> StoredAttachment:
        """写入一份已准入的附件，返回落盘位置。

        幂等：已存在且内容一致时直接复用，不会重复写盘。
        """
        if not is_attachment_id(attachment.attachment_id) or content_id(data) != attachment.attachment_id:
            raise _invalid()
        _metadata(attachment.to_json(), attachment.attachment_id, data)
        target = self.path_for(attachment.attachment_id)
        meta = target.with_name(target.name + META_SUFFIX)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = self.describe(attachment.attachment_id)
            if existing is None:
                atomic_write(target, data)
                atomic_write(meta, self._meta_bytes(attachment))
        return StoredAttachment(attachment=attachment, path=target)

    def _meta_bytes(self, attachment: Attachment) -> bytes:
        # 侧车就是 ``Attachment.to_json()`` 原样落盘：回读时不需要重建领域对象，
        # 也不存在"内存表示"和"磁盘表示"两套字段对不上的风险。
        return json.dumps(attachment.to_json(), ensure_ascii=False, indent=2).encode("utf-8")

    # ---------------------------------------------------------------- 读取

    def read(self, attachment_id: str) -> bytes:
        """有界回读并核对内容身份；损坏内容不作为不可变资源分发。"""
        path = self.path_for(attachment_id)
        try:
            if path.is_symlink() or not path.is_file():
                raise _invalid()
            with path.open('rb') as stream:
                data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        except OSError:
            raise _invalid() from None
        if not data or len(data) > MAX_ATTACHMENT_BYTES or content_id(data) != attachment_id:
            raise _invalid()
        return data

    def exists(self, attachment_id: str) -> bool:
        return is_attachment_id(attachment_id) and self.path_for(attachment_id).exists()

    def describe(self, attachment_id: str) -> StoredAttachment | None:
        """不存在返回 None；不完整或损坏的附件明确报错并保留原件。"""
        if not is_attachment_id(attachment_id):
            return None
        content_path = self.path_for(attachment_id)
        meta_path = content_path.with_name(content_path.name + META_SUFFIX)
        if not content_path.exists() and not meta_path.exists() and not content_path.is_symlink() and not meta_path.is_symlink():
            return None
        data = self.read(attachment_id)
        try:
            if meta_path.is_symlink() or not meta_path.is_file():
                raise _invalid()
            with meta_path.open('rb') as stream:
                encoded = stream.read(65537)
            if len(encoded) > 65536:
                raise _invalid()
            raw = json.loads(encoded.decode('utf-8'))
        except (OSError, ValueError):
            raise _invalid() from None
        return StoredAttachment(_metadata(raw, attachment_id, data), content_path)

    def verify(self, attachment_id: str) -> bool:
        """回读并重算哈希，确认内容没有被外部改动过。"""
        try:
            return content_id(self.read(attachment_id)) == attachment_id
        except (OSError, AttachmentStorageError):
            return False

    # ---------------------------------------------------------------- 维护

    def stats(self) -> dict[str, object]:
        """仓库统计，供设置页/健康检查展示。"""
        files = 0
        total = 0
        try:
            buckets = list(self.root.iterdir())
        except FileNotFoundError:
            return {"count": 0, "totalBytes": 0, "root": str(self.root)}
        except OSError:
            raise _invalid() from None
        try:
            for bucket in buckets:
                if not stat.S_ISDIR(bucket.stat().st_mode):
                    continue
                for entry in bucket.iterdir():
                    if entry.name.endswith(META_SUFFIX) or entry.name.startswith(".tmp-"):
                        continue
                    total += entry.stat().st_size
                    files += 1
        except OSError:
            raise _invalid() from None
        return {"count": files, "totalBytes": total, "root": str(self.root)}

    def clear(self) -> None:
        """清空仓库（仅测试与"重置本地缓存"用）。"""
        import shutil

        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)


def default_store_root(home: Path | str | None = None) -> Path:
    """默认仓库根目录：``<HARNESS_HOME>/attachments``。"""
    base = Path(home).expanduser() if home else Path.home() / ".harness"
    return base / STORE_DIRNAME


__all__ = [
    "META_SUFFIX",
    "STORE_DIRNAME",
    "AttachmentStore",
    "AttachmentStorageError",
    "StoredAttachment",
    "default_store_root",
    "is_attachment_id",
]
