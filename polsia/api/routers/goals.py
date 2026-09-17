"""Goal endpoints, nested under organization + company (ADR-0002 scoping).

Thin HTTP surface over :class:`polsia.services.planning.GoalService`:
membership and company-scope checks live in the service.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from polsia.api.deps import CurrentUser, GoalServiceDep, parse_company_id, parse_organization_id
from polsia.api.schemas import GoalCreate, GoalOut, GoalStatusChange, GoalUpdate
from polsia.core.planning.values import Priority
from polsia.core.shared.ids import GoalId

router = APIRouter(prefix="/organizations/{org_id}/companies/{company_id}/goals", tags=["goals"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=GoalOut)
async def create_goal(
    org_id: str, company_id: str, payload: GoalCreate, user: CurrentUser, service: GoalServiceDep
) -> GoalOut:
    """Create a goal in the company (membership required)."""
    goal = await service.create_goal(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        title=payload.title,
        description=payload.description,
        parent_goal_id=GoalId.parse(payload.parent_goal_id) if payload.parent_goal_id else None,
        priority=payload.priority,
        due_at=payload.due_at,
        metadata=payload.metadata,
    )
    return GoalOut.model_validate(goal)


@router.get("", response_model=list[GoalOut])
async def list_goals(
    org_id: str, company_id: str, user: CurrentUser, service: GoalServiceDep
) -> list[GoalOut]:
    """List the company's goals (membership required)."""
    goals = await service.list_goals(
        user, parse_organization_id(org_id), parse_company_id(company_id)
    )
    return [GoalOut.model_validate(goal) for goal in goals]


@router.get("/{goal_id}", response_model=GoalOut)
async def get_goal(
    org_id: str, company_id: str, goal_id: str, user: CurrentUser, service: GoalServiceDep
) -> GoalOut:
    """Fetch one goal (membership required; company-scoped lookup)."""
    goal = await service.get_goal(
        user, parse_organization_id(org_id), parse_company_id(company_id), GoalId.parse(goal_id)
    )
    return GoalOut.model_validate(goal)


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(
    org_id: str,
    company_id: str,
    goal_id: str,
    payload: GoalUpdate,
    user: CurrentUser,
    service: GoalServiceDep,
) -> GoalOut:
    """Partially update a goal (membership required)."""
    updates = payload.model_dump(exclude_unset=True)
    goal = await service.update_goal(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        GoalId.parse(goal_id),
        title=updates.get("title"),
        description=updates.get("description"),
        priority=Priority(updates["priority"]) if updates.get("priority") else None,
        due_at=updates.get("due_at"),
        metadata=updates.get("metadata"),
    )
    return GoalOut.model_validate(goal)


@router.post("/{goal_id}/status", response_model=GoalOut)
async def change_goal_status(
    org_id: str,
    company_id: str,
    goal_id: str,
    payload: GoalStatusChange,
    user: CurrentUser,
    service: GoalServiceDep,
) -> GoalOut:
    """Transition the goal status (invalid transitions -> 422 problem)."""
    goal = await service.change_status(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        GoalId.parse(goal_id),
        new_status=payload.status,
    )
    return GoalOut.model_validate(goal)


@router.get("/{goal_id}/subgoals", response_model=list[GoalOut])
async def list_subgoals(
    org_id: str, company_id: str, goal_id: str, user: CurrentUser, service: GoalServiceDep
) -> list[GoalOut]:
    """List the direct sub-goals of a goal (membership required)."""
    goals = await service.list_subgoals(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        GoalId.parse(goal_id),
    )
    return [GoalOut.model_validate(goal) for goal in goals]
