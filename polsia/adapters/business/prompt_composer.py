"""Versioned prompt loader for business blueprint generation.

Each prompt lives in ``polsia/adapters/business/prompts/<name>.v<version>.md``
and is loaded on demand (only the prompts relevant to a step are used). The
composer is deterministic and pure: it renders plain-text context, no LLM
calls, and never mutates user text.
"""

from __future__ import annotations

import importlib.resources
import json
from typing import Any

from polsia.core.shared.errors import ValidationError

PROMPT_VERSION = "v1"

#: Prompt modules used by the model-backed generator, in pipeline order.
PROMPT_MODULES = (
    "business_idea_interpretation",
    "customer_problem_analysis",
    "business_model_generation",
    "product_definition",
    "pricing_hypotheses",
    "go_to_market_strategy",
    "risk_analysis",
    "company_blueprint_generation",
)

_PROMPT_CACHE: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """Load a versioned prompt template by module name (cached)."""
    if name not in PROMPT_MODULES:
        raise ValidationError(f"unknown business prompt module {name!r}")
    if name in _PROMPT_CACHE:
        return _PROMPT_CACHE[name]
    filename = f"{name}.{PROMPT_VERSION}.md"
    try:
        text = importlib.resources.files("polsia.adapters.business").joinpath(f"prompts/{filename}")
        template = text.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ValidationError(f"business prompt {filename!r} is missing") from exc
    _PROMPT_CACHE[name] = template
    return template


def render_prompt(name: str, context: dict[str, Any]) -> str:
    """Render a versioned prompt with ``{{key}}`` placeholders (JSON-formatted
    when the value is a non-string)."""
    template = load_prompt(name)
    rendered = template
    placeholders = sorted(set(_placeholders(template)))
    for key in placeholders:
        if key not in context:
            rendered = rendered.replace("{{" + key + "}}", "<absent>")
            continue
        rendered = rendered.replace("{{" + key + "}}", _format_value(context[key]))
    return rendered


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return repr(value)


def _placeholders(template: str) -> set[str]:
    found: set[str] = set()
    index = 0
    while True:
        start = template.find("{{", index)
        if start < 0:
            break
        end = template.find("}}", start + 2)
        if end < 0:
            break
        found.add(template[start + 2 : end].strip())
        index = end + 2
    return found


def prompt_versions() -> tuple[str, ...]:
    """Explicit prompt version map (documentation and evaluation reports)."""
    return tuple(f"{name}.{PROMPT_VERSION}" for name in PROMPT_MODULES)
