"""Phase 4 evaluation runner.

Run: python scripts/run_agent_evaluations.py [output.json]

Executes the deterministic evaluation suite (routing A/B and the native agent
runtime scenarios) and writes a sanitized JSON report. Real Laya inference and
live LLM routing are only included when a backend/classifier is explicitly
wired; otherwise their arms are reported as NOT RUN — never fabricated.
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import UTC, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from polsia.eval.agent import evaluate_agent_runtime  # noqa: E402
from polsia.eval.routing import run_routing_comparison  # noqa: E402


def main() -> int:
    routing = run_routing_comparison()
    agent_report = evaluate_agent_runtime()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "routing_comparison": routing.to_dict(),
        "native_agent_evaluation": agent_report.to_dict(),
        "claimes_disclaims": (
            "Deterministic evaluation only. Laya real-model evaluation is "
            f"{routing.laya_status}. No live provider traffic was generated."
        ),
    }

    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = json.dumps(report, indent=2, sort_keys=True)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    print(payload)

    summary = agent_report
    ok = (
        summary.security_violations == 0
        and summary.budget_violations == 0
        and all(scenario.passed for scenario in summary.scenarios)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
