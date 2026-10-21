"""Performance + cost measurements for the business-creation workflow.

These are *honest* measurements of the deterministic offline path, not provider
benchmarks: latency is wall-clock on this machine, token counts come from the
scripted provider's reported usage, and the estimated dollar cost is computed
from an explicit per-million-token rate (0 for the scripted provider). Nothing
is extrapolated from a live model.

Covered (mandate §15): blueprint generation latency, model calls/tokens/cost,
bootstrap duration, retry behaviour, and repeated company initialization with
a no-tenant-mixing assertion.

SQLite serializes writes, so this suite does NOT claim parallel-write
concurrency coverage; transaction/concurrency-sensitive PostgreSQL verification
is reported separately as NOT RUN unless a PostgreSQL server is actually used.
"""

from __future__ import annotations

import json
import statistics
import time
from typing import Any

from polsia.adapters.agent_runtime import (
    ScriptedModelBehavior,
    ScriptedModelProvider,
    ScriptedResponse,
)
from polsia.adapters.business.deterministic import DeterministicBlueprintGenerator
from polsia.adapters.business.model_backed import ModelBackedBlueprintGenerator
from polsia.core.business.entities import BusinessIdea
from polsia.core.shared.ids import OrganizationId, UserId

from tests.integration.test_business_creation_e2e import (
    IDEA,
    _build_runtime,
)
from tests.integration.test_business_creation_e2e import stack as _e2e_stack_fixture

#: Re-exported so pytest discovers the shared real-service fixture in this module.
stack = _e2e_stack_fixture

#: USD per million tokens for the scripted provider (no real bill is implied).
SCRIPTED_USD_PER_MILLION = 0.0


def _final_output(output: dict[str, Any]) -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.COMPLETE,
        content=json.dumps({"type": "final", "output": output}),
        prompt_tokens=40,
        completion_tokens=60,
    )


async def _measure(coro: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - started) * 1000.0


async def _idea_for_perf() -> BusinessIdea:
    return BusinessIdea.submit(
        organization_id=OrganizationId.generate(),
        owner_user_id=UserId.generate(),
        raw_idea=IDEA,
        target_market="independent music teachers",
    )


async def _valid_payload_for_perf() -> dict[str, Any]:
    """A model-shaped payload derived from the deterministic artifact."""
    idea = await _idea_for_perf()
    blueprint = await DeterministicBlueprintGenerator().generate(idea)
    payload = blueprint.to_dict()
    payload["source"] = "model"
    return payload


class TestPerformanceAndIsolation:
    async def test_blueprint_generation_and_bootstrap_are_bounded(
        self, stack: Any, tmp_path: Any
    ) -> None:
        env = stack(_build_runtime([], tmp_path))
        idea_service = env["idea_service"]
        blueprint_service = env["blueprint_service"]
        bootstrap_service = env["bootstrap_service"]

        generation_ms: list[float] = []
        ideas = []
        for _ in range(6):
            idea = await idea_service.submit_idea(env["owner"], env["org"], raw_idea=IDEA)
            _, elapsed = await _measure(
                blueprint_service.generate_blueprint(
                    env["owner"], env["org"], idea.id, idea_service, context={}
                )
            )
            generation_ms.append(elapsed)
            ideas.append(idea)

        bootstrap_ms: list[float] = []
        bootstrapped = []
        for idea in ideas[:3]:
            outcome, elapsed = await _measure(
                bootstrap_service.bootstrap_company(env["owner"], env["org"], idea.id)
            )
            bootstrap_ms.append(elapsed)
            bootstrapped.append(outcome)

        measurement = {
            "blueprint_generation_ms": {
                "n": len(generation_ms),
                "median": round(statistics.median(generation_ms), 3),
                "max": round(max(generation_ms), 3),
            },
            "bootstrap_ms": {
                "n": len(bootstrap_ms),
                "median": round(statistics.median(bootstrap_ms), 3),
                "max": round(max(bootstrap_ms), 3),
            },
            "model_calls": 0,
            "tokens": 0,
            "estimated_cost_usd": 0.0,
            "note": "deterministic offline path; no model provider involved",
        }
        print(f"PERF {measurement}")

        # Loose, machine-independent bounds: catch accidental O(n^2)/network
        # regressions without turning CI into a timing benchmark.
        assert statistics.median(generation_ms) < 2_000
        assert statistics.median(bootstrap_ms) < 5_000
        assert all(outcome.company.organization_id == env["org"] for outcome in bootstrapped)
        assert len({outcome.company.id for outcome in bootstrapped}) == 3
        for outcome in bootstrapped:
            assert len(outcome.goals) == 4
            assert len(outcome.agents) == 2
            assert outcome.memory_record_ids

    async def test_model_backed_usage_and_estimated_cost_are_reported(
        self, stack: Any, tmp_path: Any
    ) -> None:
        payload = await _valid_payload_for_perf()
        provider = ScriptedModelProvider([_final_output(payload)] * 3)
        generator = ModelBackedBlueprintGenerator(provider)
        env = stack(_build_runtime([], tmp_path), generator)
        idea = await env["idea_service"].submit_idea(env["owner"], env["org"], raw_idea=IDEA)
        started = time.perf_counter()
        blueprint, usage = await generator.generate_with_usage(idea)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        blueprint.validate()

        tokens = usage.total_tokens
        estimated_cost = tokens / 1_000_000 * SCRIPTED_USD_PER_MILLION
        print(
            "PERF_MODEL "
            f"{{'calls': {usage.model_calls}, 'tokens': {tokens}, "
            f"'retries': {usage.retries}, 'elapsed_ms': {elapsed_ms:.3f}, "
            f"'estimated_cost_usd': {estimated_cost}}}"
        )
        assert usage.model_calls == 3
        assert tokens == 300
        assert usage.retries == 0
        assert 0.0 <= usage.elapsed_seconds < 30.0
        assert estimated_cost == 0.0

    async def test_repeated_initializations_do_not_mix_tenants(
        self, stack: Any, tmp_path: Any
    ) -> None:
        """Independent bootstraps keep company/goal/agent ownership separate.

        Writes are sequential here because SQLite serializes them; the assertion
        is about ownership isolation, not throughput. PostgreSQL parallel-write
        verification is NOT RUN in this environment.
        """
        env = stack(_build_runtime([], tmp_path))
        ideas = [
            await env["idea_service"].submit_idea(env["owner"], env["org"], raw_idea=IDEA)
            for _ in range(3)
        ]
        for idea in ideas:
            await env["blueprint_service"].generate_blueprint(
                env["owner"], env["org"], idea.id, env["idea_service"], context={}
            )
        outcomes = [
            await env["bootstrap_service"].bootstrap_company(env["owner"], env["org"], idea.id)
            for idea in ideas
        ]
        assert len({outcome.company.id for outcome in outcomes}) == 3
        assert len({outcome.company.slug for outcome in outcomes}) == 3
        for outcome in outcomes:
            assert outcome.company.organization_id == env["org"]
            goals = await env["goals"].list_for_company(outcome.company_id)
            agents = await env["agents_repo"].list_for_company(outcome.company_id)
            assert {goal.company_id for goal in goals} == {outcome.company_id}
            assert {agent.company_id for agent in agents} == {outcome.company_id}
        # Idempotent re-run of one bootstrap never adopts another company's state.
        retry = await env["bootstrap_service"].bootstrap_company(
            env["owner"], env["org"], ideas[1].id
        )
        assert retry.company.id == outcomes[1].company.id
        assert retry.reused is True
        assert len(await env["companies"].list_for_organization(env["org"])) == 3

    async def test_schema_retry_work_is_bounded_and_reported(self, stack: Any) -> None:
        """A schema repair costs one retry, not an unbounded retry loop."""
        payload = await _valid_payload_for_perf()
        provider = ScriptedModelProvider(
            [
                ScriptedResponse(
                    behavior=ScriptedModelBehavior.COMPLETE,
                    content="not json",
                    prompt_tokens=10,
                    completion_tokens=10,
                ),
                _final_output(payload),
                _final_output(payload),
                _final_output(payload),
            ]
        )
        generator = ModelBackedBlueprintGenerator(provider)
        blueprint, usage = await generator.generate_with_usage(await _idea_for_perf())
        blueprint.validate()
        print(
            f"PERF_RETRY {{'calls': {usage.model_calls}, 'retries': {usage.retries}, "
            f"'tokens': {usage.total_tokens}}}"
        )
        assert usage.retries == 1
        assert usage.model_calls <= 4


def test_prompt_template_loading_is_cached() -> None:
    """Prompt loading is cached: repeated renders do no extra file I/O."""
    from polsia.adapters.business.prompt_composer import load_prompt

    first = load_prompt("business_idea_interpretation")
    second = load_prompt("business_idea_interpretation")
    assert first is second
