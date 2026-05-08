"""Permission system -- decides whether a tool call may execute.

P2-T4.4c shipped the *interface*; P2-T6 ships the implementations:

- :class:`Decision` enum (ALLOW / DENY)
- :class:`PermissionMode` enum (DEFAULT / AUTO / DRY_RUN)
- :class:`PermissionChecker` Protocol with ``evaluate(...)``
- :class:`DenyListChecker` -- minimal Bash deny-list

Public API:

    from openharness.permissions import (
        Decision,
        DenyListChecker,
        PermissionChecker,
        PermissionMode,
    )
"""

from __future__ import annotations

from openharness.permissions.checker import (
    Decision,
    DenyListChecker,
    PermissionChecker,
    PermissionMode,
)

__all__ = [
    "Decision",
    "DenyListChecker",
    "PermissionChecker",
    "PermissionMode",
]
