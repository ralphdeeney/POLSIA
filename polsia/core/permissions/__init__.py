"""Permissions domain (Phase 2, Slice 4): agent/tool authorization.

Permission policies (principal x resource x action -> decision) are evaluated
by a pure engine in the service layer (deny by default — dangerous operations
default to deny). The Approval lifecycle implements human-in-the-loop gating
for actions a policy marks ``approval_required``. Human API authorization
(org membership) remains separate in the tenancy domain.
"""
