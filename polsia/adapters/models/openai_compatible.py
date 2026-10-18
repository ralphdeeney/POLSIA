"""OpenAI-compatible ModelPort adapter (Phase 4 mandate §4).

Works with any provider exposing a chat-completions-compatible HTTP API
(OpenAI, NVIDIA NIM, OpenRouter, 9router, vLLM, llama.cpp servers...), while
making no security or capability assumptions about them:

- capability flags are explicit configuration or detected via an opt-in probe;
  nothing is assumed about tool-calling or structured-output support;
- token usage is parsed from the provider payload only — when a provider omits
  ``usage`` the adapter reports zero tokens with an explicit
  ``usage_reported: False`` flag instead of fabricating numbers;
- cost is computed from configured pricing only; without configured pricing no
  cost value is ever invented;
- credentials are read from a named environment variable (secrets are never
  hardcoded, never logged); requests carry correlation ids;
- retries are bounded with capped exponential backoff honoring ``Retry-After``;
  per-attempt timeouts propagate as provider timeouts.

The adapter never widens authority: it is pure request/response plumbing —
tool authorization stays with the ToolGateway.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import random
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from polsia.adapters.models.errors import (
    ProviderAuthError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from polsia.core.runtime.ports import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_DEFAULT_MAX_RETRIES = 2
_BACKOFF_CAP_SECONDS = 8.0


@dataclasses.dataclass(frozen=True)
class ModelPricing:
    """Configured cost per 1k tokens; incomplete pricing disables accounting."""

    input_per_1k_usd: float | None = None
    output_per_1k_usd: float | None = None

    def cost_of(self, usage: TokenUsage) -> float | None:
        if self.input_per_1k_usd is None or self.output_per_1k_usd is None:
            return None
        return (
            usage.prompt_tokens / 1000.0 * self.input_per_1k_usd
            + usage.completion_tokens / 1000.0 * self.output_per_1k_usd
        )


@dataclasses.dataclass(frozen=True)
class OpenAICompatibleConfig:
    """All provider-specific knobs; none are hardcoded in the adapter."""

    base_url: str
    api_key_env: str  # NAME of the env var holding the key (secret-safe)
    model: str
    request_timeout_seconds: float = 60.0
    max_retries: int = _DEFAULT_MAX_RETRIES
    pricing: ModelPricing = dataclasses.field(default_factory=ModelPricing)
    supports_tools: bool | None = None  # None = unknown until configured/detected
    supports_structured_output: bool | None = None

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.strip().rstrip("/")
        return f"{base}/chat/completions"


@dataclasses.dataclass(frozen=True)
class ProviderCapabilityReport:
    """Result of the explicit, opt-in capability probe."""

    supports_tools: bool
    supports_structured_output: bool


class OpenAICompatibleModelProvider:
    """A real, provider-neutral :class:`ModelPort` implementation."""

    name = "openai_compatible"

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        if not config.base_url.strip() or not config.model.strip():
            raise ValueError("base_url and model must not be empty")
        self._config = config
        self._client = http_client
        # Clients created by the adapter (factory) are owned and closed by it;
        # injected clients are managed by the caller.
        self._client_factory: Callable[[], httpx.AsyncClient] = client_factory or (
            lambda: httpx.AsyncClient(timeout=httpx.Timeout(config.request_timeout_seconds))
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    # -- capability handling ---------------------------------------------------

    def supports_tool_calling(self) -> bool | None:
        return self._config.supports_tools

    def supports_structured_outputs(self) -> bool | None:
        return self._config.supports_structured_output

    async def detect_capabilities(self) -> ProviderCapabilityReport:
        """Explicit opt-in probe (never at import time or API startup).

        A capability counts as supported only when the probe provably succeeds;
        ambiguous failures are treated as unsupported (conservative default).
        """
        model = self._config.model
        tools_ok = self._config.supports_tools
        structured_ok = self._config.supports_structured_output
        if tools_ok is None:
            tools_ok = await self._probe(model, {"tools": [self._dummy_tool()]})
        if structured_ok is None:
            structured_ok = await self._probe(model, {"response_format": {"type": "json_object"}})
        return ProviderCapabilityReport(
            supports_tools=bool(tools_ok),
            supports_structured_output=bool(structured_ok),
        )

    async def _probe(self, model: str, extra: dict[str, Any]) -> bool:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "capability probe"}],
            "max_tokens": 1,
            **extra,
        }
        try:
            await self._post_chat(payload, correlation_id=f"probe-{model}")
        except (
            ProviderResponseError,
            ProviderRateLimited,
            ProviderUnavailable,
            ProviderTimeout,
            ProviderAuthError,
        ):
            return False
        return True

    @staticmethod
    def _dummy_tool() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "noop",
                "description": "no-op capability probe",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    # -- ModelPort -------------------------------------------------------------

    async def generate(self, request: ModelRequest) -> ModelResponse:
        body = await self._post_chat(
            self._build_payload(request),
            correlation_id=self._correlation_id(request),
        )
        return self._normalize(body)

    async def stream(self, request: ModelRequest, on_chunk: Any) -> ModelResponse:
        """Streaming: content is surfaced through ``on_chunk``; the full
        normalized response is still returned for accounting."""
        response = await self.generate(request)
        step = 16
        for index in range(0, len(response.content), step):
            await on_chunk(response.content[index : index + step])
            await asyncio.sleep(0)
        return response

    # -- payload ---------------------------------------------------------------

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model or self._config.model,
            "messages": [self._map_message(m) for m in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            if self._config.supports_tools is False:
                raise ProviderResponseError(
                    "tools requested but tool calling is disabled for this provider"
                )
            payload["tools"] = [
                {"type": "function", "function": tool} for tool in self._map_tools(request.tools)
            ]
        if request.metadata.get("structured"):
            if self._config.supports_structured_output is False:
                raise ProviderResponseError(
                    "structured output requested but disabled for this provider"
                )
            if self._config.supports_structured_output:
                payload["response_format"] = {"type": "json_object"}
                # Some gateway implementations reject response_format together
                # with tools; structured mode wins.
                payload.pop("tools", None)
        return payload

    @staticmethod
    def _map_message(message: ModelMessage) -> dict[str, Any]:
        role = message.role or "user"
        if role == "tool" and message.metadata.get("tool_call_id"):
            return {
                "role": "tool",
                "content": message.content,
                "tool_call_id": str(message.metadata["tool_call_id"]),
            }
        if role == "assistant":
            assistant: dict[str, Any] = {"role": "assistant", "content": message.content}
            tool_calls = message.metadata.get("tool_calls")
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            return assistant
        return {"role": role, "content": message.content}

    @staticmethod
    def _map_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mapped: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            mapped.append(
                {
                    "name": name,
                    "description": str(tool.get("description") or name),
                    "parameters": tool.get("parameters") or {"type": "object"},
                }
            )
        return mapped

    # -- transport --------------------------------------------------------------

    def _request_headers(self, correlation_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolve_api_key()}",
            "X-Request-Id": correlation_id,
        }

    def _resolve_api_key(self) -> str:
        env_name = self._config.api_key_env.strip()
        if not env_name:
            raise ProviderAuthError("api_key_env must name an environment variable")
        key = os.environ.get(env_name, "")
        if not key:
            raise ProviderAuthError(f"missing API key: set environment variable {env_name}")
        return key

    async def _post_chat(self, payload: dict[str, Any], *, correlation_id: str) -> dict[str, Any]:
        """One bounded POST with capped retries and normalized failures."""
        attempts = max(1, self._config.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            retry_after: str | None = None
            try:
                response = await self._http().post(
                    self._config.chat_completions_url,
                    json=payload,
                    headers=self._request_headers(correlation_id),
                )
            except httpx.TimeoutException as exc:
                # Per-attempt timeout: retries of a hung provider just hang.
                raise ProviderTimeout(
                    f"provider timed out after {self._config.request_timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                status = response.status_code
                retry_after = response.headers.get("Retry-After")
                if status < 400:
                    log.info(
                        "model_provider_ok",
                        extra={
                            "model": payload.get("model"),
                            "status": status,
                            "correlation_id": correlation_id,
                            "attempt": attempt + 1,
                        },
                    )
                    body: dict[str, Any] = response.json()
                    return body
                if status not in _RETRYABLE_STATUS:
                    raise self._normalized_error(status, correlation_id)
                last_error = self._normalized_error(status, correlation_id)
            if attempt + 1 < attempts:
                delay = self._backoff_delay(attempt, retry_after)
                # Secret-safe: only allowed metadata fields are ever logged.
                log.warning(
                    "model_provider_retry",
                    extra={
                        "model": payload.get("model"),
                        "correlation_id": correlation_id,
                        "attempt": attempt + 1,
                        "delay_s": round(delay, 3),
                    },
                )
                await asyncio.sleep(delay)
        raise ProviderUnavailable(f"provider unavailable after {attempts} attempts: {last_error}")

    @staticmethod
    def _backoff_delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(_BACKOFF_CAP_SECONDS, max(0.0, float(retry_after)))
            except ValueError:
                pass
        jitter = random.uniform(0.0, 0.05 if attempt == 0 else 0.1)
        return min(_BACKOFF_CAP_SECONDS, 0.25 * float(2**attempt) + jitter)

    def _normalized_error(self, status: int, correlation_id: str) -> Exception:
        message = (
            f"provider error status={status} model={self._config.model} "
            f"correlation_id={correlation_id}"
        )
        if status == 429:
            return ProviderRateLimited(message)
        if 500 <= status < 600:
            return ProviderUnavailable(message)
        if status in (401, 403):
            return ProviderAuthError(message)
        return ProviderResponseError(message)

    # -- response normalization --------------------------------------------------

    def _normalize(self, body: dict[str, Any]) -> ModelResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseError("provider payload has no choices")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        if not isinstance(message, dict):
            raise ProviderResponseError("provider payload has no message")
        raw_usage = body.get("usage") if isinstance(body.get("usage"), dict) else None
        if raw_usage is None:
            usage = TokenUsage()
            usage_reported = False
        else:
            usage = TokenUsage(
                prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
                completion_tokens=int(raw_usage.get("completion_tokens") or 0),
            )
            usage_reported = True

        tool_calls = [
            {
                "id": str(raw.get("id") or ""),
                "name": str((raw.get("function") or {}).get("name") or ""),
                "arguments": self._parse_arguments((raw.get("function") or {}).get("arguments")),
            }
            for raw in (message.get("tool_calls") or [])
            if isinstance(raw, dict)
        ]

        metadata: dict[str, Any] = {"usage_reported": usage_reported}
        # Cost requires provider-reported usage; without it nothing is
        # invented (not even a zero-token-derived cost).
        if usage_reported:
            cost = self._config.pricing.cost_of(usage)
            if cost is not None:
                metadata["cost_usd"] = round(cost, 6)
        if tool_calls:
            metadata["tool_calls_reported"] = True

        model_value = body.get("model")
        return ModelResponse(
            content=str(message.get("content") or ""),
            model=model_value if isinstance(model_value, str) else None,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=(
                str(first["finish_reason"]) if first.get("finish_reason") is not None else None
            ),
            metadata=metadata,
        )

    @staticmethod
    def _parse_arguments(raw: Any) -> dict[str, Any]:
        """Provider tool arguments arrive as a JSON string or dict; normalize."""
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {"_raw": raw[:2000]}
            return parsed if isinstance(parsed, dict) else {"_raw": raw[:2000]}
        return {}

    def _correlation_id(self, request: ModelRequest) -> str:
        explicit = request.metadata.get("correlation_id")
        if explicit:
            return str(explicit)
        return f"req-{uuid.uuid4().hex[:12]}"
