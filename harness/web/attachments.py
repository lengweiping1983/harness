"""附件领域模型（纯逻辑，无 IO）。

这一层刻意只做三件事，且全部可单测：

1. **内容寻址**：``attachmentId`` 就是文件字节的 sha256。同一份文件上传两次
   天然去重，id 不可伪造也无法枚举。
2. **从字节验证媒体类型**：绝不相信调用方声明的 ``Content-Type``。浏览器对
   未知类型文件会填 ``application/octet-stream``，拖进来的文件更没有类型可言；
   只认魔数（magic number）。
3. **分层限额**：单文件、单批总量、单文件数三个维度各自独立设限，避免
   "1 个超大文件" 或 "500 个小文件" 这两种绕过方式。
"""

from __future__ import annotations

from harness.i18n import tr

import hashlib
import codecs
import re
import unicodedata
from dataclasses import dataclass

#: 单个附件的字节上限（默认 32 MiB）。
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024

#: 单次请求内所有附件的合计字节上限（默认 96 MiB）。
MAX_BATCH_BYTES = 96 * 1024 * 1024

#: 单次请求内的附件个数上限。
MAX_BATCH_FILES = 20

#: 可接受的图片类型 -> (魔数前缀集合, 规范扩展名)。
#:
#: 用元组前缀而不是正则：一组前缀的 startswith 比编译正则快一个数量级，
#: 而这是每个上传都要跑的路径。
_IMAGE_SIGNATURES: dict[str, tuple[tuple[bytes, ...], str]] = {
    "image/png": ((b"\x89PNG\r\n\x1a\n",), ".png"),
    "image/jpeg": ((b"\xff\xd8\xff",), ".jpg"),
    "image/gif": ((b"GIF87a", b"GIF89a"), ".gif"),
    "image/webp": ((b"RIFF",), ".webp"),
    "image/bmp": ((b"BM",), ".bmp"),
}

#: 非图片附件允许的类型。放在白名单而不是黑名单：附件要能被模型读取，
#: 可执行文件和脚本没有任何读取价值，直接在入口拒绝。
_ALLOWED_NON_IMAGE: dict[str, tuple[str, ...]] = {
    "text/plain": (".txt", ".log"),
    "text/markdown": (".md", ".markdown"),
    "text/csv": (".csv",),
    "application/json": (".json", ".jsonl"),
    "application/pdf": (".pdf",),
    "application/xml": (".xml",),
    "text/xml": (".xml",),
    "application/x-yaml": (".yaml", ".yml"),
    "text/yaml": (".yaml", ".yml"),
}

#: 明确拒绝的类型：能执行、能被加载脚本、或者只是危险得没道理。
_DENIED_EXACT = frozenset(
    {
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-executable",
        "application/x-sharedlib",
        "application/x-msi",
        "application/vnd.microsoft.portable-executable",
        "application/x-sh",
        "application/x-bat",
    }
)

#: 危险扩展名（与类型无关，双保险）。
_DENIED_EXTENSIONS = frozenset(
    {
        ".exe", ".dll", ".com", ".bat", ".cmd", ".scr", ".pif", ".msi", ".msp",
        ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1", ".psm1",
        ".jar", ".sh", ".bash", ".zsh", ".csh", ".ksh",
        ".py", ".pyc", ".pyo", ".pyw", ".rb", ".pl", ".php",
        ".app", ".deb", ".rpm", ".dmg", ".pkg", ".apk",
        ".lnk", ".scf", ".reg", ".hta", ".cpl", ".inf", ".url",
    }
)


class AttachmentRejected(ValueError):
    """附件准入拒绝保留机器码、资源键与参数，正文只作为发生时诊断。"""

    def __init__(self, key: str, code: str = "attachment/rejected", **params: str | int) -> None:
        super().__init__(tr(key, **params))
        self.code = code
        self.copy = {"key": key, "params": params}


@dataclass(frozen=True, slots=True)
class Attachment:
    """一个已准入的附件。"""

    attachment_id: str
    name: str
    media_type: str
    size: int
    is_image: bool

    def to_json(self) -> dict[str, object]:
        return {
            "attachmentId": self.attachment_id,
            "name": self.name,
            "mediaType": self.media_type,
            "size": self.size,
            "isImage": self.is_image,
        }


def content_id(data: bytes) -> str:
    """内容寻址 id：字节的 sha256。

    不用 ``hash(data)``：Python 的 hash 带进程级随机盐，跨进程不稳定，
    也就无法当持久 id 用。
    """
    return hashlib.sha256(data).hexdigest()


def sniff_media_type(data: bytes) -> str | None:
    """从魔数判定媒体类型；认不出来返回 ``None``。

    顺序很重要：先试图片（它们有强魔数），再试文本。
    """
    for media_type, (prefixes, _ext) in _IMAGE_SIGNATURES.items():
        if any(data.startswith(prefix) for prefix in prefixes):
            # WebP 的 RIFF 魔数只占 4 字节，后面 8 字节必须是 "WEBP"，
            # 否则任意 RIFF 容器（AVI/WAV）都会被误判成图片。
            if media_type == "image/webp" and data[8:12] != b"WEBP":
                continue
            return media_type
    if _looks_like_text(data):
        return "text/plain"
    return None


def _looks_like_text(data: bytes) -> bool:
    """启发式判断是不是文本：没有 NUL 字节，且可解码。

    刻意保守：判不出来就当二进制拒绝，而不是放行。附件要进模型上下文，
    误判成文本的二进制会变成一堆乱码 token。
    """
    if not data:
        return False
    probe = data[:4096]
    if b"\x00" in probe:
        return False
    try:
        # 有界探测可能截在字符中间；只有完整输入才要求解码器收尾。
        codecs.getincrementaldecoder('utf-8')().decode(probe, final=len(probe) == len(data))
    except UnicodeDecodeError:
        return False
    # 控制字符比例过高说明是二进制（终端转储、protobuf 之类）。
    control = sum(1 for byte in probe if byte < 0x09 or 0x0E <= byte < 0x20)
    return control / len(probe) < 0.05


def extension_of(name: str) -> str:
    """取小写扩展名，含点；没有则空串。"""
    dot = name.rfind(".")
    slash = max(name.rfind("/"), name.rfind("\\"))
    if dot <= slash or dot == -1 or dot == 0:
        # dot==0 意味着这是个以点开头的整名（``.gitignore``），不是扩展名。
        return ""
    return name[dot:].lower()


def sanitize_name(name: str, *, fallback: str = "attachment") -> str:
    """清洗展示名：去掉任何路径成分和危险字符。

    名字最终会出现在 UI、markdown 引用和 ``Content-Disposition`` 里，
    所以既不能保留 ``../``（目录穿越），也不能保留控制字符或换行
    （响应头注入）。
    """
    # 只取最后一段：浏览器传的是 fakepath\\C:\\Users\\...\\a.png。
    candidate = name.replace("\\", "/").rsplit("/", 1)[-1]
    candidate = unicodedata.normalize("NFC", candidate)
    # 去掉控制字符（含换行/回车——防止响应头注入）。
    candidate = "".join(ch for ch in candidate if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    # 保留 Unicode 字母数字，以及 - _ . 空格。
    candidate = re.sub(r"[^\w\-. ]", "_", candidate, flags=re.UNICODE).strip()
    # Windows 保留名：即使不写盘也要挡掉，避免后续任何落盘实现踩坑。
    stem = candidate.rsplit(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or (
        len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3].isdigit()
    ):
        candidate = "_" + candidate
    candidate = candidate.lstrip(". ")
    if not candidate:
        return fallback
    return candidate[:120]


def admit(
    data: bytes,
    *,
    name: str = "",
    declared_type: str = "",
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> Attachment:
    """准入一个附件：查限额 → 验类型 → 清洗名字 → 生成引用。

    :param data: 文件字节。
    :param name: 浏览器给的原始文件名（可能被伪造成路径）。
    :param declared_type: 浏览器声明的 Content-Type，**仅作参考，不作依据**。
    :param max_bytes: 单文件字节上限。
    :returns: 已准入的附件引用。
    :raises AttachmentRejected: 任何一条准入规则不满足。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise AttachmentRejected('ui.attachments.server.bytesRequired', code='attachment/invalid')
    raw = bytes(data)
    if not raw:
        raise AttachmentRejected('ui.attachments.server.empty', code='attachment/empty')
    if len(raw) > max_bytes:
        raise AttachmentRejected('ui.attachments.server.fileTooLarge', code='attachment/too-large', p0=f'{max_bytes // (1024 * 1024)}', p1=f'{len(raw) // 1024}')

    safe_name = sanitize_name(name)

    # 先看扩展名黑名单：即使魔数看起来无害，.ps1 就是不该进来。
    if extension_of(safe_name) in _DENIED_EXTENSIONS:
        raise AttachmentRejected('ui.attachments.server.extensionDenied', code='attachment/type-denied', p0=f'{extension_of(safe_name)}')

    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared in _DENIED_EXACT:
        raise AttachmentRejected('ui.attachments.server.mediaDenied', code='attachment/type-denied', p0=f'{declared}')

    sniffed = sniff_media_type(raw)

    # 图片路径：魔数认出来的才准，声明的 type 一律不信。
    if sniffed in _IMAGE_SIGNATURES:
        _, ext = _IMAGE_SIGNATURES[sniffed]
        safe_name = _ensure_extension(safe_name, ext)
        return Attachment(
            attachment_id=content_id(raw),
            name=safe_name,
            media_type=sniffed,
            size=len(raw),
            is_image=True,
        )

    # 非图片路径：类型取「嗅探结果」与「扩展名推断」的交集，任一放行即可，
    # 但最终 media_type 只写可信的那一个。
    ext = extension_of(safe_name)
    allowed = _ALLOWED_NON_IMAGE.get(declared)
    if ext and any(ext in group for group in _ALLOWED_NON_IMAGE.values()):
        media_type = declared if allowed and ext in allowed else _type_for_extension(ext)
        return Attachment(
            attachment_id=content_id(raw),
            name=safe_name,
            media_type=media_type,
            size=len(raw),
            is_image=False,
        )
    if sniffed == "text/plain":
        # 无扩展名但是合法 UTF-8 文本：允许，按文本存。
        return Attachment(
            attachment_id=content_id(raw),
            name=safe_name if ext else _ensure_extension(safe_name, ".txt"),
            media_type="text/plain",
            size=len(raw),
            is_image=False,
        )

    raise AttachmentRejected('ui.attachments.server.unsupported', code='attachment/unsupported', p0=safe_name)


def admit_batch(
    items: "list[tuple[bytes, str, str]]",
    *,
    max_files: int = MAX_BATCH_FILES,
    max_total_bytes: int = MAX_BATCH_BYTES,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> list[Attachment]:
    """准入一批附件，并在批量层面再查一次限额。

    单文件限额挡不住「20 个 30 MiB 文件」这种组合攻击，所以批量总额
    必须在批量层单独设限。
    """
    if len(items) > max_files:
        raise AttachmentRejected('ui.attachments.server.tooMany', code='attachment/too-many', p0=f'{max_files}', p1=f'{len(items)}')
    total = sum(len(item[0]) for item in items)
    if total > max_total_bytes:
        raise AttachmentRejected('ui.attachments.server.batchTooLarge', code='attachment/batch-too-large', p0=f'{max_total_bytes // (1024 * 1024)}')
    return [admit(data, name=name, declared_type=ctype, max_bytes=max_bytes) for data, name, ctype in items]


def _type_for_extension(ext: str) -> str:
    for media_type, extensions in _ALLOWED_NON_IMAGE.items():
        if ext in extensions:
            return media_type
    return "text/plain"


def _ensure_extension(name: str, ext: str) -> str:
    """图片缺扩展名时补上——前端要靠扩展名决定渲染方式。"""
    return name if extension_of(name) else name + ext


__all__ = [
    "MAX_ATTACHMENT_BYTES",
    "MAX_BATCH_BYTES",
    "MAX_BATCH_FILES",
    "Attachment",
    "AttachmentRejected",
    "admit",
    "admit_batch",
    "content_id",
    "extension_of",
    "sanitize_name",
    "sniff_media_type",
]
