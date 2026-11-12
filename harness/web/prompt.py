"""把附件组装进用户提示词。

为什么需要这一层
----------------
harness 的回合入参是一个纯字符串（``Harness.stream(session, message)``），
没有多模态通道。所以附件必须**在进入 agent 之前就被翻译成文字**。翻译成什么，
直接决定模型能不能真的用上这个附件 —— 这里定下的三条规则：

1. **文本类内联**：代码、日志、配置、CSV 直接进围栏代码块。模型要的是
   内容本身，让它自己去 `read` 一个临时文件既多一次工具往返，也容易失败。
2. **超限则落盘**：单个文件超过 :data:`MAX_INLINE_BYTES`（或批量超
   :data:`MAX_TOTAL_INLINE_BYTES`）就不再内联，改为写进工作区并只给路径。
   把一个 20 MB 的日志塞进上下文会直接挤爆窗口，那时模型拿到的是一段
   被截断的噪音，比拿到一个路径更糟。
3. **二进制落盘**：图片、PDF 一律写进会话工作区并给绝对路径，模型可以用
   ``read_image`` / ``read`` 工具自己去取。这跟人把文件丢进窗口、
   然后告诉 agent "文件在 C:\\tmp\\a.png" 是同一件事。

这一层刻意是纯函数：组装逻辑（截断、顺序、措辞）是提示词质量的核心，
必须能被单测钉死，而不是只能在浏览器里肉眼判断。
"""

from __future__ import annotations

from harness.i18n import tr

from dataclasses import dataclass

from .attachments import Attachment

#: 组装用的换行常量。写成常量而不是字面量，是为了在任何生成脚本里
#: 都不必和转义打架 —— 那层转义已经让本文件重写过一次。
_NL = chr(10)

#: 单个文本附件允许内联的最大字节数（默认 48 KiB）。
MAX_INLINE_BYTES = 48 * 1024

#: 一条消息里所有内联文本的合计上限（默认 128 KiB）。
#:
#: 预算按**先到先得**分配：前面的附件吃掉预算，后面的自动改为落盘。
#: 这样"拖进来 10 个文件"不会因为最后一个太大而把前面的好处挤掉。
MAX_TOTAL_INLINE_BYTES = 128 * 1024

#: 判定为"可内联文本"的媒体类型前缀。
_INLINE_PREFIXES = ("text/",)
_INLINE_EXACT = frozenset(
    {"application/json", "application/xml", "application/x-yaml"}
)

#: 附件块的围栏语言提示（按媒体类型选，让语法高亮和模型都更好读）。
_FENCE_HINTS: dict[str, str] = {
    "text/markdown": "markdown",
    "text/csv": "csv",
    "application/json": "json",
    "application/jsonl": "jsonl",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/x-yaml": "yaml",
    "text/yaml": "yaml",
}


@dataclass(frozen=True, slots=True)
class AttachmentPart:
    """一个待组装的附件块。

    ``text`` 与 ``local_path`` 互斥：要么内联文本，要么给路径。
    """

    name: str
    media_type: str
    size: int
    text: str | None = None
    local_path: str | None = None
    is_image: bool = False

    @property
    def inlined(self) -> bool:
        return self.text is not None


def is_inlineable(media_type: str, size: int, budget: int) -> bool:
    """这个附件能否内联（类型是文本 且 大小在预算内）。"""
    if size > min(MAX_INLINE_BYTES, budget):
        return False
    lowered = media_type.lower()
    return lowered.startswith(_INLINE_PREFIXES) or lowered in _INLINE_EXACT


def render_part(part: AttachmentPart) -> str:
    """渲染单个附件块。"""
    kind = tr('python.prompt.d24c10d37db0') if part.is_image else tr('python.prompt.39932f24fe11')
    header = tr('attachments.header', kind=kind, name=part.name, media_type=part.media_type, size=format_size(part.size))
    if part.inlined:
        hint = _FENCE_HINTS.get(part.media_type.lower(), "")
        body = part.text or ""
        # 开合围栏用**同一个** _safe_fence 结果。分开写就会出现"开头六个
        # 反引号、结尾三个"这种不对称，于是 _safe_fence 精心选出来的长度
        # 保护完全失效 —— 内容里的反引号照样能提前闭合。
        fence = _safe_fence(body)
        return f"{header}{_NL}{_NL}{fence}{hint}{_NL}{body}{_NL}{fence}"
    path = part.local_path or tr('python.prompt.12d4d43cb413')
    return tr('python.prompt.d337f80516bb', p0=f'{header}', p1=f'{_NL}', p2=f'{path}')


def compose_prompt(text: str, parts: "list[AttachmentPart]") -> str:
    """把用户输入与附件块组装成一条最终提示词。

    无附件时**原样返回** —— 绝大多数回合都走这条路，不该为附件功能付出
    任何额外的 token 或格式噪音。
    """
    if not parts:
        return text
    blocks = _NL.join(render_part(part) for part in parts)
    tail = tr('python.prompt.625262e909d6', p0=f'{len(parts)}', p1=f'{blocks}')
    return (text + tail) if text else tr('python.prompt.e2b0e9f5099c', p0=f'{tail}')


def plan_parts(
    attachments: "list[Attachment]",
    resolve_text,
    resolve_path,
) -> "list[AttachmentPart]":
    """按预算决定每个附件内联还是落盘，并解析出对应内容。

    ``resolve_text(attachment) -> str | None`` 与
    ``resolve_path(attachment) -> str | None`` 由调用方注入：存储层只关心
    仓库里有没有字节，不该在这里被耦合进来。
    """
    parts: list[AttachmentPart] = []
    remaining = MAX_TOTAL_INLINE_BYTES
    for attachment in attachments:
        size = attachment.size
        if is_inlineable(attachment.media_type, size, remaining):
            text = resolve_text(attachment)
            if text is not None:
                parts.append(
                    AttachmentPart(
                        name=attachment.name,
                        media_type=attachment.media_type,
                        size=size,
                        text=text,
                        is_image=attachment.is_image,
                    )
                )
                remaining = max(0, remaining - size)
                continue
        path = resolve_path(attachment)
        parts.append(
            AttachmentPart(
                name=attachment.name,
                media_type=attachment.media_type,
                size=size,
                local_path=path,
                is_image=attachment.is_image,
            )
        )
    return parts


def _safe_fence(body: str) -> str:
    """选一个不会被正文提前闭合的围栏长度。"""
    longest = 0
    run = 0
    for char in body:
        if char == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


def format_size(size: int) -> str:
    """人类可读的大小。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


__all__ = [
    "MAX_INLINE_BYTES",
    "MAX_TOTAL_INLINE_BYTES",
    "AttachmentPart",
    "compose_prompt",
    "format_size",
    "is_inlineable",
    "plan_parts",
    "render_part",
]
