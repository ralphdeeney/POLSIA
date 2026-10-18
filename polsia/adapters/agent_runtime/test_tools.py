"""Narrow read-only test tools (Phase 4 mandate §6).

Only three capabilities exist: a whitelisted-directory file reader, a date
query, and an echo sandbox. No shell, filesystem-write, outbound network,
payment, deployment or public messaging path is reachable through the runtime
tool gateway.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from polsia.core.shared.errors import ValidationError

FILE_EXTENSIONS = frozenset({".txt", ".md", ".json"})
MAX_READ_BYTES = 16 * 1024


def build_readonly_tool_handlers(whitelist_root: Path) -> dict[str, Any]:
    """Handlers for the read-only capability, rooted at ``whitelist_root``.

    Path traversal outside the root is refused in executable code.
    """
    root = whitelist_root.resolve()

    async def read_file(*, path: str) -> dict[str, Any]:
        if not path.strip():
            raise ValidationError("path must not be empty")
        candidate = (root / path).resolve()
        if root not in candidate.parents and candidate != root:
            raise ValidationError("path escapes the whitelisted tool root")
        if candidate.suffix.lower() not in FILE_EXTENSIONS:
            raise ValidationError(f"file type {candidate.suffix!r} is not readable")
        if not candidate.is_file():
            raise ValidationError("path is not a whitelisted file")
        data = candidate.read_text(encoding="utf-8", errors="strict")[:MAX_READ_BYTES]
        return {"content": data, "truncated": len(data) >= MAX_READ_BYTES}

    async def current_date() -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        return {"utc_date": now.date().isoformat(), "utc_time": now.isoformat()}

    async def echo(*, text: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        # Deliberately does not interpret instructions: tool responses are
        # untrusted data, never commands.
        return {"echo": text[:2000], "metadata": dict(metadata or {})}

    return {
        "read_file": read_file,
        "current_date": current_date,
        "echo": echo,
    }


def register_default_readonly_tools(gateway: Any, *, whitelist_root: Path) -> None:
    """Register the narrow read-only capability on a :class:`ToolGateway`."""
    handlers = build_readonly_tool_handlers(whitelist_root)
    gateway.register_readonly_tool(
        name="read_file",
        version="1.0.0",
        handler=handlers["read_file"],
        required_permission_action="tool:read_file",
        description="Read a whitelisted text/markdown/json file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    gateway.register_readonly_tool(
        name="current_date",
        version="1.0.0",
        handler=handlers["current_date"],
        required_permission_action="tool:current_date",
        description="Return the current UTC date and time (read-only).",
        parameters={"type": "object", "properties": {}},
    )
    gateway.register_readonly_tool(
        name="echo",
        version="1.0.0",
        handler=handlers["echo"],
        required_permission_action="tool:echo",
        description="Echo back untrusted text (sandbox).",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
