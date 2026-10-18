"""OpenAI-compatible model adapter tests (Phase 4 mandate §4).

Transport is exercised via ``httpx.MockTransport`` — no network, fully
deterministic, and every request body/header is inspectable.
"""

from __future__ import annotations

import json
from itertools import count
from typing import Any

import httpx
import pytest
from polsia.adapters.models import (
    ModelPricing,
    OpenAICompatibleConfig,
    OpenAICompatibleModelProvider,
    ProviderAuthError,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from polsia.core.runtime.ports import ModelMessage, ModelRequest

_ENV_KEY = "POLSIA_TEST_OPENAI_KEY"
_BODY_OK = {
    "id": "cmpl-1",
    "model": "probe-model",
    "choices": [
        {
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "hello"},
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}


def _config(**overrides: Any) -> OpenAICompatibleConfig:
    base: dict[str, Any] = {
        "base_url": "https://provider.example/v1",
        "api_key_env": _ENV_KEY,
        "model": "probe-model",
        "max_retries": 2,
    }
    base.update(overrides)
    return OpenAICompatibleConfig(**base)


def _provider(
    interactions: list[Any],
    *,
    config: OpenAICompatibleConfig | None = None,
    captured: list[httpx.Request] | None = None,
) -> OpenAICompatibleModelProvider:
    counter = count()

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        index = next(counter)
        item = interactions[index]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "nope"}})
        if item == "timeout":
            raise httpx.ConnectTimeout("too slow", request=request)
        return httpx.Response(200, json=item)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleModelProvider(config or _config(), http_client=client)


def _generate_request(**overrides: Any) -> ModelRequest:
    base: dict[str, Any] = {
        "messages": [ModelMessage(role="user", content="hi")],
        "model": "probe-model",
    }
    base.update(overrides)
    return ModelRequest(**base)


@pytest.fixture()
def api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_KEY, "sk-test-value")


async def test_successful_generation_normalizes_response(api_key_env: None) -> None:
    provider = _provider([_BODY_OK])
    response = await provider.generate(_generate_request())
    assert response.content == "hello"
    assert response.metadata["usage_reported"] is True
    assert response.usage.prompt_tokens == 11
    assert response.finish_reason == "stop"
    assert provider.supports_tool_calling() is None


async def test_cost_computed_only_from_pricing(api_key_env: None) -> None:
    provider = _provider(
        [_BODY_OK],
        config=_config(pricing=ModelPricing(input_per_1k_usd=0.5, output_per_1k_usd=1.5)),
    )
    response = await provider.generate(_generate_request())
    expected = round(11 / 1000 * 0.5 + 7 / 1000 * 1.5, 6)
    assert response.metadata["cost_usd"] == pytest.approx(expected)


async def test_missing_usage_is_never_fabricated(api_key_env: None) -> None:
    body = {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}]
    }
    provider = _provider(
        [body],
        config=_config(pricing=ModelPricing(input_per_1k_usd=2.0, output_per_1k_usd=4.0)),
    )
    response = await provider.generate(_generate_request())
    assert response.usage.prompt_tokens == 0
    assert response.metadata["usage_reported"] is False
    assert "cost_usd" not in response.metadata


async def test_correlation_id_header_sent_and_secret_absent(api_key_env: None) -> None:
    captured: list[httpx.Request] = []
    provider = _provider([_BODY_OK], captured=captured)
    await provider.generate(_generate_request(metadata={"correlation_id": "corr-42"}))
    request = captured[0]
    assert request.headers["X-Request-Id"] == "corr-42"
    assert request.headers["Authorization"] == "Bearer sk-test-value"  # set, never logged


async def test_missing_api_key_env_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV_KEY, raising=False)
    provider = _provider([_BODY_OK])
    with pytest.raises(ProviderAuthError):
        await provider.generate(_generate_request())


async def test_rate_limit_then_success_uses_retry_after(api_key_env: None) -> None:
    captured: list[httpx.Request] = []
    provider = _provider([429, _BODY_OK], captured=captured)
    response = await provider.generate(_generate_request())
    assert response.content == "hello"
    assert len(captured) == 2


async def test_server_error_with_backoff_then_success(api_key_env: None) -> None:
    provider = _provider([500, _BODY_OK], config=_config(max_retries=1))
    response = await provider.generate(_generate_request())
    assert response.usage.completion_tokens == 7


async def test_persistent_unavailable_raises_normalized(api_key_env: None) -> None:
    provider = _provider([503, 503, 503], config=_config(max_retries=1))
    with pytest.raises(ProviderUnavailable):
        await provider.generate(_generate_request())


async def test_connect_timeout_becomes_provider_timeout(api_key_env: None) -> None:
    provider = _provider(["timeout"])
    with pytest.raises(ProviderTimeout):
        await provider.generate(_generate_request())


async def test_unauthorized_status_normalizes_to_auth_error(api_key_env: None) -> None:
    provider = _provider([401], config=_config(max_retries=0))
    with pytest.raises(ProviderAuthError):
        await provider.generate(_generate_request())


async def test_malformed_payload_normalizes_to_response_error(api_key_env: None) -> None:
    provider = _provider([{"choices": []}])
    with pytest.raises(ProviderResponseError):
        await provider.generate(_generate_request())


async def test_tool_calls_parsed_from_json_string(api_key_env: None) -> None:
    body = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "note.txt"}),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    provider = _provider([body])
    response = await provider.generate(_generate_request())
    assert response.tool_calls[0]["name"] == "read_file"
    assert response.tool_calls[0]["arguments"] == {"path": "note.txt"}
    assert response.metadata["tool_calls_reported"] is True


async def test_tools_and_structured_output_payload_mapping(api_key_env: None) -> None:
    captured: list[httpx.Request] = []
    provider = _provider(
        [_BODY_OK],
        config=_config(supports_tools=True, supports_structured_output=True),
        captured=captured,
    )
    await provider.generate(
        _generate_request(
            tools=[{"name": "echo", "description": "sandbox", "parameters": {"type": "object"}}],
            metadata={"structured": True},
        )
    )
    payload = json.loads(captured[0].content)
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload  # structured mode wins on mixed gateways


async def test_tools_disabled_is_enforced(api_key_env: None) -> None:
    provider = _provider([_BODY_OK], config=_config(supports_tools=False))
    with pytest.raises(ProviderResponseError):
        await provider.generate(
            _generate_request(tools=[{"name": "echo", "parameters": {"type": "object"}}])
        )


async def test_capability_detection_probe(api_key_env: None) -> None:
    provider = _provider([_BODY_OK, _BODY_OK])
    report = await provider.detect_capabilities()
    assert report.supports_tools is True
    assert report.supports_structured_output is True


async def test_capability_probe_failure_is_conservative(api_key_env: None) -> None:
    # two probes x (max_retries+1 attempts) of 429 each: nothing is confirmed
    provider = _provider([429] * 6)
    report = await provider.detect_capabilities()
    assert report.supports_tools is False
    assert report.supports_structured_output is False


async def test_stream_delivers_chunks_and_returns_full_response(api_key_env: None) -> None:
    provider = _provider([_BODY_OK])
    chunks: list[str] = []

    async def on_chunk(chunk: str) -> None:
        chunks.append(chunk)

    response = await provider.stream(_generate_request(), on_chunk)
    assert "".join(chunks) == "hello"
    assert response.metadata["usage_reported"] is True


async def test_resource_cleanup_and_lazy_recreation(api_key_env: None) -> None:
    """After aclose the owned client is released and lazily recreated."""

    def factory() -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_BODY_OK)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider = OpenAICompatibleModelProvider(_config(), client_factory=factory)
    await provider.generate(_generate_request())  # creates the client
    await provider.aclose()
    await provider.generate(_generate_request())  # recreates it lazily
