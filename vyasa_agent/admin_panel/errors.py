# SPDX-License-Identifier: Apache-2.0
"""RFC 7807 problem+json helpers for the admin panel."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"


def _problem(
    *,
    status_code: int,
    title: str,
    detail: str | None,
    trace_id: str | None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status_code,
    }
    if detail:
        body["detail"] = detail
    if trace_id:
        body["trace_id"] = trace_id
    if extra:
        body.update(extra)
    headers = {}
    if trace_id:
        headers["X-Trace-ID"] = trace_id
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    title = _http_title(exc.status_code)
    detail = exc.detail if isinstance(exc.detail, str) else None
    extra = None
    if isinstance(exc.detail, dict):
        extra = {"errors": exc.detail}
    return _problem(
        status_code=exc.status_code,
        title=title,
        detail=detail,
        trace_id=trace_id,
        extra=extra,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    return _problem(
        status_code=422,
        title="validation error",
        detail="request payload did not match schema",
        trace_id=trace_id,
        extra={"errors": exc.errors()},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    return _problem(
        status_code=500,
        title="internal error",
        detail="unexpected server error",
        trace_id=trace_id,
    )


def _http_title(status_code: int) -> str:
    return {
        400: "bad request",
        401: "unauthorized",
        403: "forbidden",
        404: "not found",
        409: "conflict",
        422: "validation error",
        429: "too many requests",
        500: "internal error",
        503: "service unavailable",
    }.get(status_code, "error")


__all__ = [
    "PROBLEM_CONTENT_TYPE",
    "http_exception_handler",
    "unhandled_exception_handler",
    "validation_exception_handler",
]
