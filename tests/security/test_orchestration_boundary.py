"""Regression guard: the orchestration execution surface must never be exposed
via HTTP before permission/approval enforcement is wired into the call path.

Background (Phase 2 review): ``OrchestrationService.execute_task`` resolves the
agent and passes ``tool_permissions`` through to the runtime, but does not
itself invoke the PermissionEngine gate. Phase 2 ships this as a port-level
contract only — no HTTP route reaches it, so there is no exploitable path
today. This test pins that invariant: if a Phase 3 change mounts an
orchestration/execution route, it must fail here first and force an explicit
permission-enforcement decision (documented in the Phase 2 review).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient
from polsia.api.main import create_app
from polsia.infra.settings import Settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_FORBIDDEN_FRAGMENTS = (
    "/orchestrat",  # orchestration routes
    "/execute",  # direct execution triggers
    "/runtime",
)


def _walk(route: Any) -> list[str]:
    """Collect paths, recursing into mounted routers.

    FastAPI >=0.141 wraps included routers in a lazy ``_IncludedRouter`` that
    keeps the original router in ``original_router`` instead of flattening
    routes onto the app.
    """
    path = getattr(route, "path", None)
    if path:
        return [path]
    original = getattr(route, "original_router", None)
    if original is not None:
        return [p for child in original.routes for p in _walk(child)]
    return []


def _collect_routes(app: Any) -> list[str]:
    return sorted({p for route in app.routes for p in _walk(route)})


async def test_production_app_exposes_no_orchestration_routes() -> None:
    """The fully-wired app (session_factory set) must not expose any
    orchestration/execution route in Phase 2."""
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
        paths = _collect_routes(app)
        violations = [
            p for p in paths for fragment in _FORBIDDEN_FRAGMENTS if fragment in p.lower()
        ]
        assert violations == [], (
            "Orchestration/execution surface became HTTP-exposed without a "
            f"permission-enforcement decision: {violations}"
        )
    finally:
        await engine.dispose()


async def test_execute_task_has_no_service_level_bypass_via_routers() -> None:
    """No router module may call OrchestrationService methods directly; the
    only legitimate Phase 2 callers are tests and the composition root."""
    import pathlib

    routers_dir = pathlib.Path("polsia/api/routers")
    offenders: list[str] = []
    for py in routers_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "OrchestrationService" in text or "execute_task" in text:
            offenders.append(str(py))
    assert offenders == [], (
        f"Router files reference the orchestration execution surface: {offenders}"
    )


async def test_route_mount_remains_explicit() -> None:
    """Sanity: the explicit mount point exists (routers argument) so a future
    enforcement-wired orchestration router has a single, reviewable place."""
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
        assert "/__test_marker__" in _collect_routes(app)
    finally:
        await engine.dispose()


_ = AsyncClient, ASGITransport  # referenced for typing parity with other suites
