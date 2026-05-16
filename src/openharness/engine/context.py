"""QueryContext — immutable per-query collaborators consumed by ``run_query``.

Per the P2-T1 Three-Axis discussion (D7.1 / D7.2 / D7.5):

- D7.1 originally fixed the field set at 6 entries. Two amendments since:
  P2-T4.4d added ``model`` + ``max_tokens`` (loop builds ApiMessageRequest);
  P2-T6.6b added ``permission_mode`` (DRY_RUN short-circuit needs to live
  next to permission_checker). Both recorded in learnings/08 / 10.
- D7.2 typed unfinished collaborators as ``object`` at runtime, then tightened
  per hand-off. P2-T2.2e cashed ``tool_registry``; P2-T4.4c cashed
  ``permission_checker``. The marker convention is exhausted.
- P3-T1.1d further widens ``api_client`` from the concrete
  ``OpenAICompatibleApiClient`` to the ``SupportsStreamingMessages`` Protocol —
  Phase 5 Anthropic-native client / Phase 6 sub-agent stub clients can satisfy
  the contract without inheriting.
- D7.5 fixes ``system_prompt`` as the *assembled* string. ``prompts.py`` (P2-T5)
  is the constructor; QueryContext just holds the result.

``max_turns=20`` default is the loop hard cap from
``decisions/06-phase-2-boundary.md`` D6.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openharness.hooks import HookRegistry
from openharness.permissions import PermissionMode
from openharness.skills.store import EmptySkillStore

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api import SupportsStreamingMessages
    from openharness.permissions import PermissionChecker
    from openharness.skills.store import SkillStore
    from openharness.tools import ToolRegistry


@dataclass(frozen=True)
class QueryContext:
    """Per-query collaborators that do not change across loop iterations.

    Construction is the caller's job (CLI in P2-T6 wires Settings ->
    ToolRegistry -> ``build_system_prompt(...)`` -> QueryContext); this dataclass
    intentionally has no factory method to avoid coupling to ``Settings``.
    """

    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    permission_checker: PermissionChecker
    system_prompt: str
    cwd: Path
    model: str
    max_tokens: int = 1024
    max_turns: int = 20
    permission_mode: PermissionMode = field(default=PermissionMode.DEFAULT)
    # P6-T1 (D16.5): sub-agent recursion tracking. Top-level ``oh ask``
    # constructs with default ``agent_depth=0``; ``SpawnAgent.execute``
    # builds the sub-context via ``dataclasses.replace(parent,
    # agent_depth=parent.agent_depth + 1)``. ``max_agent_depth`` is the
    # cap from :class:`Settings`; propagates through all sub-agent levels
    # so every depth check reads the same value. Engine dispatch is
    # depth-agnostic — the bound check lives entirely inside
    # ``SpawnAgent.execute`` per the cross-cutting invariant.
    agent_depth: int = 0
    max_agent_depth: int = 3
    # P3-T4.4e: hook registry for middleware (Pre/PostToolUse, Pre/PostApiCall,
    # OnError). Default empty registry = no hooks = zero dispatch overhead.
    # Users programmatically register hooks before constructing the context
    # (Phase 3:no plugin discovery,that's Phase 5).
    hook_registry: HookRegistry = field(default_factory=HookRegistry)
    # P5c-T2 (decisions/12 L3): catalog source for the "Available Skills"
    # section in build_system_prompt + lookup target for LoadSkillTool.
    # Default ``EmptySkillStore`` keeps every existing test/CLI flow that
    # ignores skills working unchanged — no field is required to flip Phase
    # 5c on for callers who don't want it.
    skill_store: SkillStore = field(default_factory=EmptySkillStore)
