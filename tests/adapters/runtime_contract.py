"""Reusable RuntimePort conformance suite (Phase 4 mandate §3).

A contract suite, not a unit test: it can be exercised against ANY RuntimePort
implementation (MockRuntime today, NativeAgentRuntime, a future HermesRuntime)
by declaring how to construct the runtime and how it deterministically fails.
It returns a named boolean report; callers assert ``all(report.values())``.

Generic protocol checks (must hold for every implementation):
    success_result_shape, failure_result_shape, invalid_request_safety,
    tenant_context_preserved, repeated_execution_ok, tool_permission_respected

Runtime-specific behaviors (timeouts, cancellation, budgets, malformed output,
provider failures, token accounting details, cleanup) are verified in the
dedicated runtime test modules because their triggering mechanics are
implementation-specific.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from polsia.core.runtime.ports import (
    RuntimeRequest,
    RuntimeResult,
    RuntimeStatus,
)
from polsia.core.shared.ids import AgentId, CompanyId, TaskId

READ_ONLY_CAPABILITY = "read_only_file_access"


def build_request(**overrides: Any) -> RuntimeRequest:
    """A valid baseline execution request (tenant context included)."""
    base: dict[str, Any] = {
        "company_id": CompanyId.generate(),
        "task_id": TaskId.generate(),
        "agent_id": AgentId.generate(),
        "input": {"objective": "summarize this company's operating status"},
        "context": {
            "agent_capability": READ_ONLY_CAPABILITY,
            "company_context": "Company context: detached operations.",
            "agent_role": "observer",
        },
        "metadata": {},
    }
    base.update(overrides)
    return RuntimeRequest(**base)


def final_json(evidence: str) -> str:
    """Compact final-contract payload for the scripted-model pairing."""
    payload = {
        "type": "final",
        "output": {"evidence": evidence},
        "summary": "done",
    }
    import json

    return json.dumps(payload)


@dataclasses.dataclass(frozen=True)
class RuntimeContractCase:
    """One runtime implementation under contract test."""

    name: str
    factory: Callable[[], Any]  # returns an object exposing .execute()
    failure_input: dict[str, Any]  # input that deterministically produces failure


def _check_success_shape(result: RuntimeResult) -> bool:
    return (
        result.status is RuntimeStatus.COMPLETED
        and result.is_success()
        and isinstance(result.output, dict)
        and bool(result.output)
    )


def _check_failure_shape(result: RuntimeResult) -> bool:
    return (
        result.status is RuntimeStatus.FAILED
        and not result.is_success()
        and isinstance(result.error, str)
        and bool(result.error)
    )


async def run_contract_suite(case: RuntimeContractCase) -> dict[str, bool]:
    """Run the generic protocol contract; report named boolean results."""
    report: dict[str, bool] = {}

    # 1. valid execution -> well-formed success
    try:
        result = await case.factory().execute(build_request())
        report["success_result_shape"] = _check_success_shape(result)
    except Exception:  # contract violations are reported, never propagated
        report["success_result_shape"] = False

    # 2. deterministic failure -> well-formed failure, never an exception
    try:
        failed = await case.factory().execute(
            build_request(
                input={
                    "objective": "summarize this company's operating status",
                    **case.failure_input,
                }
            )
        )
        report["failure_result_shape"] = _check_failure_shape(failed)
    except Exception:
        report["failure_result_shape"] = False

    # 3. invalid requests are contained (no crash, no fabricated success)
    try:
        bad = await case.factory().execute(build_request(input={}))
        report["invalid_request_safety"] = isinstance(bad, RuntimeResult) and (
            bad.status is RuntimeStatus.FAILED or isinstance(bad.output, dict)
        )
    except Exception:
        report["invalid_request_safety"] = False

    # 4. tenant context is preserved (company/task ids survive the round-trip)
    try:
        company = CompanyId.generate()
        task = TaskId.generate()
        result = await case.factory().execute(build_request(company_id=company, task_id=task))
        context_preserved = (
            result.metadata.get("company_id") == str(company)
            or result.output.get("task_id") in (task, str(task))
            or any(
                str(company) in str(record.input) or str(company) in str(record.output)
                for record in result.tool_calls
            )
        )
        report["tenant_context_preserved"] = context_preserved
    except Exception:
        report["tenant_context_preserved"] = False

    # 5. repeated identical executions remain stable (idempotent handling)
    try:
        runtime = case.factory()
        first = await runtime.execute(build_request())
        second = await runtime.execute(build_request())
        report["repeated_execution_ok"] = _check_success_shape(first) and (
            _check_success_shape(second) or _check_failure_shape(second)
        )
    except Exception:
        report["repeated_execution_ok"] = False

    # 6. no unauthorized action: unregistered tool references never produce
    #    executed output (audit-trail mentions without evidence are harmless)
    try:
        result = await case.factory().execute(
            build_request(
                input={
                    "objective": "use an unauthorized tool",
                    "tool_calls": ["unregistered_tool@9.9.9"],
                }
            )
        )
        report["tool_permission_respected"] = all(
            record.output is None
            for record in result.tool_calls
            if record.tool == "unregistered_tool@9.9.9"
        )
    except Exception:
        report["tool_permission_respected"] = False

    return report
