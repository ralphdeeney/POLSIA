"""Registry value objects: lifecycle statuses and semantic versioning."""

from __future__ import annotations

import enum
import re

from polsia.core.shared.errors import ValidationError

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class RegistryStatus(enum.StrEnum):
    """Lifecycle of a registry entry. Disabled entries are not selectable."""

    ACTIVE = "active"
    DISABLED = "disabled"


def validate_semver(version: str) -> str:
    """Semantic version (major.minor.patch), e.g. ``1.0.0``."""
    if not SEMVER_RE.match(version):
        raise ValidationError(f"version must be semver (major.minor.patch), got {version!r}")
    return version


def validate_capability_key(key: str) -> str:
    """A capability/tool reference key: ``namespace.name`` (snake case)."""
    parts = key.split(".")
    if len(parts) != 2 or not all(p and p.replace("_", "").isalnum() for p in parts):
        raise ValidationError(f"capability key must be 'namespace.name', got {key!r}")
    return key
