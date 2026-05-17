"""Tests for ``expand_command`` + ``UnknownCommandError`` — P5b-T2.

P5d-T4 adds tests for :func:`resolve_command_invocation` (returns the
expanded prompt + the resolved Command so callers can inspect
``Command.mode`` for bundle loading).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openharness.commands import (
    Command,
    CommandStore,
    EmptyCommandStore,
    UnknownCommandError,
    expand_command,
    resolve_command_invocation,
)

if TYPE_CHECKING:
    from pathlib import Path


class _InMemoryStore:
    """Test double:hold a dict of commands directly. Satisfies the
    :class:`CommandStore` Protocol structurally — no inheritance needed.
    Saves filesystem I/O in the expand tests where the focus is on
    substitution logic, not discovery.
    """

    def __init__(self, commands: dict[str, Command]) -> None:
        self._commands = commands

    def discover(self) -> dict[str, Command]:
        return dict(self._commands)

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)


def _make_command(name: str, body: str, *, tmp_path: Path, mode: str | None = None) -> Command:
    return Command(
        name=name,
        description=f"{name} command",
        body=body,
        source_path=tmp_path / f"{name}.md",
        mode=mode,
    )


def _store_with(commands: list[Command]) -> CommandStore:
    return _InMemoryStore({c.name: c for c in commands})


class TestPassThrough:
    """No leading ``/`` → prompt returned unchanged."""

    def test_regular_prompt_unchanged(self) -> None:
        result = expand_command("just a regular prompt", EmptyCommandStore())
        assert result == "just a regular prompt"

    def test_empty_prompt_unchanged(self) -> None:
        assert expand_command("", EmptyCommandStore()) == ""

    def test_prompt_with_slash_in_middle_unchanged(self) -> None:
        # Only LEADING ``/`` triggers expansion. ``"http://x"`` etc. flow through.
        prompt = "check this URL http://example.com/path"
        assert expand_command(prompt, EmptyCommandStore()) == prompt


class TestArgsSubstitution:
    """Body contains ``{args}`` → substituted in place."""

    def test_basic_substitution(self, tmp_path: Path) -> None:
        cmd = _make_command("review", "Review:\n\n{args}\n", tmp_path=tmp_path)
        store = _store_with([cmd])
        result = expand_command("/review last 3 commits", store)
        assert "{args}" not in result
        assert "last 3 commits" in result
        assert "Review:" in result

    def test_empty_args_substitutes_empty_string(self, tmp_path: Path) -> None:
        cmd = _make_command("plan", "Make a plan for:\n{args}\n", tmp_path=tmp_path)
        store = _store_with([cmd])
        result = expand_command("/plan", store)
        # ``{args}`` replaced with empty string — no placeholder remains.
        assert "{args}" not in result
        assert "Make a plan for:" in result

    def test_multiple_placeholders_all_substituted(self, tmp_path: Path) -> None:
        cmd = _make_command("x", "First: {args}\nSecond: {args}\n", tmp_path=tmp_path)
        store = _store_with([cmd])
        result = expand_command("/x hello", store)
        assert result.count("hello") == 2
        assert "{args}" not in result

    def test_args_with_special_chars_substituted_verbatim(self, tmp_path: Path) -> None:
        # Args may contain curly braces, quotes, JSON, code — must not
        # be interpreted as format specifiers (we use str.replace, not
        # str.format).
        cmd = _make_command("debug", "Examine:\n{args}\n", tmp_path=tmp_path)
        store = _store_with([cmd])
        tricky = '{"key": "value", "list": [1, 2]}'
        result = expand_command(f"/debug {tricky}", store)
        assert tricky in result

    def test_body_with_curly_brace_content_preserved(self, tmp_path: Path) -> None:
        # Body may contain examples with curly braces (Python dicts,
        # JSON snippets). Only literal ``{args}`` is the placeholder;
        # other curly content stays verbatim.
        cmd = _make_command(
            "explain",
            "Example: {{'key': 'value'}}\nUser asks: {args}\n",
            tmp_path=tmp_path,
        )
        store = _store_with([cmd])
        result = expand_command("/explain what is a dict", store)
        # The ``{{'key': 'value'}}`` literal stays untouched.
        assert "{{'key': 'value'}}" in result
        assert "what is a dict" in result


class TestTailAppendFallback:
    """Body without ``{args}`` placeholder + non-empty args → appended."""

    def test_args_appended_on_new_line(self, tmp_path: Path) -> None:
        cmd = _make_command(
            "fixed",
            "Static instructions here.\n",
            tmp_path=tmp_path,
        )
        store = _store_with([cmd])
        result = expand_command("/fixed extra detail", store)
        assert result.endswith("extra detail")
        assert "Static instructions here." in result
        # Tail-append preserves the structure:body, newline, args.
        assert result == "Static instructions here.\nextra detail"

    def test_empty_args_no_append(self, tmp_path: Path) -> None:
        cmd = _make_command("fixed", "Just static body\n", tmp_path=tmp_path)
        store = _store_with([cmd])
        result = expand_command("/fixed", store)
        assert result == "Just static body\n"

    def test_body_without_trailing_newline_adds_one(self, tmp_path: Path) -> None:
        # Body without trailing ``\n`` + args → separator inserted so
        # args don't run into the last line.
        cmd = _make_command("x", "line1\nline2", tmp_path=tmp_path)
        store = _store_with([cmd])
        result = expand_command("/x more", store)
        assert result == "line1\nline2\nmore"


class TestUnknownCommand:
    def test_unknown_name_raises(self) -> None:
        store = EmptyCommandStore()
        with pytest.raises(UnknownCommandError) as exc_info:
            expand_command("/nonexistent some args", store)
        assert exc_info.value.name == "nonexistent"
        assert exc_info.value.available == []

    def test_unknown_name_message_includes_catalog(self, tmp_path: Path) -> None:
        store = _store_with(
            [
                _make_command("alpha", "a body", tmp_path=tmp_path),
                _make_command("zebra", "z body", tmp_path=tmp_path),
            ]
        )
        with pytest.raises(UnknownCommandError) as exc_info:
            expand_command("/middle args", store)
        message = str(exc_info.value)
        assert "middle" in message
        assert "alpha" in message
        assert "zebra" in message

    def test_unknown_with_no_available_shows_none(self) -> None:
        store = EmptyCommandStore()
        with pytest.raises(UnknownCommandError) as exc_info:
            expand_command("/something", store)
        assert "(none)" in str(exc_info.value)

    def test_slash_alone_is_unknown_command(self) -> None:
        # ``"/"`` with nothing after splits to name="" — store.get("") is
        # None — raise UnknownCommandError. Defensive: user typos shouldn't
        # crash the loop.
        store = EmptyCommandStore()
        with pytest.raises(UnknownCommandError) as exc_info:
            expand_command("/", store)
        assert exc_info.value.name == ""


class TestResolveCommandInvocation:
    """P5d-T4: ``resolve_command_invocation`` returns ``(prompt, Command|None)``."""

    def test_passthrough_returns_none_command(self, tmp_path: Path) -> None:
        prompt, command = resolve_command_invocation("regular prompt", EmptyCommandStore())
        assert prompt == "regular prompt"
        assert command is None

    def test_invocation_returns_command(self, tmp_path: Path) -> None:
        cmd = _make_command(
            "review", "Please review:\n{args}\n", tmp_path=tmp_path, mode="code-review"
        )
        store = _store_with([cmd])

        prompt, command = resolve_command_invocation("/review last commit", store)
        assert prompt == "Please review:\nlast commit\n"
        assert command is cmd
        assert command.mode == "code-review"

    def test_invocation_no_mode_field(self, tmp_path: Path) -> None:
        cmd = _make_command("plain", "body", tmp_path=tmp_path)  # mode=None
        store = _store_with([cmd])

        _prompt, command = resolve_command_invocation("/plain args", store)
        assert command is not None
        assert command.mode is None

    def test_unknown_raises(self, tmp_path: Path) -> None:
        store = EmptyCommandStore()
        with pytest.raises(UnknownCommandError):
            resolve_command_invocation("/missing args", store)
