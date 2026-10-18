"""NativeAgentRuntime — the provider-neutral bounded agent executor (§5).

A lightweight alternative to external agent frameworks: a state machine loop

    INITIALIZED -> RUNNING -> (WAITING_FOR_TOOL | COMPLETED | FAILED |
                               CANCELLED | TIMED_OUT)

with hard budgets (iterations, elapsed time, model tokens, cost, tool calls),
gateway-enforced tool authorization, verifiable completion (a model *claiming*
completion is never sufficient unless ``allow_text_final`` is explicitly
enabled or the run produced successful tool evidence), sanitized traces and a
deterministic idempotency/trace id so retried executions are correlatable.

The runtime is injected with a :class:`ModelPort` provider, so it never couples
the core to a vendor; it consumes the resolved ``tool_permissions`` decision set
on each :class:`RuntimeRequest` and never widens its own authority.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from polsia.adapters.agent_runtime.guard import (
    READ_ONLY_TOOL_CAPABILITY,
    AgentRuntimeSafetyBudget,
)
from polsia.adapters.agent_runtime.prompt_composer import (
    AgentPromptComposer,
    PromptContext,
    render_skill_text,
)
from polsia.adapters.agent_runtime.state import (
    AgentExecutionTraceNode,
    AgentRuntimeState,
)
from polsia.adapters.agent_runtime.tool_gateway import (
    ToolGateway,
    ToolInvocationRequest,
)
from polsia.core.memory.ports import MemoryPort
from polsia.core.registry.ports import SkillRepository
from polsia.core.runtime.ports import (
    ModelMessage,
    ModelPort,
    ModelRequest,
    ModelResponse,
    RuntimeRequest,
    RuntimeResult,
    RuntimeStatus,
    TokenUsage,
    ToolCallRecord,
)
from polsia.core.shared.errors import PolsiaError, ValidationError

log = logging.getLogger(__name__)

MAX_TASK_INPUT_CHARS = 8_000
MAX_TOOL_OBSERVATION_CHARS = 8_000
MAX_ERROR_CHARS = 500
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_MAX_ELAPSED_SECONDS = 60.0
DEFAULT_MAX_TOOL_CALLS = 5
_EXTRA_MODEL_ATTEMPTS = 1
_RETRY_BACKOFF_SECONDS = 0.05
#: Errors that may be retried once; everything else fails immediately.
_TRANSIENT_ERRORS = (TimeoutError, OSError, ConnectionError)


@dataclasses.dataclass(frozen=True)
class ExecutionBudget:
    """Runtime-side hard budget (request metadata may only lower it)."""

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_elapsed_seconds: float = DEFAULT_MAX_ELAPSED_SECONDS
    max_model_tokens: int | None = None
    max_cost_usd: float | None = None
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_model_retries: int = _EXTRA_MODEL_ATTEMPTS


class _ExecutionStop(Exception):
    """Internal control flow: leave the loop with an explicit runtime state."""

    def __init__(self, state: AgentRuntimeState, error: str) -> None:
        super().__init__(error)
        self.state = state
        self.error = error


class NativeAgentRuntime:
    """Bounded native agent runtime implementing :class:`RuntimePort`."""

    name = "native"

    def __init__(
        self,
        *,
        model: ModelPort,
        tool_gateway: ToolGateway,
        guard: AgentRuntimeSafetyBudget | None = None,
        composer: AgentPromptComposer | None = None,
        memory: MemoryPort | None = None,
        skills: SkillRepository | None = None,
        budget: ExecutionBudget | None = None,
        allow_text_final: bool = False,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._model = model
        self._gateway = tool_gateway
        self._guard = guard or AgentRuntimeSafetyBudget()
        self._composer = composer or AgentPromptComposer()
        self._memory = memory
        self._skills = skills
        self._budget = budget or ExecutionBudget()
        self._allow_text_final = allow_text_final
        self._is_cancelled = is_cancelled

    # -- public API ----------------------------------------------------------

    async def execute(self, request: RuntimeRequest) -> RuntimeResult:
        """Run one bounded agent execution; always returns a RuntimeResult.

        Cancellation is handled internally (the worker that awaits this call
        always receives a terminal result instead of an exception).
        """
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        trace: list[AgentExecutionTraceNode] = []
        tool_calls: list[ToolCallRecord] = []
        state = AgentRuntimeState.INITIALIZED
        tokens = TokenUsage()
        cost_usd = 0.0
        error: str | None = None
        output: dict[str, Any] = {}
        iteration = 0
        idem_key = "unavailable"

        try:
            self._guard.assert_agent_allowed_for_execution(request)
            trace_id, idem_key = self._trace_identity(request)
            budget = self._resolve_budget(request)
            deadline = loop.time() + budget.max_elapsed_seconds
            agent_capability = self._agent_capability(request)
            trace.append(
                AgentExecutionTraceNode(
                    kind="state",
                    state=AgentRuntimeState.INITIALIZED,
                    sequence=0,
                    detail="execution accepted",
                )
            )
            messages, tool_specs, trace = await self._prepare_context(
                request, budget, agent_capability, trace, trace_id
            )
            state = AgentRuntimeState.RUNNING
            trace.append(
                AgentExecutionTraceNode(
                    kind="state",
                    state=state,
                    sequence=len(trace),
                    detail="execution started",
                )
            )

            iteration = 0
            successful_tool_calls = 0
            while iteration < budget.max_iterations:
                self._check_cancellation()
                if loop.time() >= deadline:
                    raise _ExecutionStop(
                        AgentRuntimeState.TIMED_OUT,
                        f"execution budget: exceeded {budget.max_elapsed_seconds}s",
                    )
                self._check_token_budget(tokens, budget)
                self._check_cost_budget(cost_usd, budget)

                iteration += 1
                state = AgentRuntimeState.RUNNING
                response = await self._call_model(request, messages, tool_specs, deadline)
                tokens = TokenUsage(
                    prompt_tokens=tokens.prompt_tokens + response.usage.prompt_tokens,
                    completion_tokens=tokens.completion_tokens + response.usage.completion_tokens,
                )
                cost_usd += self._response_cost(response)
                trace.append(
                    AgentExecutionTraceNode(
                        kind="model_call",
                        state=state,
                        sequence=len(trace),
                        model=getattr(self._model, "name", None),
                        detail=f"iteration {iteration}",
                    )
                )
                # Budgets are re-checked after every model call so overruns are
                # always detected, even if the provider returned final output.
                self._check_token_budget(tokens, budget)
                self._check_cost_budget(cost_usd, budget)

                if response.tool_calls:
                    state = AgentRuntimeState.WAITING_FOR_TOOL
                    for call in response.tool_calls:
                        if len(tool_calls) >= budget.max_tool_calls:
                            raise _ExecutionStop(
                                AgentRuntimeState.FAILED,
                                f"tool budget: exceeded {budget.max_tool_calls} tool calls",
                            )
                        record = await self._invoke_tool(
                            request, call, agent_capability, trace_id, trace, messages
                        )
                        tool_calls.append(record)
                        if record.ok:
                            successful_tool_calls += 1
                    continue

                parsed = self._parse_final(request, response)
                if parsed is None:
                    raise _ExecutionStop(
                        AgentRuntimeState.FAILED,
                        "unverifiable completion: response did not satisfy the output contract",
                    )
                if parsed.get("failed"):
                    raise _ExecutionStop(
                        AgentRuntimeState.FAILED,
                        f"agent reported failure: {parsed.get('summary', 'no summary')}",
                    )
                candidate = parsed.get("output")
                summary = str(parsed.get("summary", ""))
                if not isinstance(candidate, dict):
                    candidate = {}
                if not candidate and not (summary and successful_tool_calls):
                    raise _ExecutionStop(
                        AgentRuntimeState.FAILED,
                        "unverifiable completion: no output evidence for the declared result",
                    )
                if tool_calls and successful_tool_calls == 0:
                    # Forged-evidence rule: a non-empty output with zero
                    # successful tool executions is a claim, not a result.
                    raise _ExecutionStop(
                        AgentRuntimeState.FAILED,
                        "unverifiable completion: no successful tool execution "
                        "supports the declared output",
                    )
                state = AgentRuntimeState.COMPLETED
                output = {**candidate, "summary": summary}
                break
            else:
                raise _ExecutionStop(
                    AgentRuntimeState.FAILED,
                    f"execution budget: reached {budget.max_iterations} iterations",
                )
        except _ExecutionStop as stop:
            state = stop.state
            error = stop.error
        except asyncio.CancelledError:
            state = AgentRuntimeState.CANCELLED
            error = "execution cancelled"
            log.info("agent_execution_cancelled", extra={"task_id": str(request.task_id)})
        except (PolsiaError, TimeoutError, OSError) as exc:
            state = (
                AgentRuntimeState.TIMED_OUT
                if isinstance(exc, TimeoutError)
                else AgentRuntimeState.FAILED
            )
            error = _short(str(exc))
        except Exception as exc:  # provider/tool failures must not crash callers
            state = AgentRuntimeState.FAILED
            error = _short(f"unexpected error: {type(exc).__name__}: {exc}")

        duration = max(0.0, time.monotonic() - started)
        result = RuntimeResult(
            status=RuntimeStatus.COMPLETED
            if state is AgentRuntimeState.COMPLETED
            else RuntimeStatus.FAILED,
            output=output,
            tool_calls=tool_calls,
            token_usage=tokens,
            duration_seconds=duration,
            error=error,
            model=getattr(self._model, "name", None),
            runtime=self.name,
            metadata={
                "runtime_state": state.value,
                "trace": [dataclasses.asdict(node) for node in trace],
                "idempotency_key": idem_key,
                "trace_id": idem_key,
                "company_id": str(request.company_id),
                "task_id": str(request.task_id),
                "cost_usd": round(cost_usd, 6),
                "iterations": iteration,
                "prompt_version": self._composer.version,
            },
        )
        log.info(
            "agent_execution_finished",
            extra={
                "state": state.value,
                "task_id": str(request.task_id),
                "company_id": str(request.company_id),
            },
        )
        return result

    # -- internals -----------------------------------------------------------

    def _trace_identity(self, request: RuntimeRequest) -> tuple[str, str]:
        """Deterministic trace/idempotency identity for retry correlation."""
        identity = json.dumps(
            {
                "company": str(request.company_id),
                "task": str(request.task_id),
                "agent": str(request.agent_id or ""),
                "skill": str(request.skill_id or ""),
                "input": request.input,
                "prompt_version": self._composer.version,
            },
            sort_keys=True,
            default=str,
        )
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"ar_{key}", key

    def _resolve_budget(self, request: RuntimeRequest) -> ExecutionBudget:
        # Request metadata may only tighten the runtime-side budget; the guard
        # validates types and clamps absurd values in one place.
        try:
            iter_req = self._guard.max_iterations(request)
            elapsed_req = self._guard.max_elapsed_seconds(request)
        except ValidationError:
            raise
        iterations = min(self._budget.max_iterations, iter_req)
        elapsed = min(self._budget.max_elapsed_seconds, elapsed_req)
        tokens = _min_optional(self._budget.max_model_tokens, None)
        tokens_req = self._guard.max_model_tokens(request)
        tokens = _min_optional(tokens, tokens_req)
        cost_req = self._guard.max_cost_usd(request)
        cost = _min_optional(self._budget.max_cost_usd, cost_req)
        tool_calls = min(self._budget.max_tool_calls, self._guard.agent_tool_limit(request))
        return ExecutionBudget(
            max_iterations=max(1, iterations),
            max_elapsed_seconds=elapsed,
            max_model_tokens=int(tokens) if tokens is not None else None,
            max_cost_usd=cost,
            max_tool_calls=tool_calls,
        )

    def _agent_capability(self, request: RuntimeRequest) -> str | None:
        capability = request.context.get("agent_capability", None)
        if capability is None:
            return None
        capability = str(capability).strip()
        return capability or None

    def _check_cancellation(self) -> None:
        if self._is_cancelled is not None and self._is_cancelled():
            raise _ExecutionStop(AgentRuntimeState.CANCELLED, "execution cancelled")

    def _check_token_budget(self, tokens: TokenUsage, budget: ExecutionBudget) -> None:
        if budget.max_model_tokens is None:
            return
        if tokens.total_tokens > budget.max_model_tokens:
            raise _ExecutionStop(
                AgentRuntimeState.FAILED,
                f"token budget: {tokens.total_tokens} > {budget.max_model_tokens}",
            )

    def _check_cost_budget(self, cost_usd: float, budget: ExecutionBudget) -> None:
        if budget.max_cost_usd is None:
            return
        if cost_usd > budget.max_cost_usd:
            raise _ExecutionStop(
                AgentRuntimeState.FAILED,
                f"cost budget: ${cost_usd:.4f} > ${budget.max_cost_usd:.4f}",
            )

    @staticmethod
    def _response_cost(response: ModelResponse) -> float:
        raw = response.metadata.get("cost_usd", 0.0)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 0.0

    async def _call_model(
        self,
        request: RuntimeRequest,
        messages: list[ModelMessage],
        tool_specs: list[dict[str, Any]],
        deadline: float,
    ) -> ModelResponse:
        model_request = ModelRequest(
            messages=messages,
            model=request.model,
            tools=tool_specs,
            metadata={
                "trace_id": self._trace_identity(request)[0],
                "company_id": str(request.company_id),
                "task_id": str(request.task_id),
            },
        )
        remaining = max(1.0, deadline - asyncio.get_running_loop().time())
        attempts = self._budget.max_model_retries + 1
        last_error: Exception | None = None
        for index in range(attempts):
            try:
                return await asyncio.wait_for(
                    self._model.generate(model_request), timeout=remaining
                )
            except asyncio.CancelledError:
                raise
            except _TRANSIENT_ERRORS as exc:
                last_error = exc
                if index + 1 < attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (index + 1))
            except PolsiaError as exc:
                raise _ExecutionStop(AgentRuntimeState.FAILED, _short(str(exc))) from exc
        raise _ExecutionStop(
            AgentRuntimeState.TIMED_OUT,
            _short(f"provider unavailable after retries: {last_error}"),
        )

    async def _prepare_context(
        self,
        request: RuntimeRequest,
        budget: ExecutionBudget,
        agent_capability: str | None,
        trace: list[AgentExecutionTraceNode],
        trace_id: str,
    ) -> tuple[list[ModelMessage], list[dict[str, Any]], list[AgentExecutionTraceNode]]:
        """Compose the bounded system prompt and initial task message."""
        objective = str(request.input.get("objective") or "").strip()
        if not objective:
            raise ValidationError("task input requires a non-empty 'objective'")
        company_context = str(request.context.get("company_context") or "not provided").strip()
        agent_role = str(request.context.get("agent_role") or "generic agent").strip()
        skills = tuple(await self._load_skills(request))
        memory = tuple(await self._load_memory(request, objective))

        prompt_context = PromptContext(
            company_id=str(request.company_id),
            company_context=company_context,
            agent_role=agent_role,
            task_objective=objective,
            skills=skills,
            memory_records=memory,
            authorized_tools=self._exposed_tools(request, agent_capability),
            max_iterations=budget.max_iterations,
            max_elapsed_seconds=budget.max_elapsed_seconds,
            max_tool_calls=budget.max_tool_calls,
            max_model_tokens=budget.max_model_tokens,
            max_cost_usd=budget.max_cost_usd,
        )
        system_prompt = self._composer.compose(prompt_context)
        if request.system_instructions:
            system_prompt = f"{request.system_instructions}\n\n{system_prompt}"

        messages = [
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(
                role="user",
                content=json.dumps({"objective": objective, "input": request.input})[
                    :MAX_TASK_INPUT_CHARS
                ],
            ),
        ]
        trace.append(
            AgentExecutionTraceNode(
                kind="state",
                state=AgentRuntimeState.RUNNING,
                sequence=len(trace),
                detail=f"context prepared (trace {trace_id})",
            )
        )
        tool_specs = [
            {"name": name, "parameters": parameters}
            for name, parameters in (
                (spec.name, spec.parameters)
                for spec in self._gateway.registered()
                if spec.allowed_for_agent(agent_capability)
                and self._permission_decision(request, spec.name) == "allow"
            )
        ]
        return messages, tool_specs, trace

    def _permission_decision(self, request: RuntimeRequest, tool_name: str) -> str | None:
        """Resolved decision, or None when no decision set was provided."""
        if not request.tool_permissions:
            return None
        return str(request.tool_permissions.get(tool_name, "")).strip().lower() or None

    def _exposed_tools(
        self, request: RuntimeRequest, agent_capability: str | None
    ) -> tuple[str, ...]:
        names: list[str] = []
        for spec in self._gateway.registered():
            if not spec.allowed_for_agent(agent_capability):
                continue
            decision = self._permission_decision(request, spec.name)
            if decision is not None and decision != "allow":
                continue
            names.append(f"{spec.name}@{spec.version}")
        return tuple(names)

    async def _load_skills(self, request: RuntimeRequest) -> list[str]:
        selected = request.context.get("skills")
        if self._skills is None or not isinstance(selected, list) or not selected:
            return []
        rendered: list[str] = []
        for item in selected:
            if len(rendered) >= 3:
                break
            try:
                name = str(item.get("name", "")).strip() if isinstance(item, dict) else ""
                version = str(item.get("version", "")).strip() if isinstance(item, dict) else ""
            except AttributeError:
                continue
            if not name:
                continue
            skill = await self._skills.get_by_key(name, version) if version else None
            if skill is None:
                candidates = await self._skills.list_by_name(name)
                skill = candidates[0] if candidates else None
            if skill is None or not skill.is_selectable():
                continue
            rendered.append(render_skill_text(skill))
        return rendered

    async def _load_memory(self, request: RuntimeRequest, objective: str) -> list[str]:
        if self._memory is None:
            return []
        records = await self._memory.search(request.company_id, objective, scope=None, limit=3)
        texts: list[str] = []
        for record in records:
            content = getattr(record, "content", None)
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
        return texts

    async def _invoke_tool(
        self,
        request: RuntimeRequest,
        call: dict[str, Any],
        agent_capability: str | None,
        trace_id: str,
        trace: list[AgentExecutionTraceNode],
        messages: list[ModelMessage],
    ) -> ToolCallRecord:
        tool_name = str(call.get("name") or "").strip()
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = str(call.get("id") or uuid.uuid4().hex)
        invocation = ToolInvocationRequest(
            tool_name=tool_name,
            tool_version=None,
            arguments=dict(arguments),
            company_id=str(request.company_id),
            task_id=str(request.task_id),
            agent_capability=agent_capability,
            trace_id=trace_id,
            tool_call_id=call_id,
        )
        result = await self._gateway.execute(invocation)
        record = ToolCallRecord(
            tool=tool_name,
            action="invoke",
            input=dict(arguments),
            output=dict(result.output) if result.ok else {"error": result.error},
            ok=result.ok,
        )
        observation = {
            "tool_call_id": call_id,
            "tool": tool_name,
            "ok": result.ok,
            "result": record.output,
        }
        trace.append(
            AgentExecutionTraceNode(
                kind="tool_call",
                state=AgentRuntimeState.WAITING_FOR_TOOL,
                sequence=len(trace),
                tool_name=tool_name,
                tool_call_id=call_id,
                ok=result.ok,
            )
        )
        # Tool output is untrusted data: conveyed as an observation, never as
        # instructions, and truncated before it can flood the model context.
        messages.append(
            ModelMessage(role="tool", content=json.dumps(observation)[:MAX_TOOL_OBSERVATION_CHARS])
        )
        return record

    def _parse_final(
        self, request: RuntimeRequest, response: ModelResponse
    ) -> dict[str, Any] | None:
        text = (response.content or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            if self._allow_text_final or request.metadata.get("allow_text_final"):
                return {"type": "final", "output": {"text": text}, "summary": text[:200]}
            return None
        if not isinstance(payload, dict) or payload.get("type") != "final":
            return None
        return payload


def _min_optional(*values: float | int | None) -> float | int | None:
    """Smallest non-None of the inputs (None participates only if all are None)."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    return min(present)


def _short(message: str) -> str:
    cleaned = " ".join(message.split())
    if len(cleaned) > MAX_ERROR_CHARS:
        cleaned = cleaned[: MAX_ERROR_CHARS - 3] + "..."
    return cleaned


def read_only_capability() -> str:
    """Expose the strict read-only capability constant for callers."""
    return READ_ONLY_TOOL_CAPABILITY
