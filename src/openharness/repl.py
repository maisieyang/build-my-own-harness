"""Interactive REPL input layer — recognition over recall.

``oh chat`` has carried a full slash-command system since Phase 5b/18
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

import sys
from dataclasses import dataclass
from enum import Enum
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory

from openharness.permissions.rules import (
    PermissionRules,
    accept_edits_preset,
    plan_mode_preset,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document


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
    SlashCommand("/plan", "enter plan mode — read-only exploration until you approve a plan"),
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

    Deliberately NOT a :class:`~openharness.permissions.PermissionMode` value —
    plan mode is a rules-preset overlay ("收编为规则预设" stance, D47.1), and
    it lives only in REPL memory (D47.7: not persisted; a dead session falls
    back to the ground state).
    """

    DEFAULT = "default"
    PLAN = "plan"


class PlanMenuChoice(Enum):
    """The four approval-menu options (D47.2). Values are the input keys."""

    APPROVE_MANUAL = "1"
    APPROVE_ACCEPT_EDITS = "2"
    KEEP_PLANNING = "3"
    DISCARD = "4"


# Rendered by the harness after every assistant turn while in plan mode
# (D47.5: no plan-presented detection in v1 — option 3 covers "not ready").
# The approve options name their landing permission mode (follow CC:
# "选项即落地模式", D47.3).
PLAN_MENU_TEXT = """\
plan mode — approve this plan?
  [1] yes, approve — execute with edits reviewed as usual (default permissions)
  [2] yes, approve — auto-accept edits for the execution turn
  [3] no, keep planning
  [4] no, discard plan mode (back to default)"""


# Injected as the auto-launched execution turn after approval (D47.3):
# approval must start execution, not silently flip permissions and wait.
PLAN_APPROVAL_MESSAGE = "The plan has been approved. Execute the plan you presented now."


def parse_plan_menu_choice(raw: str) -> PlanMenuChoice | None:
    """Map raw menu input to a choice; ``None`` = invalid → caller re-asks."""
    stripped = raw.strip()
    for choice in PlanMenuChoice:
        if stripped == choice.value:
            return choice
    return None


def overlay_plan_permissions(
    base: PermissionRules,
    *,
    mode: ChatMode,
    grant: PlanMenuChoice | None,
) -> PermissionRules:
    """Turn-scoped permission overlay for the plan state machine.

    - ``mode=PLAN`` → append :func:`plan_mode_preset` to ``deny`` (the clamp;
      engine precedence deny > allow keeps it authoritative over any allow).
    - ``grant=APPROVE_ACCEPT_EDITS`` → append :func:`accept_edits_preset` to
      ``allow`` for the one execution turn (D47.4: the caller consumes the
      grant so it never outlives that turn).
    - Anything else → ``base`` unchanged (identity, so the ground state stays
      byte-for-byte on the pre-plan code path).
    """
    if mode is ChatMode.PLAN:
        return base.model_copy(update={"deny": (*base.deny, *plan_mode_preset())})
    if grant is PlanMenuChoice.APPROVE_ACCEPT_EDITS:
        return base.model_copy(update={"allow": (*base.allow, *accept_edits_preset())})
    return base


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
    if threshold_ratio is not None:
        bar += f" · auto-compact @{threshold_ratio:.0%}"
    return bar


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
