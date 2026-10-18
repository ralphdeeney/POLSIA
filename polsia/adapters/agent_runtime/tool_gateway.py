"""Strict tool gateway for the native agent runtime (Phase 4 mandate §6).

The gateway is the only path from a runtime tool decision to real tool
execution. Every call is checked in executable code by the
:class:`~polsia.adapters.agent_runtime.guard.AgentRuntimeSafetyBudget` and only
``read_only_file_access`` tools can ever be registered for the runtime. Shell,
filesystem-write, outbound-network, payments, deployment and public messaging
capabilities are structurally unreachable, not prompt-negotiated.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable

from polsia.adapters.agent_runtime.guard import (
    READ_ONLY_TOOL_CAPABILITY,
    UNSAFE_TOOL_CAPABILITIES,
    AgentRuntimeSafetyBudget,
    StrictToolCapabilityPolicy,
)
from polsia.core.permissions.errors import PermissionDenied
from polsia.core.shared.errors import NotFoundError, ValidationError

log = logging.getLogger(__name__)

ToolHandler = Callable[..., Awaitable[dict[str, object]]]


@dataclasses.dataclass(frozen=True)
class ToolSpecification:
    """A whitelisted runtime tool binding (registry-conformant side only)."""

    name: str
    version: str
    capabilities: tuple[str, ...]
    handler: ToolHandler
    required_permission_action: str
    parameters: dict[str, object] = dataclasses.field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def allowed_for_agent(self, agent_capability: str | None) -> bool:
        return agent_capability == READ_ONLY_TOOL_CAPABILITY and set(self.capabilities) <= {
            READ_ONLY_TOOL_CAPABILITY
        }


@dataclasses.dataclass(frozen=True)
class ToolInvocationRequest:
    """Trusted-context tool invocation (model-controlled ids never override
    company/task context)."""

    tool_name: str
    tool_version: str | None
    arguments: dict[str, object]
    company_id: str
    task_id: str
    agent_capability: str | None
    trace_id: str
    tool_call_id: str


@dataclasses.dataclass(frozen=True)
class ToolExecutionResult:
    """Sanitized tool output (no secrets; error stays a message)."""

    ok: bool
    output: dict[str, object] = dataclasses.field(default_factory=dict)
    error: str | None = None


class UnsafeToolError(PermissionDenied):
    """Raised for any attempt to register or execute an unsafe tool."""


def _refuse_unsafe_capabilities(name: str, capabilities: tuple[str, ...]) -> None:
    offending = sorted(set(capabilities) & UNSAFE_TOOL_CAPABILITIES)
    if offending:
        raise UnsafeToolError(f"tool {name} declares unsafe capabilities {offending}")


class ToolGateway:
    """The narrow execution boundary for runtime tool calls."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpecification] = {}
        self._guard = AgentRuntimeSafetyBudget(StrictToolCapabilityPolicy())

    def register_readonly_tool(
        self,
        *,
        name: str,
        version: str,
        handler: ToolHandler,
        required_permission_action: str,
        description: str = "",
        parameters: dict[str, object] | None = None,
    ) -> ToolSpecification:
        capabilities: tuple[str, ...] = (READ_ONLY_TOOL_CAPABILITY,)
        _refuse_unsafe_capabilities(name, capabilities)
        if not name.strip() or not version.strip():
            raise ValidationError("tool name and version must not be empty")
        spec = ToolSpecification(
            name=name.strip(),
            version=version.strip(),
            capabilities=capabilities,
            handler=handler,
            required_permission_action=required_permission_action,
            parameters=dict(parameters or {}),
        )
        if spec.key in self._tools:
            raise ValidationError(f"tool {spec.key} is already registered")
        self._tools[spec.key] = spec
        return spec

    def registered(self) -> list[ToolSpecification]:
        return sorted(self._tools.values(), key=lambda spec: spec.key)

    def get(self, tool_name: str, tool_version: str | None = None) -> ToolSpecification:
        if tool_version is not None:
            spec = self._tools.get(f"{tool_name}@{tool_version}")
            if spec is None:
                raise NotFoundError(f"tool {tool_name}@{tool_version} is not registered")
            return spec
        candidates = [spec for key, spec in self._tools.items() if key.startswith(f"{tool_name}@")]
        if not candidates:
            raise NotFoundError(f"tool {tool_name} is not registered")
        return candidates[0]

    def assert_tool_allowed(self, request: ToolInvocationRequest) -> ToolSpecification:
        spec = self.get(request.tool_name, request.tool_version)
        if not spec.allowed_for_agent(request.agent_capability):
            raise PermissionDenied(
                f"tool {spec.key} is not permitted for agent capability "
                f"{request.agent_capability!r}"
            )
        return spec

    async def execute(self, request: ToolInvocationRequest) -> ToolExecutionResult:
        """Authorize then execute, or return a sanitized failure result."""
        try:
            spec = self.assert_tool_allowed(request)
        except (PermissionDenied, NotFoundError) as exc:
            log.info(
                "tool_authorization_denied",
                extra={"tool": request.tool_name, "trace_id": request.trace_id},
            )
            return ToolExecutionResult(ok=False, output={}, error=str(exc))
        try:
            output = await spec.handler(**dict(request.arguments))
        except Exception as exc:  # provider/tool failures are expected, not fatal
            log.warning(
                "tool_execution_failed",
                extra={
                    "tool": spec.key,
                    "trace_id": request.trace_id,
                    "error": type(exc).__name__,
                },
            )
            return ToolExecutionResult(ok=False, output={}, error=str(exc))
        return ToolExecutionResult(ok=True, output=dict(output), error=None)


async def execute_tool_once(
    gateway: ToolGateway,
    request: ToolInvocationRequest,
) -> ToolExecutionResult:
    """One-shot invocation used by tests and the evaluation harness."""
    return await gateway.execute(request)
