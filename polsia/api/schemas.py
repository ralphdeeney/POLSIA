"""API request/response schemas (Pydantic v2).

Only the schemas the API layer needs to speak to clients; domain entities
stay plain dataclasses. Conversion happens in the routers via
``model_validate`` — domain entities are never returned directly.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from polsia.core.planning.values import GoalStatus, KpiDirection, Priority
from polsia.core.tenancy.values import CompanyStatus, MembershipRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=72,
        description="Plaintext password; min 8 chars, max 72 (bcrypt input limit)",
    )
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str


class RegisterResponse(BaseModel):
    user: UserOut
    tokens: TokenResponse


# -- Organizations ---------------------------------------------------------


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=2, max_length=63)


class OrganizationUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    owner_user_id: str
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime


class MemberAdd(BaseModel):
    user_id: str
    role: MembershipRole


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    user_id: str
    role: str
    created_at: dt.datetime


# -- Companies ---------------------------------------------------------------


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=2, max_length=63)
    identity: dict[str, Any] | None = None
    mission: str | None = Field(default=None, max_length=2000)


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    identity: dict[str, Any] | None = None
    mission: str | None = Field(default=None, max_length=2000)
    strategy: dict[str, Any] | None = None
    configuration: dict[str, Any] | None = None


class CompanyStatusChange(BaseModel):
    status: CompanyStatus


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    name: str
    slug: str
    identity: dict[str, Any]
    mission: str | None
    strategy: dict[str, Any]
    configuration: dict[str, Any]
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime


# -- Phase 2: planning (goals, KPIs) ------------------------------------------


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    parent_goal_id: str | None = None
    priority: Priority = Priority.MEDIUM
    due_at: dt.datetime | None = None
    metadata: dict[str, Any] | None = None


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    priority: Priority | None = None
    due_at: dt.datetime | None = None
    metadata: dict[str, Any] | None = None


class GoalStatusChange(BaseModel):
    status: GoalStatus


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    title: str
    description: str | None
    parent_goal_id: str | None
    status: str
    priority: str
    due_at: dt.datetime | None
    metadata: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


class KpiCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    direction: KpiDirection
    target_value: float
    goal_id: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None


class KpiUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    direction: KpiDirection | None = None
    target_value: float | None = None
    unit: str | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None


class KpiMeasurement(BaseModel):
    current_value: float


class KpiOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    name: str
    description: str | None
    direction: str
    target_value: float
    current_value: float | None
    goal_id: str | None
    unit: str | None
    status: str
    metadata: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


# -- Phase 2: tasks + executions ----------------------------------------------

from polsia.core.tasks.values import TaskStatus  # noqa: E402


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    goal_id: str | None = None
    parent_task_id: str | None = None
    priority: Priority = Priority.MEDIUM
    assigned_agent_id: str | None = None
    assigned_skill: str | None = Field(default=None, max_length=255)
    input: dict[str, Any] | None = None
    max_attempts: int = Field(default=3, ge=1, le=20)
    due_at: dt.datetime | None = None
    metadata: dict[str, Any] | None = None
    depends_on: list[str] | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    priority: Priority | None = None
    assigned_agent_id: str | None = None
    assigned_skill: str | None = Field(default=None, max_length=255)
    input: dict[str, Any] | None = None
    due_at: dt.datetime | None = None
    metadata: dict[str, Any] | None = None


class TaskStatusChange(BaseModel):
    status: TaskStatus


class TaskDependencyChange(BaseModel):
    depends_on_task_id: str


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    title: str
    description: str | None
    goal_id: str | None
    parent_task_id: str | None
    status: str
    priority: str
    assigned_agent_id: str | None
    assigned_skill: str | None
    input: dict[str, Any]
    output: dict[str, Any] | None
    attempts: int
    max_attempts: int
    due_at: dt.datetime | None
    metadata: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    task_id: str
    attempt_number: int
    status: str
    agent_id: str | None
    skill_id: str | None
    runtime: str | None
    model: str | None
    input: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    started_at: dt.datetime
    completed_at: dt.datetime | None
    metadata: dict[str, Any]


class ExecutionComplete(BaseModel):
    output: dict[str, Any] | None = None


class ExecutionFail(BaseModel):
    error: str | None = Field(default=None, max_length=2000)


# -- Phase 2: registries (agents, skills, tools, models) -----------------------

from polsia.core.registry.values import RegistryStatus  # noqa: E402


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(default="generic", max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    model_reference: str | None = Field(default=None, max_length=255)
    system_instructions: str | None = Field(default=None, max_length=4000)
    capabilities: list[str] | None = None
    configuration: dict[str, Any] | None = None


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    model_reference: str | None = Field(default=None, max_length=255)
    system_instructions: str | None = Field(default=None, max_length=4000)
    capabilities: list[str] | None = None
    configuration: dict[str, Any] | None = None


class AgentStatusChange(BaseModel):
    status: RegistryStatus


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    name: str
    type: str
    description: str | None
    status: str
    model_reference: str | None
    system_instructions: str | None
    capabilities: list[str]
    configuration: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=3, max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    required_capabilities: list[str] | None = None
    required_tools: list[str] | None = None
    required_permissions: list[str] | None = None
    configuration: dict[str, Any] | None = None


class SkillUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    required_capabilities: list[str] | None = None
    required_tools: list[str] | None = None
    required_permissions: list[str] | None = None
    configuration: dict[str, Any] | None = None


class SkillStatusChange(BaseModel):
    status: RegistryStatus


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: str
    description: str | None
    status: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_capabilities: list[str]
    required_tools: list[str]
    required_permissions: list[str]
    configuration: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


class ToolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=3, max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    configuration: dict[str, Any] | None = None


class ToolUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    configuration: dict[str, Any] | None = None


class ToolStatusChange(BaseModel):
    status: RegistryStatus


class ToolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: str
    description: str | None
    status: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: list[str]
    configuration: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=3, max_length=20)
    provider: str = Field(default="generic", max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    capabilities: list[str] | None = None
    configuration: dict[str, Any] | None = None


class ModelUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    provider: str | None = Field(default=None, max_length=100)
    capabilities: list[str] | None = None
    configuration: dict[str, Any] | None = None


class ModelStatusChange(BaseModel):
    status: RegistryStatus


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: str
    provider: str
    description: str | None
    status: str
    capabilities: list[str]
    configuration: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


# -- Phase 2: permissions + approvals ------------------------------------------

from polsia.core.permissions.values import PermissionDecision  # noqa: E402


class PermissionSet(BaseModel):
    principal_type: str = Field(min_length=1, max_length=100)
    principal_id: str | None = Field(default=None, max_length=255)
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: str | None = Field(default=None, max_length=255)
    action: str = Field(min_length=1, max_length=255)
    decision: PermissionDecision
    constraints: dict[str, Any] | None = None


class PermissionCheck(BaseModel):
    principal_type: str = Field(min_length=1, max_length=100)
    principal_id: str | None = Field(default=None, max_length=255)
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: str | None = Field(default=None, max_length=255)
    action: str = Field(min_length=1, max_length=255)


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    principal_type: str
    principal_id: str | None
    resource_type: str
    resource_id: str | None
    action: str
    decision: str
    constraints: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


class ApprovalCreate(BaseModel):
    requested_by: str
    action: str = Field(min_length=1, max_length=255)
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)
    expires_at: dt.datetime | None = None
    metadata: dict[str, Any] | None = None


class ApprovalDecide(BaseModel):
    decision: Literal["approved", "rejected"]


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    requested_by: str
    action: str
    resource_type: str
    resource_id: str | None
    reason: str | None
    status: str
    expires_at: dt.datetime | None
    decided_by: str | None
    decided_at: dt.datetime | None
    metadata: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


# -- Phase 2: memory ------------------------------------------------------------


class MemoryWrite(BaseModel):
    scope: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=8000)
    metadata: dict[str, Any] | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    scope: str
    type: str
    content: str
    metadata: dict[str, Any]
    importance: float
    created_at: dt.datetime
    updated_at: dt.datetime


# -- Phase 2: usage tracking ----------------------------------------------------


class UsageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    execution_id: str
    task_id: str
    agent_id: str | None
    runtime: str | None
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    tool_calls: int
    duration_seconds: float
    estimated_cost: str | None = Field(
        default=None, description="Decimal cost estimate as string, when priced"
    )
    metadata: dict[str, Any]
    created_at: dt.datetime


# -- CEO read-only query DTOs --------------------------------------------------


class DecisionRecordOut(BaseModel):
    @model_validator(mode="after")
    def validate_json_payload_size(self) -> DecisionRecordOut:
        from polsia.core.ceo.records import validate_decision_record_json

        validate_decision_record_json(self.payload, self.outcome_context)
        return self

    id: str
    organization_id: str
    company_id: str
    decision_type: str
    payload: dict[str, Any]
    rationale: str | None
    confidence: float | None
    model_reference: str | None
    cycle_id: str | None
    task_id: str | None
    decision_created_at: dt.datetime
    observation_digest: str
    outcome_context: dict[str, Any]
    recorded_at: dt.datetime


class CycleSummaryOut(BaseModel):
    cycle_id: str
    decision_count: int
    outcome_counts: dict[str, int]
    first_recorded_at: dt.datetime
    last_recorded_at: dt.datetime


class KpiAttentionSummaryOut(BaseModel):
    kpi_id: str
    status: str
    reason: str


class FailedTaskSummaryOut(BaseModel):
    task_id: str
    attempts: int
    can_retry: bool
    error: str | None


class PendingApprovalSummaryOut(BaseModel):
    id: str
    action: str
    resource_type: str
    resource_id: str | None


class ObservationSummaryOut(BaseModel):
    """Bounded observation response. List caps mirror ObservationLimits; count
    fields are uncapped totals so clients can detect truncation."""

    organization_id: str
    company_id: str
    generated_at: dt.datetime
    digest: str
    goals_total: int
    active_goal_ids: list[str] = Field(max_length=1000)
    kpis_total: int
    kpis_attention: list[KpiAttentionSummaryOut] = Field(max_length=1000)
    tasks_total: int
    ready_task_ids: list[str] = Field(max_length=1000)
    pending_task_ids: list[str] = Field(max_length=1000)
    running_task_ids: list[str] = Field(max_length=1000)
    failed_tasks: list[FailedTaskSummaryOut] = Field(max_length=1000)
    blocked_task_ids: list[str] = Field(max_length=1000)
    selectable_agent_ids: list[str] = Field(max_length=1000)
    pending_approvals: list[PendingApprovalSummaryOut] = Field(max_length=1000)


# -- Phase 4 wave 3: business intake, blueprints, bootstrap -------------------


class BusinessIdeaCreate(BaseModel):
    """Submit a business idea. ``raw_idea`` is preserved verbatim."""

    raw_idea: str = Field(min_length=1, max_length=8000)
    target_market: str | None = Field(default=None, max_length=2000)
    industry: str | None = Field(default=None, max_length=255)
    geo_preferences: list[str] = Field(default_factory=list, max_length=50)
    budget_constraints: str | None = Field(default=None, max_length=2000)
    additional_constraints: list[str] = Field(default_factory=list, max_length=50)
    #: Optional caller-supplied idempotency key for safe request retries.
    idempotency_key: str | None = Field(default=None, max_length=200)


class BusinessIdeaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    owner_user_id: str
    raw_idea: str
    description: str
    target_market: str | None
    industry: str | None
    geo_preferences: list[str]
    budget_constraints: str | None
    additional_constraints: list[str]
    intake_status: str
    blueprint_status: str
    blueprint_version: int
    submitted_at: dt.datetime


class BusinessIdeaReject(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class BlueprintOut(BaseModel):
    """The persisted blueprint artifact + its provenance/lifecycle status."""

    idea_id: str
    organization_id: str
    blueprint_status: str
    blueprint_version: int
    source: str
    business_model: str
    research_used: bool
    blueprint: dict[str, Any]


class BootstrapOut(BaseModel):
    company: CompanyOut
    company_id: str
    goal_ids: list[str]
    kpi_ids: list[str]
    agent_ids: list[str]
    memory_record_ids: list[str]
    planning_ready: bool
    idempotency_key: str
    reused: bool


class CeoInitOut(BaseModel):
    company_id: str
    agent_id: str
    agent_name: str
    goal_count: int
    kpi_count: int
    system_instructions: str


class InitialPlanOut(BaseModel):
    company_id: str
    goal_id: str
    objective: str
    plan: dict[str, Any]
    task_ids: list[str]
