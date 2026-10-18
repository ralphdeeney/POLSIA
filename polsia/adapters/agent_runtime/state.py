"""Native agent runtime execution states and execution traces (Phase 4).

The runtime is a bounded state machine, not an unbounded recursive agent loop.
``AgentRuntimeState`` is runtime-scoped and deliberately distinct from the task
domain's ``RuntimeStatus``/``ExecutionStatus``; every runtime failure state maps
onto ``RuntimeStatus.FAILED`` and no domain status is duplicated here.
"""

from __future__ import annotations

import dataclasses
import enum
import uuid


class AgentRuntimeState(enum.StrEnum):
    """Explicit runtime states (assignment §5)."""

    INITIALIZED = "initialized"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


#: Runtime failure states produce no successful result.
FAILURE_STATES = frozenset(
    {AgentRuntimeState.FAILED, AgentRuntimeState.CANCELLED, AgentRuntimeState.TIMED_OUT}
)


@dataclasses.dataclass(frozen=True)
class AgentExecutionTraceNode:
    """One node of the execution trace (sanitized, audit-only)"""

    kind: str  # "model_call" | "tool_call" | "final" | "state"
    state: AgentRuntimeState
    sequence: int
    model: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    ok: bool | None = None
    detail: str | None = None


@dataclasses.dataclass(frozen=True)
class AgentExecutionResult:
    """The runtime outcome including budgets, trace and an idempotency key."""

    state: AgentRuntimeState
    runtime_status: str
    output: dict[str, object] = dataclasses.field(default_factory=dict)
    error: str | None = None
    tool_calls: tuple[object, ...] = ()
    trace: tuple[AgentExecutionTraceNode, ...] = ()
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            # Runtime errors are not re-attemptable; completed runs are keyed.
            object.__setattr__(self, "idempotency_key", str(uuid.uuid4()))
