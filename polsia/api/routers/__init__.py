"""API routers for the Polsia OS (auth, organizations, companies)."""

from polsia.api.routers.auth import router as auth_router
from polsia.api.routers.companies import router as companies_router
from polsia.api.routers.organizations import router as organizations_router

__all__ = ["auth_router", "companies_router", "organizations_router"]
