"""Permission system -- decides whether a tool call may execute.

P2-T4 (this scaffold) ships only the *interface* (D10.1):

- :class:`Decision` enum (ALLOW / DENY)
- :class:`PermissionChecker` Protocol with ``evaluate(...)``

The actual deny-list implementation lands in P2-T6 alongside the CLI flags
(``--auto`` / ``--dry-run``).

Public API:

    from openharness.permissions import Decision, PermissionChecker
"""

from __future__ import annotations

from openharness.permissions.checker import Decision, PermissionChecker

__all__ = [
    "Decision",
    "PermissionChecker",
]
