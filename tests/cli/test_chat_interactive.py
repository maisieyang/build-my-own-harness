"""Tests for the TTY-gated interactive input branch of ``oh chat``
(repl-ux plan §2/§3 — 识别层 + 状态行).

Contract under test: when stdin+stdout are a real terminal,
``_run_chat`` reads input through a prompt_toolkit session created by
``openharness.repl.create_prompt_session`` (slash completion menu +
persistent history + status toolbar). When either stream is NOT a TTY
— every CliRunner test, every pipe, every CI run — the legacy
``input(">>> ")`` path is used unchanged. The gate lives in
``openharness.repl.is_interactive`` so both halves are patchable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typer.testing import CliRunner

import openharness.cli as cli_module
import openharness.repl as repl_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.stream_events import ApiStreamEvent


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


class _ChatStubClient:
    async def stream_message(self, request: object) -> AsyncIterator[ApiStreamEvent]:
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ack")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class _FakePromptSession:
    """Stands in for prompt_toolkit's PromptSession; feeds scripted lines."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)
        self.prompts_seen: list[str] = []

    async def prompt_async(self, prompt: str = "") -> str:
        self.prompts_seen.append(prompt)
        try:
            return next(self._lines)
        except StopIteration as exc:
            raise EOFError from exc


class TestInteractiveBranch:
    def test_tty_input_goes_through_prompt_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        monkeypatch.setattr(repl_module, "is_interactive", lambda: True)

        fake_session = _FakePromptSession(["/exit"])
        factory_kwargs: dict[str, Any] = {}

        def _fake_factory(**kwargs: Any) -> _FakePromptSession:
            factory_kwargs.update(kwargs)
            return fake_session

        monkeypatch.setattr(repl_module, "create_prompt_session", _fake_factory)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
        assert fake_session.prompts_seen == [">>> "]

    def test_session_factory_receives_commands_history_and_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        monkeypatch.setattr(repl_module, "is_interactive", lambda: True)

        factory_kwargs: dict[str, Any] = {}

        def _fake_factory(**kwargs: Any) -> _FakePromptSession:
            factory_kwargs.update(kwargs)
            return _FakePromptSession(["/exit"])

        monkeypatch.setattr(repl_module, "create_prompt_session", _fake_factory)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
        # Candidates include the built-ins (stores may add more).
        names = {c.name for c in factory_kwargs["commands"]}
        assert {"/help", "/exit", "/compact"} <= names
        # History file is the per-project path.
        assert factory_kwargs["history_path"].suffix == ".txt"
        # Status provider is live and reflects the configured model.
        status = factory_kwargs["status_provider"]()
        assert "qwen3.7-max" in status

    def test_non_tty_keeps_legacy_input_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())

        # CliRunner stdin is not a TTY — is_interactive() must be False
        # without patching, and create_prompt_session must never run.
        def _boom(**kwargs: Any) -> None:
            raise AssertionError("create_prompt_session called on non-TTY path")

        monkeypatch.setattr(repl_module, "create_prompt_session", _boom)

        import builtins

        lines = iter(["/exit"])

        def _next(prompt: str = "") -> str:
            try:
                return next(lines)
            except StopIteration as exc:
                raise EOFError from exc

        monkeypatch.setattr(builtins, "input", _next)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])

        assert result.exit_code == 0
