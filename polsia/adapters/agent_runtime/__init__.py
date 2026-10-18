"""Native agent runtime adapters (Phase 4).

Exports the bounded :class:`NativeAgentRuntime`, the strict tool gateway, the
versioned prompt composer, execution states/traces, the deterministic scripted
model provider used by tests/evaluations, and the read-only tool capability.
"""

from __future__ import annotations

from polsia.adapters.agent_runtime.fake_model import (
    ScriptedModelBehavior,
    ScriptedModelProvider,
    ScriptedResponse,
)
from polsia.adapters.agent_runtime.guard import (
    AgentActionInvocation,
    AgentRuntimeSafetyBudget,
    StrictToolCapabilityPolicy,
)
from polsia.adapters.agent_runtime.native import ExecutionBudget, NativeAgentRuntime
from polsia.adapters.agent_runtime.prompt_composer import (
    AgentPromptComposer,
    PromptContext,
    load_templates,
    render_skill_text,
    template_version,
)
from polsia.adapters.agent_runtime.state import (
    AgentExecutionTraceNode,
    AgentRuntimeState,
)
from polsia.adapters.agent_runtime.test_tools import register_default_readonly_tools
from polsia.adapters.agent_runtime.tool_gateway import (
    ToolExecutionResult,
    ToolGateway,
    ToolInvocationRequest,
    ToolSpecification,
    UnsafeToolError,
)

__all__ = [
    "AgentActionInvocation",
    "AgentExecutionTraceNode",
    "AgentPromptComposer",
    "AgentRuntimeSafetyBudget",
    "AgentRuntimeState",
    "ExecutionBudget",
    "NativeAgentRuntime",
    "PromptContext",
    "ScriptedModelBehavior",
    "ScriptedModelProvider",
    "ScriptedResponse",
    "StrictToolCapabilityPolicy",
    "ToolExecutionResult",
    "ToolGateway",
    "ToolInvocationRequest",
    "ToolSpecification",
    "UnsafeToolError",
    "load_templates",
    "register_default_readonly_tools",
    "render_skill_text",
    "template_version",
]
