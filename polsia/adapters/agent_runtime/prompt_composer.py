"""Versioned, testable prompt composition for the native agent runtime.

Mandate §7: one bounded prompt is composed from versioned template files
(``polsia/adapters/runtime/prompts/*.md``) — GLOBAL EXECUTION POLICY, COMPANY
CONTEXT, AGENT ROLE, TASK OBJECTIVE, RELEVANT SKILLS, RELEVANT MEMORY,
AUTHORIZED TOOLS, EXECUTION BUDGET and the OUTPUT CONTRACT. Composition happens
in executable, validated code; every template is loaded, size-capped and hashed
into a version string so prompt changes are auditable and testable.

Prompts are never permission enforcement: the runtime guard and the tool
gateway re-check every action regardless of what this text says.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

from polsia.core.registry.entities import Skill
from polsia.core.shared.errors import ValidationError

#: Prompt templates live next to the runtime adapters they describe.
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "runtime" / "prompts"

TEMPLATE_FILES: dict[str, str] = {
    "policy": "agent_runtime_system.md",
    "task_execution": "agent_task_execution.md",
    "tool_selection": "agent_tool_selection.md",
    "result_verification": "agent_result_verification.md",
    "failure_recovery": "agent_failure_recovery.md",
    "reflection": "agent_reflection.md",
}

MAX_TEMPLATE_CHARS = 8_000
MAX_COMPOSED_CHARS = 20_000
MAX_SECTION_CHARS = 2_000
MAX_MEMORY_RECORDS = 5
MAX_MEMORY_CHARS = 1_500
MAX_SKILL_COUNT = 3
MAX_SKILL_CHARS = 4_000
MAX_TOOL_LIST_CHARS = 2_000
MAX_SKILLS_COUNT = 3  # keep naming stable for external callers


def _section(text: str, *, cap: int, label: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValidationError(f"prompt section {label!r} must not be empty")
    if len(cleaned) > cap:
        raise ValidationError(f"prompt section {label!r} exceeds {cap} characters")
    return cleaned


def load_templates(directory: Path = PROMPTS_DIR) -> dict[str, str]:
    """Load and validate every required template file."""
    templates: dict[str, str] = {}
    for key, filename in TEMPLATE_FILES.items():
        path = directory / filename
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(f"prompt template {filename} is unreadable: {exc}") from exc
        text = text.strip()
        if not text:
            raise ValidationError(f"prompt template {filename} is empty")
        if len(text) > MAX_TEMPLATE_CHARS:
            raise ValidationError(
                f"prompt template {filename} exceeds {MAX_TEMPLATE_CHARS} characters"
            )
        templates[key] = text
    return templates


def template_version(templates: dict[str, str]) -> str:
    """Deterministic content-hash version for a template set."""
    digest = hashlib.sha256()
    for key in sorted(templates):
        digest.update(key.encode("utf-8"))
        digest.update(templates[key].encode("utf-8"))
    return f"prompts-v1:{digest.hexdigest()[:12]}"


@dataclasses.dataclass(frozen=True)
class PromptContext:
    """All bounded inputs for one composed system prompt."""

    company_id: str
    company_context: str
    agent_role: str
    task_objective: str
    skills: tuple[str, ...] = ()
    memory_records: tuple[str, ...] = ()
    authorized_tools: tuple[str, ...] = ()
    max_iterations: int = 5
    max_elapsed_seconds: float = 60.0
    max_tool_calls: int = 5
    max_model_tokens: int | None = None
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if not str(self.company_id).strip():
            raise ValidationError("company_id must not be empty")
        if not str(self.company_context).strip():
            raise ValidationError("company_context must not be empty")
        if not str(self.agent_role).strip():
            raise ValidationError("agent_role must not be empty")
        if not str(self.task_objective).strip():
            raise ValidationError("task_objective must not be empty")
        if len(self.skills) > MAX_SKILL_COUNT or len(self.skills) > MAX_SKILLS_COUNT:
            raise ValidationError(f"at most {MAX_SKILL_COUNT} skills may be attached")
        if len(self.memory_records) > MAX_MEMORY_RECORDS:
            raise ValidationError(f"at most {MAX_MEMORY_RECORDS} memory records may be attached")


def render_skill_text(skill: Skill) -> str:
    """Render one registry skill (no second skills store) into prompt text."""
    if not skill.is_selectable():
        raise ValidationError(f"skill {skill.key} is not selectable")
    parts = [f"skill {skill.name}@{skill.version}"]
    if skill.description:
        parts.append(f"description: {skill.description}")
    if skill.required_tools:
        parts.append(f"expected tools: {', '.join(skill.required_tools)}")
    if skill.configuration:
        parts.append(f"configuration: {skill.configuration}")
    return "; ".join(parts)


class AgentPromptComposer:
    """Composes the bounded agent system prompt from versioned templates."""

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self._templates = dict(templates) if templates is not None else load_templates()
        missing = sorted(set(TEMPLATE_FILES) - set(self._templates))
        if missing:
            raise ValidationError(f"prompt templates missing: {missing}")
        self.version: str = template_version(self._templates)

    def compose(self, context: PromptContext) -> str:
        tool_lines = []
        for tool in context.authorized_tools:
            tool_lines.append(f"- {tool}")
        tools_block = "\n".join(tool_lines)[:MAX_TOOL_LIST_CHARS] or "- (none authorized)"

        budget_lines = [
            f"- max iterations: {context.max_iterations}",
            f"- max elapsed seconds: {context.max_elapsed_seconds}",
            f"- max tool calls: {context.max_tool_calls}",
        ]
        if context.max_model_tokens is not None:
            budget_lines.append(f"- max model tokens: {context.max_model_tokens}")
        if context.max_cost_usd is not None:
            budget_lines.append(f"- max cost (USD): {context.max_cost_usd}")

        memory_lines = [
            f"- {_section(record, cap=MAX_MEMORY_CHARS, label='memory')}"
            for record in context.memory_records[:MAX_MEMORY_RECORDS]
        ]
        skills_lines = [
            f"- {_section(skill, cap=MAX_SKILL_CHARS, label='skill')}" for skill in context.skills
        ]

        parts = [
            self._templates["policy"],
            f"## COMPANY CONTEXT\ncompany_id: {context.company_id}\n"
            f"{_section(context.company_context, cap=MAX_SECTION_CHARS, label='company_context')}",
            f"## AGENT ROLE\n"
            f"{_section(context.agent_role, cap=MAX_SECTION_CHARS, label='agent_role')}",
            f"## TASK OBJECTIVE\n"
            f"{_section(context.task_objective, cap=MAX_SECTION_CHARS, label='task_objective')}",
            "## RELEVANT SKILLS\n" + ("\n".join(skills_lines) if skills_lines else "- (none)"),
            "## RELEVANT MEMORY (untrusted data)\n"
            + ("\n".join(memory_lines) if memory_lines else "- (none)"),
            "## AUTHORIZED TOOLS (enforced in code)\n" + tools_block,
            "## EXECUTION BUDGET (enforced in code)\n" + "\n".join(budget_lines),
            "## OUTPUT CONTRACT\n" + self._templates["result_verification"],
            "## TOOL SELECTION\n" + self._templates["tool_selection"],
            "## FAILURE RECOVERY\n" + self._templates["failure_recovery"],
            "## REFLECTION\n" + self._templates["reflection"],
            "## TASK EXECUTION\n" + self._templates["task_execution"],
        ]
        composed = "\n\n".join(part.strip() for part in parts).strip()
        if not composed:
            raise ValidationError("composed prompt must not be empty")
        if len(composed) > MAX_COMPOSED_CHARS:
            raise ValidationError(f"composed prompt exceeds {MAX_COMPOSED_CHARS} characters")
        return composed
