"""NativeAgentRuntime behavior tests (Phase 4 mandate §5)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from polsia.adapters.agent_runtime import (
    ExecutionBudget,
    NativeAgentRuntime,
    ScriptedModelBehavior,
    ScriptedModelProvider,
    ScriptedResponse,
    ToolGateway,
    register_default_readonly_tools,
)
from polsia.core.runtime.ports import (
    ModelRequest,
    ModelResponse,
    RuntimeStatus,
    TokenUsage,
)
from polsia.core.shared.ids import CompanyId, TaskId

from tests.adapters.runtime_contract import build_request, final_json

_CAPABILITY = "read_only_file_access"
_CONSUMER_CONTEXT = {
    "agent_capability": _CAPABILITY,
    "company_context": "Company A runs checkout operations.",
    "agent_role": "operations analyst",
}


def _runtime(
    script: list[ScriptedResponse],
    tmp_path: Path,
    *,
    budget: ExecutionBudget | None = None,
    allow_text_final: bool = False,
    is_cancelled: Any = None,
) -> NativeAgentRuntime:
    gateway = ToolGateway()
    register_default_readonly_tools(gateway, whitelist_root=tmp_path)
    model = tmp_path / "note.txt"
    model.write_text("operating note", encoding="utf-8")
    provider = ScriptedModelProvider(script)
    return NativeAgentRuntime(
        model=provider,
        tool_gateway=gateway,
        budget=budget,
        allow_text_final=allow_text_final,
        is_cancelled=is_cancelled,
    )


def _request(**overrides: Any) -> Any:
    return build_request(
        context={**_CONSUMER_CONTEXT, **overrides.pop("context", {})},
        **overrides,
    )


def _final(evidence: str = "evidence-ok") -> ScriptedResponse:
    return ScriptedResponse(behavior=ScriptedModelBehavior.COMPLETE, content=final_json(evidence))


async def test_completes_with_success_and_records_trace(tmp_path: Path) -> None:
    runtime = _runtime([_final("ok")], tmp_path)
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.COMPLETED
    assert result.error is None
    assert result.output["evidence"] == "ok"
    assert result.metadata["runtime_state"] == "completed"
    assert result.metadata["iterations"] == 1
    assert result.metadata["trace_id"] == result.metadata["idempotency_key"]
    states = {node["state"] for node in result.metadata["trace"]}
    assert "initialized" in states and "running" in states


async def test_tool_roundtrip_then_completion(tmp_path: Path) -> None:
    script = [
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="echo",
            tool_arguments={"text": "ping"},
        ),
        _final("after-tool"),
    ]
    runtime = _runtime(script, tmp_path)
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.COMPLETED
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].ok is True
    assert result.tool_calls[0].output["echo"] == "ping"
    assert result.metadata["iterations"] == 2
    tool_nodes = [n for n in result.metadata["trace"] if n["kind"] == "tool_call"]
    assert tool_nodes and tool_nodes[0]["ok"] is True


async def test_claimed_text_completion_is_not_verifiable(tmp_path: Path) -> None:
    runtime = _runtime(
        [ScriptedResponse(behavior=ScriptedModelBehavior.COMPLETE, content="I completed it")],
        tmp_path,
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "unverifiable" in (result.error or "")


async def test_text_completion_allowed_only_when_explicit(tmp_path: Path) -> None:
    runtime = _runtime(
        [ScriptedResponse(behavior=ScriptedModelBehavior.COMPLETE, content="plain text")],
        tmp_path,
        allow_text_final=True,
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.COMPLETED
    assert result.output["text"] == "plain text"


async def test_model_reported_failure_is_failure(tmp_path: Path) -> None:
    payload = json.dumps({"type": "final", "failed": True, "summary": "missing evidence"})
    runtime = _runtime(
        [ScriptedResponse(behavior=ScriptedModelBehavior.COMPLETE, content=payload)],
        tmp_path,
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "missing evidence" in (result.error or "")


async def test_malformed_output_fails_without_faux_success(tmp_path: Path) -> None:
    runtime = _runtime(
        [ScriptedResponse(behavior=ScriptedModelBehavior.MALFORMED, content="garbage")],
        tmp_path,
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED


async def test_provider_failure_is_normalized(tmp_path: Path) -> None:
    runtime = _runtime(
        [ScriptedResponse(behavior=ScriptedModelBehavior.PROVIDER_FAILURE)],
        tmp_path,
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert result.error and "provider" in (result.error or "")


async def test_provider_timeout_state(tmp_path: Path) -> None:
    runtime = _runtime(
        [ScriptedResponse(behavior=ScriptedModelBehavior.TIMEOUT)],
        tmp_path,
    )
    result = await runtime.execute(_request())
    assert result.metadata["runtime_state"] == "timed_out"
    assert result.status is RuntimeStatus.FAILED


async def test_cancellation_is_terminal(tmp_path: Path) -> None:
    called = {"n": 0}

    def cancelled() -> bool:
        called["n"] += 1
        return True

    runtime = _runtime([_final()], tmp_path, is_cancelled=cancelled)
    result = await runtime.execute(_request())
    assert result.metadata["runtime_state"] == "cancelled"
    assert result.status is RuntimeStatus.FAILED


async def test_iteration_budget(tmp_path: Path) -> None:
    loop_script = [
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="echo",
            tool_arguments={"text": f"step-{i}"},
        )
        for i in range(5)
    ]
    runtime = _runtime(loop_script, tmp_path, budget=ExecutionBudget(max_iterations=2))
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "iterations" in (result.error or "")


async def test_tool_call_budget(tmp_path: Path) -> None:
    script = [
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="echo",
            tool_arguments={"text": "attempt"},
        ),
        _final("nope"),
    ]
    runtime = _runtime(script, tmp_path, budget=ExecutionBudget(max_tool_calls=0))
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "tool budget" in (result.error or "")


async def test_token_budget(tmp_path: Path) -> None:
    script = [
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="echo",
            tool_arguments={"text": "x"},
            prompt_tokens=10,
            completion_tokens=5,
        ),
        _final("never reached"),
    ]
    runtime = _runtime(script, tmp_path, budget=ExecutionBudget(max_model_tokens=10))
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "token budget" in (result.error or "")


class _CostlyModel:
    name = "costly-test-double"

    def __init__(self, cost: float) -> None:
        self._cost = cost

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=final_json("cost evidence"),
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
            metadata={"cost_usd": self._cost},
        )

    async def stream(self, request: ModelRequest, on_chunk: Any) -> ModelResponse:
        return await self.generate(request)


async def test_cost_budget(tmp_path: Path) -> None:
    gateway = ToolGateway()
    provider = _CostlyModel(cost=9.5)
    runtime = NativeAgentRuntime(
        model=provider,
        tool_gateway=gateway,
        budget=ExecutionBudget(max_cost_usd=5.0),
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "cost budget" in (result.error or "")


async def test_cost_accounting_within_budget(tmp_path: Path) -> None:
    gateway = ToolGateway()
    provider = _CostlyModel(cost=2.0)
    runtime = NativeAgentRuntime(
        model=provider,
        tool_gateway=gateway,
        budget=ExecutionBudget(max_cost_usd=5.0),
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.COMPLETED
    assert result.metadata["cost_usd"] == 2.0


async def test_candidate_output_must_be_evidence_backed(tmp_path: Path) -> None:
    payload = json.dumps({"type": "final", "output": {}, "summary": ""})
    runtime = _runtime(
        [ScriptedResponse(behavior=ScriptedModelBehavior.COMPLETE, content=payload)],
        tmp_path,
    )
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "no output evidence" in (result.error or "")


async def test_unauthorized_tool_attempts_recorded_and_denied(tmp_path: Path) -> None:
    script = [
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="read_file",
            tool_arguments={"path": "note.txt"},
        ),
        _final("after-tool"),
    ]
    runtime = _runtime(script, tmp_path)
    # no agent_capability -> nothing is authorized
    without_capability = _request(
        context={"company_context": "x", "agent_role": "r", "agent_capability": ""}
    )
    result = await runtime.execute(without_capability)
    assert result.status is RuntimeStatus.FAILED or result.tool_calls == []
    rejected = [record for record in result.tool_calls if not record.ok]
    assert any("not permitted" in str(record.output.get("error", "")) for record in rejected)


async def test_failed_tool_is_fed_back_then_recovered(tmp_path: Path) -> None:
    script = [
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="read_file",
            tool_arguments={"path": "missing.txt"},
        ),
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="read_file",
            tool_arguments={"path": "note.txt"},
        ),
        _final("recovered"),
    ]
    runtime = _runtime(script, tmp_path)
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.COMPLETED
    assert [record.ok for record in result.tool_calls] == [False, True]
    assert result.tool_calls[0].output["error"]


async def test_idempotency_identity_is_stable_for_identical_inputs(
    tmp_path: Path,
) -> None:
    script = [_final("dup")]
    runtime_a = _runtime(list(script), tmp_path)
    runtime_b = _runtime(list(script), tmp_path)
    request = _request()
    result_a = await runtime_a.execute(request)
    result_b = await runtime_b.execute(dataclasses.replace(request))
    assert result_a.metadata["idempotency_key"] == result_b.metadata["idempotency_key"]


async def test_company_id_recorded_in_metadata(tmp_path: Path) -> None:
    runtime = _runtime([_final("meta")], tmp_path)
    request = _request(company_id=CompanyId.generate(), task_id=TaskId.generate())
    result = await runtime.execute(request)
    assert result.metadata["company_id"] == str(request.company_id)
    assert result.metadata["task_id"] == str(request.task_id)


async def test_forged_output_without_successful_tool_calls_fails(tmp_path: Path) -> None:
    script = [
        ScriptedResponse(
            behavior=ScriptedModelBehavior.TOOL_CALL,
            tool_name="read_file",
            tool_arguments={"path": "missing.txt"},
        ),
        # model forges rich output although every tool call failed
        ScriptedResponse(
            behavior=ScriptedModelBehavior.COMPLETE,
            content=final_json(json.dumps({"file_content": "I promise I read it"})),
        ),
    ]
    runtime = _runtime(script, tmp_path)
    result = await runtime.execute(_request())
    assert result.status is RuntimeStatus.FAILED
    assert "no successful tool execution" in (result.error or "")
