"""Business blueprint generation.

The :class:`~polsia.core.business.ports.BlueprintPort` produces a typed,
validated :class:`CompanyBlueprint` from a :class:`BusinessIdea`:

- :class:`DeterministicBlueprintGenerator` — no LLM, used for contract tests,
  offline E2E, CI and as a safe fallback.
- :class:`ModelBackedBlueprintGenerator` — reuses the Phase 4 ``ModelPort``.

Prompts are versioned (``prompts/*.md``) and loaded per step so only the
prompts relevant to the generation step are used.
"""
