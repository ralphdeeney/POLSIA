"""Runtime request/result dataclasses and the RuntimePort.

Provider-neutral by construction: ``runtime``/``model`` are free-form labels,
tool permissions are passed in as data (already resolved by the
PermissionEngine), and results carry generic usage/artifact/tool-call info.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from polsia.core.shared.ids import AgentId, CompanyId, TaskId


class RuntimeStatus(enum.StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class ToolCallRecord:
    """A single tool invocation made during an execution (audit trail)."""

    tool: str  # free-form reference, e.g. "web_search@1.0.0"
    action: str | None = None
    input: dict[str, Any] = dataclasses.field(default_factory=dict)
    output: dict[str, Any] | None = None
    ok: bool = True


@dataclasses.dataclass(frozen=True)
class TokenUsage:
    """Generic token accounting; not tied to any provider's API."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclasses.dataclass(frozen=True)
class RuntimeRequest:
    """Everything a runtime needs to attempt a task.

    ``tool_permissions`` is the *resolved* permission decision set (the
    PermissionEngine's output) — a runtime must not widen its own authority.
    """

    company_id: CompanyId
    task_id: TaskId
    agent_id: AgentId | None = None
    skill_id: str | None = None
    runtime: str | None = None
    model: str | None = None
    input: dict[str, Any] = dataclasses.field(default_factory=dict)
    context: dict[str, Any] = dataclasses.field(default_factory=dict)
    tool_permissions: dict[str, str] = dataclasses.field(default_factory=dict)
    system_instructions: str | None = None
    max_steps: int = 10
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class RuntimeResult:
    """The outcome of one runtime attempt."""

    status: RuntimeStatus
    output: dict[str, Any] = dataclasses.field(default_factory=dict)
    artifacts: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    tool_calls: list[ToolCallRecord] = dataclasses.field(default_factory=list)
    token_usage: TokenUsage = dataclasses.field(default_factory=TokenUsage)
    duration_seconds: float = 0.0
    error: str | None = None
    model: str | None = None
    runtime: str | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    @staticmethod
    def completed(
        output: dict[str, Any] | None = None,
        *,
        runtime: str | None = None,
        model: str | None = None,
        tool_calls: list[ToolCallRecord] | None = None,
        token_usage: TokenUsage | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        duration_seconds: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        return RuntimeResult(
            status=RuntimeStatus.COMPLETED,
            output=dict(output or {}),
            artifacts=list(artifacts or []),
            tool_calls=list(tool_calls or []),
            token_usage=token_usage or TokenUsage(),
            duration_seconds=duration_seconds,
            runtime=runtime,
            model=model,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def failed(
        error: str,
        *,
        runtime: str | None = None,
        model: str | None = None,
        duration_seconds: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        return RuntimeResult(
            status=RuntimeStatus.FAILED,
            error=error,
            runtime=runtime,
            model=model,
            duration_seconds=duration_seconds,
            metadata=dict(metadata or {}),
        )

    def is_success(self) -> bool:
        return self.status is RuntimeStatus.COMPLETED


class RuntimePort(Protocol):
    """Port for executing an agent/skill task.

    Implementations: MockRuntime (Phase 2), and later adapter-driven runtimes
    (custom Python runtime, hosted agent runtimes, provider harnesses).
    The core depends only on this Protocol.
    """

    name: str

    async def execute(self, request: RuntimeRequest) -> RuntimeResult: ...


# -- Model provider abstraction -------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ModelMessage:
    role: str  # "system" | "user" | "assistant" | "tool" — free-form, unvalidated set
    content: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ModelRequest:
    """Provider-neutral generation request."""

    messages: list[ModelMessage]
    model: str | None = None
    tools: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ModelResponse:
    """Provider-neutral generation response."""

    content: str
    model: str | None = None
    tool_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    usage: TokenUsage = dataclasses.field(default_factory=TokenUsage)
    finish_reason: str | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


ModelStreamHandler = Callable[[str], Awaitable[None]]


class ModelPort(Protocol):
    """Port for model providers. Enables later model routing without coupling
    the core to any vendor SDK."""

    async def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def stream(
        self, request: ModelRequest, on_chunk: ModelStreamHandler
    ) -> ModelResponse: ...
