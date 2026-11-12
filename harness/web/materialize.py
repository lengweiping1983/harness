"""把已上传的附件物化成回合提示词。

这一层是 Web 层与提示词层之间唯一的翻译点，做两件事：

1. **验明正身**：客户端发来的 ``attachmentId`` 是**声明**，不是事实。
   这里逐个回仓库核对 —— 少一个核对，一个"我上传了一个不存在的附件"
   的 id 就会一路混到提示词里，给模型一句指向不存在文件的指令。
2. **按需物化**：文本走内存，二进制落盘到会话工作区，让 agent 能用
   ``read`` / ``read_image`` 自己去取。

物化目录放在工作区内的 ``.harness/attachments``：跟着会话走、
用户能直接在自己的文件系统里找到，同时不会污染工作区根目录。
"""

from __future__ import annotations

from harness.i18n import tr

import os
from pathlib import Path
from typing import Any

from .attachment_store import AttachmentStore, AttachmentStorageError
from .attachments import Attachment, MAX_BATCH_FILES
from .prompt import AttachmentPart, plan_parts

#: 工作区内的物化子目录。
MATERIALIZED_DIRNAME = ".harness"

#: 内联文本解码时最多读取的字节数，防止一个超大 text/plain 把内存吃光。
#: 与提示词层的内联上限同源，但这里更严格一点：内存里不该出现
#: 一个比任何单文件内联上限都大的字符串。
_DECODE_LIMIT = 256 * 1024


class AttachmentContext:
    """一次回合的附件解析结果。"""

    __slots__ = ("attachments", "parts")

    def __init__(
        self,
        attachments: "list[Attachment]",
        parts: "list[AttachmentPart]",
    ) -> None:
        self.attachments = attachments
        self.parts = parts

    def __bool__(self) -> bool:
        return bool(self.parts)


def resolve_refs(
    store: AttachmentStore,
    refs: "list[Any] | None",
    *,
    workspace_root: Path | None = None,
) -> AttachmentContext:
    """把客户端给的 id 列表解析成可用的附件与提示词块。"""
    if refs is None or refs == []:
        return AttachmentContext([], [])
    if not isinstance(refs, list) or len(refs) > MAX_BATCH_FILES:
        raise AttachmentStorageError(tr('ui.collaboration.error.attachment/invalid-refs'), code='attachment/invalid-refs')

    attachments: list[Attachment] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get('attachmentId'), str):
            raise AttachmentStorageError(tr('ui.collaboration.error.attachment/invalid-refs'), code='attachment/invalid-refs')
        identity = ref['attachmentId']
        if identity in seen:
            continue
        entry = store.describe(identity)
        if entry is None:
            raise AttachmentStorageError(tr('ui.collaboration.error.attachment/unavailable'), code='attachment/unavailable')
        # 仅复用已通过仓库查询的同一引用，不按名称或路径猜测。
        seen.add(identity)
        attachments.append(entry.attachment)

    materialized_dir = _materialized_dir(workspace_root)

    def resolve_text(attachment: Attachment) -> str | None:
        if attachment.is_image:
            return None
        raw = store.read(attachment.attachment_id)
        if len(raw) >= _DECODE_LIMIT:
            return None
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            # 二进制或非 UTF-8 原文落盘，不用替换字符冒充原始正文。
            return None

    def resolve_path(attachment: Attachment) -> str:
        return materialize(store, attachment, materialized_dir)

    parts = plan_parts(attachments, resolve_text, resolve_path)
    return AttachmentContext(attachments, parts)


def _materialized_dir(workspace_root: Path | None) -> Path | None:
    if workspace_root is None:
        return None
    root = Path(workspace_root).resolve()
    target = root / MATERIALIZED_DIRNAME / "attachments"
    if not target.resolve().is_relative_to(root):
        raise AttachmentStorageError(tr('ui.collaboration.error.attachment/materialize-failed'), code='attachment/materialize-failed')
    return target


def materialize(
    store: AttachmentStore,
    attachment: Attachment,
    dest_dir: Path | None,
) -> str:
    """把附件字节写进工作区，返回可读路径。

    落盘文件名用 ``<展示名>`` 而不是哈希：模型（和人）在提示词里读到的
    是 ``diagram.png`` 而不是 ``9f2c....png``，可读性差别很大。
    所以这里在 ``dest_dir`` 内按展示名去重 —— 同名不同内容会带短哈希后缀，
    绝不覆盖。
    """
    if dest_dir is None:
        raise AttachmentStorageError(tr('ui.collaboration.error.attachment/materialize-failed'), code='attachment/materialize-failed')
    raw = store.read(attachment.attachment_id)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for index in range(10000):
            name = attachment.name if index == 0 else f"{_stem(attachment.name)}-{attachment.attachment_id[:8]}{('-' + str(index)) if index > 1 else ''}{_suffix(attachment.name)}"
            target = dest_dir / name
            try:
                # 独占创建，已有文件和链接均不能被写入或替换。
                stream = target.open('xb')
            except FileExistsError:
                if not target.is_symlink() and target.is_file():
                    with target.open('rb') as existing:
                        if existing.read(len(raw) + 1) == raw:
                            return str(target)
                continue
            with stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            return str(target)
    except OSError:
        raise AttachmentStorageError(tr('ui.collaboration.error.attachment/materialize-failed'), code='attachment/materialize-failed') from None
    raise AttachmentStorageError(tr('ui.collaboration.error.attachment/materialize-failed'), code='attachment/materialize-failed')


def _stem(name: str) -> str:
    index = name.rfind(".")
    return name[:index] if index > 0 else name


def _suffix(name: str) -> str:
    index = name.rfind(".")
    return name[index:] if index > 0 else ""


__all__ = [
    "MATERIALIZED_DIRNAME",
    "AttachmentContext",
    "materialize",
    "resolve_refs",
]
