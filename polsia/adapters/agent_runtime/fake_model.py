"""Deterministic fake LLM used by runtime contract, E2E and evaluation tests.

A scripted provider: each call returns the next scripted response, so tool
calls, malformed output, provider failures and token usage are fully
controllable without any network access. It is also the baseline "LLM-assisted
routing" stand-in for evaluation, and doubles as the native runtime's
verification harness for a model claiming completion without evidence.
"""

from __future__ import annotations

import dataclasses
import enum
import itertools
from typing import Any

from polsia.core.runtime.ports import ModelRequest, ModelResponse, TokenUsage


class ScriptedModelBehavior(enum.StrEnum):
    """Deterministic behaviors the fake provider can be loaded with."""

    COMPLETE = "complete"
    TOOL_CALL = "tool_call"
    MALFORMED = "malformed"
    PROVIDER_FAILURE = "provider_failure"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TEXT_ONLY = "text_only"


@dataclasses.dataclass(frozen=True)
class ScriptedResponse:
    """One step of a scripted provider session."""

    behavior: ScriptedModelBehavior
    content: str = ""
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = dataclasses.field(default_factory=dict)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = "stop"


class ScriptedModelProvider:
    """A provider-neutral fake (ModelPort conformant) with a fixed script."""

    name = "scripted"

    def __init__(
        self,
        script: list[ScriptedResponse],
        *,
        usage_when_absent: bool = False,
    ) -> None:
        self._script = list(script)
        self._calls = itertools.count()
        self.requests: list[ModelRequest] = []
        self.usage_when_absent = usage_when_absent

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = next(self._calls)
        if index >= len(self._script):
            step = self._script[-1]
        else:
            step = self._script[index]
        if step.behavior is ScriptedModelBehavior.PROVIDER_FAILURE:
            raise RuntimeError("simulated provider failure")
        if step.behavior is ScriptedModelBehavior.RATE_LIMITED:
            raise TimeoutError("simulated rate limit")
        if step.behavior is ScriptedModelBehavior.TIMEOUT:
            raise TimeoutError("simulated provider timeout")
        usage = TokenUsage(
            prompt_tokens=0 if step.prompt_tokens is None else step.prompt_tokens,
            completion_tokens=0 if step.completion_tokens is None else step.completion_tokens,
        )
        if step.behavior is ScriptedModelBehavior.MALFORMED:
            return ModelResponse(
                content=step.content,
                finish_reason="length",
                usage=usage,
                metadata={"scripted": True},
            )
        if step.behavior is ScriptedModelBehavior.TOOL_CALL:
            return ModelResponse(
                content=step.content,
                tool_calls=[
                    {
                        "id": f"call_{index}",
                        "name": step.tool_name or "echo",
                        "arguments": dict(step.tool_arguments),
                    }
                ],
                finish_reason="tool_calls",
                usage=usage,
                metadata={"scripted": True},
            )
        if step.behavior is ScriptedModelBehavior.COMPLETE:
            return ModelResponse(
                content=step.content or "__FINAL__",
                finish_reason=step.finish_reason or "stop",
                usage=usage,
                metadata={"scripted": True},
            )
        return ModelResponse(
            content=step.content,
            finish_reason=step.finish_reason or "stop",
            usage=usage,
            metadata={"scripted": True},
        )

    async def stream(self, request: ModelRequest, on_chunk: Any) -> ModelResponse:
        response = await self.generate(request)
        for chunk in (response.content[i : i + 7] for i in range(0, len(response.content), 7)):
            await on_chunk(chunk)
        return response

    # -- evaluation introspection ------------------------------------------------

    @property
    def call_count(self) -> int:
        return next(self._calls)


def final_response(
    text: str = "task complete", *, tokens: tuple[int, int] = (0, 0)
) -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.COMPLETE,
        content=text,
        prompt_tokens=tokens[0],
        completion_tokens=tokens[1],
    )


def tool_call_response(
    tool_name: str, arguments: dict[str, Any], *, tokens: tuple[int, int] = (0, 0)
) -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.TOOL_CALL,
        tool_name=tool_name,
        tool_arguments=arguments,
        prompt_tokens=tokens[0],
        completion_tokens=tokens[1],
    )


def malformed_response() -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.MALFORMED,
        content="not-a-verifiable-final-answer",
    )


def _script_from_steps(steps: list[Any]) -> list[ScriptedResponse]:
    return list(steps)
