"""Test-only ModelPort backed by OpenCode's local one-shot generation API.

Why this exists
---------------
OpenCode's free-tier inference is only callable *from inside OpenCode*: a direct
``POST https://opencode.ai/inference/openai/v1/chat/completions`` returns
``403 FreeTierError``. This adapter shells out to the installed ``opencode``
CLI, which authenticates to the local OpenCode service, so the model that
drives the agent session can exercise the Phase 4 blueprint prompts.

It is deliberately **not** part of ``polsia``: it is evaluation plumbing, not a
production provider, and it is not imported by the application. It also does
not implement tool calling; the blueprint pipeline attaches no tools.

The OpenCode ``/api/session/{id}/generate`` endpoint is stateless (a probe
confirmed that a secret shared in one call is not visible in the next), so a
fresh short-lived session is created for every model call and deleted
afterwards.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import time
from typing import Any

from polsia.adapters.models.errors import (
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from polsia.core.runtime.ports import ModelRequest, ModelResponse, TokenUsage

DEFAULT_MODEL = "deepseek-v4.1-flash-free"
_CHARS_PER_TOKEN = 4
_MAX_ERROR_CHARS = 300
_TRANSIENT_MARKERS = (
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "service unavailable",
    "temporarily",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "socket hang up",
    "econnreset",
    "eai_again",
)


def _is_transient(exc: Exception) -> bool:
    """Transport failures are retryable; contract/auth failures are not."""
    if isinstance(exc, ProviderTimeout):
        return True
    detail = str(exc).lower()
    return any(marker in detail for marker in _TRANSIENT_MARKERS)


def _find_cli() -> str:
    """Locate the desktop CLI, preferring the real executable over shell shims.

    ``shutil.which("opencode")`` commonly resolves to an npm ``.CMD`` wrapper
    on Windows. Launching that shim without a console is unreliable (it can
    fail with "The system cannot find the file specified"), so the desktop
    ``opencode-cli.exe`` wins whenever it is installed.
    """
    override = os.environ.get("OPENCODE_CLI", "").strip()
    if override:
        return override
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        versions = sorted(
            (pathlib.Path(appdata) / "ai.opencode.desktop" / "cli").glob("*/opencode-cli.exe"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if versions:
            return str(versions[0])
    for name in ("opencode-cli", "opencode"):
        found = shutil.which(name)
        if found and not found.lower().endswith((".cmd", ".bat")):
            return found
    raise ProviderUnavailable(
        "OpenCode CLI executable not found; set OPENCODE_CLI to opencode-cli.exe"
    )


def _render_prompt(request: ModelRequest) -> str:
    """Flatten the provider-neutral message list into one prompt.

    ``generate`` accepts a single prompt, so roles are preserved as headings.
    Tool requests are rejected: silently dropping tools would produce a
    misleading "no tool call" result.
    """
    if request.tools:
        raise ProviderUnavailable(
            "OpenCode one-shot generation does not support tool calling; "
            "use the OpenAI-compatible provider for tool-enabled runs"
        )
    parts: list[str] = []
    for message in request.messages:
        content = message.content.strip()
        if content:
            parts.append(f"[{message.role}]\n{content}")
    return "\n\n".join(parts)


class OpenCodeSessionModel:
    """``ModelPort`` implementation that calls OpenCode's own generate route."""

    name = "opencode-session"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        directory: str | None = None,
        cli_path: str | None = None,
        timeout_seconds: float = 180.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self._directory = directory or os.getcwd()
        self._cli = cli_path or _find_cli()
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        prompt = _render_prompt(request)
        return await asyncio.to_thread(self._generate_sync, prompt)

    # -- internals ---------------------------------------------------------

    def _generate_sync(self, prompt: str) -> ModelResponse:
        session_id = self._create_session()
        try:
            payload = self._call(
                "post",
                f"/api/session/{session_id}/generate",
                {"prompt": prompt},
            )
            text = payload.get("data", {}).get("text")
            if not isinstance(text, str):
                raise ProviderResponseError("OpenCode generate response has no text")
        finally:
            self._delete_session(session_id)

        return ModelResponse(
            content=text,
            model=self.model,
            usage=TokenUsage(
                prompt_tokens=max(1, len(prompt) // _CHARS_PER_TOKEN),
                completion_tokens=max(1, len(text) // _CHARS_PER_TOKEN),
            ),
            finish_reason="stop",
            metadata={"transport": "opencode-session"},
        )

    def _create_session(self) -> str:
        payload = self._call(
            "post",
            "/api/session",
            {
                "model": {"providerID": "opencode", "id": self.model},
                "title": "polsia-blueprint-eval",
                "location": {"directory": self._directory},
            },
        )
        session_id = payload.get("data", {}).get("id")
        if not isinstance(session_id, str) or not session_id:
            raise ProviderResponseError("OpenCode session response has no id")
        return session_id

    def _delete_session(self, session_id: str) -> None:
        try:
            self._call("delete", f"/api/session/{session_id}", retry=False)
        except Exception:  # noqa: BLE001 - cleanup must not mask the model result
            pass

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        retry: bool = True,
    ) -> dict[str, Any]:
        """Call the CLI, retrying transport failures with a short backoff."""
        attempts = self._max_retries + 1 if retry else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._call_once(method, path, body)
            except (ProviderTimeout, ProviderUnavailable) as exc:
                last_error = exc
                if attempt + 1 >= attempts or not _is_transient(exc):
                    raise
                time.sleep(min(2.0 * (attempt + 1), 5.0))
        raise last_error  # pragma: no cover - the loop returns or raises

    def _call_once(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        args = [self._cli, "api", method, path]
        if body is not None:
            args += ["--data", json.dumps(body, ensure_ascii=False)]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                creationflags=creationflags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeout(
                f"OpenCode API {method} {path} timed out after {self._timeout:.0f}s"
            ) from exc

        stdout = completed.stdout.strip()
        if completed.returncode != 0:
            detail = (completed.stderr or stdout).strip()[-_MAX_ERROR_CHARS:]
            raise ProviderUnavailable(
                f"OpenCode API {method} {path} failed (rc={completed.returncode}): {detail}"
            )
        if not stdout:
            return {}
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError(
                f"OpenCode API {method} {path} returned non-JSON: {stdout[:200]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderResponseError(
                f"OpenCode API {method} {path} returned a non-object payload"
            )
        return payload
