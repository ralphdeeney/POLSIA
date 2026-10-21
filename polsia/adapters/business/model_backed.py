"""ModelBackedBlueprintGenerator — ModelPort-backed blueprint pipeline.

Reuses the Phase 4 :class:`polsia.core.runtime.ports.ModelPort` (never a new
LLM client). Bounded by construction:

- a fixed, versioned step plan (a subset of ``PROMPT_MODULES``);
- per-call prompt token budget (char-based estimate), ``max_tokens`` per call
  and a total token budget across the pipeline;
- a wall-clock deadline;
- bounded structured-output retries with schema feedback to the model.

A model-generated blueprint is a *proposal*: callers must validate it
(``CompanyBlueprint.validate()``) and persist it through the services before
any idea lifecycle change. No research tool is attached, so market/pricing/
financial claims must stay labelled hypothesis/unknown — validation rejects
otherwise.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from typing import Any

from polsia.adapters.business.errors import (
    BlueprintBudgetExceeded,
    BlueprintGenerationError,
    BlueprintSchemaError,
)
from polsia.adapters.business.prompt_composer import PROMPT_MODULES, render_prompt
from polsia.adapters.models.errors import ModelProviderError
from polsia.core.business.entities import BusinessIdea, CompanyBlueprint, blueprint_from_dict
from polsia.core.business.values import BlueprintSource
from polsia.core.runtime.ports import ModelMessage, ModelPort, ModelRequest

#: 4 chars ≈ 1 token is a deliberately conservative input budget proxy.
_CHARS_PER_TOKEN = 4
DEFAULT_MAX_PROMPT_TOKENS = 3_000
DEFAULT_MAX_COMPLETION_TOKENS = 2_000
DEFAULT_MAX_TOTAL_TOKENS = 12_000
DEFAULT_MAX_ELAPSED_SECONDS = 90.0
DEFAULT_MAX_RETRIES = 2

#: Modular steps available for pipeline construction (the default plan is the
#: minimal 3-step path; the remaining templates stay available for variants).
DEFAULT_STEP_PLAN = (
    "business_idea_interpretation",
    "business_model_generation",
    "company_blueprint_generation",
)

_REPAIR_SUFFIX = (
    "The previous response did not satisfy the prompt's Output contract. "
    "Return the corrected JSON only — no prose, no markdown fence, no apology."
)


@dataclasses.dataclass(frozen=True)
class BlueprintGenerationBudget:
    """Hard caps for the model-backed pipeline."""

    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS
    max_elapsed_seconds: float = DEFAULT_MAX_ELAPSED_SECONDS
    max_model_calls: int = 6
    max_retries_per_step: int = DEFAULT_MAX_RETRIES


@dataclasses.dataclass(frozen=True)
class BlueprintGenerationUsage:
    """Honest token/cost accounting for one pipeline run (never fabricated)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_calls: int = 0
    retries: int = 0
    elapsed_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "model_calls": self.model_calls,
            "retries": self.retries,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


class _BudgetStop(Exception):
    """Internal control flow: the pipeline hit a hard budget."""


class ModelBackedBlueprintGenerator:
    """Bounded pipeline of structured-output model calls producing a blueprint."""

    name = "model-blueprint"

    def __init__(
        self,
        model: ModelPort,
        *,
        steps: tuple[str, ...] = DEFAULT_STEP_PLAN,
        budget: BlueprintGenerationBudget | None = None,
    ) -> None:
        for step in steps:
            if step not in PROMPT_MODULES:
                raise BlueprintGenerationError(f"unknown prompt module {step!r}")
        if not steps or len(steps) > self._budget_calls_limit(budget):
            raise BlueprintGenerationError("step plan must be between 1 and max_model_calls steps")
        self._model = model
        self._steps = tuple(steps)
        self._budget = budget or BlueprintGenerationBudget()
        #: Usage of the most recent ``generate`` call (diagnostics/accounting).
        self.last_usage = BlueprintGenerationUsage()

    @staticmethod
    def _budget_calls_limit(budget: BlueprintGenerationBudget | None) -> int:
        return (budget or BlueprintGenerationBudget()).max_model_calls

    async def generate(
        self, idea: BusinessIdea, *, context: dict[str, Any] | None = None
    ) -> CompanyBlueprint:
        """Port-conformant entry point: a validated-able blueprint proposal.

        Callers that need token/call/retry accounting should use
        :meth:`generate_with_usage`; ``last_usage`` always reflects the most
        recent call.
        """
        blueprint, usage = await self.generate_with_usage(idea, context=context)
        self.last_usage = usage
        return blueprint

    async def generate_with_usage(
        self, idea: BusinessIdea, *, context: dict[str, Any] | None = None
    ) -> tuple[CompanyBlueprint, BlueprintGenerationUsage]:
        """Run the bounded pipeline; return ``(blueprint_proposal, usage)``.

        The returned blueprint is unvalidated structured data. Callers apply
        ``CompanyBlueprint.validate()`` through the services layer.
        """
        start = time.monotonic()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._budget.max_elapsed_seconds
        usage = BlueprintGenerationUsage()
        run_context: dict[str, Any] = {**(context or {}), **self._idea_context(idea)}
        output: dict[str, Any] = {}

        try:
            for index, step in enumerate(self._steps):
                if loop.time() >= deadline:
                    raise _BudgetStop(f"elapsed budget exhausted before step {step!r}")
                output, usage = await self._run_step(step, run_context, usage, deadline)
                if index < len(self._steps) - 1:
                    # Intermediate steps feed forward under their own names.
                    run_context = {**run_context, **_flatten(output)}
        except _BudgetStop as stop:
            raise BlueprintBudgetExceeded(str(stop)) from stop
        except ModelProviderError:
            raise

        usage = dataclasses.replace(usage, elapsed_seconds=time.monotonic() - start)
        return await self._validate_with_repair(
            idea, output, run_context, usage, deadline, loop, start
        )

    async def _validate_with_repair(
        self,
        idea: BusinessIdea,
        output: dict[str, Any],
        run_context: dict[str, Any],
        usage: BlueprintGenerationUsage,
        deadline: float,
        loop: asyncio.AbstractEventLoop,
        start: float,
    ) -> tuple[CompanyBlueprint, BlueprintGenerationUsage]:
        """Turn the composed output into the domain artifact, with bounded repair.

        The model is a proposal source: a syntactically valid envelope can still
        name an unsupported ``business_model`` family. Instead of surfacing a
        bare ``ValueError`` from the core parser, re-run the composing step once
        with the exact validation error, so the model can correct the field.
        """
        last_step = self._steps[-1]
        attempts = max(1, self._budget.max_retries_per_step)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                blueprint = blueprint_from_dict(idea.id, idea.organization_id, output)
                # Provenance is the producer's responsibility: a model-built
                # proposal must never be persisted as a deterministic artifact.
                if blueprint.source is not BlueprintSource.MODEL:
                    blueprint = dataclasses.replace(blueprint, source=BlueprintSource.MODEL)
            except (ValueError, TypeError, KeyError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                if loop.time() >= deadline:
                    raise BlueprintBudgetExceeded(
                        "elapsed budget exhausted during validation repair"
                    ) from exc
                usage = dataclasses.replace(usage, retries=usage.retries + 1)
                try:
                    output, usage = await self._run_step(
                        last_step,
                        run_context,
                        usage,
                        deadline,
                        repair=(json.dumps(output), f"{type(exc).__name__}: {exc}"),
                    )
                except _BudgetStop as stop:
                    raise BlueprintBudgetExceeded(str(stop)) from stop
                continue
            return blueprint, dataclasses.replace(usage, elapsed_seconds=time.monotonic() - start)
        raise BlueprintGenerationError(
            f"model blueprint failed domain validation after {attempts} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    # -- internals ----------------------------------------------------------

    def _idea_context(self, idea: BusinessIdea) -> dict[str, Any]:
        return {
            "raw_idea": idea.raw_idea,
            "description": idea.description,
            "target_market": idea.target_market or "<absent>",
            "industry": idea.industry or "<absent>",
            "geo_preferences": list(idea.geo_preferences),
            "budget_constraints": idea.budget_constraints or "<absent>",
            "additional_constraints": list(idea.additional_constraints),
            "company_name": "<provisional>",
        }

    async def _run_step(
        self,
        step: str,
        run_context: dict[str, Any],
        usage: BlueprintGenerationUsage,
        deadline: float,
        *,
        repair: tuple[str, str] | None = None,
    ) -> tuple[dict[str, Any], BlueprintGenerationUsage]:
        prompt = render_prompt(step, run_context)
        prompt_tokens_estimate = len(prompt) // _CHARS_PER_TOKEN
        if prompt_tokens_estimate > self._budget.max_prompt_tokens:
            raise BlueprintBudgetExceeded(
                f"prompt for {step!r} exceeds {self._budget.max_prompt_tokens} tokens "
                f"(estimated {prompt_tokens_estimate})"
            )
        messages: list[ModelMessage] = [
            ModelMessage(
                role="system",
                content="You are a strict JSON-producing business analysis module. Never assert "
                "market size, competitor counts or financial figures as facts. Follow the "
                "prompt's Output contract exactly.",
            ),
            ModelMessage(role="user", content=prompt),
        ]
        if repair is not None:
            previous_output, validation_error = repair
            messages += [
                ModelMessage(role="assistant", content=previous_output[:2_000]),
                ModelMessage(
                    role="user",
                    content=f"{_REPAIR_SUFFIX}\nValidation error: {validation_error}",
                ),
            ]
        last_error: Exception | None = None
        for attempt in range(max(1, self._budget.max_retries_per_step)):
            if asyncio.get_running_loop().time() >= deadline:
                raise _BudgetStop(f"elapsed budget exhausted during {step!r}")
            if usage.model_calls + 1 > self._budget.max_model_calls:
                raise _BudgetStop("model call budget exhausted")
            response = await self._model.generate(
                ModelRequest(
                    messages=messages,
                    max_tokens=self._budget.max_completion_tokens,
                    metadata={"step": step, "attempt": attempt + 1},
                )
            )
            usage = dataclasses.replace(
                usage,
                prompt_tokens=usage.prompt_tokens + response.usage.prompt_tokens,
                completion_tokens=usage.completion_tokens + response.usage.completion_tokens,
                model_calls=usage.model_calls + 1,
            )
            if usage.total_tokens > self._budget.max_total_tokens:
                raise _BudgetStop(
                    f"total token budget {self._budget.max_total_tokens} exceeded after {step!r}"
                )
            try:
                return self._parse_final(response.content), usage
            except BlueprintSchemaError as exc:
                last_error = exc
                if attempt + 1 >= self._budget.max_retries_per_step:
                    break
                usage = dataclasses.replace(usage, retries=usage.retries + 1)
                # Structured repair feedback: the schema error is surfaced to
                # the model, never treated as proof of anything.
                messages += [
                    ModelMessage(role="assistant", content=response.content[:2_000]),
                    ModelMessage(
                        role="user",
                        content=f"{_REPAIR_SUFFIX}\nValidation error: {exc}",
                    ),
                ]
        raise BlueprintSchemaError(
            f"model output could not satisfy the {step!r} contract: {last_error}"
        )

    @staticmethod
    def _parse_final(content: str) -> dict[str, Any]:
        if not content:
            raise BlueprintSchemaError("model produced no content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BlueprintSchemaError(f"model output was not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("type") != "final":
            raise BlueprintSchemaError("model did not return a 'final' output envelope")
        output = payload.get("output")
        if not isinstance(output, dict):
            raise BlueprintSchemaError("model final envelope missing 'output' object")
        return dict(output)


def _flatten(output: dict[str, Any]) -> dict[str, Any]:
    """Lift top-level keys so the next template can reference them."""
    flat: dict[str, Any] = {}
    keys_to_lift = (
        "canonical_description",
        "problem_framings",
        "revenue_model",
        "target_customer",
        "target_market",
        "differentiation",
        "customer_acquisition",
        "initial_goals",
        "initial_kpis",
        "missing_details",
    )
    for key in keys_to_lift:
        if key in output:
            flat[key] = output[key]
    # The interpretation step names the family inside ``business_family_result``;
    # later prompts expect the flat ``business_model`` key. Dropping this mapping
    # silently forced every downstream step to re-infer (and occasionally change)
    # the family.
    family = output.get("business_family_result")
    if isinstance(family, dict) and isinstance(family.get("business_model"), str):
        flat["business_model"] = family["business_model"]
    problem = output.get("customer_problem_hypothesis")
    if isinstance(problem, str) and problem.strip():
        flat.setdefault("problem_framings", [problem])
    return flat


__all__ = [
    "DEFAULT_STEP_PLAN",
    "BlueprintGenerationBudget",
    "BlueprintGenerationUsage",
    "ModelBackedBlueprintGenerator",
    "BlueprintGenerationError",
]
