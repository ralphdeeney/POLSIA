"""FastAPI application shell.

Provides:
- ``create_app()`` factory with optional wiring hooks (no repos wired by
  default — integration is done by the composition root);
- GET /healthz (liveness, always 200) and GET /readyz (DB connectivity when
  a session factory is wired);
- request-context middleware (X-Request-ID / trace ids, structlog contextvars);
- global RFC 9457 problem-details error handling.

Routers for orgs/companies/auth endpoints are mounted via the ``routers``
argument or :func:`mount_routers` / ``app.state`` (see below); this module
does not define them.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from polsia.api.context import RequestContextMiddleware
from polsia.api.errors import install_error_handlers
from polsia.infra.logging import configure_logging, get_logger
from polsia.infra.settings import Settings, get_settings

_PROBLEM_DB_ERROR = "database unreachable"


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    routers: Iterable[APIRouter] = (),
    services: dict[str, Any] | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Wiring hooks (all optional, default = no repositories wired):

    - ``settings``: polsia Settings; defaults to ``get_settings()``.
    - ``session_factory``: SQLAlchemy async sessionmaker; when set, /readyz
      checks DB connectivity. Stored on ``app.state.session_factory``.
    - ``routers``: APIRouter instances to include immediately. Routers can
      also be attached later via :func:`mount_routers`.
    - ``services``: dependency-injection mapping (e.g. ``{"auth": AuthService(...)}``)
      stored on ``app.state.services`` so routers can resolve dependencies.
    """
    configure_logging()
    settings = settings or get_settings()

    app = FastAPI(
        title="Polsia",
        description="Autonomous Business Operating System",
        version="0.1.0",
    )

    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.services = services or {}
    app.state.router_registry = []

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app, hide_internals=settings.env != "development")

    _register_health_endpoints(app)

    for router in routers:
        mount_routers(app, router)

    return app


def mount_routers(app: FastAPI, *routers: APIRouter) -> None:
    """Attach APIRouter instances to the app and record them in app.state.

    This is the registry point for the org/company/auth routers.
    """
    for router in routers:
        app.include_router(router)
        app.state.router_registry.append(router)


def _register_health_endpoints(app: FastAPI) -> None:
    logger = get_logger("polsia.api.health")

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> Any:
        session_factory = app.state.session_factory
        if session_factory is None:
            return {"status": "ok", "db": "not-configured"}
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            logger.warning("readiness_check_failed")
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "db": "error"},
            )
        return {"status": "ok", "db": "ok"}
