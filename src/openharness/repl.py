"""Interactive REPL input layer — recognition over recall.

The ``oh`` REPL has carried a full slash-command system since Phase 5b/18
(built-ins → CommandStore → SkillStore, D38.1), but discovery was gated
on recalling ``/help`` first. This module adds the *affordance*: typing
``/`` pops a completion menu of everything dispatchable, a persistent
input history survives across sessions, and a status toolbar shows the
model + context usage the compaction subsystem already measures.
Design record: ``tasks/repl-ux-plan.md``.

Split from ``cli.py`` so every piece is unit-testable without a TTY:
the only terminal-touching call (``PromptSession.prompt_async``) stays
in ``cli._run_chat``, gated by :func:`is_interactive`. Non-TTY runs
(pipes, CliRunner tests, CI) never construct a session and keep the
legacy ``input(">>> ")`` path byte-for-byte.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import Enum
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory

from openharness.tools import ExecutionDomain, ExternalEffectSurface, ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from openharness.execution import EnforcedBoundary
    from openharness.permissions import (
        ExternalToolPolicy,
        PermissionDeltaRequest,
        RuntimePermissionProfile,
    )
    from openharness.protocols.messages import ConversationMessage


@dataclass(frozen=True)
class SlashCommand:
    """One completion-menu candidate: display name (with leading ``/``)
    plus the description shown in the menu's meta column."""

    name: str
    description: str


class _NamedEntry(Protocol):
    """What the menu needs from a Command/Skill: name + description."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...


class _NamedEntryStore(Protocol):
    """Structural view of CommandStore/SkillStore: ``discover()`` maps
    name → an entry carrying ``name`` + ``description``."""

    def discover(self) -> Mapping[str, _NamedEntry]: ...


# Descriptions mirror _CHAT_HELP_TEXT — the menu is /help in popup form.
BUILTIN_SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "show available commands"),
    SlashCommand("/clear", "reset conversation history (keeps tools + mode)"),
    SlashCommand("/compact", "force full LLM-based compaction of the conversation"),
    SlashCommand(
        "/plan", "enter plan mode, optionally with a first prompt — read-only until approval"
    ),
    SlashCommand(
        "/goal", "set a session goal — an independent checker auto-continues turns until met"
    ),
    SlashCommand("/permissions", "show configured intent and verified runtime boundary"),
    SlashCommand("/approve", "approve a postponed exact permission request"),
    SlashCommand("/deny", "deny a postponed exact permission request"),
    SlashCommand("/resume", "consume an externally recorded permission decision"),
    SlashCommand("/skills", "list available skills"),
    SlashCommand("/memory", "list memories in this project's memory store"),
    SlashCommand("/exit", "leave the REPL"),
    SlashCommand("/quit", "leave the REPL"),
)


# --------------------------------------------------------------------------- #
# plan mode (D47) — state-machine primitives, pure + TTY-free                 #
# --------------------------------------------------------------------------- #


class ChatMode(Enum):
    """REPL posture: the ground state vs the plan-mode clamp (D47).

    Plan mode is a tool-catalog clamp, not authorization intent. It lives only
    in REPL memory; a dead session falls back to the ground state.
    """

    DEFAULT = "default"
    PLAN = "plan"


class PlanMenuChoice(Enum):
    """The approval-menu options (D47.2). Values are the input keys."""

    APPROVE = "1"
    KEEP_PLANNING = "2"
    DISCARD = "3"


class PermissionMenuChoice(Enum):
    """No-default decision for one exact parked request."""

    APPROVE = "1"
    DENY = "2"


PERMISSION_MENU_TEXT = """\
permission required — choose an exact one-shot decision
  [1] Approve once and continue
  [2] Deny and continue"""


def parse_permission_menu_choice(raw: str) -> PermissionMenuChoice | None:
    """Parse only explicit numbered decisions; blank is never approval."""
    value = raw.strip()
    for choice in PermissionMenuChoice:
        if value == choice.value:
            return choice
    return None


def shape_plan_tool_registry(base: ToolRegistry) -> ToolRegistry:
    """Return the model-visible plan catalog without widening authority.

    Plan mode exposes only read-only, non-delegated tools. The returned view
    contains the original tool instances so execution behavior stays
    identical, while the caller retains the full registry for forged-call
    detection at dispatch.
    """
    shaped = ToolRegistry()
    for tool in base.list_tools():
        if tool.is_read_only and tool.execution_domain is not ExecutionDomain.DELEGATED_RUNTIME:
            shaped.register(tool)
    return shaped


# Rendered after every completed assistant turn while in plan mode. A turn
# interrupted by a parked permission stays in Plan but has nothing to approve.
# Approval exits the read-only planning clamp; it does not auto-launch an
# execution turn. The next user message is the handoff point where they can
# refine the plan, ask for a goal-shaped condition, or start ``/goal``.
PLAN_MENU_TEXT = """\
plan mode — approve this plan?
  [1] yes, approve — return to default mode
  [2] no, keep planning
  [3] no, discard plan mode (back to default)"""

_PLAN_SENTINEL_PREFIX = "[plan-status] "


def build_plan_approval_sentinel() -> ConversationMessage:
    """Message-history marker for the post-plan handoff.

    The menu event is UI-owned, but the next model turn needs to know that
    the preceding plan was approved and that approval did not mean "execute
    now". This marker is context, not a queued turn.
    """
    from openharness.protocols.content import TextBlock
    from openharness.protocols.messages import ConversationMessage

    return ConversationMessage(
        role="user",
        content=[
            TextBlock(
                text=(
                    f"{_PLAN_SENTINEL_PREFIX}approved: The user approved the preceding "
                    "plan and returned to default mode. Do not execute the plan unless "
                    "the user explicitly asks. If the user asks for a /goal, convert "
                    "the approved plan into a concrete /goal condition with verification "
                    "criteria, runnable verification commands, and stop bounds."
                )
            )
        ],
    )


def parse_plan_menu_choice(raw: str) -> PlanMenuChoice | None:
    """Map raw menu input to a choice; ``None`` = invalid → caller re-asks."""
    stripped = raw.strip()
    for choice in PlanMenuChoice:
        if stripped == choice.value:
            return choice
    return None


# --------------------------------------------------------------------------- #
# session goal (D48) — 续跑式条件循环的原语层, pure + TTY-free                #
# --------------------------------------------------------------------------- #

# CC 同款清除别名(逆向 2.1.218:clear/stop/off/reset/none/cancel).
_GOAL_CLEAR_ALIASES = frozenset({"clear", "stop", "off", "reset", "none", "cancel"})

# D48.2 — 续跑消息的判官身份框架:进 history 的是 user 角色(API 只有
# user/assistant),但内容明确框定为 checker 反馈,不冒充用户口吻.
GOAL_FEEDBACK_PREFIX = "[goal checker] not met: "

# D48.9(dogfood 修正)— set 即开工:kickoff 指令框架.CC 同款语义
# ("treat the condition itself as your directive... immediately start
# working... do not pause to ask"),不等用户再输入.
GOAL_KICKOFF_PREFIX = "[goal set] "

# D48.7 — transcript 哨兵前缀:设定/达成/清除都以带标记消息落进 history,
# snapshot 自然持久化;resume 时扫描重建(对话流是唯一事实源,CC 同构).
_GOAL_SENTINEL_PREFIX = "[goal-status] "


@dataclass
class GoalState:
    """Active session goal (D48.1) — REPL-memory; counters reset on resume.

    Mutable on purpose: ``iterations``/``last_reason`` advance every judge
    round; ``tokens_at_start`` anchors the estimate-based spend report
    (CC's ``tokensAtStart`` twin, D48.6).
    """

    condition: str
    set_at: float
    tokens_at_start: int
    iterations: int = 0
    last_reason: str | None = None


@dataclass(frozen=True)
class GoalCommand:
    """Parsed ``/goal`` invocation: set (with condition) / show / clear."""

    action: str  # "set" | "show" | "clear"
    condition: str | None


def parse_goal_command(raw: str) -> GoalCommand:
    """Parse a ``/goal ...`` line (caller guarantees the ``/goal`` prefix).

    Bare ``/goal`` → show; a single clear-alias token → clear; anything
    else → set with the full remaining text as the condition. Alias match
    is exact-single-word only — "clearly document the API" is a condition.
    """
    rest = raw.strip().removeprefix("/goal").strip()
    if not rest:
        return GoalCommand(action="show", condition=None)
    if rest.lower() in _GOAL_CLEAR_ALIASES:
        return GoalCommand(action="clear", condition=None)
    return GoalCommand(action="set", condition=rest)


def goal_prompt_section(condition: str) -> str:
    """Turn-scoped system-prompt section while a goal is active (D48.8).

    Posture, not contract — the loop's authority is the independent judge.
    Mirrors CC's design note: evidence must land in the conversation for
    the checker to see, so the section tells the model to surface it.
    """
    return (
        "## Session goal\n\n"
        "An independent checker evaluates the conversation after each turn "
        "against this goal condition; incomplete work auto-continues while "
        "the task remains runnable:\n\n"
        f"    {condition}\n\n"
        "Work toward the condition and surface verifiable evidence in the "
        "conversation (e.g. actually run the relevant checks so their output "
        "is visible) — the checker judges only what appears here. If the goal "
        "names an exact verification command, only a successful result from that "
        "command satisfies it; alternatives are partial evidence. Do not treat "
        "self-selected examples as proof of an open-ended universal condition; "
        "obtain an authoritative exhaustive check or make the scope explicit. Automation "
        "pauses on permission or checker failures."
    )


def build_goal_kickoff(condition: str) -> str:
    """Kickoff directive launched the moment a goal is set (D48.9).

    CC's /goal injects "immediately start working toward it — treat the
    condition itself as your directive"; setting a goal that then waits
    for the user to speak is a dead condition (dogfood 2026-07-24)."""
    return (
        f"{GOAL_KICKOFF_PREFIX}Work toward this goal now: {condition}\n"
        "Treat the condition itself as your directive — briefly acknowledge "
        "it, then immediately start working toward it; do not pause to ask "
        "what to do. Surface verifiable evidence as you go. If the goal "
        "contains verification commands, run the commands as written when "
        "allowed. "
        "If Bash is permission-denied, report the exact blocker and the needed "
        "permission or sandbox setting. Do not create temporary files or scripts "
        "as a substitute for running a denied command."
    )


def build_goal_continuation(condition: str, feedback: str) -> str:
    """Continuation message after a not-met verdict (D48.2).

    Framed as checker feedback (CC's "Stop hook feedback" twin), carrying
    the judge's reason plus the condition so the next turn re-anchors."""
    return (
        f"{GOAL_FEEDBACK_PREFIX}{feedback}\n"
        f"Goal condition: {condition}\n"
        "Continue working toward the goal and surface verifiable evidence. If "
        "Bash is permission-denied, report the exact blocker and the needed "
        "permission or sandbox setting. Do not create temporary files or scripts "
        "as a substitute for running a denied command."
    )


def build_goal_sentinel(event: str, condition: str) -> ConversationMessage:
    """A goal-status sentinel message (``event`` ∈ set/met/cleared, D48.7)."""
    from openharness.protocols.content import TextBlock
    from openharness.protocols.messages import ConversationMessage

    return ConversationMessage(
        role="user",
        content=[TextBlock(text=f"{_GOAL_SENTINEL_PREFIX}{event}: {condition}")],
    )


def find_active_goal(messages: list[ConversationMessage]) -> str | None:
    """Scan history newest-first for the latest goal sentinel (D48.7).

    Latest "set" that isn't followed by a "met"/"cleared" → its condition;
    otherwise ``None``. Used by ``--resume`` to restore an active goal
    (counters reset, CC 同款)."""
    from openharness.protocols.content import TextBlock

    for message in reversed(messages):
        if message.role != "user":
            continue
        for block in message.content:
            if not isinstance(block, TextBlock):
                continue
            text = block.text
            if not text.startswith(_GOAL_SENTINEL_PREFIX):
                continue
            event, _, condition = text.removeprefix(_GOAL_SENTINEL_PREFIX).partition(": ")
            if event == "set":
                return condition
            return None  # met / cleared — goal extinguished
    return None


def goal_evidence_messages(
    messages: list[ConversationMessage], condition: str
) -> list[ConversationMessage]:
    """Return only evidence produced since the current goal was set.

    A completion controller must not let an unrelated earlier task satisfy a
    newly declared goal. The latest matching ``set`` sentinel is the durable
    boundary and survives snapshot/resume. Legacy or compacted histories that
    no longer contain the sentinel fall back to the available history so an
    already-running goal does not lose all evidence.
    """
    from openharness.protocols.content import TextBlock

    expected = f"{_GOAL_SENTINEL_PREFIX}set: {condition}"
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "user":
            continue
        if any(
            isinstance(block, TextBlock) and block.text == expected for block in message.content
        ):
            return messages[index:]
    return list(messages)


def is_interactive() -> bool:
    """True when both stdin and stdout are terminals.

    The gate for the prompt_toolkit path. Both streams matter: a piped
    stdin can't drive a menu, and a piped stdout would get ANSI control
    sequences smeared into its output.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def collect_slash_commands(
    command_store: _NamedEntryStore | None,
    skill_store: _NamedEntryStore | None,
) -> list[SlashCommand]:
    """Merge menu candidates in D38.1 dispatch order.

    Built-ins → CommandStore → SkillStore; on a name collision the
    higher layer wins because that is what dispatch will actually run —
    the menu must never advertise a shadowed entry's description.
    """
    merged: dict[str, SlashCommand] = {}
    for builtin in BUILTIN_SLASH_COMMANDS:
        merged[builtin.name] = builtin
    for store in (command_store, skill_store):
        if store is None:
            continue
        for entry in store.discover().values():
            name = f"/{entry.name}"
            if name not in merged:
                merged[name] = SlashCommand(name, entry.description)
    return list(merged.values())


class SlashCompleter(Completer):
    """Completion menu that fires ONLY on a bare ``/``-prefixed word.

    Natural language is the primary input mode; a ``/`` mid-sentence
    ("explain a/b testing") or arguments after the command name must
    never trigger the menu.
    """

    def __init__(self, commands: Iterable[SlashCommand]) -> None:
        self._commands = list(commands)

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        del complete_event
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for command in self._commands:
            if command.name.startswith(text):
                yield Completion(
                    command.name,
                    start_position=-len(text),
                    display_meta=command.description,
                )


def format_status_bar(
    *,
    model: str,
    used_tokens: int,
    context_window: int,
    threshold_ratio: float | None,
    mode: str | None = None,
    goal_active: bool = False,
) -> str:
    """Render the bottom-toolbar line: [mode] · model · context · threshold.

    Pure formatting over numbers the compaction subsystem already
    produces (``estimate_message_tokens`` / ``get_context_window``);
    ``threshold_ratio=None`` means auto-compact is off and the segment
    is omitted rather than shown as a dead value. ``mode=None`` (the
    ground state) omits the mode segment the same way — only a
    non-default posture (e.g. ``"plan"``) earns a marker.
    """
    percent = round(used_tokens / context_window * 100) if context_window else 0
    bar = f"{model} · ctx {used_tokens / 1000:.1f}k/{context_window // 1000}k ({percent}%)"
    if mode is not None:
        bar = f"[{mode}] {bar}"
    if goal_active:
        bar += " · ◎ goal"
    if threshold_ratio is not None:
        bar += f" · auto-compact @{threshold_ratio:.0%}"
    return bar


def format_permissions_status(
    *,
    profile: RuntimePermissionProfile | None,
    external_policy: ExternalToolPolicy,
    boundary: EnforcedBoundary | None,
    tool_domains: Mapping[ExecutionDomain, tuple[str, ...]],
    external_surfaces: Mapping[ExternalEffectSurface, tuple[str, ...]],
    mcp_server_postures: Mapping[str, str],
    trusted_control_status: Mapping[str, str],
    parked_request: PermissionDeltaRequest | None = None,
) -> str:
    """Render configured permission intent separately from enforced facts.

    Configured intent and installed enforcement are shown separately so the UI
    never implies that an unverified profile has become runtime authority.
    """
    lines = ["Configured intent"]
    if profile is None:
        lines.append("  canonical profile: not configured")
    else:
        lines.append(f"  canonical profile: {profile.name}")
        lines.append(f"  profile fingerprint: {profile.fingerprint[:12]}")
    lines.append(
        "  external policy (independent of local sandbox): "
        f"mcp={external_policy.mcp.value}, web={external_policy.web.value}, "
        f"browser={external_policy.browser.value}, "
        f"computer_use={external_policy.computer_use.value}"
    )

    lines.append("Installed facts")
    if boundary is None:
        lines.append("  verified boundary: none")
    else:
        covered = ", ".join(effect.value for effect in boundary.covered_effects) or "none"
        lines.append(
            f"  verified boundary: {boundary.backend} {boundary.backend_version} "
            f"({boundary.verification.value})"
        )
        lines.append(f"  covered effects: {covered}")

    lines.append("Tool execution domains")
    if not tool_domains:
        lines.append("  none registered")
    else:
        for domain, tools in sorted(tool_domains.items(), key=lambda item: item[0].value):
            lines.append(f"  {domain.value}: {', '.join(tools)}")
    lines.append("External surfaces")
    for surface in ExternalEffectSurface:
        tools = external_surfaces.get(surface, ())
        tool_status = f"tools={', '.join(tools)}" if tools else "not registered"
        mode = getattr(external_policy, surface.value).value
        lines.append(f"  {surface.value}: {mode}; {tool_status}; not covered by local sandbox")

    lines.append("stdio MCP process postures")
    if not mcp_server_postures:
        lines.append("  none configured")
    else:
        for name, posture in sorted(mcp_server_postures.items()):
            lines.append(f"  {name}: {posture}")

    lines.append("Trusted control plane (in-process host authority; not sandboxed)")
    for control_surface in ("hooks", "plugins"):
        lines.append(
            f"  {control_surface}: {trusted_control_status.get(control_surface, 'not declared')}"
        )
    if parked_request is not None:
        lines.append("Parked permission request")
        lines.append(f"  id: {parked_request.request_id}")
        lines.append(f"  tool: {parked_request.tool_name} ({parked_request.tool_use_id})")
        lines.append(
            "  final arguments: "
            + json.dumps(
                parked_request.final_arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        lines.append(f"  delta: {parked_request.delta.kind.value}={parked_request.delta.value}")
        lines.append(
            "  data flow: "
            f"{', '.join(parked_request.data_sources) or 'none'} -> "
            f"{', '.join(parked_request.data_destinations) or 'none'}"
        )
        lines.append(
            f"  boundary: {parked_request.backend} {parked_request.boundary_fingerprint[:12]}"
        )
    return "\n".join(lines)


def default_history_path(cwd: str | Path) -> Path:
    """Per-project input-history file, cwd-hashed under user home.

    Same shape as ``~/.openharness/snapshots`` and ``session-memory``
    (basename + sha1[:12]): history from one project never bleeds into
    another's up-arrow. ``Path.home()`` is evaluated at call time so the
    HOME-isolation test fixture takes effect.
    """
    resolved = Path(cwd).resolve()
    digest = sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".openharness" / "chat-history" / f"{resolved.name}-{digest}.txt"


def create_prompt_session(
    *,
    commands: Iterable[SlashCommand],
    history_path: Path,
    status_provider: Callable[[], str],
) -> PromptSession[str]:
    """Build the interactive session: menu + history + status toolbar.

    ``complete_while_typing`` makes the menu appear on the ``/``
    keystroke itself — the recognition moment — instead of waiting for
    an explicit Tab.
    """
    history_path.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        completer=SlashCompleter(commands),
        history=FileHistory(str(history_path)),
        bottom_toolbar=lambda: status_provider(),
        complete_while_typing=True,
    )
