"""Domain errors for the permissions domain.

Subclasses the shared taxonomy (polsia.core.shared.errors) — the API layer
maps them into RFC 9457 problem-details via the shared handlers.
"""

from __future__ import annotations

from polsia.core.shared.errors import PolsiaError


class PermissionDenied(PolsiaError):
    """The principal is explicitly denied (or not allowed) for the action."""

    status_code = 403
    code = "permission_denied"


class ApprovalRequired(PolsiaError):
    """The action requires human approval before it may proceed.

    ``approval_id`` carries the pending approval reference; it is ``None``
    until the caller creates the approval and attaches it.
    """

    status_code = 403
    code = "approval_required"

    def __init__(self, message: str | None = None, *, approval_id: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.approval_id: str | None = approval_id
