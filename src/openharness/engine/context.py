"""QueryContext — immutable per-query collaborators consumed by ``run_query``.

Per the P2-T1 Three-Axis discussion (D7.1 / D7.2 / D7.5):

- D7.1 fixes the field set at the final shape (6 fields). ``tool_registry`` and
  ``permission_checker`` exist now even though their types do not — see D7.2.
- D7.2 lets us keep the engine independent of P2-T2 / P2-T6 by typing the
  unfinished collaborators as ``object`` at runtime. P2-T2 (tool system) and
  P2-T6 (permissions) each tighten the corresponding annotation; their
  acceptance criteria include this hand-off.
- D7.5 fixes ``system_prompt`` as the *assembled* string. ``prompts.py`` (P2-T5)
  is the constructor; QueryContext just holds the result.

``max_turns=20`` default is the loop hard cap from
``decisions/06-phase-2-boundary.md`` D6.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api import OpenAICompatibleApiClient
    from openharness.tools import ToolRegistry


@dataclass(frozen=True)
class QueryContext:
    """Per-query collaborators that do not change across loop iterations.

    Construction is the caller's job (CLI in P2-T6 wires Settings ->
    ToolRegistry -> ``build_system_prompt(...)`` -> QueryContext); this dataclass
    intentionally has no factory method to avoid coupling to ``Settings``.
    """

    api_client: OpenAICompatibleApiClient
    tool_registry: ToolRegistry
    permission_checker: object  # tighten to PermissionChecker in P2-T6
    system_prompt: str
    cwd: Path
    max_turns: int = 20
