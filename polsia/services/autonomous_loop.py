"""Internal bounded CEO loop; every runtime attempt uses orchestration security."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.ceo.observation import CompanyObservation, FailedTask
from polsia.core.ceo.records import DecisionRecord, DecisionRecordRepository
from polsia.core.ceo.triggers import (
    TRIGGER_EVENT_TYPES,
    EventTriggerPolicy,
    SafetyGuard,
    TriggerDispatchContext,
)
from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.shared.errors import CompanyNotFoundError, NotAMember, ValidationError
from polsia.core.shared.events import EventEnvelope
from polsia.core.shared.ids import CompanyId, OrganizationId, TaskId
from polsia.core.tasks.entities import Task
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.core.tenancy.values import CompanyStatus
from polsia.services.delegation import DelegationService
from polsia.services.observation import CompanyStateObserver
from polsia.services.orchestration import OrchestrationService
from polsia.services.prioritization import PriorityEngine
from polsia.services.tasks import ExecutionService, TaskService
from polsia.services.verification import RecoveryEngine


@dataclasses.dataclass(frozen=True)
class AutonomousLoopResult:
    cycle_id: str
    actions_taken: int
    stop_reason: str
    decisions: tuple[DecisionRecord, ...]


@dataclasses.dataclass(frozen=True)
class _Candidate:
    task: Task
    decision_type: DecisionType
    rationale: str


class AutonomousLoop:
    """Runs a finite, tenant-scoped number of attempts; not exposed over HTTP."""

    def __init__(
        self,
        *,
        observer: CompanyStateObserver,
        prioritizer: PriorityEngine,
        delegation: DelegationService,
        executions: ExecutionService,
        orchestration: OrchestrationService,
        tasks: TaskService,
        records: DecisionRecordRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        recovery: RecoveryEngine | None = None,
        max_actions: int = 3,
        safety_guard: SafetyGuard | None = None,
    ) -> None:
        if max_actions < 1 or max_actions > 100:
            raise ValueError("max_actions must be between 1 and 100")
        self._observer = observer
        self._prioritizer = prioritizer
        self._delegation = delegation
        self._executions = executions
        self._orchestration = orchestration
        self._tasks = tasks
        self._records = records
        self._companies = companies
        self._memberships = memberships
        self._recovery = recovery or RecoveryEngine()
        self._max_actions = max_actions
        self._safety_guard = safety_guard or SafetyGuard()

    async def run(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        max_actions: int | None = None,
    ) -> AutonomousLoopResult:
        if await self._memberships.get(organization_id, actor.id) is None:
            raise NotAMember(f"user {actor.id} is not a member of organization {organization_id}")
        company = await self._companies.get(organization_id, company_id)
        if company is None:
            raise CompanyNotFoundError(
                f"company {company_id} not found in organization {organization_id}"
            )
        if company.status is not CompanyStatus.ACTIVE:
            raise ValidationError("autonomous loop requires an active company")

        budget = self._max_actions if max_actions is None else max_actions
        if budget < 1 or budget > self._max_actions:
            raise ValueError(
                f"max_actions must be between 1 and configured ceiling {self._max_actions}"
            )

        cycle_id = str(uuid.uuid4())
        records: list[DecisionRecord] = []
        actions = 0
        iterations = 0
        iteration_budget = budget * 2
        stop_reason = "no_eligible_work"

        while actions < budget and iterations < iteration_budget:
            iterations += 1
            observation = await self._observe(organization_id, company_id)
            candidate = self.select_candidate(observation)
            if candidate is None:
                if observation.pending_approvals:
                    stop_reason = "wait_for_approval"
                elif observation.running_tasks:
                    stop_reason = "wait_for_running_work"
                else:
                    stop_reason = "no_eligible_work"
                await self._record_local(
                    records,
                    organization_id,
                    company_id,
                    observation,
                    DecisionType.WAIT,
                    {},
                    stop_reason,
                    cycle_id,
                )
                break

            task = candidate.task
            decision = Decision.create(
                company_id=company_id,
                decision_type=candidate.decision_type,
                payload={"task_id": str(task.id)},
                rationale=candidate.rationale,
                cycle_id=cycle_id,
            )
            record = DecisionRecord.create(
                organization_id=organization_id,
                company_id=company_id,
                decision=decision,
                observation_digest=observation.digest,
                outcome_context={"status": "selected", "action": candidate.decision_type.value},
            )
            current = await self._tasks.get_task(actor, organization_id, company_id, task.id)
            ready_candidate = (
                candidate.decision_type is DecisionType.PRIORITIZE_TASK
                and current.status is TaskStatus.READY
            )
            retry_candidate = (
                candidate.decision_type is DecisionType.RETRY_TASK
                and current.status is TaskStatus.FAILED
                and current.can_retry()
            )
            if retry_candidate:
                retry_candidate = self._recovery.decide(
                    attempts=current.attempts, max_attempts=current.max_attempts
                ).retry
            if not (ready_candidate or retry_candidate):
                record = dataclasses.replace(
                    record,
                    outcome_context={
                        "status": "skipped_status_changed",
                        "task_status": current.status.value,
                    },
                )
                await self._records.add(record)
                records.append(record)
                continue
            if decision.decision_type not in {
                DecisionType.PRIORITIZE_TASK,
                DecisionType.RETRY_TASK,
            }:
                raise ValidationError("autonomous loop selected a non-dispatch decision")
            try:
                task_after, execution, result = await self._orchestration.execute_task(
                    actor,
                    organization_id,
                    company_id,
                    current.id,
                    retry_failed=(candidate.decision_type is DecisionType.RETRY_TASK),
                )
            except ApprovalRequired as exc:
                record = dataclasses.replace(
                    record,
                    outcome_context={"status": "approval_required", "approval_id": exc.approval_id},
                )
                await self._records.add(record)
                records.append(record)
                stop_reason = "approval_required"
                break
            except PermissionDenied:
                record = dataclasses.replace(
                    record, outcome_context={"status": "permission_denied"}
                )
                await self._records.add(record)
                records.append(record)
                stop_reason = "permission_denied"
                break
            record = dataclasses.replace(
                record,
                outcome_context={
                    "status": "executed",
                    "task_status": task_after.status.value,
                    "execution_id": str(execution.id),
                    "result_status": result.status.value,
                },
            )
            await self._records.add(record)
            records.append(record)
            actions += 1
            stop_reason = "action_budget_reached" if actions >= budget else "reobserve"

        if actions < budget and iterations >= iteration_budget:
            stop_reason = "iteration_budget_reached"
        return AutonomousLoopResult(cycle_id, actions, stop_reason, tuple(records))

    async def _observe(
        self, organization_id: OrganizationId, company_id: CompanyId
    ) -> CompanyObservation:
        observation = await self._observer.observe(organization_id, company_id)
        if observation.organization_id != organization_id or observation.company_id != company_id:
            raise ValidationError("observer returned a mismatched tenant scope")
        return observation

    def select_candidate(
        self, observation: CompanyObservation, *, recovery: RecoveryEngine | None = None
    ) -> _Candidate | None:
        report = self._prioritizer.prioritize(observation)
        by_id: dict[TaskId, Task] = {task.id: task for task in observation.ready_tasks}
        retryable: dict[TaskId, FailedTask] = {
            item.task.id: item for item in observation.failed_tasks if item.can_retry
        }
        engine = recovery or self._recovery
        for ranked in report.items:
            task = ranked.task
            if task.id in by_id and by_id[task.id].status is TaskStatus.READY:
                return _Candidate(task, DecisionType.PRIORITIZE_TASK, ranked.rationale)
            failed = retryable.get(task.id)
            if failed is not None and task.status is TaskStatus.FAILED:
                decision = engine.decide(attempts=failed.attempts, max_attempts=task.max_attempts)
                if decision.retry and task.can_retry():
                    return _Candidate(task, DecisionType.RETRY_TASK, decision.rationale)
        return None

    async def run_triggered_cycle(
        self,
        actor: User,
        policy: EventTriggerPolicy,
        event: EventEnvelope,
        cycle_id: str,
    ) -> dict[str, Any]:
        """Run one eligible task under the trigger policy and permission gates."""
        if (
            not policy.enabled
            or event.type != policy.event_type
            or event.type not in TRIGGER_EVENT_TYPES
            or event.organization_id != policy.organization_id
            or event.company_id != policy.company_id
        ):
            raise ValidationError("trigger policy is disabled or mismatched")
        membership = await self._memberships.get(policy.organization_id, actor.id)
        if membership is None or membership.role.value != "owner":
            raise NotAMember(f"user {actor.id} is not an authorized trigger actor")
        company = await self._companies.get(policy.organization_id, policy.company_id)
        if company is None or company.status is not CompanyStatus.ACTIVE:
            raise ValidationError("trigger company is missing or inactive")
        self._safety_guard.check_event(policy=policy, event=event)
        observation = await self._observe(policy.organization_id, policy.company_id)
        candidate = self.select_candidate(
            observation,
            recovery=RecoveryEngine(max_retries=policy.limits.max_retries_per_task),
        )
        if candidate is None:
            decision = Decision.create(
                company_id=policy.company_id,
                decision_type=DecisionType.WAIT,
                payload={},
                rationale="no eligible work for triggered event",
                cycle_id=cycle_id,
            )
            await self._records.add(
                DecisionRecord.create(
                    organization_id=policy.organization_id,
                    company_id=policy.company_id,
                    decision=decision,
                    observation_digest=observation.digest,
                    outcome_context={"status": "stopped", "reason": "no_eligible_work"},
                )
            )
            return {"status": "no_eligible_work", "observation_digest": observation.digest}

        task = await self._tasks.get_task(
            actor, policy.organization_id, policy.company_id, candidate.task.id
        )
        if task.company_id != policy.company_id or task.id != candidate.task.id:
            raise ValidationError("trigger task tenant or identity mismatch")
        decision = Decision.create(
            company_id=policy.company_id,
            decision_type=candidate.decision_type,
            payload={"task_id": str(task.id)},
            rationale=candidate.rationale,
            cycle_id=cycle_id,
        )
        context = TriggerDispatchContext(
            event=event,
            policy=policy,
            decision=decision,
            observation_digest=observation.digest,
            observed_task_status=task.status,
            observed_task_attempts=task.attempts,
        )
        self._safety_guard.check_dispatch(company=company, task=task, context=context)
        task_after, execution, result = await self._orchestration.execute_task(
            actor,
            policy.organization_id,
            policy.company_id,
            task.id,
            safety_context=context,
            retry_failed=(candidate.decision_type is DecisionType.RETRY_TASK),
        )
        record = DecisionRecord.create(
            organization_id=policy.organization_id,
            company_id=policy.company_id,
            decision=decision,
            observation_digest=observation.digest,
            outcome_context={
                "status": "executed",
                "task_status": task_after.status.value,
                "execution_id": str(execution.id),
                "result_status": result.status.value,
            },
        )
        await self._records.add(record)
        return {
            "status": "executed",
            "task_id": str(task_after.id),
            "execution_id": str(execution.id),
            "result_status": result.status.value,
            "cycle_id": cycle_id,
        }

    async def _record_local(
        self,
        records: list[DecisionRecord],
        organization_id: OrganizationId,
        company_id: CompanyId,
        observation: CompanyObservation,
        decision_type: DecisionType,
        payload: dict[str, Any],
        rationale: str,
        cycle_id: str,
    ) -> None:
        decision = Decision.create(
            company_id=company_id,
            decision_type=decision_type,
            payload=payload,
            rationale=rationale,
            cycle_id=cycle_id,
        )
        record = DecisionRecord.create(
            organization_id=organization_id,
            company_id=company_id,
            decision=decision,
            observation_digest=observation.digest,
            outcome_context={"status": "stopped", "reason": rationale},
        )
        await self._records.add(record)
        records.append(record)
