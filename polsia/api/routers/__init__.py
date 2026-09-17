"""API routers for the Polsia OS."""

from polsia.api.routers.auth import router as auth_router
from polsia.api.routers.companies import router as companies_router
from polsia.api.routers.goals import router as goals_router
from polsia.api.routers.kpis import router as kpis_router
from polsia.api.routers.organizations import router as organizations_router
from polsia.api.routers.tasks import executions_router, tasks_router

__all__ = [
    "auth_router",
    "companies_router",
    "executions_router",
    "goals_router",
    "kpis_router",
    "organizations_router",
    "tasks_router",
]
