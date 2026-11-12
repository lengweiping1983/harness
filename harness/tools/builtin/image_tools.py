"""内置图片工具：read_image。

这是"读图排障"能力：
模型能直接看截图（UI 走查、报错界面、设计稿对照），而不是只看代码猜。

关键设计：
* 校验扩展名 + 魔数（magic number），避免伪装成图片的任意文件被 base64 进上下文；
* 限制大小，超大图直接拒绝并给出建议（避免撑爆上下文 / 内存）；
* 探测像素尺寸，PNG/GIF/JPEG/WebP 头都能纯标准库解析；
* 返回 ``data:`` URI，模型（多模态）可直接消费。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import base64
import struct
import urllib.parse
from pathlib import Path
from typing import Any

from ...core.errors import ToolExecutionError
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext
from ...net import ipv4_preferred_get
from .fs_tools import resolve_path
from .web_tools import _check_url

#: 单张图片最大字节数（5MB；多数多模态接口上限在 5~20MB）
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BATCH_IMAGE_BYTES = 20 * 1024 * 1024
MAX_BATCH_IMAGES = 8

#: 扩展名 -> MIME
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

#: 魔数前缀（bytes）-> MIME，用于确认文件确实是图片
_MAGIC_BY_MIME = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"BM": "image/bmp",
}


def _sniff_mime(data: bytes) -> str | None:
    """按魔数判断真实图片类型（WebP 需要额外判断）。"""
    for magic, mime in _MAGIC_BY_MIME.items():
        if data.startswith(magic):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_size(data: bytes, mime: str) -> tuple[int, int] | None:
    """纯标准库解析图片尺寸（像素）。解析不了就返回 None。"""
    try:
        if mime == "image/png" and len(data) >= 24 and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if mime == "image/gif" and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if mime == "image/bmp" and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(width), abs(height)
        if mime == "image/jpeg":
            return _jpeg_size(data)
        if mime == "image/webp":
            return _webp_size(data)
    except (struct.error, IndexError, ValueError):
        return None
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """扫描 JPEG 的 SOF 段获取尺寸。"""
    index = 2
    length = len(data)
    while index + 3 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # SOF0..SOF15，排除 DHT(C4)/JPG(C8)/DAC(CC)
        if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            if index + 9 < length:
                height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                return int(width), int(height)
            return None
        if index + 4 <= length:
            seg_len = struct.unpack(">H", data[index + 2 : index + 4])[0]
            if seg_len <= 0:
                return None
            index += 2 + seg_len
        else:
            return None
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """解析 WebP 的 VP8 / VP8L / VP8X 头。"""
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _validate_image(
    data: bytes, *, label: str, suffix: str, max_bytes: int
) -> tuple[str, int | None, int | None]:
    """校验已读入内存的图片字节，返回 ``(mime, 宽, 高)``。

    与 :func:`read_image_file` 共用同一套规则：非空、不超大小、魔数与扩展名
    一致。抽成独立函数是为了让本地文件与远程 URL 共用同一套校验。
    """
    size = len(data)
    if size == 0:
        raise ToolExecutionError(tr('python.image_tools.e6839ff077f9', p0=f'{label}'), path=label)
    if size > max_bytes:
        raise ToolExecutionError(
            tr('python.image_tools.a7a084d07e2e', p0=f'{size}', p1=f'{max_bytes}', p2=f'{label}'),
            path=label,
            size=size,
        )
    real_mime = _sniff_mime(data[:32])
    if real_mime is None:
        raise ToolExecutionError(
            tr('python.image_tools.2ea7444f2373', p0=f'{suffix}', p1=f'{label}'),
            path=label,
        )
    expected = MIME_BY_SUFFIX.get(suffix)
    if expected is not None and real_mime != expected:
        raise ToolExecutionError(
            tr('python.image_tools.931447e71a51', p0=f'{real_mime}', p1=f'{suffix}', p2=f'{label}'),
            path=label,
        )
    # _image_size 对不支持的图片头会返回 None，这里不能直接解包，
    # 否则会把"图片头异常"变成看不懂的 TypeError。
    dimensions = _image_size(data, real_mime)
    if dimensions is None:
        # 魔数对了但图片头解析不出来（截断或伪造文件）。
        # 不能带着“无尺寸”继续返回：那会把一个无效文件
        # 变成一块 base64 直接占满模型上下文。
        raise ToolExecutionError(
            tr('python.image_tools.5556a76946f2', p0=f'{label}'),
            path=label,
        )
    width, height = dimensions
    return real_mime, width, height


def read_image_file(path: Path, *, max_bytes: int = MAX_IMAGE_BYTES) -> dict[str, Any]:
    """读取并校验一张图片，返回 data URI 与元信息。"""
    if not path.exists():
        raise ToolExecutionError(tr('python.image_tools.b75fee93554c', p0=f'{path}'), path=str(path))
    if path.is_dir():
        raise ToolExecutionError(tr('python.image_tools.4d109fb0c9cb', p0=f'{path}'), path=str(path))

    suffix = path.suffix.lower()
    if suffix not in MIME_BY_SUFFIX:
        supported = tr('ui.common.list_separator').join(sorted(MIME_BY_SUFFIX))
        raise ToolExecutionError(
            tr('python.image_tools.e02a74d6198a', p0=f"{suffix or tr('common.no_extension')}", p1=f'{supported}'), path=str(path)
        )

    max_bytes = max(1, min(MAX_IMAGE_BYTES, max_bytes))
    # 先限量读取，避免在校验前把任意大的文件载入内存；也覆盖 stat 后增长。
    with path.open('rb') as handle:
        data = handle.read(max(0, max_bytes) + 1)
    real_mime, width, height = _validate_image(
        data, label=str(path), suffix=suffix, max_bytes=max_bytes
    )
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "path": str(path),
        "mime_type": real_mime,
        "bytes": len(data),
        "width": width,
        "height": height,
        "data_url": f"data:{real_mime};base64,{encoded}",
    }


def _suffix_from_url(url: str) -> str:
    """从 URL 路径推图片扩展名（忽略 query）。"""
    path = urllib.parse.urlsplit(url).path
    return Path(path).suffix.lower()


async def read_image_url(url: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> dict[str, Any]:
    """下载并校验一张远程图片。

    为什么支持 URL：模型往往直接引用图片地址（截图、文档配图），要
    先下载到本地再读，只多一个工具调用。

    安全上复用 web_tools 的 URL 白名单 + 内网拦截（SSRF 防护），
    并走 IPv4 优先连接（避免双栈选路超时）。
    """
    max_bytes = max(1, min(MAX_IMAGE_BYTES, max_bytes))
    parsed = _check_url(url)  # 协议白名单 + 内网地址拦截
    if parsed.scheme not in ("http", "https"):
        raise ToolExecutionError(tr('python.image_tools.be0ff6f8ca0f', p0=f'{parsed.scheme!r}'), url=url)
    suffix = _suffix_from_url(url)
    if suffix not in MIME_BY_SUFFIX:
        supported = tr('ui.common.list_separator').join(sorted(MIME_BY_SUFFIX))
        raise ToolExecutionError(
            tr('python.image_tools.e02a74d6198a', p0=f"{suffix or tr('common.no_extension')}", p1=f'{supported}'), url=url
        )

    def _fetch() -> bytes:
        """在线程里同步下载（防阻塞事件循环）。"""
        response = None
        try:
            response = ipv4_preferred_get(
                url,
                headers={
                    "User-Agent": "Harness/1.0 (+https://github.com/harness)",
                    "Accept": "image/*,*/*;q=0.8",
                },
                timeout=30.0,
                url_validator=_check_url,
            )
            status = getattr(response, "status", 200)
            if status >= 400:
                raise ToolExecutionError(tr('python.image_tools.d9b3e4c50d22', p0=f'{status}'), url=url, status=status)
            # 多读一个字节的小片段，超限立即中止，不下载整张大图。
            return response.read(max_bytes + 1)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    data = await asyncio.to_thread(_fetch)
    if len(data) > max_bytes:
        raise ToolExecutionError(
            tr('python.image_tools.9eb076cfc7c4', p0=f'{max_bytes}', p1=f'{url}'),
            url=url,
            size=len(data),
        )
    real_mime, width, height = _validate_image(
        data, label=url, suffix=suffix, max_bytes=max_bytes
    )
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "path": url,
        "source": "url",
        "mime_type": real_mime,
        "bytes": len(data),
        "width": width,
        "height": height,
        "data_url": f"data:{real_mime};base64,{encoded}",
    }


def build_image_tools(cwd: str | None) -> list[ToolDefinition]:
    """构造 read_image 工具。"""

    async def read_image(args: Any, ctx: ToolRunContext) -> Any:
        raw = args.get("path") if isinstance(args, dict) else None
        if not isinstance(raw, str):
            raise ToolExecutionError(tr("python.image_tools.7af9a07106bf"))
        raw = raw.strip()
        if not raw:
            raise ToolExecutionError(tr('python.image_tools.7af9a07106bf'))
        max_bytes = int(args.get("max_bytes", MAX_IMAGE_BYTES))
        if raw.lower().startswith(("http://", "https://")):
            return await read_image_url(raw, max_bytes=max_bytes)
        target = resolve_path(ctx.cwd or cwd, raw)
        info = read_image_file(target, max_bytes=max_bytes)
        info.setdefault("source", "file")
        return info

    def read_image_to_content(args: Any, value: Any) -> list[TextBlock]:
        key = 'image.summary.dimensions' if value.get('width') and value.get('height') else 'image.summary.basic'
        header = tr(key, path=value['path'], mime=value['mime_type'], size=value['bytes'],
                    width=value.get('width'), height=value.get('height'))
        # OpenRouter / Anthropic 兼容协议接受 data URI 内联图片。
        # 这里把 data URI 单独放一个块，方便模型视觉通道直接消费。
        return [
            TextBlock(text=header),
            TextBlock(text=value["data_url"]),
        ]

    def read_image_display(args: Any, value: Any) -> dict[str, Any]:
        return {
            "path": value["path"],
            "mime_type": value["mime_type"],
            "bytes": value["bytes"],
            "width": value.get("width"),
            "height": value.get("height"),
        }

    async def read_images(args: Any, ctx: ToolRunContext) -> Any:
        """按输入顺序读取一组图片，保留每张成功结果和独立失败证据。"""
        paths = args.get('paths', [])
        max_bytes = int(args.get('max_bytes', MAX_IMAGE_BYTES))
        if not 1 <= len(paths) <= MAX_BATCH_IMAGES or not 1 <= max_bytes <= MAX_IMAGE_BYTES:
            raise ToolExecutionError(tr('python.image_tools.61f1fd745ba5'))
        images, errors = [], []
        remaining = MAX_BATCH_IMAGE_BYTES
        for raw in dict.fromkeys(paths):
            try:
                if not str(raw).strip():
                    raise ToolExecutionError(tr('python.image_tools.85fe2b3ddf61'))
                if remaining <= 0:
                    raise ToolExecutionError(tr('python.image_tools.6878b46987bc'))
                info = await read_image({'path': raw, 'max_bytes': min(max_bytes, remaining)}, ctx)
                images.append(info)
                remaining -= info['bytes']
            except Exception as exc:
                errors.append({'path': raw, 'message': str(exc)})
        return {'images': images, 'errors': errors, 'error': bool(errors)}

    def batch_content(args: Any, value: Any) -> list[TextBlock]:
        content = [TextBlock(text=tr('python.image_tools.38000ceba873', p0=f"{len(value['images'])}", p1=f"{len(value['errors'])}"))]
        for image in value['images']:
            content.extend(read_image_to_content(args, image))
        content.extend(TextBlock(text=tr('python.image_tools.fc8536748187', p0=f"{error['path']}", p1=f"{error['message']}")) for error in value['errors'])
        return content

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.image_tools.dbc2850f9708',
                "parameter_keys": {
                    ('properties', 'path', 'description'): 'python.image_tools.5254f0552d0e',
                    ('properties', 'max_bytes', 'description'): ('python.image_tools.4479bfd3a70d', {'p0': f'{MAX_IMAGE_BYTES}'}),
                },
                "name": "read_image",
                "description": (
                    tr('python.image_tools.dbc2850f9708')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                tr('python.image_tools.5254f0552d0e')
                            ),
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": tr('python.image_tools.4479bfd3a70d', p0=f'{MAX_IMAGE_BYTES}'),
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            execute=read_image,
            to_content=read_image_to_content,
            describe=read_image_display,
            timeout=60.0,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.image_tools.a20cddcd14ba',
                'name': 'read_images',
                'description': tr('python.image_tools.a20cddcd14ba'),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'paths': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'minItems': 1, 'maxItems': MAX_BATCH_IMAGES},
                        'max_bytes': {'type': 'integer', 'minimum': 1, 'maximum': MAX_IMAGE_BYTES},
                    },
                    'required': ['paths'], 'additionalProperties': False,
                },
            },
            execute=read_images, to_content=batch_content,
            describe=lambda args, value: {'images': [read_image_display(args, image) for image in value['images']], 'errors': value['errors']},
            effect='read', cancellable=True, idempotent=True, timeout=120.0,
        )
    ]


__all__ = ["build_image_tools", "read_image_file"]
