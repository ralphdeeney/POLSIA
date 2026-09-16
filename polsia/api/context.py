"""Request-context middleware: request-id/trace-id contextvars + structlog.

Every request gets:
- ``request_id``: taken from the incoming ``X-Request-ID`` header when
  present, otherwise generated (ULID). Always echoed back in the
  ``X-Request-ID`` response header.
- ``trace_id``: taken from the incoming ``X-Trace-ID`` header when present,
  otherwise equal to the request id. Echoed back in ``X-Trace-ID``.

Both are bound to structlog's contextvars (merged by
``structlog.contextvars.merge_contextvars`` in polsia.infra.logging) and to
plain contextvars exposed via :func:`get_request_id` / :func:`get_trace_id`
so any layer can read them without threading parameters through.

Security: only method, path, status and ids are logged — never headers,
passwords or tokens.
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from ulid import ULID

from polsia.infra.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_request_id() -> str:
    """Request id of the current request (empty string outside a request)."""
    return _request_id_var.get()


def get_trace_id() -> str:
    """Trace id of the current request (empty string outside a request)."""
    return _trace_id_var.get()


def _new_request_id() -> str:
    return f"req_{ULID()}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates request+trace ids and logs request completion."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or _new_request_id()
        trace_id = request.headers.get(TRACE_ID_HEADER) or request_id

        # Reset per request so stale values from previous requests on the
        # same task context cannot leak into logs.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, trace_id=trace_id)
        request_id_token = _request_id_var.set(request_id)
        trace_id_token = _trace_id_var.set(trace_id)

        logger = get_logger("polsia.api.request")
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                request_id=request_id,
                trace_id=trace_id,
            )
            raise
        finally:
            _request_id_var.reset(request_id_token)
            _trace_id_var.reset(trace_id_token)

        response.headers[REQUEST_ID_HEADER] = request_id
        if TRACE_ID_HEADER in request.headers:
            response.headers[TRACE_ID_HEADER] = trace_id
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            request_id=request_id,
            trace_id=trace_id,
        )
        return response
