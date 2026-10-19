"""Phase 4 wave 3 blueprint evaluation runner.

Run: python scripts/run_business_evaluations.py [output.json]

Executes the deterministic, offline blueprint scenario suite (mandate §12) and
writes a sanitized JSON report. The model-backed arm is reported as NOT RUN
unless a live provider is explicitly wired elsewhere — it is never simulated.

The report is reproducible: ``dataset_fingerprint`` identifies the labeled
inputs and ``report_digest`` identifies the scenario outcomes, so two runs of
the same commit produce the same digests.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import UTC, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from polsia.eval.business import evaluate_blueprints  # noqa: E402


def main(argv: list[str]) -> int:
    target = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path("business_evaluation.json")
    first = evaluate_blueprints()
    second = evaluate_blueprints()

    reproducible = (
        first.report_digest == second.report_digest
        and first.dataset_fingerprint == second.dataset_fingerprint
        and first.passed == second.passed
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "report": first.to_dict(),
        "reproducibility": {
            "checked": True,
            "same_report_digest": first.report_digest == second.report_digest,
            "same_dataset_fingerprint": first.dataset_fingerprint == second.dataset_fingerprint,
            "same_pass_count": first.passed == second.passed,
            "reproducible": reproducible,
        },
        "claims_disclaimers": {
            "deterministic_arm": "RUN — offline deterministic generator, CI-safe",
            "model_backed_arm": "LIVE MODEL TEST NOT RUN — no provider configured",
            "ground_truth": "deterministic predicates only; no model judge",
            "real_world_validation": "none — market/pricing/financial claims remain hypotheses",
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"blueprint evaluation: {first.passed}/{first.total} scenarios passed "
        f"(pass_rate={first.pass_rate}); report_digest={first.report_digest}; "
        f"reproducible={reproducible}"
    )
    for scenario in first.scenarios:
        status = "PASS" if scenario.passed else "FAIL"
        failed = ", ".join(check.name for check in scenario.failed_checks)
        print(f"  [{status}] {scenario.scenario} ({scenario.business_model}) {failed}")
    print(f"wrote {target}")
    return 0 if first.passed == first.total and reproducible else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
