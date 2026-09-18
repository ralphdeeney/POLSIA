"""Deterministic adapter implementations of the runtime/scheduling/
orchestration ports (Phase 2).

These are intentionally simple and provider-free:
- MockRuntime: executes a request deterministically (no LLM).
- StubScheduler: records schedules without running them.
- DeterministicOrchestrator: goal -> task drafts by rule, no AI reasoning.
"""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Any

from polsia.core.runtime.ports import (
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamHandler,
    RuntimePort,
    RuntimeRequest,
    RuntimeResult,
    TokenUsage,
    ToolCallRecord,
)
from polsia.core.scheduling import (
    DecompositionPlan,
    OrchestratorPort,
    ScheduledHandle,
    SchedulerPort,
    ScheduleSpec,
    TaskDraft,
)


class MockRuntime:
    """Deterministic runtime for Phase 2.

    Behaviour is driven by the request input so tests (and the capstone flow)
    can exercise success and failure paths without any model provider:

    - ``input["fail"] = True`` -> a failed result with an error string.
    - ``input["error"] = "..."`` -> custom error message.
    - ``input["tool_calls"] = ["web_search@1.0.0"]`` -> recorded tool usage.
    - ``input["artifacts"]`` -> passed through, so downstream verification can
      inspect produced artifacts.
    - ``input["sleep_seconds"]`` -> recorded as duration (not actually slept).
    - Any other input is echoed back under ``output["echo"]``.
    """

    name = "mock"

    def __init__(self, *, default_model: str | None = None) -> None:
        self._default_model = default_model
        self._counter = itertools.count(1)

    async def execute(self, request: RuntimeRequest) -> RuntimeResult:
        payload = dict(request.input)
        model = request.model or self._default_model
        duration = float(payload.get("sleep_seconds", 0.0) or 0.0)

        if payload.get("fail"):
            return RuntimeResult.failed(
                str(payload.get("error") or "mock runtime failure"),
                runtime=self.name,
                model=model,
                duration_seconds=duration,
            )

        tool_calls = [
            ToolCallRecord(tool=str(tool), action="execute", ok=True)
            for tool in payload.get("tool_calls", []) or []
        ]
        steps = int(payload.get("steps", 1) or 1)
        usage = TokenUsage(
            prompt_tokens=int(payload.get("prompt_tokens", 0) or 0),
            completion_tokens=int(payload.get("completion_tokens", 0) or 0),
        )
        output: dict[str, Any] = {
            "task_id": str(request.task_id),
            "attempt_marker": next(self._counter),
            "echo": {k: v for k, v in payload.items() if k not in {"fail", "error"}},
            "steps": steps,
        }
        # Generic measurement passthrough: a runtime may report a numeric
        # measurement for the caller's KPIs at the top level of the output.
        if "kpi_value" in payload:
            output["kpi_value"] = payload["kpi_value"]
        return RuntimeResult.completed(
            output,
            runtime=self.name,
            model=model,
            tool_calls=tool_calls,
            token_usage=usage,
            artifacts=list(payload.get("artifacts", []) or []),
            duration_seconds=duration,
            metadata={"runtime": self.name},
        )


class NullModelProvider:
    """Provider-neutral no-op ModelPort for Phase 2 (no external providers).

    ``generate`` returns an empty completion and ``stream`` emits nothing;
    real adapters (any vendor SDK) implement the same port later.
    """

    name = "null"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="",
            model=request.model,
            finish_reason="stop",
            metadata={"provider": self.name},
        )

    async def stream(self, request: ModelRequest, on_chunk: ModelStreamHandler) -> ModelResponse:
        response = await self.generate(request)
        await on_chunk(response.content)
        return response


class StubScheduler:
    """In-memory SchedulerPort that records schedules without executing them.

    Deterministic and dependency-free; a production scheduler adapter can
    replace it behind the same port.
    """

    def __init__(self, *, clock: Any = None) -> None:
        self._counter = itertools.count(1)
        self._schedules: dict[str, tuple[ScheduleSpec, Any]] = {}
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))

    async def schedule(self, spec: ScheduleSpec, *, company_id: Any) -> ScheduledHandle:
        handle_id = f"sched_{next(self._counter)}"
        next_run = spec.at if spec.kind.value in {"immediate", "at"} else None
        self._schedules[handle_id] = (spec, company_id)
        return ScheduledHandle(id=handle_id, kind=spec.kind, next_run_at=next_run)

    async def cancel(self, handle_id: str) -> None:
        self._schedules.pop(handle_id, None)

    def registered(self) -> list[tuple[str, ScheduleSpec]]:
        """Introspection for tests."""
        return [(handle, spec) for handle, (spec, _) in self._schedules.items()]


class DeterministicOrchestrator:
    """Rule-based OrchestratorPort: goal -> task drafts, no AI reasoning.

    Default: one task per goal. If ``context["objectives"]`` is provided (a
    list of strings), one sequential task draft is produced per objective with
    a linear dependency chain (each depends on the previous).
    """

    name = "deterministic"

    async def decompose(
        self,
        *,
        company_id: Any,
        goal_id: Any,
        goal_title: str,
        goal_description: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> DecompositionPlan:
        ctx = dict(context or {})
        objectives = ctx.get("objectives") or []
        if not objectives:
            return DecompositionPlan(
                tasks=[
                    TaskDraft(
                        title=goal_title,
                        description=goal_description,
                        metadata={"goal_id": str(goal_id), "generated_by": self.name},
                    )
                ],
                rationale="single task derived from the goal title",
                metadata={"goal_id": str(goal_id)},
            )
        drafts: list[TaskDraft] = []
        for index, objective in enumerate(objectives):
            drafts.append(
                TaskDraft(
                    title=str(objective),
                    description=None,
                    depends_on_indexes=[index - 1] if index > 0 else [],
                    metadata={"goal_id": str(goal_id), "generated_by": self.name},
                )
            )
        return DecompositionPlan(
            tasks=drafts,
            rationale="one sequential task per provided objective",
            metadata={"goal_id": str(goal_id), "objective_count": len(drafts)},
        )


_ = (ModelPort, RuntimePort, SchedulerPort, OrchestratorPort)  # port references for docs
