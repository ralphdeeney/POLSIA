# ADR-0014: OpenAI-compatible model provider adapter

Status: accepted (Phase 4)

## Context

Phase 4 required a real `ModelPort` implementation without coupling the core to
a vendor SDK, while remaining usable against custom OpenAI-compatible gateways
(NVIDIA NIM, OpenRouter, 9router, vLLM, llama.cpp servers).

## Decision

`OpenAICompatibleModelProvider` (`polsia/adapters/models/openai_compatible.py`)
implements the `ModelPort` contract over `httpx.AsyncClient`:

- configuration (`OpenAICompatibleConfig`): configurable base URL, API key
  resolved from a named environment variable (never hardcoded, never logged),
  configurable model identifier, per-request timeout, bounded retries with
  capped exponential backoff honoring `Retry-After`;
- capability flags (`supports_tools`, `supports_structured_output`) are
  either explicit configuration or the output of an opt-in
  `detect_capabilities()` probe; ambiguous probe outcomes are treated as
  unsupported (conservative). No capability is assumed;
- token usage is parsed from the provider payload only; a missing `usage`
  block yields zero tokens with an explicit `usage_reported: False` metadata
  flag — usage is never fabricated, and cost is computed only from
  provider-reported usage plus configured pricing (`ModelPricing`);
- error normalization: 401/403 → ProviderAuthError, 429 →
  ProviderRateLimited, 5xx/connect → ProviderUnavailable, timeouts →
  ProviderTimeout, malformed payloads → ProviderResponseError; retries cover
  408/425/429/500/502/503/504 and connection errors;
- logging is secret-safe (model/status/correlation id only); requests carry
  `X-Request-Id` correlation ids;
- owned HTTP clients are released by `aclose()` and recreated lazily; injected
  clients remain caller-managed.

`NullModelProvider` and the deterministic `ScriptedModelProvider` remain
available for tests; no keys or model names are hardcoded anywhere.

## Limitations

- Streaming is HTTP-nonce: `stream()` currently delivers the collected
  completion through the chunk handler rather than consuming SSE deltas; real
  token-streaming is future work.
- The adapter validates payloads structurally; semantic validation of model
  content is the runtime's and gateway's responsibility.

## Verification

19 transport-level tests via `httpx.MockTransport`
(`tests/adapters/test_models_openai_compatible.py`): usage/cost honesty,
retry/backoff, error normalization, correlation headers, structured/tools
payload mapping, capability probe, cleanup.

LIVE SMOKE TEST: NOT RUN (no provider credentials in the execution
environment). The opt-in script `scripts/live_model_smoke.py` performs the
bounded harmless-read-only-tool run when credentials exist.
