"""FastAPI application shell.

Provides:
- ``create_app()`` factory with optional wiring hooks (no repos wired by
  default — integration is done by the composition root);
- GET /healthz (liveness, always 200) and GET /readyz (DB connectivity when
  a session factory is wired);
- request-context middleware (X-Request-ID / trace ids, structlog contextvars);
- global RFC 9457 problem-details error handling.

Auth/organization/company routers are mounted automatically when a
``session_factory`` is provided (the composition-root wiring); additional or
replacement routers can be attached via the ``routers`` argument or
:func:`mount_routers`. This module defines no endpoints of its own beyond the
health probes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from polsia.adapters.events import InProcessEventBus, LoggingSubscriber
from polsia.adapters.security.passwords import BcryptPasswordHasher
from polsia.adapters.security.tokens import PyJWTTokenIssuer
from polsia.api.context import RequestContextMiddleware
from polsia.api.errors import install_error_handlers
from polsia.api.routers import (
    agents_router,
    auth_router,
    companies_router,
    executions_router,
    goals_router,
    kpis_router,
    memory_router,
    models_router,
    organizations_router,
    permissions_router,
    skills_router,
    tasks_router,
    tools_router,
)
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
    - ``session_factory``: SQLAlchemy async sessionmaker. When set, the full
      stack is wired: /readyz checks DB connectivity, the auth/org/company
      routers are mounted, and an :class:`InProcessEventBus` (with a
      :class:`LoggingSubscriber`) is installed on ``app.state.event_bus``.
      Repositories and use-case services are constructed per request from the
      request-scoped session by :mod:`polsia.api.deps`.
    - ``routers``: APIRouter instances to include immediately. Routers can
      also be attached later via :func:`mount_routers`.
    - ``services``: dependency-injection mapping stored on
      ``app.state.services`` (e.g. ``{"auth": AuthService(...)}``,
      ``{"orgs": ...}``, ``{"companies": ...}``, ``{"tokens": ...}``).
      Entries take precedence over the built implementations when
      :mod:`polsia.api.deps` resolves dependencies.
    """
    configure_logging()
    settings = settings or get_settings()
    services = services or {}

    app = FastAPI(
        title="Polsia",
        description="Autonomous Business Operating System",
        version="0.1.0",
        # Docs/openapi are a development convenience — disabled in production.
        docs_url="/docs" if settings.env == "development" else None,
        redoc_url="/redoc" if settings.env == "development" else None,
        openapi_url="/openapi.json" if settings.env == "development" else None,
    )

    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.services = services
    app.state.router_registry = []
    app.state.password_hasher = services.get("hasher") or BcryptPasswordHasher()
    app.state.token_issuer = services.get("tokens") or PyJWTTokenIssuer(settings)

    app.add_middleware(RequestContextMiddleware)
    _install_security_headers(app)
    install_error_handlers(app, hide_internals=settings.env != "development")

    _register_health_endpoints(app)

    for router in routers:
        mount_routers(app, router)

    if session_factory is not None:
        bus = services.get("bus")
        if not isinstance(bus, InProcessEventBus):
            bus = InProcessEventBus()
            bus.subscribe("*", LoggingSubscriber())
        app.state.event_bus = bus
        mount_routers(
            app,
            auth_router,
            organizations_router,
            companies_router,
            goals_router,
            kpis_router,
            tasks_router,
            executions_router,
            agents_router,
            permissions_router,
            memory_router,
            skills_router,
            tools_router,
            models_router,
        )
    else:
        app.state.event_bus = None

    return app


def mount_routers(app: FastAPI, *routers: APIRouter) -> None:
    """Attach APIRouter instances to the app and record them in app.state.

    This is the registry point for the org/company/auth routers.
    """
    for router in routers:
        app.include_router(router)
        app.state.router_registry.append(router)


def _install_security_headers(app: FastAPI) -> None:
    """Baseline security headers on every response."""

    @app.middleware("http")
    async def security_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response


def _production_app() -> FastAPI:
    """Composition root for production: env settings -> engine -> session
    factory -> fully-wired app (routers mounted, event bus installed)."""
    settings = get_settings()
    from polsia.infra.db import make_engine, make_session_factory

    engine = make_engine(settings.database_url, settings=settings)
    return create_app(settings, session_factory=make_session_factory(engine))


def __getattr__(name: str) -> Any:
    """PEP 562 lazy ``app`` attribute so ``uvicorn polsia.api.main:app`` works
    in production without creating an engine on import (tests build their own
    app via :func:`create_app`)."""
    if name == "app":
        instance = _production_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
