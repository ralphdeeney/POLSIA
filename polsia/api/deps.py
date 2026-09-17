"""FastAPI dependencies: sessions, current user, and services.

The composition root (:func:`polsia.api.main.create_app`) stores the session
factory, security adapters, event bus and any injected services on
``app.state``. This module builds the per-request object graph from those:

- ``get_db_session``: request-scoped :class:`AsyncSession` (commits on clean
  exit via :func:`polsia.infra.db.get_session`; repositories only flush).
- ``get_current_user``: HTTPBearer -> verify access token -> load the User.
  Any failure maps to 401 with a ``WWW-Authenticate: Bearer`` header.
- ``get_auth_service`` / ``get_organization_service`` / ``get_company_service``:
  cheap per-request construction of repos + services from the request session.
  Entries in ``app.state.services`` (e.g. ``{"auth": ...}`` from tests) take
  precedence over the built implementations.

Routers stay thin: all authorization happens inside the services.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyGoalRepository,
    SqlAlchemyKpiRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.task_repositories import (
    SqlAlchemyTaskDependencyRepository,
    SqlAlchemyTaskExecutionRepository,
    SqlAlchemyTaskRepository,
)
from polsia.core.auth.service import AuthService
from polsia.core.shared.errors import (
    CompanyNotFoundError,
    InvalidCredentials,
    OrganizationNotFoundError,
    UserNotFoundError,
)
from polsia.core.shared.ids import (
    AgentId,
    CompanyId,
    GoalId,
    KpiId,
    OrganizationId,
    TaskId,
    UserId,
)
from polsia.core.tenancy.entities import User
from polsia.infra.db import get_session
from polsia.infra.settings import Settings
from polsia.services.planning import GoalService, KpiService
from polsia.services.tasks import ExecutionService, TaskService
from polsia.services.tenancy import CompanyService, OrganizationService

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def _services(request: Request) -> dict[str, Any]:
    return cast(dict[str, Any], request.app.state.services)


# -- Session -----------------------------------------------------------------


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped, transactional session from app.state."""
    factory = request.app.state.session_factory
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="database not configured",
        )
    async for session in get_session(cast(Any, factory)):
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


# -- Current user --------------------------------------------------------------


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Resolve the authenticated User from a bearer access token.

    Raises 401 (with WWW-Authenticate) on missing/invalid/expired tokens and
    when the token's subject no longer exists.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers=_WWW_AUTHENTICATE,
        )
    issuer = _services(request).get("tokens") or request.app.state.token_issuer
    try:
        claims = issuer.verify_token(credentials.credentials, expected_typ="access")
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers=_WWW_AUTHENTICATE,
        ) from exc
    user = await SqlAlchemyUserRepository(session).get(claims.sub)
    if user is None or not user.is_active():
        # Suspended users with a still-valid token are rejected here too.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers=_WWW_AUTHENTICATE,
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# -- Services ----------------------------------------------------------------


def get_auth_service(request: Request, session: SessionDep) -> AuthService:
    injected = _services(request).get("auth")
    if injected is not None:
        return cast(AuthService, injected)
    hasher = _services(request).get("hasher") or request.app.state.password_hasher
    tokens = _services(request).get("tokens") or request.app.state.token_issuer
    return AuthService(SqlAlchemyUserRepository(session), hasher, tokens)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_organization_service(request: Request, session: SessionDep) -> OrganizationService:
    injected = _services(request).get("orgs")
    if injected is not None:
        return cast(OrganizationService, injected)
    bus = request.app.state.event_bus
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="event bus not configured",
        )
    return OrganizationService(
        organizations=SqlAlchemyOrganizationRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
        bus=bus,
        users=SqlAlchemyUserRepository(session),
    )


OrganizationServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]


def get_company_service(request: Request, session: SessionDep) -> CompanyService:
    injected = _services(request).get("companies")
    if injected is not None:
        return cast(CompanyService, injected)
    bus = request.app.state.event_bus
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="event bus not configured",
        )
    return CompanyService(
        companies=SqlAlchemyCompanyRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
        bus=bus,
    )


CompanyServiceDep = Annotated[CompanyService, Depends(get_company_service)]


# -- Phase 2: planning services -------------------------------------------------


def get_goal_service(request: Request, session: SessionDep) -> GoalService:
    injected = _services(request).get("goals")
    if injected is not None:
        return cast(GoalService, injected)
    bus = request.app.state.event_bus
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="event bus not configured",
        )
    return GoalService(
        goals=SqlAlchemyGoalRepository(session),
        companies=SqlAlchemyCompanyRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
        bus=bus,
    )


GoalServiceDep = Annotated[GoalService, Depends(get_goal_service)]


def get_kpi_service(request: Request, session: SessionDep) -> KpiService:
    injected = _services(request).get("kpis")
    if injected is not None:
        return cast(KpiService, injected)
    bus = request.app.state.event_bus
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="event bus not configured",
        )
    return KpiService(
        kpis=SqlAlchemyKpiRepository(session),
        goals=SqlAlchemyGoalRepository(session),
        companies=SqlAlchemyCompanyRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
        bus=bus,
    )


KpiServiceDep = Annotated[KpiService, Depends(get_kpi_service)]


# -- Path/id parsing ------------------------------------------------------------


def parse_organization_id(raw: str) -> OrganizationId:
    """Parse an org id from a path parameter; malformed ids are 404s."""
    try:
        return OrganizationId.parse(raw)
    except ValueError as exc:
        raise OrganizationNotFoundError(f"organization {raw!r} not found") from exc


def parse_company_id(raw: str) -> CompanyId:
    """Parse a company id from a path parameter; malformed ids are 404s."""
    try:
        return CompanyId.parse(raw)
    except ValueError as exc:
        raise CompanyNotFoundError(f"company {raw!r} not found") from exc


def parse_user_id(raw: str) -> UserId:
    """Parse a user id from a request payload; malformed ids are 404s."""
    try:
        return UserId.parse(raw)
    except ValueError as exc:
        raise UserNotFoundError(f"user {raw!r} not found") from exc


def parse_goal_id(raw: str) -> GoalId:
    """Parse a goal id from a path/payload; malformed ids are 404s."""
    from polsia.core.shared.errors import NotFoundError

    try:
        return GoalId.parse(raw)
    except ValueError as exc:
        raise NotFoundError(f"goal {raw!r} not found") from exc


def parse_kpi_id(raw: str) -> KpiId:
    """Parse a KPI id from a path/payload; malformed ids are 404s."""
    from polsia.core.shared.errors import NotFoundError

    try:
        return KpiId.parse(raw)
    except ValueError as exc:
        raise NotFoundError(f"kpi {raw!r} not found") from exc


def parse_task_id(raw: str) -> TaskId:
    """Parse a task id from a path/payload; malformed ids are 404s."""
    from polsia.core.shared.errors import NotFoundError

    try:
        return TaskId.parse(raw)
    except ValueError as exc:
        raise NotFoundError(f"task {raw!r} not found") from exc


def parse_agent_id(raw: str) -> AgentId:
    """Parse an agent id from a path/payload; malformed ids are 404s."""
    from polsia.core.shared.errors import NotFoundError

    try:
        return AgentId.parse(raw)
    except ValueError as exc:
        raise NotFoundError(f"agent {raw!r} not found") from exc


# -- Phase 2: task services ------------------------------------------------------


def get_task_service(request: Request, session: SessionDep) -> TaskService:
    injected = _services(request).get("tasks")
    if injected is not None:
        return cast(TaskService, injected)
    bus = request.app.state.event_bus
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="event bus not configured",
        )
    return TaskService(
        tasks=SqlAlchemyTaskRepository(session),
        dependencies=SqlAlchemyTaskDependencyRepository(session),
        companies=SqlAlchemyCompanyRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
        bus=bus,
    )


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


def get_execution_service(request: Request, session: SessionDep) -> ExecutionService:
    injected = _services(request).get("executions")
    if injected is not None:
        return cast(ExecutionService, injected)
    bus = request.app.state.event_bus
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="event bus not configured",
        )
    return ExecutionService(
        tasks=SqlAlchemyTaskRepository(session),
        executions=SqlAlchemyTaskExecutionRepository(session),
        dependencies=SqlAlchemyTaskDependencyRepository(session),
        companies=SqlAlchemyCompanyRepository(session),
        memberships=SqlAlchemyMembershipRepository(session),
        bus=bus,
    )


ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]
