"""RFC 9457 problem-details mapping for PolsiaError and unexpected errors."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from polsia.core.shared.errors import PolsiaError
from polsia.infra.logging import get_logger

PROBLEM_CONTENT_TYPE = "application/problem+json"
_PROBLEM_TYPE_BASE = "https://docs.polsia.dev/problems/"


def problem_body(
    *,
    status: int,
    code: str,
    detail: str | None,
    title: str | None = None,
) -> dict[str, Any]:
    """Build an RFC 9457 problem-details document (with Polsia's ``code``)."""
    return {
        "type": f"{_PROBLEM_TYPE_BASE}{code}",
        "title": title or code.replace("_", " "),
        "status": status,
        "code": code,
        "detail": detail or code.replace("_", " "),
    }


def _problem_response(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=body["status"],
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
    )


def install_error_handlers(app: FastAPI, *, hide_internals: bool = True) -> None:
    """Register global exception handlers on the app.

    - PolsiaError -> problem-details using the error's ``status_code``/``code``.
    - RequestValidationError -> 422 problem-details (consistent shape).
    - Starlette HTTPException -> problem-details (404s etc. keep the same shape).
    - Exception -> 500 problem-details; internals hidden unless
      ``hide_internals`` is False (dev convenience).
    """
    logger = get_logger("polsia.api.errors")

    @app.exception_handler(PolsiaError)
    async def polsia_error_handler(request: Request, err: PolsiaError) -> JSONResponse:
        body = problem_body(
            status=err.status_code,
            code=err.code,
            detail=str(err),
        )
        return _problem_response(body)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = problem_body(
            status=422,
            code="validation_error",
            detail="request validation failed",
        )
        # Error locations/field names only — never echo user secrets.
        body["errors"] = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg", "")} for err in exc.errors()
        ]
        return _problem_response(body)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        body = problem_body(
            status=exc.status_code,
            code=code,
            detail=str(exc.detail) if exc.detail else code,
        )
        return _problem_response(body)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error",
            method=request.method,
            path=request.url.path,
        )
        detail = None if hide_internals else str(exc)
        body = problem_body(
            status=500,
            code="internal_error",
            detail=detail or "an unexpected error occurred",
        )
        return _problem_response(body)
