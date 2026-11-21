"""统一错误分类与错误码（依据 C05 契约 7 节建议，映射 HTTP 状态）。

错误分类：
VALIDATION_ERROR / AUTHORIZATION_ERROR / NOT_FOUND / CONFLICT /
PRECONDITION_FAILED / RESOURCE_UNAVAILABLE / TIMEOUT / RATE_LIMITED /
DEPENDENCY_ERROR / INTERNAL_ERROR
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AUTHORIZATION_ERROR: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.PRECONDITION_FAILED: 412,
    ErrorCode.RESOURCE_UNAVAILABLE: 503,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.DEPENDENCY_ERROR: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


class AppError(Exception):
    """所有业务/平台错误的基类。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]


def error_response(
    code: ErrorCode, message: str, *, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=_HTTP_STATUS[code],
        content={
            "error": {
                "code": code.value,
                "message": message,
                "details": details or {},
            }
        },
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.code, exc.message, details=exc.details)
