"""Phase 3 Slice 5 tests: safe delegation, verification, bounded recovery."""

from __future__ import annotations

from typing import Any, cast

import pytest
from polsia.adapters.events import InProcessEventBus
from polsia.core.registry.entities import Agent
from polsia.core.runtime.ports import RuntimeResult
from polsia.core.shared.errors import NotAMember, ValidationError
from polsia.core.shared.events import EventEnvelope
from polsia.core.shared.ids import AgentId, CompanyId, OrganizationId, TaskId
from polsia.core.tasks.entities import Task
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import Company, User
from polsia.services.delegation import DelegationService
from polsia.services.tasks import TaskService
from polsia.services.verification import RecoveryEngine, ResultVerifier


class _Memberships:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    async def get(self, organization_id: OrganizationId, user_id: Any) -> object | None:
        _ = organization_id, user_id
        return object() if self.allowed else None


class _Companies:
    def __init__(self, company: Company) -> None:
        self.company = company

    async def get(self, organization_id: OrganizationId, company_id: CompanyId) -> Company | None:
        if organization_id == self.company.organization_id and company_id == self.company.id:
            return self.company
        return None


class _Agents:
    def __init__(self, agents: list[Agent]) -> None:
        self.agents = {agent.id: agent for agent in agents}

    async def get(self, company_id: CompanyId, agent_id: AgentId) -> Agent | None:
        agent = self.agents.get(agent_id)
        return agent if agent and agent.company_id == company_id else None

    async def list_for_company(self, company_id: CompanyId) -> list[Agent]:
        return [agent for agent in self.agents.values() if agent.company_id == company_id]


class _Tasks:
    def __init__(self, task: Task) -> None:
        self.task = task
        self.updated: list[dict[str, Any]] = []

    async def get_task(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
    ) -> Task:
        _ = actor, organization_id
        if company_id != self.task.company_id or task_id != self.task.id:
            raise LookupError("task not found")
        return self.task

    async def update_task(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        *,
        assigned_agent_id: AgentId | None = None,
    ) -> Task:
        _ = actor, organization_id, company_id, task_id
        self.updated.append({"assigned_agent_id": assigned_agent_id})
        self.task = self.task.update(assigned_agent_id=assigned_agent_id)
        return self.task


class _RecordingBus(InProcessEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[EventEnvelope] = []

    def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)
        super().publish(event)


def _delegation_env(
    *,
    allowed: bool = True,
    capabilities: list[str] | None = None,
    assigned_skill: str | None = None,
) -> tuple[DelegationService, User, OrganizationId, CompanyId, Task, Agent, _Tasks, _RecordingBus]:
    organization_id = OrganizationId.generate()
    company = Company.create(organization_id=organization_id, name="Co", slug="company")
    company_id = company.id
    agent = Agent.create(company_id=company_id, name="worker", capabilities=capabilities or [])
    task = Task.create(
        company_id=company_id,
        title="work",
        assigned_skill=assigned_skill,
    ).transition_status(TaskStatus.READY)
    tasks = _Tasks(task)
    bus = _RecordingBus()
    service = DelegationService(
        cast(TaskService, tasks),
        cast(Any, _Agents([agent])),
        cast(Any, _Companies(company)),
        cast(Any, _Memberships(allowed)),
        bus,
    )
    user = User.register(email="worker@example.test", hashed_password="hash", display_name="worker")
    return service, user, organization_id, company_id, task, agent, tasks, bus


@pytest.mark.asyncio
async def test_delegation_assigns_stable_selectable_agent_and_emits_event() -> None:
    service, user, org, company, task, agent, tasks, bus = _delegation_env()
    result = await service.delegate(user, org, company, task.id)
    assert result.agent_id == agent.id
    assert tasks.task.assigned_agent_id == agent.id
    assert [event.type for event in bus.events] == ["orchestration.delegated"]


@pytest.mark.asyncio
async def test_delegation_filters_by_declared_capability() -> None:
    service, user, org, company, task, agent, tasks, bus = _delegation_env(
        capabilities=["research@1.0.0"], assigned_skill="research@1.0.0"
    )
    result = await service.delegate(user, org, company, task.id)
    assert result.agent_id == agent.id
    assert tasks.task.assigned_agent_id == agent.id
    assert bus.events[-1].type == "orchestration.delegated"


@pytest.mark.asyncio
async def test_delegation_rejects_agent_missing_required_capability() -> None:
    service, user, org, company, task, agent, _tasks, _bus = _delegation_env(
        capabilities=["other"], assigned_skill="required"
    )
    with pytest.raises(ValidationError, match="does not declare"):
        await service.delegate(user, org, company, task.id, agent_id=agent.id)


@pytest.mark.asyncio
async def test_delegation_rejects_nonmember_before_assignment() -> None:
    service, user, org, company, task, _agent, tasks, bus = _delegation_env(allowed=False)
    with pytest.raises(NotAMember):
        await service.delegate(user, org, company, task.id)
    assert tasks.updated == []
    assert bus.events == []


def test_result_verifier_checks_structured_fields_not_claims() -> None:
    verifier = ResultVerifier()
    missing = verifier.verify(
        RuntimeResult.completed({"claim": "everything is done"}),
        {"required_output_fields": ["receipt_id"]},
    )
    assert not missing.passed
    assert missing.missing_fields == ("receipt_id",)
    passing = verifier.verify(
        RuntimeResult.completed({"receipt_id": "r-1"}), {"required_output_fields": ["receipt_id"]}
    )
    assert passing.passed


def test_result_verifier_rejects_failed_or_malformed_result() -> None:
    verifier = ResultVerifier()
    assert not verifier.verify(RuntimeResult.failed("runtime error")).passed
    malformed = verifier.verify(RuntimeResult.completed(), {"required_output_fields": "id"})
    assert not malformed.passed
    assert "malformed" in malformed.reason


def test_recovery_engine_obeys_both_retry_budgets() -> None:
    engine = RecoveryEngine(max_retries=2)
    assert engine.decide(attempts=1, max_attempts=5).retry
    assert engine.decide(attempts=2, max_attempts=5).retries_remaining == 1
    assert not engine.decide(attempts=3, max_attempts=5).retry
    assert not engine.decide(attempts=1, max_attempts=1).retry
    assert not engine.decide(attempts=0, max_attempts=3).retry


def test_result_verifier_fails_closed_for_non_object_metadata() -> None:
    outcome = ResultVerifier().verify(RuntimeResult.completed(), cast(Any, ["invalid"]))
    assert not outcome.passed
    assert "malformed" in outcome.reason


def test_recovery_engine_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError):
        RecoveryEngine(max_retries=-1)
    with pytest.raises(ValueError):
        RecoveryEngine().decide(attempts=-1, max_attempts=3)
