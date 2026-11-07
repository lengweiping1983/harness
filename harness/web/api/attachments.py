"""附件接口：上传（准入 + 落盘）与回读。

线格式选择：``POST /api/v1/attachments`` 收JSON 而不是 ``multipart/form-data``。
当前接口使用 ``json`` + ``base64`` 编码附件内容。代价是大小膨胀约 33%，但反过来
换来了三个更重要的东西：限额可以在解析前就按声明长度拒掉、
base64 可以校验规范性（见 :func:`decode_canonical_base64`）、
以及请求体大小能被 :mod:`harness.web.server` 一次性拦住。

回读端点 ``GET /api/v1/attachments/{id}`` 是**前端性能的主要杠杆**：内容寻址
意味着同一个 id 的字节永远不变，所以响应直接打
``Cache-Control: private, max-age=31536000, immutable``。历史消息里的
截图第二次打开就是纯内存命中，零网络。
"""

from __future__ import annotations

from harness.i18n import tr

import base64
import binascii
from typing import Any

from ..attachments import AttachmentRejected, admit_batch
from ..attachment_store import AttachmentStore, AttachmentStorageError, default_store_root, is_attachment_id
from ..errors import ApiError, BadRequest, NotFound, PayloadTooLarge, ServiceUnavailable
from ..responses import bytes_response, json_response, strong_etag, content_disposition as _content_disposition
from ..router import Router
from ...tools.schema import validate
from ..input_schemas import UPLOAD_SCHEMA

#: 声明长度就能拒掉的上限。base64 会把大小放大 4/3，所以线上的门槛
#: 比领域层的批量限额更宽一点，否则合法的最大批次会在解码前被误杀。
MAX_REQUEST_BYTES = 192 * 1024 * 1024


def _failure(error_type: type[ApiError], key: str, *, code: str, details: dict[str, Any] | None = None, **params: str | int) -> ApiError:
    """附件拒绝保留资源身份与原有诊断细节，不按正文反查。"""
    return error_type(tr(key, **params), code=code, details={**(details or {}), "copy": {"key": key, "params": params}})


def decode_canonical_base64(text: object) -> bytes:
    """解码 base64，并要求它是**规范**编码。

    Python 的 :func:`base64.b64decode` 默认 ``validate=False``，会悄悄跳过
    换行、空格，甚至容忍错误的 padding。这意味着同一个 id 的请求可以有无数种
    字节表示，而 :func:`harness.web.attachments.content_id` 只对**一种**正确。
    所以这里解码后再编一次，对不上就拒绝 —— 用一条清晰的错误换掉一整类
    难查的"内容寻址对不上"故障。
    """
    if not isinstance(text, str):
        raise _failure(BadRequest, 'ui.attachments.server.dataRequired', code='attachment/bad-payload')
    if len(text) % 4 != 0:
        raise _failure(BadRequest, 'ui.attachments.server.base64Length', code='attachment/bad-base64')
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _failure(BadRequest, 'ui.attachments.server.base64Decode', code='attachment/bad-base64', p0=f'{exc}') from exc
    if base64.b64encode(raw).decode("ascii") != text:
        raise _failure(BadRequest, 'ui.attachments.server.base64Canonical', code='attachment/non-canonical-base64')
    return raw


def _store(ctx: Any) -> AttachmentStore:
    """本上下文独占的附件仓库。

    挂在 ctx 上而不是模块级单例：同一进程可能先后起多个 ``serve()``
    （测试里就是这样），模块级状态会让后一个实例写到前一个的目录。
    """
    store = ctx.__dict__.get("_attachment_store")
    if store is None:
        home = getattr(getattr(ctx.harness, "config", None), "home", None)
        store = AttachmentStore(default_store_root(home))
        ctx.__dict__["_attachment_store"] = store
    return store


def build_router(ctx: Any) -> Router:
    router = Router()

    @router.post("/api/v1/attachments")
    def upload(request: Any) -> Any:
        """上传一批附件。

        请求体 ``{"files": [{"name": ..., "mediaType": ..., "data": "<base64>"}]}``。
        准入规则完全交给领域层（:func:`admit_batch`）：只看字节魔数，
        不信调用方声明的类型。任何一个文件不合格就整批拒绝，返回 4xx 与
        机器可读的 ``code``，前端据此给出针对性提示。
        """
        payload = request.json()
        raw_files = payload.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise _failure(BadRequest, 'ui.attachments.server.filesRequired', code='attachment/no-files')

        for index, entry in enumerate(raw_files):
            if not isinstance(entry, dict):
                raise _failure(BadRequest, 'ui.attachments.server.entryObject', code='attachment/bad-payload', p0=f'{index + 1}')
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise _failure(BadRequest, 'ui.attachments.server.nameRequired', code='attachment/missing-name', p0=f'{index + 1}')
        if validate(payload, UPLOAD_SCHEMA):
            raise _failure(BadRequest, 'ui.attachments.server.invalidPayload', code='attachment/bad-payload')

        items: list[tuple[bytes, str, str]] = []
        declared_total = 0
        for entry in raw_files:
            name = entry['name']
            data = decode_canonical_base64(entry.get("data"))
            declared = entry.get("mediaType")
            items.append((data, name, declared if isinstance(declared, str) else ""))
            declared_total += len(data)

        if declared_total > MAX_REQUEST_BYTES:
            raise _failure(PayloadTooLarge, 'ui.attachments.server.requestTooLarge', code='attachment/request-too-large', details={'bytes': declared_total, 'maxBytes': MAX_REQUEST_BYTES})

        try:
            admitted = admit_batch(items)
        except AttachmentRejected as exc:
            raise BadRequest(str(exc), code=exc.code, details={"copy": exc.copy}) from exc

        store = _store(ctx)
        stored = [store.put(data, meta) for (data, _, _), meta in zip(items, admitted)]
        try:
            stats = store.stats()
        except AttachmentStorageError as exc:
            # 附件已经写入，统计失败不能作为执行前拒绝；保留引用供核对，不重放上传。
            raise _failure(ServiceUnavailable, 'ui.attachments.server.statsFailed', code='attachment/stats-unavailable',
                           details={'attachments': [entry.attachment.to_json() for entry in stored]}) from exc

        return json_response(
            {
                "attachments": [entry.attachment.to_json() for entry in stored],
                # 顺带回一份仓库统计：前端可以显示"本地已缓存 N 个附件"。
                "stats": stats,
            },
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/attachments/{attachment_id}")
    def fetch(request: Any, attachment_id: str = "") -> Any:
        """按 id 回读字节。

        内容寻址 = 不可变，所以这张图/这个文件在它整个生命周期里字节不变，
        可以放心给一年期immutable 缓存。
        """
        if not is_attachment_id(attachment_id):
            # 非法 id 直接 404 而不是 400：调用方只有两种可能 —— 打错了，
            # 或者在试探目录穿越。前者不该看到 400 的细节，后者不该看到任何提示。
            raise _failure(NotFound, 'ui.attachments.server.notFound', code='attachment/not-found')

        entry = _store(ctx).describe(attachment_id)
        if entry is None:
            raise _failure(NotFound, 'ui.attachments.server.notFound', code='attachment/not-found')

        raw = _store(ctx).read(attachment_id)

        etag = strong_etag(raw)
        if request.header("If-None-Match") == etag:
            # 命中：204 秒回。历史消息反复滚动时前端会刷出大量重复请求，
            # 这一步把它们变成几十字节的空响应。
            response = bytes_response(b"", status=304)
            response.headers["ETag"] = etag
            return response

        response = bytes_response(
            raw,
            content_type=entry.attachment.media_type,
            headers={
                "ETag": etag,
                "Cache-Control": "private, max-age=31536000, immutable",
                "Content-Disposition": _content_disposition(entry.attachment.name),
                "X-Content-Type-Options": "nosniff",
            },
        )
        return response

    return router


__all__ = ["build_router", "decode_canonical_base64", "MAX_REQUEST_BYTES"]
