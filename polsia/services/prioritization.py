"""CEO prioritization (Phase 3, mandate §7): deterministic, inspectable.

Priority is computed from configured policy + observable state only — no
model intuition. Every scored item carries a human-readable rationale listing
the contributing factors, so the system can always answer "why was this
prioritized?".
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from polsia.core.ceo.observation import (
    BlockedTask,
    CompanyObservation,
    FailedTask,
    KpiAttention,
)
from polsia.core.planning.values import Priority as GoalPriority
from polsia.core.tasks.entities import Task


@dataclasses.dataclass(frozen=True)
class PriorityPolicy:
    """Weights for the deterministic priority score (documented defaults).

    score = g · goal_priority_rank
          + k · kpi_attention
          + f · failure_urgency
          + b · blocking_factor

    - goal_priority_rank: 0..3 from the goal's Priority (medium=0 … critical=3)
    - kpi_attention: 1.0 when any measurable goal KPI is at_risk/missed
    - failure_urgency: /\attempts  (0 before the first attempt, scaled)
    - blocking_factor: 1.0 when the task blocks dependents

    Each term is multiplied by its weight; the result is not normalized across
    items (only used for relative ordering). Rationale strings are built from
    the nonzero terms.
    """

    weight_goal: float = 1.0
    weight_kpi: float = 1.2
    weight_failure: float = 1.5
    weight_blocking: float = 0.8
    #: KPI attention threshold: business-agnostic signal that a measurable
    #: goal is drifting.
    kpi_attention_value: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return dataclasses.asdict(self)


_GOAL_RANK: dict[GoalPriority, float] = {
    GoalPriority.LOW: 1.0,
    GoalPriority.MEDIUM: 2.0,
    GoalPriority.HIGH: 3.0,
    GoalPriority.CRITICAL: 4.0,
}


def _task_goal_rank(task: Task, obs: CompanyObservation) -> float:
    """Priority rank of the task's goal (1..4); 2 (medium) when unknown."""
    if task.goal_id is None:
        return _GOAL_RANK[GoalPriority.MEDIUM]
    for goal in obs.active_goals:
        if goal.id == task.goal_id:
            return _GOAL_RANK[goal.priority]
    return _GOAL_RANK[GoalPriority.MEDIUM]


@dataclasses.dataclass(frozen=True)
class PrioritizedItem:
    """A task or attention item with its deterministic score and rationale."""

    task: Task
    score: float
    rationale: str
    #: optional human label for what needs attention (e.g. a KPI reason)
    attention_note: str | None = None


@dataclasses.dataclass(frozen=True)
class PriorityReport:
    """Ordered (highest first) list of items needing attention, plus the
    policy snapshot used to produce it (inspectable)."""

    items: list[PrioritizedItem]
    policy: PriorityPolicy

    def explain(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": str(item.task.id),
                "score": round(item.score, 4),
                "rationale": item.rationale,
                "attention_note": item.attention_note,
            }
            for item in self.items
        ]


def _attention_kpis(obs: CompanyObservation) -> list[KpiAttention]:
    return list(obs.kpis_attention)


class PriorityEngine:
    """Deterministic, explainable priority scoring over an observation."""

    def __init__(self, policy: PriorityPolicy | None = None) -> None:
        self._policy = policy or PriorityPolicy()

    @property
    def policy(self) -> PriorityPolicy:
        return self._policy

    def prioritize(self, obs: CompanyObservation) -> PriorityReport:
        """Score every actionable task in the observation (ready, pending,
        failed-retryable, blocked) and order by descending score."""
        items: list[PrioritizedItem] = []

        for task in obs.ready_tasks + obs.pending_tasks + obs.running_tasks:
            score, rationale = self._score_task(task, obs)
            items.append(PrioritizedItem(task=task, score=score, rationale=rationale))

        for failed in obs.failed_tasks:
            if not failed.can_retry:
                continue
            score, rationale = self._score_failed(failed, obs)
            items.append(
                PrioritizedItem(
                    task=failed.task,
                    score=score,
                    rationale=rationale,
                    attention_note="failed; retryable",
                )
            )

        for blocked in obs.blocked_tasks:
            score, rationale = self._score_blocked(blocked, obs)
            items.append(
                PrioritizedItem(
                    task=blocked.task,
                    score=score,
                    rationale=rationale,
                    attention_note="blocked by dependencies",
                )
            )

        items.sort(key=lambda i: (-i.score, str(i.task.id)))
        # after PR sorting, ensure at least deterministic ties
        return PriorityReport(items=items, policy=self._policy)

    def _score_task(self, task: Task, obs: CompanyObservation) -> tuple[float, str]:
        p = self._policy
        terms: list[tuple[str, float]] = []

        rank = _task_goal_rank(task, obs)
        terms.append((f"goal_priority={rank:.1f}", p.weight_goal * (rank - 2.0)))

        has_kpi_attention = (
            any(a.goal is not None and a.goal.id == str(task.goal_id) for a in _attention_kpis(obs))
            if task.goal_id
            else any(a.goal is None for a in _attention_kpis(obs))
        )
        if has_kpi_attention:
            terms.append(
                (f"kpi_attention={p.kpi_attention_value:.1f}", p.weight_kpi * p.kpi_attention_value)
            )

        return self._assemble("task", task, terms)

    def _score_failed(self, failed: FailedTask, obs: CompanyObservation) -> tuple[float, str]:
        p = self._policy
        terms: list[tuple[str, float]] = [("goal_priority", 0.0)]
        urgency = math.sqrt(failed.attempts) if failed.attempts > 0 else 0.0
        terms.append((f"failure_urgency={urgency:.1f}", p.weight_failure * urgency))
        return self._assemble("failed-task", failed.task, terms)

    def _score_blocked(self, blocked: BlockedTask, obs: CompanyObservation) -> tuple[float, str]:
        p = self._policy
        terms: list[tuple[str, float]] = [("blocking=1.0", p.weight_blocking * 1.0)]
        return self._assemble("blocked-task", blocked.task, terms)

    @staticmethod
    def _assemble(kind: str, task: Task, terms: list[tuple[str, float]]) -> tuple[float, str]:
        score = sum(v for _, v in terms)
        rationale = "; ".join(f"{label}→{value:+.2f}" for label, value in terms)
        return score, f"{kind} {task.title!r}: scored {score:.2f} ({rationale})"
