"""响应构造与 HTTP 传输层优化（压缩 / ETag / 缓存）。

响应按客户端声明选择 gzip，使用 ETag 处理条件请求。
"""

from __future__ import annotations

from harness.i18n import tr

import gzip
import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import quote

from .router import Response
from .error_messages import public_error_message

#: 超过这个字节数才值得压缩（太小压缩开销反而占优）。
GZIP_MIN_BYTES = 1024

#: 这些 MIME 类型走 gzip；图片/视频/字体已经压缩过，再压是浪费 CPU。
GZIP_CONTENT_TYPES = (
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
    "text/",
)


def content_disposition(name: str, disposition: str = 'attachment') -> str:
    """使用 ASCII 安全的 HTTP 响应头，保留准确的 UTF-8 文件名（RFC 6266）。"""
    if disposition not in {'inline', 'attachment'}:
        raise ValueError(tr('runtime.validation.fa7c8789a814'))
    # 工作区文件名未经过附件准入检查，在构造任一响应头参数前，
    # 也须在此移除控制字符。
    clean = ''.join(char if ord(char) >= 32 and ord(char) != 127 else '_' for char in name)
    fallback = clean.encode('ascii', 'replace').decode('ascii').replace('\\', '_').replace('"', "'") or 'attachment'
    return f'{disposition}; filename="{fallback}"; filename*=UTF-8\'\'{quote(clean, safe="")}'


def accepts_gzip(accept_encoding: str) -> bool:
    """按编码名和质量值协商，明确的 gzip 声明优先于通配符。"""
    wildcard = False
    for part in accept_encoding.lower().split(","):
        coding, *parameters = (piece.strip() for piece in part.split(";"))
        if coding not in {"gzip", "*"}:
            continue
        quality = 1.0
        seen_quality = False
        for parameter in parameters:
            name, separator, value = parameter.partition("=")
            if name.strip() != "q":
                continue
            if seen_quality or not separator or not re.fullmatch(r"(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)", value.strip()):
                quality = 0.0
                break
            seen_quality = True
            quality = float(value.strip())
        if coding == "gzip":
            return quality > 0
        wildcard = quality > 0
    return wildcard


def json_response(
    payload: Any,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    accept_encoding: str = "",
    pretty: bool = False,
) -> Response:
    """构造 JSON 响应，按需 gzip。"""
    # 统一过 to_jsonable：事件 data 是 deep_freeze 后的 MappingProxyType，
    # 直接 dumps 会抛 "Object of type mappingproxy is not JSON serializable"。
    from ..core.jsonutil import to_jsonable

    text = json.dumps(
        to_jsonable(payload),
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    raw = text.encode("utf-8")
    response = Response(status=status, body=raw)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    # API 响应默认不缓存：会话状态一直在变，缓存只会带来"看不到新消息"的错觉。
    response.headers["Cache-Control"] = "no-store"
    if headers:
        response.headers.update(headers)
    return _maybe_gzip(response, accept_encoding)


def text_response(
    text: str,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    headers: Mapping[str, str] | None = None,
    accept_encoding: str = "",
) -> Response:
    response = Response(status=status, body=text.encode("utf-8"))
    response.headers["Content-Type"] = content_type
    if headers:
        response.headers.update(headers)
    return _maybe_gzip(response, accept_encoding)


def bytes_response(
    raw: bytes,
    *,
    status: int = 200,
    content_type: str = "application/octet-stream",
    headers: Mapping[str, str] | None = None,
    accept_encoding: str = "",
) -> Response:
    response = Response(status=status, body=raw)
    response.headers["Content-Type"] = content_type
    if headers:
        response.headers.update(headers)
    return _maybe_gzip(response, accept_encoding)


def error_response(
    message: str,
    *,
    status: int = 400,
    code: str = "error",
    details: Any = None,
    accept_encoding: str = "",
) -> Response:
    payload: dict[str, Any] = {"error": public_error_message(message, code), "code": code}
    if details is not None:
        payload["details"] = details
    return json_response(payload, status=status, accept_encoding=accept_encoding)


def _maybe_gzip(response: Response, accept_encoding: str) -> Response:
    """按需压缩：只压文本类、只压够大的响应。"""
    body = response.body
    if len(body) < GZIP_MIN_BYTES or not accepts_gzip(accept_encoding):
        return response
    content_type = response.headers.get("Content-Type", "")
    if not any(content_type.startswith(prefix) for prefix in GZIP_CONTENT_TYPES):
        return response
    # level=5 是 gzip 在"压缩率 / 速度"上最稳的点：level=9 在大 JSON 上
    # 只多压 2~3%，却要多花一倍 CPU，实时推送场景不值当。
    compressed = gzip.compress(body, compresslevel=5, mtime=0)
    if len(compressed) >= len(body):
        return response
    response.body = compressed
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    return response


def strong_etag(raw: bytes) -> str:
    """内容哈希 ETag。"""
    digest = hashlib.sha1(raw).hexdigest()[:20]
    return f'"{digest}"'


def weak_etag(*parts: object) -> str:
    """由 (路径, mtime, 大小) 组成的弱 ETag —— 静态资源不必每次读全文。"""
    digest = hashlib.sha1("|".join(str(part) for part in parts).encode()).hexdigest()[:20]
    return f'W/"{digest}"'


__all__ = [
    "GZIP_MIN_BYTES",
    "GZIP_CONTENT_TYPES",
    "accepts_gzip",
    "json_response",
    "text_response",
    "bytes_response",
    "content_disposition",
    "error_response",
    "strong_etag",
    "weak_etag",
]
