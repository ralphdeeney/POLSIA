"""Read-only CEO decision history and current observation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from polsia.api.deps import CEOQueryServiceDep, CurrentUser, parse_company_id, parse_organization_id
from polsia.api.schemas import (
    CycleSummaryOut,
    DecisionRecordOut,
    FailedTaskSummaryOut,
    KpiAttentionSummaryOut,
    ObservationSummaryOut,
    PendingApprovalSummaryOut,
)
from polsia.core.ceo.observation import CompanyObservation
from polsia.core.ceo.records import DecisionRecord
from polsia.services.ceo_queries import CycleSummary

router = APIRouter(prefix="/organizations/{org_id}/companies/{company_id}/ceo", tags=["ceo"])


@router.get("/decisions", response_model=list[DecisionRecordOut])
async def list_decisions(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: CEOQueryServiceDep,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[DecisionRecordOut]:
    records = await service.list_decisions(
        user, parse_organization_id(org_id), parse_company_id(company_id), limit=limit
    )
    return [_decision_out(record) for record in records]


@router.get("/cycles", response_model=list[CycleSummaryOut])
async def list_cycles(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: CEOQueryServiceDep,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[CycleSummaryOut]:
    summaries = await service.list_cycle_summaries(
        user, parse_organization_id(org_id), parse_company_id(company_id), limit=limit
    )
    return [_cycle_out(summary) for summary in summaries]


@router.get("/observation", response_model=ObservationSummaryOut)
async def current_observation(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: CEOQueryServiceDep,
) -> ObservationSummaryOut:
    observation = await service.current_observation(
        user, parse_organization_id(org_id), parse_company_id(company_id)
    )
    return _observation_out(observation)


def _decision_out(record: DecisionRecord) -> DecisionRecordOut:
    return DecisionRecordOut(
        id=str(record.id),
        organization_id=str(record.organization_id),
        company_id=str(record.company_id),
        decision_type=record.decision.decision_type.value,
        payload=record.decision.payload,
        rationale=record.decision.rationale,
        confidence=record.decision.confidence,
        model_reference=record.decision.model_reference,
        cycle_id=record.decision.cycle_id,
        task_id=str(record.decision.task_id) if record.decision.task_id else None,
        decision_created_at=record.decision.created_at,
        observation_digest=record.observation_digest,
        outcome_context=record.outcome_context,
        recorded_at=record.recorded_at,
    )


def _cycle_out(summary: CycleSummary) -> CycleSummaryOut:
    return CycleSummaryOut(
        cycle_id=summary.cycle_id,
        decision_count=summary.decision_count,
        outcome_counts=summary.outcome_counts,
        first_recorded_at=summary.first_recorded_at,
        last_recorded_at=summary.last_recorded_at,
    )


def _observation_out(observation: CompanyObservation) -> ObservationSummaryOut:
    return ObservationSummaryOut(
        organization_id=str(observation.organization_id),
        company_id=str(observation.company_id),
        generated_at=observation.generated_at,
        digest=observation.digest,
        goals_total=observation.goals_total,
        active_goal_ids=[str(goal.id) for goal in observation.active_goals],
        kpis_total=observation.kpis_total,
        kpis_attention=[
            KpiAttentionSummaryOut(
                kpi_id=str(attention.kpi.id),
                status=attention.kpi.status.value,
                reason=attention.reason,
            )
            for attention in observation.kpis_attention
        ],
        tasks_total=observation.tasks_total,
        ready_task_ids=[str(task.id) for task in observation.ready_tasks],
        pending_task_ids=[str(task.id) for task in observation.pending_tasks],
        running_task_ids=[str(task.id) for task in observation.running_tasks],
        failed_tasks=[
            FailedTaskSummaryOut(
                task_id=str(failed.task.id),
                attempts=failed.attempts,
                can_retry=failed.can_retry,
                error=failed.last_error,
            )
            for failed in observation.failed_tasks
        ],
        blocked_task_ids=[str(blocked.task.id) for blocked in observation.blocked_tasks],
        selectable_agent_ids=[str(agent.id) for agent in observation.selectable_agents],
        pending_approvals=[
            PendingApprovalSummaryOut(
                id=approval.id,
                action=approval.action,
                resource_type=approval.resource_type,
                resource_id=approval.resource_id,
            )
            for approval in observation.pending_approvals
        ],
    )
