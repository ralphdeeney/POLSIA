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
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import UTC, datetime

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


def _live_generator() -> object:
    from polsia.adapters.business.model_backed import (
        BlueprintGenerationBudget,
        ModelBackedBlueprintGenerator,
    )
    from polsia.adapters.models import OpenAICompatibleConfig, OpenAICompatibleModelProvider

    configured = _configured()
    if configured is None:
        raise SystemExit(
            "LIVE EVALUATION NOT RUN: set POLSIA_LIVE_BASE_URL, "
            "POLSIA_LIVE_API_KEY_ENV, POLSIA_LIVE_MODEL and the key variable "
            "in the project-root .env (or the shell)."
        )
    base_url, api_key_env, model = configured
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleConfig(
            base_url=base_url,
            api_key_env=api_key_env,
            model=model,
            request_timeout_seconds=90.0,
            max_retries=1,
        )
    )
    return ModelBackedBlueprintGenerator(
        provider,
        budget=BlueprintGenerationBudget(
            max_total_tokens=300_000,
            max_model_calls=200,
            max_elapsed_seconds=1_800.0,
        ),
    )


def _write(target: pathlib.Path, report: BlueprintEvaluationReport, *, mode: str) -> None:
    configured = _configured()
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "report": report.to_dict(),
        "provider": (
            {
                "base_url": configured[0],
                "api_key_env": configured[1],
                "model": configured[2],
                "api_key": "<redacted>",
            }
            if configured
            else None
        ),
        "claims_disclaimers": {
            "deterministic_arm": (
                "RUN - offline deterministic generator, CI-safe"
                if mode == "deterministic"
                else "NOT RUN in --live mode"
            ),
            "model_backed_arm": (
                "LIVE MODEL TEST RUN - real provider calls"
                if mode == "live"
                else "LIVE MODEL TEST NOT RUN - no provider configured"
            ),
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


def _run_live(target: pathlib.Path) -> int:
    configured = _configured()
    if configured is None:
        print(
            "LIVE EVALUATION NOT RUN: set POLSIA_LIVE_BASE_URL, "
            "POLSIA_LIVE_API_KEY_ENV, POLSIA_LIVE_MODEL and the key variable "
            "in the project-root .env (or the shell)."
        )
        return 2
    generator = _live_generator()
    report = evaluate_blueprints(generator)
    _write(target, report, mode="live")
    print(
        f"blueprint evaluation (LIVE): {report.passed}/{report.total} passed "
        f"(pass_rate={report.pass_rate}); model={configured[2]}; "
        f"report_digest={report.report_digest} (not a reproducibility guarantee)"
    )
    _print_scenarios(report)
    print(f"wrote {target}")
    return 0 if report.passed == report.total else 1


def _print_scenarios(report: BlueprintEvaluationReport) -> None:
    for scenario in report.scenarios:
        status = "PASS" if scenario.passed else "FAIL"
        failed = ", ".join(check.name for check in scenario.failed_checks)
        print(f"  [{status}] {scenario.scenario} ({scenario.business_model}) {failed}")


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if not arg.startswith("--")]
    live = "--live" in argv[1:]
    target = (
        pathlib.Path(args[0])
        if args
        else pathlib.Path("business_evaluation_live.json" if live else "business_evaluation.json")
    )
    # Guard against a stale full-suite assumption: the dataset is fixed.
    if len(SCENARIOS) != 15:  # pragma: no cover - dataset drift is a test failure
        print("ERROR: scenario dataset drifted from the 15 labeled scenarios")
        return 3
    return _run_live(target) if live else _run_deterministic(target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
