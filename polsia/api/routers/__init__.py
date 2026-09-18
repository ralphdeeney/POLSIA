"""API routers for the Polsia OS."""

from polsia.api.routers.auth import router as auth_router
from polsia.api.routers.companies import router as companies_router
from polsia.api.routers.goals import router as goals_router
from polsia.api.routers.kpis import router as kpis_router
from polsia.api.routers.memory import memory_router
from polsia.api.routers.organizations import router as organizations_router
from polsia.api.routers.permissions import permissions_router
from polsia.api.routers.registries import (
    agents_router,
    models_router,
    skills_router,
    tools_router,
)
from polsia.api.routers.tasks import executions_router, tasks_router
from polsia.api.routers.usage import router as usage_router

__all__ = [
    "agents_router",
    "auth_router",
    "companies_router",
    "executions_router",
    "goals_router",
    "kpis_router",
    "memory_router",
    "models_router",
    "organizations_router",
    "permissions_router",
    "skills_router",
    "tasks_router",
    "tools_router",
    "usage_router",
]
