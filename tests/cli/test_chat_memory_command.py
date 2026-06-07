"""``/memory`` REPL built-in tests — UX hotfix mirroring D38.4 ``/skills``.

Read-only catalog list inside ``oh chat`` so users don't have to leave
the REPL to inspect what the LLM is reading from the memory store.
Output schema matches ``oh memory list`` (3 columns: name / type /
description) — D37.4.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module

if TYPE_CHECKING:
    import pytest


# --------------------------------------------------------------------------- #
# Helpers — minimal CC-style memory frontmatter (D36.10 / Phase 17 T1)        #
# --------------------------------------------------------------------------- #


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _project_memory_dir() -> Path:
    """Resolve the per-project memory dir the same way the chat REPL does.

    Per ``openharness.memory.paths.get_project_memory_dir``, the dir is
    ``~/.openharness/memory/<basename>-<sha1(resolved_cwd)[:12]>/``.
    Root conftest sets ``HOME`` and chdirs into isolation tmpdirs per
    test so this resolves cleanly into the per-test isolation home.
    """
    from openharness.memory.paths import get_project_memory_dir

    return get_project_memory_dir(Path(os.getcwd()))


def _write_memory(name: str, description: str, body: str = "body", mem_type: str = "user") -> Path:
    """Write a CC-style memory frontmatter file (D36.10 schema)."""
    dir_ = _project_memory_dir()
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\nmetadata:\n"
        f"  node_type: memory\n  type: {mem_type}\n---\n{body}",
        encoding="utf-8",
    )
    return path


def _stub_input_sequence(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    iterator = iter(lines)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    import builtins

    monkeypatch.setattr(builtins, "input", _next)


class _ChatStubClient:
    """LLM stand-in — never reached; /memory is read-only and returns
    before any LLM call."""

    async def stream_message(self, request: object) -> object:  # pragma: no cover
        raise AssertionError("/memory must not invoke the LLM client")


# --------------------------------------------------------------------------- #
# Empty store + missing subsystem                                             #
# --------------------------------------------------------------------------- #


class TestMemoryBuiltinEmpty:
    def test_no_memories_yet_when_store_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/memory", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "(no memories yet)" in result.stdout

    def test_memory_subsystem_disabled_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # --no-memory flips the memory_store wiring off → /memory should
        # tell the user the subsystem isn't running, not silently echo
        # "(no memories yet)" which would be misleading.
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/memory", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat", "--no-enable-memory"])
        assert result.exit_code == 0
        assert "(memory subsystem disabled)" in result.stdout


# --------------------------------------------------------------------------- #
# Non-empty rendering — alphabetical, 3 columns matching oh memory list       #
# --------------------------------------------------------------------------- #


class TestMemoryBuiltinRendering:
    def test_multi_entry_alphabetical_three_columns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _write_memory("zebra-pref", "alphabetically last", mem_type="user")
        _write_memory("alpha-pref", "alphabetically first", mem_type="feedback")
        _stub_input_sequence(monkeypatch, ["/memory", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # Alphabetical: alpha appears before zebra.
        alpha_idx = result.stdout.find("alpha-pref")
        zebra_idx = result.stdout.find("zebra-pref")
        assert alpha_idx != -1
        assert zebra_idx != -1
        assert alpha_idx < zebra_idx
        # Both descriptions render
        assert "alphabetically first" in result.stdout
        assert "alphabetically last" in result.stdout
        # Type column shows the discriminator
        assert "user" in result.stdout
        assert "feedback" in result.stdout

    def test_help_text_mentions_slash_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/help", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "/memory" in result.stdout
