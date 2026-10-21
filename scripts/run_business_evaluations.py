"""Phase 4 wave 3 blueprint evaluation runner.

Deterministic (default)::

    python scripts/run_business_evaluations.py [output.json]

Runs the offline scenario suite twice and writes a reproducible JSON report.

Live model (opt-in, real provider calls)::

    python scripts/run_business_evaluations.py --live [output.json]

Requires a root ``.env`` (gitignored) or exported environment variables:

- ``POLSIA_LIVE_BASE_URL``      e.g. https://generativelanguage.googleapis.com/v1beta/openai/
- ``POLSIA_LIVE_API_KEY_ENV``   NAME of the variable holding the key, e.g. GEMINI_API_KEY
- ``POLSIA_LIVE_MODEL``         exact model id served by that endpoint
- ``<API_KEY_ENV>``             the key itself (never logged or written)

The live arm is never simulated: without configuration it exits with a clear
message and performs no network call. Live results are not reproducible by
design (provider non-determinism) and are reported separately from the
deterministic CI arm.

Live model driven through OpenCode itself (test-only, no API key)::

    python scripts/run_business_evaluations.py --opencode-session [output.json]

OpenCode's free-tier inference rejects direct gateway calls (``403
FreeTierError``), so this mode shells out to the installed OpenCode CLI and
uses its stateless ``/api/session/{id}/generate`` route. The model defaults to
``deepseek-v4.1-flash-free``; override it with ``--model=<id>``. This validates
the prompts and the domain validation path against a real model, but it does
**not** exercise the production OpenAI-compatible adapter (``--live`` does).
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

#: Load the project-root .env before reading any provider configuration.
load_dotenv(ROOT / ".env")

from polsia.eval.business import (  # noqa: E402
    SCENARIOS,
    BlueprintEvaluationReport,
    evaluate_blueprints,
)


def _configured() -> tuple[str, str, str] | None:
    base_url = os.environ.get("POLSIA_LIVE_BASE_URL", "").strip()
    api_key_env = os.environ.get("POLSIA_LIVE_API_KEY_ENV", "").strip()
    model = os.environ.get("POLSIA_LIVE_MODEL", "").strip()
    key = os.environ.get(api_key_env, "").strip() if api_key_env else ""
    if not (base_url and api_key_env and model and key):
        return None
    return base_url, api_key_env, model


#: Per-call and per-scenario caps for the live arm. Reasoning models are slow
#: (tens of seconds per call) and billed reasoning tokens are not attributed to
#: completion_tokens by the OpenAI-compatible endpoint, so budgets stay generous
#: while still bounded.
LIVE_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("POLSIA_LIVE_TIMEOUT", "180"))


def _live_generator_factory() -> object:
    from polsia.adapters.business.model_backed import (
        BlueprintGenerationBudget,
        ModelBackedBlueprintGenerator,
    )
    from polsia.adapters.models import OpenAICompatibleConfig, OpenAICompatibleModelProvider

    configured = _configured()
    if configured is None:  # pragma: no cover - guarded by _run_live
        raise RuntimeError("live provider is not configured")
    base_url, api_key_env, model = configured
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleConfig(
            base_url=base_url,
            api_key_env=api_key_env,
            model=model,
            request_timeout_seconds=LIVE_REQUEST_TIMEOUT_SECONDS,
            max_retries=1,
        )
    )
    return ModelBackedBlueprintGenerator(
        provider,
        budget=BlueprintGenerationBudget(
            max_total_tokens=60_000,
            max_model_calls=12,
            max_elapsed_seconds=900.0,
            # Reasoning models may spend completion budget on hidden thinking,
            # so leave headroom beyond the deterministic default of 2,000.
            max_completion_tokens=4_096,
        ),
    )


def _opencode_generator_factory(model: str) -> object:
    """Fresh generator per scenario, backed by OpenCode's local generate route."""
    from opencode_session_model import OpenCodeSessionModel
    from polsia.adapters.business.model_backed import (
        BlueprintGenerationBudget,
        ModelBackedBlueprintGenerator,
    )

    provider = OpenCodeSessionModel(
        model=model,
        directory=str(ROOT),
        timeout_seconds=float(os.environ.get("POLSIA_OPENCODE_TIMEOUT", "180")),
    )
    return ModelBackedBlueprintGenerator(
        provider,
        budget=BlueprintGenerationBudget(
            max_total_tokens=60_000,
            max_model_calls=12,
            max_elapsed_seconds=900.0,
            max_completion_tokens=4_096,
        ),
    )


def _write(
    target: pathlib.Path,
    report: BlueprintEvaluationReport,
    *,
    mode: str,
    provider: dict[str, Any] | None = None,
) -> None:
    if provider is None and mode == "live":
        configured = _configured()
        if configured is not None:
            provider = {
                "base_url": configured[0],
                "api_key_env": configured[1],
                "model": configured[2],
                "api_key": "<redacted>",
            }
    if mode == "deterministic":
        deterministic_arm = "RUN - offline deterministic generator, CI-safe"
        model_arm = "NOT RUN in deterministic mode"
    elif mode == "live":
        deterministic_arm = "NOT RUN in --live mode"
        model_arm = (
            "LIVE MODEL TEST RUN - real provider calls through the OpenAI-compatible adapter"
        )
    elif mode == "opencode-session":
        deterministic_arm = "NOT RUN in --opencode-session mode"
        model_arm = (
            "LIVE MODEL TEST RUN - real model calls through OpenCode's local "
            "one-shot API (test-only transport; not the production provider path)"
        )
    else:  # pragma: no cover - defensive
        deterministic_arm = "NOT RUN"
        model_arm = "LIVE MODEL TEST NOT RUN - no provider configured"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "report": report.to_dict(),
        "provider": provider,
        "claims_disclaimers": {
            "deterministic_arm": deterministic_arm,
            "model_backed_arm": model_arm,
            "ground_truth": "deterministic predicates only; no model judge",
            "real_world_validation": ("none - market/pricing/financial claims remain hypotheses"),
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_deterministic(target: pathlib.Path) -> int:
    first = evaluate_blueprints()
    second = evaluate_blueprints()
    reproducible = (
        first.report_digest == second.report_digest
        and first.dataset_fingerprint == second.dataset_fingerprint
        and first.passed == second.passed
    )
    _write(target, first, mode="deterministic")
    # Append the reproducibility block without rewriting the shared writer.
    data = json.loads(target.read_text(encoding="utf-8"))
    data["reproducibility"] = {
        "checked": True,
        "same_report_digest": first.report_digest == second.report_digest,
        "same_dataset_fingerprint": first.dataset_fingerprint == second.dataset_fingerprint,
        "same_pass_count": first.passed == second.passed,
        "reproducible": reproducible,
    }
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"blueprint evaluation (deterministic): {first.passed}/{first.total} passed "
        f"(pass_rate={first.pass_rate}); report_digest={first.report_digest}; "
        f"reproducible={reproducible}"
    )
    _print_scenarios(first)
    print(f"wrote {target}")
    return 0 if first.passed == first.total and reproducible else 1


def _preflight(configured: tuple[str, str, str]) -> int:
    """One tiny real call before the suite; aborts fast when the endpoint is dead."""
    import asyncio
    import time

    from polsia.adapters.models import OpenAICompatibleConfig, OpenAICompatibleModelProvider
    from polsia.core.runtime.ports import ModelMessage, ModelRequest

    base_url, api_key_env, model = configured
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleConfig(
            base_url=base_url,
            api_key_env=api_key_env,
            model=model,
            request_timeout_seconds=LIVE_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
    )

    async def run() -> tuple[bool, str]:
        started = time.perf_counter()
        try:
            response = await provider.generate(
                ModelRequest(
                    messages=[ModelMessage(role="user", content="Reply with exactly: OK")],
                    max_tokens=256,
                )
            )
        except Exception as exc:  # noqa: BLE001 - preflight reports the real cause
            return False, f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        return True, (
            f"HTTP ok in {elapsed:.1f}s; prompt={response.usage.prompt_tokens} "
            f"completion={response.usage.completion_tokens} "
            f"content={response.content.strip()[:60]!r}"
        )

    ok, detail = asyncio.run(run())
    if not ok:
        print(f"LIVE EVALUATION ABORTED: provider preflight failed: {detail}")
        print(
            "Check POLSIA_LIVE_BASE_URL, the model id, the key, and network/proxy. "
            "Raise POLSIA_LIVE_TIMEOUT if the provider is simply slow. A 429 means "
            "the quota is exhausted (the 15-scenario suite needs 45+ calls)."
        )
        return 2
    print(f"preflight: {detail}")
    return 0


def _run_live(target: pathlib.Path, limit: int | None = None, offset: int = 0) -> int:
    configured = _configured()
    if configured is None:
        print(
            "LIVE EVALUATION NOT RUN: set POLSIA_LIVE_BASE_URL, "
            "POLSIA_LIVE_API_KEY_ENV, POLSIA_LIVE_MODEL and the key variable "
            "in the project-root .env (or the shell)."
        )
        return 2
    base_url, api_key_env, model = configured
    end = offset + limit if limit is not None else None
    scenarios = SCENARIOS[offset:end]
    print(
        f"live provider: model={model} base_url={base_url} "
        f"request_timeout={LIVE_REQUEST_TIMEOUT_SECONDS:.0f}s "
        f"scenarios={len(scenarios)} offset={offset}"
    )
    preflight = _preflight(configured)
    if preflight != 0:
        return preflight
    print("live calls are slow and rate-limited; one generator per scenario, failures reported")

    def _progress(result: Any) -> None:
        status = "PASS" if result.passed else "FAIL"
        failed = ", ".join(check.name for check in result.failed_checks)
        detail = f" error={result.error}" if result.error else ""
        print(
            f"  [{status}] {result.scenario} ({result.business_model or 'unknown'}) "
            f"{round(result.latency_ms / 1000.0, 1)}s {failed}{detail}",
            flush=True,
        )

    report = evaluate_blueprints(
        generator_factory=_live_generator_factory,
        scenarios=scenarios,
        on_scenario=_progress,
        generator_label=f"model_backed:{model}",
        model_arm=(
            f"RUN: live provider model={model} (provider timeouts/errors are recorded "
            "per scenario, never retried into a false pass)"
        ),
        notes=(
            "Live arm: real provider calls; results are not reproducible by design.",
            "Assertions are still deterministic predicates; no model judge.",
            "Reasoning tokens may be billed without being reported as completion tokens.",
        ),
    )
    _write(target, report, mode="live")
    print(
        f"blueprint evaluation (LIVE): {report.passed}/{report.total} passed "
        f"(pass_rate={report.pass_rate}); model={model}; "
        f"report_digest={report.report_digest} (not a reproducibility guarantee)"
    )
    print(f"wrote {target}")
    return 0 if report.passed == report.total else 1


def _opencode_preflight(model: str) -> int:
    """One tiny real call through the local OpenCode service."""
    import asyncio
    import time

    from opencode_session_model import OpenCodeSessionModel
    from polsia.core.runtime.ports import ModelMessage, ModelRequest

    provider = OpenCodeSessionModel(
        model=model,
        directory=str(ROOT),
        timeout_seconds=float(os.environ.get("POLSIA_OPENCODE_TIMEOUT", "180")),
    )

    async def run() -> tuple[bool, str]:
        started = time.perf_counter()
        try:
            response = await provider.generate(
                ModelRequest(messages=[ModelMessage(role="user", content="Reply with exactly: OK")])
            )
        except Exception as exc:  # noqa: BLE001 - preflight reports the real cause
            return False, f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        return True, (
            f"OpenCode session ok in {elapsed:.1f}s; content={response.content.strip()[:60]!r}"
        )

    ok, detail = asyncio.run(run())
    if not ok:
        print(f"OPENCODE EVALUATION ABORTED: preflight failed: {detail}")
        print(
            "Check that the OpenCode desktop app is running and the CLI is on PATH "
            "(or set OPENCODE_CLI), and that the model id is available."
        )
        return 2
    print(f"preflight: {detail}")
    return 0


def _run_opencode_session(
    target: pathlib.Path, limit: int | None, model: str, offset: int = 0
) -> int:
    end = offset + limit if limit is not None else None
    scenarios = SCENARIOS[offset:end]
    print(
        f"opencode session model: model={model} transport=local one-shot API "
        f"scenarios={len(scenarios)} offset={offset}"
    )
    preflight = _opencode_preflight(model)
    if preflight != 0:
        return preflight
    print("model calls run through the installed OpenCode CLI; failures are recorded per scenario")

    def _progress(result: Any) -> None:
        status = "PASS" if result.passed else "FAIL"
        failed = ", ".join(check.name for check in result.failed_checks)
        detail = f" error={result.error}" if result.error else ""
        print(
            f"  [{status}] {result.scenario} ({result.business_model or 'unknown'}) "
            f"{round(result.latency_ms / 1000.0, 1)}s {failed}{detail}",
            flush=True,
        )

    report = evaluate_blueprints(
        generator_factory=lambda: _opencode_generator_factory(model),
        scenarios=scenarios,
        on_scenario=_progress,
        generator_label=f"model_backed:opencode-session:{model}",
        model_arm=(
            f"RUN: local OpenCode one-shot model={model} (test-only transport; "
            "provider errors are recorded per scenario, never retried into a false pass)"
        ),
        notes=(
            "Model arm uses OpenCode's local /api/session/{id}/generate route; "
            "not the production OpenAI-compatible adapter.",
            "The OpenCode generate route does not expose token usage; usage is estimated.",
            "Assertions are still deterministic predicates; no model judge.",
        ),
    )
    _write(
        target,
        report,
        mode="opencode-session",
        provider={
            "transport": "opencode-session",
            "base_url": "local OpenCode API",
            "model": model,
            "api_key": "<not required: local OpenCode session>",
        },
    )
    print(
        f"blueprint evaluation (OPENCODE SESSION): {report.passed}/{report.total} passed "
        f"(pass_rate={report.pass_rate}); model={model}; "
        f"report_digest={report.report_digest} (not a reproducibility guarantee)"
    )
    print(f"wrote {target}")
    return 0 if report.passed == report.total else 1


def _print_scenarios(report: BlueprintEvaluationReport) -> None:
    for scenario in report.scenarios:
        status = "PASS" if scenario.passed else "FAIL"
        failed = ", ".join(check.name for check in scenario.failed_checks)
        print(f"  [{status}] {scenario.scenario} ({scenario.business_model}) {failed}")


def main(argv: list[str]) -> int:
    from opencode_session_model import DEFAULT_MODEL

    flags = argv[1:]
    args = [arg for arg in flags if not arg.startswith("--")]
    live = "--live" in flags
    opencode_session = "--opencode-session" in flags
    if live and opencode_session:
        print("ERROR: choose either --live or --opencode-session, not both")
        return 3
    limit: int | None = None
    offset = 0
    model = DEFAULT_MODEL
    for arg in flags:
        if arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg.startswith("--offset="):
            offset = int(arg.split("=", 1)[1])
        elif arg.startswith("--model="):
            model = arg.split("=", 1)[1].strip()
    if offset < 0 or (limit is not None and limit < 1):
        print("ERROR: --offset must be >= 0 and --limit must be >= 1")
        return 3
    if args:
        target = pathlib.Path(args[0])
    elif opencode_session:
        target = pathlib.Path("business_evaluation_opencode.json")
    else:
        target = pathlib.Path(
            "business_evaluation_live.json" if live else "business_evaluation.json"
        )
    # Guard against a stale full-suite assumption: the dataset is fixed.
    if len(SCENARIOS) != 15:  # pragma: no cover - dataset drift is a test failure
        print("ERROR: scenario dataset drifted from the 15 labeled scenarios")
        return 3
    if opencode_session:
        return _run_opencode_session(target, limit, model, offset)
    return _run_live(target, limit, offset) if live else _run_deterministic(target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
