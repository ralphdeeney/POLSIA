"""Regression guards for the Phase 3 HTTP execution boundary.

The public API may expose the approved task-attempt endpoint, which records an
attempt only after the shared permission/approval boundary allows it. It must
not expose orchestration or runtime dispatch over HTTP.
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter
from polsia.api.deps import get_execution_service
from polsia.api.main import create_app
from polsia.infra.settings import Settings
from polsia.services.tasks import ExecutionService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_ATTEMPT_START_PATH = (
    "/organizations/{org_id}/companies/{company_id}/executions/tasks/{task_id}/start"
)


def _walk(route: Any) -> list[Any]:
    """Collect route objects, recursing into FastAPI lazy included routers."""
    if getattr(route, "path", None):
        return [route]
    original = getattr(route, "original_router", None)
    if original is not None:
        return [item for child in original.routes for item in _walk(child)]
    return []


def _collect_routes(app: Any) -> list[Any]:
    return [item for route in app.routes for item in _walk(route)]


def _collect_paths(app: Any) -> list[str]:
    return sorted({route.path for route in _collect_routes(app)})


async def test_production_app_exposes_only_attempt_start_not_runtime_dispatch() -> None:
    """Approved attempt recording is mounted; runtime/orchestration dispatch is not."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        app = create_app(
            Settings(env="test", jwt_secret="test-secret"),
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
        )
        routes = _collect_routes(app)
        attempt_routes = [
            route
            for route in routes
            if route.path == _ATTEMPT_START_PATH and "POST" in (route.methods or set())
        ]
        assert len(attempt_routes) == 1
        assert attempt_routes[0].endpoint.__name__ == "start_execution"

        forbidden = [
            route.path
            for route in routes
            if "/orchestration" in route.path.lower()
            or "/runtime" in route.path.lower()
            or route.path.lower().endswith("/execute")
            or "/execute/" in route.path.lower()
        ]
        assert forbidden == [], f"runtime/orchestration HTTP surface exposed: {forbidden}"
    finally:
        await engine.dispose()


def test_attempt_service_and_api_provider_require_shared_authorization() -> None:
    """Attempt creation cannot be composed without the shared authorization boundary."""
    constructor = inspect.signature(ExecutionService.__init__).parameters["authorization"]
    provider = inspect.signature(get_execution_service).parameters["authorization"]
    assert constructor.default is inspect.Parameter.empty
    assert provider.default is inspect.Parameter.empty


async def test_execute_task_has_no_service_level_bypass_via_routers() -> None:
    """No router may dispatch through OrchestrationService or execute_task directly."""
    import pathlib

    routers_dir = pathlib.Path("polsia/api/routers")
    offenders: list[str] = []
    for py in routers_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "OrchestrationService" in text or "execute_task" in text:
            offenders.append(str(py))
    assert offenders == [], f"router files reference runtime orchestration: {offenders}"


async def test_route_mount_remains_explicit() -> None:
    """Custom router mounting remains explicit and reviewable."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        marker_router = APIRouter()

        @marker_router.get("/__test_marker__")
        async def marker() -> dict[str, str]:
            return {"ok": "1"}

        app = create_app(
            Settings(env="test", jwt_secret="test-secret"),
            routers=(marker_router,),
        )
        assert "/__test_marker__" in _collect_paths(app)
    finally:
        await engine.dispose()
