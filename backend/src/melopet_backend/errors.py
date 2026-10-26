from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("melopet_backend")


@dataclass(slots=True)
class AppError(Exception):
    error_code: str
    safe_message: str
    status_code: int = 400
    details: dict[str, Any] | None = None


def error_envelope(request_id: str, error: AppError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "requestId": request_id,
        "status": "error",
        "errorCode": error.error_code,
        "safeMessage": error.safe_message,
    }
    if error.details:
        payload["details"] = error.details
    return payload


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(error_envelope(request_id, exc), status_code=exc.status_code)


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception(
        "unhandled_request_error request_id=%s method=%s path=%s error_type=%s",
        request_id,
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    error = AppError(
        "INTERNAL_ERROR",
        "服务暂时无法完成该操作，请稍后重试。",
        500,
    )
    return JSONResponse(error_envelope(request_id, error), status_code=500)
