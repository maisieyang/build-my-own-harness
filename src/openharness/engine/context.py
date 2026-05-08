"""QueryContext — immutable per-query collaborators consumed by ``run_query``.

Per the P2-T1 Three-Axis discussion (D7.1 / D7.2 / D7.5):

- D7.1 originally fixed the field set at 6 entries. P2-T4.4d revised this to
  add ``model`` and ``max_tokens`` -- both are per-query immutables that the
  loop needs to construct each ``ApiMessageRequest``. Recorded as an
  amendment in ``learnings/08-run-query.md``.
- D7.2 typed unfinished collaborators as ``object`` at runtime, then tightened
  per hand-off. P2-T2.2e cashed ``tool_registry``; P2-T4.4c cashed
  ``permission_checker``. The marker convention (``rg "tighten to"``) is now
  exhausted.
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
    from openharness.permissions import PermissionChecker
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
    permission_checker: PermissionChecker
    system_prompt: str
    cwd: Path
    model: str
    max_tokens: int = 1024
    max_turns: int = 20
