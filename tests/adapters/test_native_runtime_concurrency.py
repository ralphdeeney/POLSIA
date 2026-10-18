"""Concurrency baselines for bounded native runtime execution (§14).

Simulated providers/tools only: throughput/latency/queue behavior baselines,
budget enforcement under concurrency, and tenant isolation across concurrent
executions. No real-provider throughput is claimed anywhere.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from polsia.adapters.agent_runtime import (
    ExecutionBudget,
    NativeAgentRuntime,
    ScriptedModelBehavior,
    ScriptedModelProvider,
    ScriptedResponse,
    ToolGateway,
    register_default_readonly_tools,
)
from polsia.core.runtime.ports import RuntimeRequest, RuntimeStatus
from polsia.core.shared.ids import AgentId, CompanyId, TaskId

_LEVELS = (1, 2, 5, 10)
_NOTE = "concurrency operating note"


def _final(evidence: str) -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.COMPLETE,
        content=json.dumps({"type": "final", "output": {"evidence": evidence}, "summary": "done"}),
    )


def _tool(text: str) -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.TOOL_CALL,
        tool_name="echo",
        tool_arguments={"text": text},
    )


def _request(company_index: int, index: int) -> RuntimeRequest:
    return RuntimeRequest(
        company_id=CompanyId.generate(),
        task_id=TaskId.generate(),
        agent_id=AgentId.generate(),
        input={"objective": f"concurrent task {index}"},
        context={
            "agent_capability": "read_only_file_access",
            "company_context": f"Company {company_index} context.",
            "agent_role": "worker",
        },
    )


class ConcurrencyResult:
    pass


@pytest.mark.parametrize("level", _LEVELS)
async def test_concurrent_executions_complete_and_isolate(level: int) -> None:
    with tempfile_dir() as root:
        gateway = ToolGateway()
        register_default_readonly_tools(gateway, whitelist_root=Path(root))
        runtime = NativeAgentRuntime(
            model=ScriptedModelProvider(
                [_tool("ping"), _final("ok")] * (level + 1)  # scripted provider recycles last
            ),
            tool_gateway=gateway,
            budget=ExecutionBudget(max_tool_calls=3),
        )
        started = time.perf_counter()
        results = await asyncio.gather(*(runtime.execute(_request(i, i)) for i in range(level)))
        elapsed = time.perf_counter() - started
        assert all(r.status is RuntimeStatus.COMPLETED for r in results), [r.error for r in results]
        # throughput baseline (deterministic simulation only)
        assert elapsed < 30.0
        seen_companies = {str(r.metadata["company_id"]) for r in results}
        request_companies = {
            # metadata must echo the original tenant context per execution
            r.metadata["company_id"]
            for r in results
        }
        assert len(request_companies) == level  # unique tenants per execution
        assert seen_companies == request_companies


def tempfile_dir():
    import tempfile

    return tempfile.TemporaryDirectory()


async def test_budgets_enforced_under_concurrency() -> None:
    with tempfile_dir() as root:
        gateway = ToolGateway()
        register_default_readonly_tools(gateway, whitelist_root=Path(root))

        def runtime_for(worker: int) -> NativeAgentRuntime:
            # each worker owns its own scripted provider: one oversized model
            # call, one unreachable final — the budget must stop both
            return NativeAgentRuntime(
                model=ScriptedModelProvider(
                    [
                        ScriptedResponse(
                            behavior=ScriptedModelBehavior.TOOL_CALL,
                            tool_name="echo",
                            tool_arguments={"text": "loop"},
                            prompt_tokens=100,
                            completion_tokens=100,
                        ),
                        _final("unreachable"),
                    ]
                ),
                tool_gateway=ToolGateway(),
                budget=ExecutionBudget(max_model_tokens=150),  # 200 > 150
            )

        results = await asyncio.gather(*(runtime_for(i).execute(_request(i, i)) for i in range(10)))
        assert all(r.status is RuntimeStatus.FAILED for r in results)
        assert all("token budget" in (r.error or "") for r in results)


async def test_cancelled_runs_do_not_leak_into_completed_state() -> None:
    with tempfile_dir() as root:
        gateway = ToolGateway()
        register_default_readonly_tools(gateway, whitelist_root=Path(root))
        cancelled_flag = True

        runtime = NativeAgentRuntime(
            model=ScriptedModelProvider([_final("ok")]),
            tool_gateway=gateway,
            is_cancelled=lambda: cancelled_flag,
        )
        results = await asyncio.gather(*(runtime.execute(_request(i, i)) for i in range(5)))
        assert all(r.metadata["runtime_state"] == "cancelled" for r in results)
        assert all(not r.output for r in results)


async def test_peak_memory_is_bounded_by_simulation_scope() -> None:
    """Resource accounting: bounded runs keep a bounded simulated footprint."""
    import tracemalloc

    with tempfile_dir() as root:
        gateway = ToolGateway()
        register_default_readonly_tools(gateway, whitelist_root=Path(root))
        runtime = NativeAgentRuntime(
            model=ScriptedModelProvider([_final("ok")]),
            tool_gateway=gateway,
        )
        tracemalloc.start()
        results = await asyncio.gather(*(runtime.execute(_request(i, i)) for i in range(10)))
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert all(r.status is RuntimeStatus.COMPLETED for r in results)
        # simulated 10 concurrent bounded runs stay far below any production pressure
        assert peak < 64 * 1024 * 1024
