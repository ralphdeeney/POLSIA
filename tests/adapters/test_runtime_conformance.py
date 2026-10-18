"""Applies the reusable RuntimePort conformance suite (§3) to implementations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from polsia.adapters.agent_runtime import (
    NativeAgentRuntime,
    ScriptedModelBehavior,
    ScriptedModelProvider,
    ScriptedResponse,
    ToolGateway,
    register_default_readonly_tools,
)
from polsia.adapters.runtime import MockRuntime

from .runtime_contract import RuntimeContractCase, final_json, run_contract_suite

_NATIVE_OK_SCRIPT = [
    ScriptedResponse(
        behavior=ScriptedModelBehavior.COMPLETE,
        content=final_json("native-contract-ok"),
    )
]


def _native_factory(tool_root: Path) -> Callable[[], NativeAgentRuntime]:
    def factory() -> NativeAgentRuntime:
        gateway = ToolGateway()
        register_default_readonly_tools(gateway, whitelist_root=tool_root)
        provider = ScriptedModelProvider(_NATIVE_OK_SCRIPT)
        return NativeAgentRuntime(model=provider, tool_gateway=gateway)

    return factory


async def test_mock_runtime_contract() -> None:
    case = RuntimeContractCase(name="mock", factory=MockRuntime, failure_input={"fail": True})
    report = await run_contract_suite(case)
    assert all(report.values()), report


async def test_native_runtime_contract(tmp_path: Path) -> None:
    case = RuntimeContractCase(
        name="native",
        factory=_native_factory(tmp_path),
        # an empty objective deterministically fails the native loop
        failure_input={"objective": ""},
    )
    report = await run_contract_suite(case)
    assert all(report.values()), report
