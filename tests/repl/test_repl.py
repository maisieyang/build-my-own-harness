"""Tests for the interactive REPL input layer (``openharness.repl``).

The repl-ux plan (``tasks/repl-ux-plan.md``) adds a recognition layer to
``oh chat``: typing ``/`` pops a completion menu whose candidates merge
built-in REPL commands + CommandStore + SkillStore (same precedence as
the D38.1 dispatch order), plus a status bar showing model + context
usage while waiting for input. Four user-facing contracts:

1. **Candidate collection**: ``collect_slash_commands`` merges the three
   sources with built-in > command > skill precedence and tolerates
   absent stores (``no_commands`` passes ``None``).
2. **Completion gating**: the completer fires ONLY on a line that is a
   bare ``/``-prefixed word — never mid-sentence, never after a space —
   so natural-language input containing ``/`` is left alone.
3. **Status bar**: pure formatting of model + used/window tokens +
   auto-compact threshold; no new measurement paths.
4. **History path**: per-project, cwd-hashed, under user home — the
   same shape as snapshot directories.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.document import Document

from openharness.commands.model import Command
from openharness.repl import (
    BUILTIN_SLASH_COMMANDS,
    SlashCommand,
    SlashCompleter,
    collect_slash_commands,
    create_prompt_session,
    default_history_path,
    format_status_bar,
)
from openharness.skills.model import Skill

if TYPE_CHECKING:
    from prompt_toolkit.completion import Completion


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


class _DictStore:
    """Minimal CommandStore/SkillStore stand-in over a fixed dict."""

    def __init__(self, entries: dict[str, object]) -> None:
        self._entries = entries

    def discover(self) -> dict[str, object]:
        return dict(self._entries)

    def get(self, name: str) -> object | None:
        return self._entries.get(name)


def _command(name: str, description: str) -> Command:
    return Command(
        name=name,
        description=description,
        body="body",
        source_path=Path(f"/fake/{name}.md"),
    )


def _skill(name: str, description: str) -> Skill:
    return Skill(
        name=name,
        description=description,
        body="body",
        version=None,
        source_path=Path(f"/fake/{name}/SKILL.md"),
    )


def _complete(completer: SlashCompleter, text: str) -> list[Completion]:
    document = Document(text, cursor_position=len(text))
    return list(completer.get_completions(document, None))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 1. Candidate collection                                                     #
# --------------------------------------------------------------------------- #


class TestCollectSlashCommands:
    def test_builtins_always_present(self) -> None:
        commands = collect_slash_commands(command_store=None, skill_store=None)

        names = {c.name for c in commands}
        assert {"/help", "/clear", "/compact", "/exit"} <= names

    def test_command_store_entries_included_with_slash_prefix(self) -> None:
        store = _DictStore({"deploy": _command("deploy", "run the deploy checklist")})

        commands = collect_slash_commands(command_store=store, skill_store=None)

        by_name = {c.name: c for c in commands}
        assert by_name["/deploy"].description == "run the deploy checklist"

    def test_skill_store_entries_included_with_slash_prefix(self) -> None:
        store = _DictStore({"review": _skill("review", "structured code review")})

        commands = collect_slash_commands(command_store=None, skill_store=store)

        by_name = {c.name: c for c in commands}
        assert by_name["/review"].description == "structured code review"

    def test_precedence_builtin_over_command_over_skill(self) -> None:
        # Mirrors the D38.1 dispatch order: built-ins → CommandStore →
        # SkillStore. A name shadowed by a higher layer must show the
        # higher layer's description (that's what dispatch will run).
        command_store = _DictStore(
            {
                "clear": _command("clear", "command-store clear"),
                "dup": _command("dup", "command wins"),
            }
        )
        skill_store = _DictStore(
            {
                "dup": _skill("dup", "skill loses"),
                "unique-skill": _skill("unique-skill", "only in skills"),
            }
        )

        commands = collect_slash_commands(command_store=command_store, skill_store=skill_store)

        by_name = {c.name: c.description for c in commands}
        builtin_clear = next(c for c in BUILTIN_SLASH_COMMANDS if c.name == "/clear")
        assert by_name["/clear"] == builtin_clear.description
        assert by_name["/dup"] == "command wins"
        assert by_name["/unique-skill"] == "only in skills"


# --------------------------------------------------------------------------- #
# 2. Completion gating                                                        #
# --------------------------------------------------------------------------- #


class TestSlashCompleter:
    def _completer(self) -> SlashCompleter:
        return SlashCompleter(
            [
                SlashCommand("/compact", "compact the conversation"),
                SlashCommand("/clear", "reset history"),
                SlashCommand("/exit", "leave"),
            ]
        )

    def test_bare_slash_offers_everything(self) -> None:
        completions = _complete(self._completer(), "/")
        assert [c.text for c in completions] == ["/compact", "/clear", "/exit"]

    def test_prefix_narrows_candidates(self) -> None:
        completions = _complete(self._completer(), "/c")
        assert [c.text for c in completions] == ["/compact", "/clear"]

    def test_natural_language_is_left_alone(self) -> None:
        assert _complete(self._completer(), "explain a/b testing") == []

    def test_no_completion_after_args_begin(self) -> None:
        assert _complete(self._completer(), "/compact now") == []

    def test_completion_replaces_typed_prefix(self) -> None:
        completions = _complete(self._completer(), "/cl")
        assert completions[0].start_position == -3  # replaces "/cl" wholesale

    def test_completion_carries_description_meta(self) -> None:
        completions = _complete(self._completer(), "/exit")
        # display_meta is a FormattedText; to_plain_text keeps the assert
        # robust across prompt_toolkit versions.
        from prompt_toolkit.formatted_text import to_plain_text

        assert to_plain_text(completions[0].display_meta) == "leave"


# --------------------------------------------------------------------------- #
# 3. Status bar                                                               #
# --------------------------------------------------------------------------- #


class TestFormatStatusBar:
    def test_shows_model_usage_and_threshold(self) -> None:
        bar = format_status_bar(
            model="qwen3.7-max",
            used_tokens=13_107,
            context_window=262_144,
            threshold_ratio=0.83,
        )

        assert "qwen3.7-max" in bar
        assert "13.1k/262k" in bar
        assert "(5%)" in bar
        assert "auto-compact @83%" in bar

    def test_zero_usage_is_zero_percent(self) -> None:
        bar = format_status_bar(
            model="m", used_tokens=0, context_window=32_000, threshold_ratio=0.83
        )

        assert "0.0k/32k" in bar
        assert "(0%)" in bar

    def test_no_threshold_omits_compact_segment(self) -> None:
        bar = format_status_bar(
            model="m", used_tokens=100, context_window=32_000, threshold_ratio=None
        )

        assert "auto-compact" not in bar


# --------------------------------------------------------------------------- #
# 4. History path                                                             #
# --------------------------------------------------------------------------- #


class TestDefaultHistoryPath:
    def test_lives_under_home_openharness(self, tmp_path: Path) -> None:
        path = default_history_path(tmp_path)

        assert path.is_relative_to(Path.home() / ".openharness" / "chat-history")
        assert path.suffix == ".txt"

    def test_stable_for_same_cwd_distinct_for_different(self, tmp_path: Path) -> None:
        a = tmp_path / "proj-a"
        b = tmp_path / "proj-b"
        a.mkdir()
        b.mkdir()

        assert default_history_path(a) == default_history_path(a)
        assert default_history_path(a) != default_history_path(b)


# --------------------------------------------------------------------------- #
# 5. Session factory                                                          #
# --------------------------------------------------------------------------- #


class TestCreatePromptSession:
    def test_wires_completer_history_and_toolbar(self, tmp_path: Path) -> None:
        history_path = tmp_path / "nested" / "history.txt"

        session = create_prompt_session(
            commands=[SlashCommand("/exit", "leave")],
            history_path=history_path,
            status_provider=lambda: "STATUS-LINE",
        )

        assert isinstance(session.completer, SlashCompleter)
        assert history_path.parent.is_dir()  # parent created eagerly
        toolbar = session.bottom_toolbar
        assert callable(toolbar)
        assert toolbar() == "STATUS-LINE"
