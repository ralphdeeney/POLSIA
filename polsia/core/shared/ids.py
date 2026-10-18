"""Typed, prefixed identifiers for all Polsia entities.

Each entity id is a distinct string type so IDs cannot be mixed accidentally
(mypy strict catches it). Format: `<prefix>_<ULID>` — e.g. ``usr_01J...``.
ULIDs are lexicographically sortable by creation time.
"""

from __future__ import annotations

from typing import TypeVar

from ulid import ULID

S = TypeVar("S", bound="_PrefixedId")


def _new_ulid() -> str:
    return str(ULID())


class _PrefixedId(str):
    """Base for typed identifiers. Subclasses declare their prefix."""

    prefix: str

    def __init__(self, raw: str) -> None:  # noqa: ARG002 - kept for clarity
        if not str.__str__(self).startswith(self.prefix):
            raise ValueError(f"{type(self).__name__} must start with {self.prefix!r}")

    @classmethod
    def generate(cls: type[S]) -> S:
        return cls(f"{cls.prefix}_{_new_ulid()}")

    @classmethod
    def parse(cls: type[S], raw: str) -> S:
        return cls(raw)  # validates prefix in __init__

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}({str.__str__(self)!r})"


class UserId(_PrefixedId):
    prefix = "usr"


class OrganizationId(_PrefixedId):
    prefix = "org"


class CompanyId(_PrefixedId):
    prefix = "cmp"


class MembershipId(_PrefixedId):
    prefix = "mem"


class EventId(_PrefixedId):
    prefix = "evt"


# -- Phase 2 (OS core primitives) ---------------------------------------------


class GoalId(_PrefixedId):
    prefix = "goal"


class KpiId(_PrefixedId):
    prefix = "kpi"


class TaskId(_PrefixedId):
    prefix = "tsk"


class TaskExecutionId(_PrefixedId):
    prefix = "tex"


class DecisionRecordId(_PrefixedId):
    prefix = "dcr"


class TriggerPolicyId(_PrefixedId):
    prefix = "trp"


class TriggerDeliveryId(_PrefixedId):
    prefix = "tdl"


class AgentId(_PrefixedId):
    prefix = "agt"


class SkillId(_PrefixedId):
    prefix = "skl"


class ToolId(_PrefixedId):
    prefix = "tol"


class ModelId(_PrefixedId):
    prefix = "mdl"


class PermissionId(_PrefixedId):
    prefix = "prm"


class ApprovalId(_PrefixedId):
    prefix = "apr"


class MemoryRecordId(_PrefixedId):
    prefix = "memr"


class UsageRecordId(_PrefixedId):
    prefix = "usg"
