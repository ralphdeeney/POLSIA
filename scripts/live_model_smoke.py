"""Opt-in live model smoke test (Phase 4 mandate §12).

Run ONLY when provider credentials are intentionally provided, e.g.:

    POLSIA_LIVE_BASE_URL=https://api.openai.com/v1 \
    POLSIA_LIVE_API_KEY_ENV=OPENAI_API_KEY \
    POLSIA_LIVE_MODEL=gpt-4o-mini \
    python scripts/live_model_smoke.py

The run is tightly bounded (one objective, a harmless read-only echo tool,
tiny token/token-cost budgets). It verifies: real provider connection, model
response, tool-call parsing, gateway authorization, tool execution, token
accounting and trace persistence in the returned result metadata.

Without configuration this script exits 0 and prints `LIVE TEST NOT RUN`.
It never sends public messages, deploys software, or spends money through
external business tools.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

#: Root .env is gitignored; shell-exported variables still take precedence.
load_dotenv(ROOT / ".env")

from polsia.adapters.agent_runtime import (  # noqa: E402
    ExecutionBudget,
    NativeAgentRuntime,
    ToolGateway,
    register_default_readonly_tools,
)
from polsia.adapters.models import (  # noqa: E402
    OpenAICompatibleConfig,
    OpenAICompatibleModelProvider,
)
from polsia.core.runtime.ports import RuntimeRequest, RuntimeStatus  # noqa: E402
from polsia.core.shared.ids import AgentId, CompanyId, TaskId  # noqa: E402


def main() -> int:
    base_url = os.environ.get("POLSIA_LIVE_BASE_URL", "").strip()
    api_key_env = os.environ.get("POLSIA_LIVE_API_KEY_ENV", "").strip()
    model = os.environ.get("POLSIA_LIVE_MODEL", "").strip()
    if not (base_url and api_key_env and model):
        print(
            "LIVE TEST NOT RUN: set POLSIA_LIVE_BASE_URL, POLSIA_LIVE_API_KEY_ENV "
            "and POLSIA_LIVE_MODEL to opt in."
        )
        return 0

    async def run() -> int:
        with tempfile.TemporaryDirectory() as root:
            gateway = ToolGateway()
            register_default_readonly_tools(gateway, whitelist_root=pathlib.Path(root))
            provider = OpenAICompatibleModelProvider(
                OpenAICompatibleConfig(
                    base_url=base_url,
                    api_key_env=api_key_env,
                    model=model,
                    request_timeout_seconds=20.0,
                    max_retries=1,
                )
            )
            runtime = NativeAgentRuntime(
                model=provider,
                tool_gateway=gateway,
                budget=ExecutionBudget(
                    max_iterations=3,
                    max_tool_calls=2,
                    max_elapsed_seconds=60.0,
                    max_model_tokens=2000,
                    max_cost_usd=0.05,
                ),
                allow_text_final=True,  # live models may not obey the strict contract
            )
            result = await runtime.execute(
                RuntimeRequest(
                    company_id=CompanyId.generate(),
                    task_id=TaskId.generate(),
                    agent_id=AgentId.generate(),
                    input={
                        "objective": "Use the current_date tool once, then report the "
                        "UTC date. Do not use any other tool."
                    },
                    context={
                        "agent_capability": "read_only_file_access",
                        "company_context": "Live smoke test, synthetic context.",
                        "agent_role": "smoke-test agent",
                    },
                )
            )
        print("status:", result.status.value)
        print("state:", result.metadata.get("runtime_state"))
        print("usage:", result.token_usage.prompt_tokens, "+", result.token_usage.completion_tokens)
        print("cost_usd:", result.metadata.get("cost_usd"))
        print("tool_calls:", [{"tool": c.tool, "ok": c.ok} for c in result.tool_calls])
        print("trace_nodes:", len(result.metadata.get("trace", [])))
        return 0 if result.status is RuntimeStatus.COMPLETED else 1

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
