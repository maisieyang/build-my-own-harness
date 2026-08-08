"""QueryContext — immutable per-query collaborators consumed by ``run_query``.

Per the P2-T1 Three-Axis discussion (D7.1 / D7.2 / D7.5):

- D7.1 originally fixed the field set at 6 entries. Two amendments since:
  P2-T4.4d added ``model`` + ``max_tokens`` (loop builds ApiMessageRequest);
  P2-T6.6b added ``permission_mode`` (DRY_RUN short-circuit needs to live
  next to permission_checker). Both recorded in learnings/08 / 10.
- ``permission_mode`` is now a retained public/config diagnostic. Independent
  reviewer and execution postures carry current runtime authority.
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
from typing import TYPE_CHECKING, Any

from openharness.execution.host import _HOST_EXECUTION
from openharness.hooks import HookRegistry
from openharness.permissions import (
    ExecutionPosture,
    ExternalToolPolicy,
    PermissionMode,
    ReviewerPosture,
)
from openharness.skills.store import EmptySkillStore

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api import SupportsStreamingMessages
    from openharness.execution import EnforcedBoundary, ExecutionEnvironment, SandboxSession
    from openharness.permissions import (
        ActionDenyPolicy,
        PermissionChecker,
        PermissionRuntime,
        RuntimePermissionProfile,
    )
    from openharness.protocols.messages import ConversationMessage
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
    max_tokens: int = 8192
    max_turns: int = 20
    # Optional full dispatch catalog when the model-visible registry is
    # capability-shaped (for example, plan mode). Hidden forged calls are
    # resolved here only so the authoritative deny policy can reject them.
    dispatch_tool_registry: ToolRegistry | None = None
    # Canonical negative-only semantic guard. It can deny but never grant.
    action_deny_policy: ActionDenyPolicy | None = None
    permission_mode: PermissionMode = field(default=PermissionMode.DEFAULT)
    reviewer_posture: ReviewerPosture = field(default=ReviewerPosture.MANUAL)
    execution_posture: ExecutionPosture = field(default=ExecutionPosture.EXECUTE)
    # True when no online human turn is expected to authorize model-selected
    # local/delegated actions (for example --auto, a Goal loop, or headless).
    autonomous: bool = False
    # Independent from the local filesystem/process boundary. External calls
    # remain governed even when the session intentionally has no sandbox.
    external_tool_policy: ExternalToolPolicy = field(default_factory=ExternalToolPolicy)
    # Human-authored or human-derived authorization scope. Subagents inherit
    # this immutable envelope; their delegated user-role prompt cannot widen it.
    authorization_context: tuple[str, ...] = ()
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
    # P7-T2 (decisions/15 D17.3): substrate-execution abstraction.
    # Defaults to the module-level ``HostExecution`` singleton — every
    # existing test / CLI flow passes unchanged because BashTool's
    # current behavior is byte-identical to ``HostExecution.run_command``.
    # Phase 7b will allow injection of ``SandboxExecution`` (Docker) via
    # ``--sandbox`` CLI flag. Sub-agent inherits this field via
    # ``dataclasses.replace`` automatically.
    execution_env: ExecutionEnvironment = field(default_factory=lambda: _HOST_EXECUTION)
    sandbox_session: SandboxSession | None = None
    runtime_permission_profile: RuntimePermissionProfile | None = None
    enforced_boundary: EnforcedBoundary | None = None
    permission_runtime: PermissionRuntime | None = None
    # P11-T3.3f (decisions/26 D29.3): proactive auto-compact knobs.
    # Engine calls ``auto_compact_if_needed`` BEFORE each LLM request
    # (in front of PreApiCall hooks) when ``compact_enabled=True``.
    # All defaults match the boundary doc D29.8 — pre-Phase-11 callers
    # using the no-kwarg constructor get the same auto-compact
    # threshold (~26.5k tokens for qwen-plus) without explicit opt-in.
    # The full-compact L4 LLM call only fires when L1-L3 don't free
    # enough; existing reactive PTL retry in the loop remains the
    # final safety net.
    compact_enabled: bool = True
    compact_threshold_ratio: float = 0.83
    compact_full_max_tokens: int = 20_000
    compact_full_timeout_s: float = 25.0
    # When set, compact L3 reads this 5-slot checkpoint file (written
    # by ``services.session_memory.update_session_memory_file``) to
    # skip the L4 LLM call entirely. ``None`` (default) skips L3 — L0
    # / L2 / L4 still run.
    session_memory_path: Path | None = None
    memory_store: Any = None  # FilesystemMemoryStore | None — Any avoids
    # circular imports (FilesystemMemoryStore lives under memory/ and
    # the engine references it as opaque storage). Runtime type-checked
    # by callers that actually use the store.
    # P12-T3 (decisions/27 D30.8): per-turn JSON snapshot writer.
    # When True, the engine calls ``services.snapshot.write_session_snapshot``
    # at the end of each user turn (alongside the session_memory
    # writer; both share the same ``tool_metadata`` producer).
    # **Default False** here so unit tests constructing QueryContext
    # directly don't accidentally write snapshots to ~/.openharness/.
    # The CLI bootstrap opts in via ``settings.snapshot.enabled``
    # (default True in production).
    snapshot_enabled: bool = False
    snapshot_max_age_warn_days: int = 7
    # P13-T1 (D31.2): history/ rotation knobs passed through to
    # ``write_session_snapshot``. Defaults match
    # ``SnapshotHistorySettings`` defaults so QueryContext-direct
    # callers (unit tests) get the same behavior as CLI-bootstrapped
    # production (100 entries / 90 days).
    snapshot_history_max_count: int = 100
    snapshot_history_max_age_days: int = 90
    # P13-T3 (D31.7): opt-in LLM-authored task_focus_state. When
    # True, the engine awaits ``infer_focus_state`` at turn end
    # (after extract, before snapshot write) and stores the result
    # in ``tool_metadata.task_focus_state``. Default False preserves
    # Phase 12 zero-cost behavior (placeholder dict).
    # ``llm_focus_state_model`` overrides the model — None means
    # use the same as the main conversation.
    llm_focus_state_enabled: bool = False
    llm_focus_state_model: str | None = None

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        api_client: SupportsStreamingMessages,
        tool_registry: ToolRegistry,
        dispatch_tool_registry: ToolRegistry | None = None,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        reviewer_posture: ReviewerPosture = ReviewerPosture.MANUAL,
        execution_posture: ExecutionPosture = ExecutionPosture.EXECUTE,
        autonomous: bool = False,
        permission_checker: PermissionChecker,
        cwd: Path,
        action_deny_policy: ActionDenyPolicy | None = None,
        hook_registry: HookRegistry | None = None,
        execution_env: ExecutionEnvironment | None = None,
        sandbox_session: SandboxSession | None = None,
        runtime_permission_profile: RuntimePermissionProfile | None = None,
        enforced_boundary: EnforcedBoundary | None = None,
        permission_runtime: PermissionRuntime | None = None,
        external_tool_policy: ExternalToolPolicy | None = None,
        authorization_context: tuple[str, ...] = (),
        skill_store: SkillStore | None = None,
        memory_store: Any = None,
        session_memory_path: Path | None = None,
        snapshot_enabled: bool = False,
        snapshot_max_age_warn_days: int = 7,
        snapshot_history_max_count: int = 100,
        snapshot_history_max_age_days: int = 90,
        llm_focus_state_enabled: bool = False,
        llm_focus_state_model: str | None = None,
        compact_enabled: bool = True,
        compact_threshold_ratio: float = 0.83,
        compact_full_max_tokens: int = 20_000,
        compact_full_timeout_s: float = 25.0,
        max_turns: int = 20,
        max_agent_depth: int = 3,
    ) -> tuple[QueryContext, list[ConversationMessage]]:
        """P12-T4 (D30.7): rebuild a QueryContext from a snapshot dict.

        The split: AGENT-STATE (what the LLM saw) comes from
        ``snapshot``; RUNTIME-STATE (what the harness provides this
        invocation) is passed by the caller. The caller's job is to
        build a fresh registry / hooks / execution_env tree as if
        starting a new ``oh ask`` — then pass it in. We don't try to
        re-validate snapshot fields against the current registries
        (a tool removed since the snapshot is fine — the LLM may
        still reference it; engine's standard "tool not found"
        recovery handles).

        Loaded from snapshot:
        - ``model``, ``max_tokens``, ``system_prompt``, ``messages``

        The v1 ``permission_mode`` value is migration diagnostics only. Current
        reviewer/execution postures are caller-supplied runtime state.

        Caller-required runtime kwargs (no defaults — must reconstruct
        each invocation):
        - ``api_client`` / ``tool_registry`` / ``permission_checker``
          / ``cwd``

        Caller-optional runtime kwargs (sane defaults for tests):
        - everything else

        Returns ``(context, messages)``: ``messages`` is the parsed
        history ready to be passed as ``initial_messages`` to
        ``run_query`` (typically with one new user message appended
        for the resume's "next question").

        Raises:
            KeyError: required snapshot field missing.
            pydantic.ValidationError: snapshot's message blocks fail
                ``ConversationMessage.model_validate`` (indicates a
                snapshot from a different content-block schema).
        """
        from openharness.protocols.messages import ConversationMessage as _CM

        model = snapshot["model"]
        max_tokens = snapshot["max_tokens"]
        # v1 carried a single permission_mode. It is retained as migration
        # diagnostics only; the current invocation supplies both postures.
        snapshot.get("permission_mode")
        system_prompt = snapshot["system_prompt"]
        messages = [_CM.model_validate(m) for m in snapshot["messages"]]

        restored_permission_runtime = permission_runtime
        extra = snapshot.get("extra", {})
        runtime_state = extra.get("permission_runtime") if isinstance(extra, dict) else None
        if runtime_state is not None:
            if permission_runtime is None:
                raise ValueError(
                    "snapshot contains permission runtime state but no current runtime was provided"
                )
            from openharness.permissions import PermissionRuntime as _PermissionRuntime

            restored_permission_runtime = _PermissionRuntime.from_state(
                profile=permission_runtime.profile,
                boundary=permission_runtime.boundary,
                state=runtime_state,
                reviewer=permission_runtime.reviewer,
                denial_limit=permission_runtime.denial_limit,
            )

        context = cls(
            api_client=api_client,
            tool_registry=tool_registry,
            dispatch_tool_registry=dispatch_tool_registry,
            permission_checker=permission_checker,
            action_deny_policy=action_deny_policy,
            system_prompt=system_prompt,
            cwd=cwd,
            model=model,
            max_tokens=max_tokens,
            max_turns=max_turns,
            permission_mode=permission_mode,
            reviewer_posture=reviewer_posture,
            execution_posture=execution_posture,
            autonomous=autonomous,
            external_tool_policy=(
                external_tool_policy if external_tool_policy is not None else ExternalToolPolicy()
            ),
            authorization_context=authorization_context,
            max_agent_depth=max_agent_depth,
            hook_registry=hook_registry if hook_registry is not None else HookRegistry(),
            skill_store=skill_store if skill_store is not None else EmptySkillStore(),
            execution_env=execution_env if execution_env is not None else _HOST_EXECUTION,
            sandbox_session=sandbox_session,
            runtime_permission_profile=runtime_permission_profile,
            enforced_boundary=enforced_boundary,
            permission_runtime=restored_permission_runtime,
            compact_enabled=compact_enabled,
            compact_threshold_ratio=compact_threshold_ratio,
            compact_full_max_tokens=compact_full_max_tokens,
            compact_full_timeout_s=compact_full_timeout_s,
            session_memory_path=session_memory_path,
            memory_store=memory_store,
            snapshot_enabled=snapshot_enabled,
            snapshot_max_age_warn_days=snapshot_max_age_warn_days,
            snapshot_history_max_count=snapshot_history_max_count,
            snapshot_history_max_age_days=snapshot_history_max_age_days,
            llm_focus_state_enabled=llm_focus_state_enabled,
            llm_focus_state_model=llm_focus_state_model,
        )
        return context, messages
