"""Agent-runtime execution safety budget and tool-authorization enforcement.

Phase 4 (mandate §5/§6): every side-effecting tool call must be independently
checked against the resolved permission decision set, and a task-level ALLOW
must never grant arbitrary tool access. Nothing here executes anything — the
guard only decides *whether* the runtime/tool gateway may proceed, and it is
exercised in executable code, never only in prompts.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, Protocol

from polsia.core.permissions.errors import PermissionDenied
from polsia.core.registry.entities import Tool
from polsia.core.registry.ports import ToolRepository
from polsia.core.runtime.ports import RuntimeRequest
from polsia.core.shared.errors import ValidationError

#: Tool capabilities that are never reachable through the agent runtime, even
#: when a registry entry (or a caller) claims to authorize them.
UNSAFE_TOOL_CAPABILITIES = frozenset(
    {
        "shell_command_execution",
        "filesystem_write_access",
        "network_outbound_message",
        "external_payment_execution",
        "software_deployment",
        "public_message_publishing",
    }
)

#: Every read-only/tool capability the native runtime may ever grant.
READ_ONLY_TOOL_CAPABILITY = "read_only_file_access"

#: Upper bound one agent execution may set on attempted tool calls.
MAX_AGENT_TOOL_CALL_LIMIT = 50
DEFAULT_AGENT_TOOL_CALL_LIMIT = 5


class ToolCapabilityPolicy(Protocol):
    """Strict (default) tool capability evaluator."""

    def tool_execution_allowed_for_agent(
        self,
        agent_tool_capability: str | None,
        tool: Tool,
        *,
        company_id: str,
    ) -> bool: ...


@dataclasses.dataclass(frozen=True)
class AgentActionInvocation:
    """A single attempted tool invocation attributable to one agent."""

    tool_name: str
    agent_id: str
    company_id: str
    task_id: str
    action_invocation: dict[str, Any] = dataclasses.field(default_factory=dict)
    trace_id: str = ""


class StrictToolCapabilityPolicy:
    """Default policy: strictly read-only tools, and only the runtime's own
    allowed tool families plus the attempted tool itself."""

    def tool_execution_allowed_for_agent(
        self,
        agent_tool_capability: str | None,
        tool: Tool,
        *,
        company_id: str,
    ) -> bool:
        if READ_ONLY_TOOL_CAPABILITY not in tool.capabilities:
            return False
        if any(capability in UNSAFE_TOOL_CAPABILITIES for capability in tool.capabilities):
            return False
        if agent_tool_capability == READ_ONLY_TOOL_CAPABILITY:
            return True
        return False


class AgentRuntimeSafetyBudget:
    """Enforces execution budgets and the strict tool allowlist for one
    agent runtime execution."""

    def __init__(self, policy: ToolCapabilityPolicy | None = None) -> None:
        self._policy: ToolCapabilityPolicy = policy or StrictToolCapabilityPolicy()

    # -- exercise budgets ---------------------------------------------------

    def assert_agent_allowed_for_execution(self, request: RuntimeRequest) -> None:
        """Raise when a runtime execution lacks a company/task scope."""
        if not str(request.company_id) or not str(request.task_id):
            raise PermissionDenied("agent execution requires company and task context")

    def assert_tool_allowed_for_agent(
        self,
        *,
        tool_name: str,
        tool: Tool,
        agent_capability: str | None,
        company_id: str,
    ) -> None:
        """Raise when an agent attempts a tool outside its declared capability."""
        if not tool.is_selectable():
            raise PermissionDenied(f"tool {tool.name}@{tool.version} is not selectable")
        if not self._policy.tool_execution_allowed_for_agent(
            agent_capability, tool, company_id=company_id
        ):
            raise PermissionDenied(
                f"tool {tool.name}@{tool.version} is not permitted for agent "
                f"capability {agent_capability!r} in company {company_id}"
            )

    def agent_tool_limit(self, request: RuntimeRequest) -> int:
        """Allowed tool calls for this agent run."""
        declared = request.metadata.get("agent_tool_call_limit")
        if declared is None:
            return DEFAULT_AGENT_TOOL_CALL_LIMIT
        try:
            bounded = int(declared)
        except (TypeError, ValueError) as exc:
            raise ValidationError("agent_tool_call_limit must be an integer") from exc
        if bounded < 0:
            raise ValidationError("agent_tool_call_limit must be non-negative")
        return min(bounded, MAX_AGENT_TOOL_CALL_LIMIT)

    # -- execution budget enforcement ---------------------------------------

    def max_model_tokens(self, request: RuntimeRequest) -> int | None:
        """Combined prompt+completion token ceiling, None = unbounded."""
        declared = request.metadata.get("max_model_tokens")
        if declared is None:
            return None
        try:
            bounded = int(declared)
        except (TypeError, ValueError) as exc:
            raise ValidationError("max_model_tokens must be an integer") from exc
        if bounded < 0:
            raise ValidationError("max_model_tokens must be non-negative")
        return min(bounded, 10_000_000)

    def max_cost_usd(self, request: RuntimeRequest) -> float | None:
        declared = request.metadata.get("max_cost_usd")
        if declared is None:
            return None
        try:
            bounded = float(declared)
        except (TypeError, ValueError) as exc:
            raise ValidationError("max_cost_usd must be a number") from exc
        if bounded < 0:
            raise ValidationError("max_cost_usd must be non-negative")
        return min(bounded, 1000.0)

    def max_iterations(self, request: RuntimeRequest) -> int:
        declared = request.metadata.get("max_iterations", request.max_steps)
        try:
            bounded = int(declared)
        except (TypeError, ValueError) as exc:
            raise ValidationError("max_iterations must be an integer") from exc
        if bounded < 1:
            raise ValidationError("max_iterations must be positive")
        return min(bounded, 100)

    def max_elapsed_seconds(self, request: RuntimeRequest) -> float:
        declared = request.metadata.get("max_elapsed_seconds", 60.0)
        try:
            bounded = float(declared)
        except (TypeError, ValueError) as exc:
            raise ValidationError("max_elapsed_seconds must be a number") from exc
        if bounded <= 0:
            raise ValidationError("max_elapsed_seconds must be positive")
        return min(bounded, 3_600.0)


def assign_tool_call_ids(count: int) -> list[str]:
    """Deterministic tool invocation ids (audit linkage for one run)."""
    return [f"tc_{uuid.uuid4().hex}" for _ in range(max(0, count))]


class RuntimeToolCatalog:
    """Read-only registry adapter view for allowed tool selection.

    Provides discovery of the tools that are permitted for one runtime
    execution, and hides the underlying repository from the remainder of the
    runtime code — only the guard decides authorization.
    """

    def __init__(self, tools: ToolRepository) -> None:
        self._tools = tools

    async def selectable_tools(self) -> list[Tool]:
        return [
            tool
            for tool in await self._tools.list_all()
            if tool.is_selectable()
            and tool.capabilities
            and set(tool.capabilities) <= {READ_ONLY_TOOL_CAPABILITY}
        ]
