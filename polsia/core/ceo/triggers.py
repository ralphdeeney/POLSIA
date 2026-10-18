"""Validated internal event-trigger policy and dispatch safety contracts."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Protocol

from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import EventEnvelope, utcnow
from polsia.core.shared.ids import (
    CompanyId,
    EventId,
    OrganizationId,
    TaskId,
    TriggerPolicyId,
)
from polsia.core.tasks.entities import Task
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import Company
from polsia.core.tenancy.values import CompanyStatus

TRIGGER_EVENT_TYPES = frozenset({"task.ready", "task.failed"})
_MAX_ACTIONS = 100
_MAX_TASKS = 100
_MAX_EXECUTIONS = 100
_MAX_RETRIES = 10


@dataclasses.dataclass(frozen=True)
class SafetyLimits:
    max_actions: int = 3
    max_tasks: int = 3
    max_executions: int = 3
    max_retries_per_task: int = 2
    max_event_age_seconds: int = 300
    max_future_skew_seconds: int = 30
    require_explicit_allow: bool = True

    def __post_init__(self) -> None:
        bounded = (
            ("max_actions", self.max_actions, _MAX_ACTIONS),
            ("max_tasks", self.max_tasks, _MAX_TASKS),
            ("max_executions", self.max_executions, _MAX_EXECUTIONS),
            ("max_retries_per_task", self.max_retries_per_task, _MAX_RETRIES),
            ("max_event_age_seconds", self.max_event_age_seconds, 86400),
        )
        for name, value, ceiling in bounded:
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValidationError(f"{name} must be an integer between 1 and {ceiling}")
        if (
            type(self.max_future_skew_seconds) is not int
            or not 0 <= self.max_future_skew_seconds <= 300
        ):
            raise ValidationError("max_future_skew_seconds must be an integer between 0 and 300")
        if not self.require_explicit_allow:
            raise ValidationError("trigger execution always requires an explicit ALLOW")


@dataclasses.dataclass(frozen=True)
class EventTriggerPolicy:
    id: TriggerPolicyId
    organization_id: OrganizationId
    company_id: CompanyId
    event_type: str
    enabled: bool = False
    limits: SafetyLimits = dataclasses.field(default_factory=SafetyLimits)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    def __post_init__(self) -> None:
        try:
            typed_id = TriggerPolicyId.parse(str(self.id))
        except ValueError as exc:
            raise ValidationError("trigger policy id must use the trp_ prefix") from exc
        object.__setattr__(self, "id", typed_id)
        if type(self.enabled) is not bool:
            raise ValidationError("enabled must be a boolean")
        if self.event_type not in TRIGGER_EVENT_TYPES:
            raise ValidationError("event_type is not in the explicit trigger allowlist")
        if self.created_at.tzinfo is None:
            raise ValidationError("created_at must be timezone-aware")

    @staticmethod
    def create(
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        event_type: str,
        enabled: bool = False,
        limits: SafetyLimits | None = None,
        now: dt.datetime | None = None,
    ) -> EventTriggerPolicy:
        return EventTriggerPolicy(
            id=TriggerPolicyId.generate(),
            organization_id=organization_id,
            company_id=company_id,
            event_type=event_type,
            enabled=enabled,
            limits=limits or SafetyLimits(),
            created_at=now or utcnow(),
        )


@dataclasses.dataclass(frozen=True)
class TriggerDispatchContext:
    event: EventEnvelope
    policy: EventTriggerPolicy
    decision: Decision
    observation_digest: str
    cycle_actions: int = 0
    cycle_tasks: int = 0
    cycle_executions: int = 0
    observed_task_status: TaskStatus | None = None
    observed_task_attempts: int | None = None


class SafetyGuard:
    """Rejects unsafe trigger context immediately before execution creation."""

    def check_event(
        self,
        *,
        policy: EventTriggerPolicy,
        event: EventEnvelope,
        now: dt.datetime | None = None,
    ) -> None:
        if not policy.enabled:
            raise ValidationError("SafetyGuard rejected a disabled trigger policy")
        if event.organization_id != policy.organization_id or event.company_id != policy.company_id:
            raise ValidationError("SafetyGuard rejected cross-tenant trigger context")
        if event.type != policy.event_type or event.type not in TRIGGER_EVENT_TYPES:
            raise ValidationError("SafetyGuard rejected an unapproved event type")
        if (
            "trigger_event_id" in event.metadata
            or "trigger_policy_id" in event.metadata
            or "trigger_cycle_id" in event.metadata
        ):
            raise ValidationError("SafetyGuard rejected a recursive trigger envelope")
        timestamp = now or utcnow()
        age = (timestamp - event.occurred_at).total_seconds()
        if (
            age < -policy.limits.max_future_skew_seconds
            or age > policy.limits.max_event_age_seconds
        ):
            raise ValidationError("SafetyGuard rejected a stale or future-dated event")

    def check_dispatch(
        self,
        *,
        company: Company,
        task: Task,
        context: TriggerDispatchContext | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        if context is None:
            return
        if company.id != task.company_id or company.status is not CompanyStatus.ACTIVE:
            raise ValidationError("SafetyGuard rejected inactive or cross-company dispatch")
        if task.status not in {TaskStatus.READY, TaskStatus.RETRYING, TaskStatus.FAILED}:
            raise ValidationError("SafetyGuard rejected a task that is not executable")
        event = context.event
        policy = context.policy
        limits = policy.limits
        self.check_event(policy=policy, event=event, now=now)
        if (
            company.organization_id != policy.organization_id
            or task.company_id != policy.company_id
        ):
            raise ValidationError("SafetyGuard rejected cross-tenant trigger context")
        if not context.observation_digest.strip():
            raise ValidationError("SafetyGuard requires an observation digest")
        context.decision.validate()
        if context.decision.company_id != company.id:
            raise ValidationError("SafetyGuard rejected a decision/task scope mismatch")
        if context.decision.task_id != task.id:
            raise ValidationError("SafetyGuard rejected a decision task_id mismatch")
        if context.decision.created_at.tzinfo is None:
            raise ValidationError("SafetyGuard rejected a timezone-naive decision")
        if context.cycle_actions < 0 or context.cycle_tasks < 0 or context.cycle_executions < 0:
            raise ValidationError("SafetyGuard rejected negative cycle counters")
        if (
            context.cycle_actions > context.cycle_executions
            or context.cycle_actions > context.cycle_tasks
        ):
            raise ValidationError("SafetyGuard rejected inconsistent cycle counters")
        if (
            context.observed_task_status is not None
            and context.observed_task_status is not task.status
        ):
            raise ValidationError("SafetyGuard rejected a stale task status")
        if (
            context.observed_task_attempts is not None
            and context.observed_task_attempts != task.attempts
        ):
            raise ValidationError("SafetyGuard rejected a stale task attempt count")
        if context.decision.cycle_id is None or not context.decision.cycle_id.startswith("trg_"):
            raise ValidationError("SafetyGuard rejected a missing trigger cycle key")
        if context.cycle_actions >= limits.max_actions:
            raise ValidationError("SafetyGuard action cap reached")
        if context.cycle_tasks >= limits.max_tasks:
            raise ValidationError("SafetyGuard task cap reached")
        if context.cycle_executions >= limits.max_executions:
            raise ValidationError("SafetyGuard execution cap reached")
        if context.decision.decision_type is DecisionType.RETRY_TASK:
            retries_used = max(0, task.attempts - 1)
            if (
                retries_used >= limits.max_retries_per_task
                or task.attempts >= task.max_attempts
                or task.status not in {TaskStatus.FAILED, TaskStatus.RETRYING}
            ):
                raise ValidationError("SafetyGuard retry cap reached")
        elif context.decision.decision_type is not DecisionType.PRIORITIZE_TASK:
            raise ValidationError("SafetyGuard rejected a non-dispatch decision")
        if context.decision.task_id != TaskId.parse(
            str(context.decision.payload.get("task_id", ""))
        ):
            raise ValidationError("SafetyGuard rejected inconsistent decision payload")


class EventTriggerPolicyRepository(Protocol):
    """Port implemented by the persistence adapter."""

    async def add(self, policy: EventTriggerPolicy) -> None: ...

    async def list_matching(
        self, organization_id: OrganizationId, company_id: CompanyId, event_type: str
    ) -> list[EventTriggerPolicy]: ...


DELIVERY_INTERRUPTED = "interrupted"
DELIVERY_COMPLETED = "completed"
DELIVERY_FAILED = "failed"
DELIVERY_REJECTED = "rejected"
DELIVERY_RECURSION_SUPPRESSED = "recursion_suppressed"
#: Terminal delivery statuses are never reclaimed.
DELIVERY_TERMINAL_STATUSES = frozenset(
    {DELIVERY_COMPLETED, DELIVERY_FAILED, DELIVERY_REJECTED, DELIVERY_RECURSION_SUPPRESSED}
)
#: A delivery may be re-claimed for recovery from this status (transient failure).
DELIVERY_RETRYABLE_STATUSES = frozenset({DELIVERY_INTERRUPTED})


@dataclasses.dataclass(frozen=True)
class DeliveryLease:
    """A granted, fenced claim on one ``(policy, event)`` delivery attempt.

    ``fencing_token`` increments on every re-claim of the same pair, so a stale
    worker that lost its lease cannot overwrite the durable delivery outcome.
    ``attempt`` is the 1-based delivery attempt number, bounded by the
    dispatcher's configured ``max_delivery_attempts``.
    """

    fencing_token: int
    attempt: int


class TriggerDeliveryRepository(Protocol):
    """Leased, fenced, bounded at-least-once event/policy delivery claims.

    A ``(policy_id, event_id)`` pair is claimed at most once at a time. The
    claim is leased: if the worker stops without recording a terminal outcome,
    the lease expires and a later claim may take over with a strictly greater
    ``fencing_token``. Recovery is bounded by ``max_attempts``.
    """

    async def claim(
        self,
        policy: EventTriggerPolicy,
        event_id: EventId,
        cycle_id: str,
        *,
        owner: str,
        lease_seconds: int,
        max_attempts: int,
        now: dt.datetime,
    ) -> DeliveryLease | None: ...

    async def finish(
        self,
        policy_id: str,
        event_id: EventId,
        status: str,
        detail: dict[str, Any],
        *,
        fencing_token: int,
        now: dt.datetime,
    ) -> bool: ...


class StoredEventRepository(Protocol):
    """Port for loading committed envelopes by their stable event ID."""

    async def get(self, event_id: EventId) -> EventEnvelope | None: ...
