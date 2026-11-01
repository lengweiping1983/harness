"""Web 层统一异常。

API 层用 :class:`ApiError` 表达"可预期的失败"，由 HTTP 层统一翻译成
``{"error": ..., "code": ..., "details": ...}`` 的 JSON；未捕获异常则退化成
500，避免把栈信息泄露到浏览器。
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """带 HTTP 状态码与机器可读 code 的 API 异常。"""

    status: int = 400
    code: str = "bad-request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.message, "code": self.code}
        if self.details is not None:
            payload["details"] = self.details
        return payload


class BadRequest(ApiError):
    status = 400
    code = "bad-request"


class NotFound(ApiError):
    status = 404
    code = "not-found"


class Conflict(ApiError):
    status = 409
    code = "conflict"


class PayloadTooLarge(ApiError):
    status = 413
    code = "payload-too-large"


class ServiceUnavailable(ApiError):
    status = 503
    code = "unavailable"


__all__ = [
    "ApiError",
    "BadRequest",
    "NotFound",
    "Conflict",
    "PayloadTooLarge",
    "ServiceUnavailable",
]
