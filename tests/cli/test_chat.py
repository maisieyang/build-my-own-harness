"""Tests for ``oh chat`` — P6+-T2.

The REPL is driven by ``input()`` which we stub via monkeypatch.
``run_query`` is stubbed via the existing ``_patch_run_query_capture``
pattern so we capture the QueryContext + watch history grow across
turns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.execution import (
    BoundaryVerification,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ConversationCompleteEvent,
)
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.stream_events import ApiStreamEvent


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class _ChatStubClient:
    """Stand-in for the LLM client; just satisfies the iterator
    protocol so run_query doesn't crash."""

    async def stream_message(self, request: object) -> AsyncIterator[ApiStreamEvent]:
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ack")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _stub_input_sequence(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    """Replace builtins.input with one that returns successive lines.
    Raising EOFError after exhaustion (which the REPL treats as exit).
    """
    iterator = iter(lines)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    # ``input`` is called inside asyncio.to_thread, so patching the
    # builtin namespace is what reaches the call site.
    import builtins

    monkeypatch.setattr(builtins, "input", _next)


# --------------------------------------------------------------------------- #
# Exit paths                                                                  #
# --------------------------------------------------------------------------- #


class TestChatExit:
    def test_exit_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0

    def test_project_instructions_load_when_memory_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pathlib import Path

        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        (Path.cwd() / "AGENTS.md").write_text("Project-owned rules.\n", encoding="utf-8")
        prompts: list[str] = []

        async def _capture_prompt(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            prompts.append(context.system_prompt)  # type: ignore[attr-defined]
            assistant = ConversationMessage(role="assistant", content=[TextBlock(text="ok")])
            yield ApiMessageCompleteEvent(
                message=assistant,
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            yield ConversationCompleteEvent(messages=[*initial_messages, assistant])

        monkeypatch.setattr(cli_module, "run_query", _capture_prompt)
        _stub_input_sequence(monkeypatch, ["hello", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat", "--no-enable-memory"])

        assert result.exit_code == 0, result.stderr
        assert len(prompts) == 1
        assert "Project-owned rules." in prompts[0]
        assert "## Memory" not in prompts[0]

    def test_quit_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/quit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0

    def test_eof_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        # Empty input list → first call raises EOFError.
        _stub_input_sequence(monkeypatch, [])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0


# --------------------------------------------------------------------------- #
# Built-in slash commands                                                     #
# --------------------------------------------------------------------------- #


class TestBuiltinSlashCommands:
    def test_help_prints_commands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/help", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "/clear" in result.stdout
        assert "/exit" in result.stdout

    def test_clear_resets_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        # Three turns: greeting, /clear, follow-up. After /clear, the
        # third turn's request should NOT include the first turn's
        # message — we verify by inspecting captured run_query calls.
        captured_initial_messages: list[list[ConversationMessage]] = []

        async def _capturing_run_query(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            del context
            # Snapshot the list as seen at call time.
            captured_initial_messages.append(list(initial_messages))
            # Emit a minimal turn: complete + conversation event.
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            # The engine would emit ConversationCompleteEvent at end —
            # we simulate that here.
            yield ConversationCompleteEvent(
                messages=[
                    *initial_messages,
                    ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
                ]
            )

        monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)
        _stub_input_sequence(monkeypatch, ["hello", "/clear", "world", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # Two turns ran (first + third). The third turn's initial
        # messages contain ONLY the "world" message (clear erased prior).
        assert len(captured_initial_messages) == 2
        third_turn_messages = captured_initial_messages[1]
        # Should be exactly 1 user message ("world"), not accumulated history.
        assert len(third_turn_messages) == 1
        assert third_turn_messages[0].role == "user"


class TestVerifiedSandboxStatus:
    def test_permissions_reports_active_profile_and_verified_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        closed: list[bool] = []

        class _Backend:
            def __init__(self, **kwargs: object) -> None:
                pass

            async def open(self, profile: object) -> object:
                class _Session:
                    boundary = EnforcedBoundary(
                        profile_fingerprint=profile.fingerprint,  # type: ignore[attr-defined]
                        backend="macos-seatbelt",
                        backend_version="test",
                        covered_effects=(
                            ExecutionEffect.COMMAND,
                            ExecutionEffect.FILE_READ,
                            ExecutionEffect.FILE_WRITE,
                            ExecutionEffect.FILE_SEARCH,
                        ),
                        verification=BoundaryVerification.VERIFIED,
                    )

                    async def close(self) -> None:
                        closed.append(True)

                return _Session()

        monkeypatch.setattr(cli_module, "SeatbeltBackend", _Backend)
        _stub_input_sequence(monkeypatch, ["/permissions", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat", "--sandbox"])

        assert result.exit_code == 0, result.stderr
        assert "canonical profile: workspace" in result.stdout
        assert "verified boundary: macos-seatbelt test (verified)" in result.stdout
        assert "covered effects: command, file_read, file_write, file_search" in result.stdout
        assert closed == [True]

    def test_one_sandbox_session_is_reused_across_chat_turns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        opened = 0
        session_ids: list[int] = []

        class _Backend:
            def __init__(self, **kwargs: object) -> None:
                pass

            async def open(self, profile: object) -> object:
                nonlocal opened
                opened += 1

                class _Session:
                    boundary = EnforcedBoundary(
                        profile_fingerprint=profile.fingerprint,  # type: ignore[attr-defined]
                        backend="macos-seatbelt",
                        backend_version="test",
                        covered_effects=(
                            ExecutionEffect.COMMAND,
                            ExecutionEffect.FILE_READ,
                            ExecutionEffect.FILE_WRITE,
                            ExecutionEffect.FILE_SEARCH,
                        ),
                        verification=BoundaryVerification.VERIFIED,
                    )

                    async def close(self) -> None:
                        pass

                return _Session()

        async def _capture_session(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            session_ids.append(id(context.sandbox_session))  # type: ignore[attr-defined]
            assistant = ConversationMessage(role="assistant", content=[TextBlock(text="ok")])
            yield ApiMessageCompleteEvent(
                message=assistant,
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            yield ConversationCompleteEvent(messages=[*initial_messages, assistant])

        monkeypatch.setattr(cli_module, "SeatbeltBackend", _Backend)
        monkeypatch.setattr(cli_module, "run_query", _capture_session)
        _stub_input_sequence(monkeypatch, ["one", "two", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat", "--sandbox"])

        assert result.exit_code == 0, result.stderr
        assert opened == 1
        assert len(session_ids) == 2
        assert session_ids[0] == session_ids[1]


# --------------------------------------------------------------------------- #
# Multi-turn history accumulation                                             #
# --------------------------------------------------------------------------- #


class TestMultiTurnHistory:
    def test_history_grows_across_turns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        captured_initial_messages: list[list[ConversationMessage]] = []

        async def _capturing_run_query(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            del context
            captured_initial_messages.append(list(initial_messages))
            assistant = ConversationMessage(role="assistant", content=[TextBlock(text="reply")])
            yield ApiMessageCompleteEvent(
                message=assistant,
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            yield ConversationCompleteEvent(messages=[*initial_messages, assistant])

        monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)
        _stub_input_sequence(monkeypatch, ["first prompt", "second prompt", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # First turn sees 1 user message; second turn sees the full
        # history (user + assistant + user).
        assert len(captured_initial_messages) == 2
        assert len(captured_initial_messages[0]) == 1
        assert len(captured_initial_messages[1]) == 3
        # Roles in turn 2: user, assistant, user.
        roles = [m.role for m in captured_initial_messages[1]]
        assert roles == ["user", "assistant", "user"]


# --------------------------------------------------------------------------- #
# REPL recovery paths — continue back to prompt instead of breaking the loop. #
# --------------------------------------------------------------------------- #


class TestReplRecoveryPaths:
    def test_keyboard_interrupt_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ctrl-C at the prompt prints a hint and re-prompts (no exit)."""
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        # First input() raises KeyboardInterrupt, second returns /exit.
        call_count = [0]

        def _next(prompt: str = "") -> str:
            del prompt
            call_count[0] += 1
            if call_count[0] == 1:
                raise KeyboardInterrupt
            return "/exit"

        import builtins

        monkeypatch.setattr(builtins, "input", _next)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # The "(use /exit to quit)" hint is rendered to stdout, not stderr.
        assert "use /exit to quit" in result.stdout

    def test_empty_input_continues_without_invoking_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty / whitespace-only input re-prompts; no LLM round-trip."""
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        called: list[int] = []

        async def _run_query_spy(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            del context
            called.append(1)
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="x")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )
            yield ConversationCompleteEvent(
                messages=[
                    *initial_messages,
                    ConversationMessage(role="assistant", content=[TextBlock(text="x")]),
                ]
            )

        monkeypatch.setattr(cli_module, "run_query", _run_query_spy)
        _stub_input_sequence(monkeypatch, ["", "   ", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # Two empty inputs + /exit → 0 LLM calls.
        assert called == []

    def test_unknown_slash_command_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``/<name>`` with no matching command file prints + continues."""
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        # Bare HOME (root conftest sets it to an empty isolation dir) →
        # no ~/.openharness/commands → store yields nothing.
        _stub_input_sequence(monkeypatch, ["/nonexistent_command", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Unknown command" in combined

    def test_loop_error_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A LoopError during ``run_query`` is caught and the prompt re-enters."""
        from openharness.errors import LoopError

        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        async def _raising_run_query(
            initial_messages: list[ConversationMessage], context: object
        ) -> AsyncIterator[ApiStreamEvent]:
            del initial_messages, context
            if False:  # never executes; satisfies async-generator shape
                yield  # pragma: no cover
            raise LoopError("synthetic loop limit hit")

        monkeypatch.setattr(cli_module, "run_query", _raising_run_query)
        _stub_input_sequence(monkeypatch, ["please", "/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Loop error" in combined
        assert "synthetic loop limit hit" in combined


# --------------------------------------------------------------------------- #
# chat() entry-point flag validation                                          #
# --------------------------------------------------------------------------- #


class TestChatFlagValidation:
    def test_auto_and_dry_run_mutually_exclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--auto`` and ``--dry-run`` together fails fast with exit 2."""
        _set_minimum_env(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat", "--auto", "--dry-run"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "mutually exclusive" in combined
