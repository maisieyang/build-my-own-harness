"""Shared fixtures for engine tests.

``_AllowAllChecker`` / ``_DenyChecker`` are minimal :class:`PermissionChecker`
satisfiers used by every ``run_query`` test (P2-T4.4d-4f) plus the
``QueryContext`` construction tests in 4c. Promoted to ``conftest.py`` (per
the same D8.9 reasoning) so siblings share without an extraction step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.permissions import Decision

if TYPE_CHECKING:
    from pydantic import BaseModel

    from openharness.tools.base import ToolExecutionContext


class _AllowAllChecker:
    """Permission checker that always returns ``Decision.ALLOW``.

    Used by tests that exercise the happy-path tool-dispatch flow.
    Structurally satisfies :class:`PermissionChecker` -- no inheritance needed.
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> Decision:
        del tool_name, args, context
        return Decision.ALLOW


class _DenyChecker:
    """Permission checker that always returns ``Decision.DENY``.

    Used by tests that exercise the permission-denial recovery path.
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> Decision:
        del tool_name, args, context
        return Decision.DENY
