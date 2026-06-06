"""``oh chat`` slash-skill resolver tests — Phase 18 T2.

Covers D38.1 (resolver order) / D38.2 (envelope shape) / D38.4
(``/skills`` built-in) / D38.5 (observability forcing function +
hook+permission bypass by construction).

Layout: each test stubs ``input()`` to drive the REPL, stubs
``run_query`` so we capture the conversation history visible to the
engine at LLM-call time, and stubs ``_build_client``. Skill files
land under ``$HOME/.openharness/skills/`` per D38.7 (single-file
schema).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.engine.slash_skill import SYNTH_ID_PREFIX
from openharness.protocols.content import (
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
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
# Local helpers — mirror tests/cli/test_chat.py conventions                   #
# --------------------------------------------------------------------------- #


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


def _write_skill(home_dir: Path, name: str, description: str, body: str) -> Path:
    skills_dir = home_dir / ".openharness" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skills_dir / f"{name}.md"
    skill_path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return skill_path


def _write_command(home_dir: Path, name: str, body: str) -> Path:
    cmd_dir = home_dir / ".openharness" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    cmd_path = cmd_dir / f"{name}.md"
    cmd_path.write_text(
        f"---\nname: {name}\ndescription: a command\n---\n{body}",
        encoding="utf-8",
    )
    return cmd_path


def _home(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The root conftest stashes the isolation HOME in env — read it back."""
    import os

    return Path(os.environ["HOME"])


def _make_capturing_run_query(
    captured: list[list[ConversationMessage]],
) -> object:
    async def _capturing_run_query(
        initial_messages: list[ConversationMessage], context: object
    ) -> AsyncIterator[ApiStreamEvent]:
        del context
        captured.append(list(initial_messages))
        assistant = ConversationMessage(role="assistant", content=[TextBlock(text="reply")])
        yield ApiMessageCompleteEvent(
            message=assistant,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )
        yield ConversationCompleteEvent(messages=[*initial_messages, assistant])

    return _capturing_run_query


# --------------------------------------------------------------------------- #
# D38.4 — /skills built-in                                                    #
# --------------------------------------------------------------------------- #


class TestSkillsBuiltinCommand:
    def test_empty_catalog_prints_no_skills_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/skills", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "(no skills installed)" in result.stdout

    def test_non_empty_catalog_lists_alphabetical_with_descriptions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_skill(home, "zebra-skill", "alphabetically last", "body z")
        _write_skill(home, "alpha-skill", "alphabetically first", "body a")
        _stub_input_sequence(monkeypatch, ["/skills", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # Alphabetical: alpha appears before zebra in output
        alpha_idx = result.stdout.find("alpha-skill")
        zebra_idx = result.stdout.find("zebra-skill")
        assert alpha_idx != -1
        assert zebra_idx != -1
        assert alpha_idx < zebra_idx
        # Descriptions render next to the name
        assert "alphabetically first" in result.stdout
        assert "alphabetically last" in result.stdout

    def test_help_text_mentions_slash_skills(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        _stub_input_sequence(monkeypatch, ["/help", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "/skills" in result.stdout


# --------------------------------------------------------------------------- #
# D38.1 — resolver order (built-in → cmd → skill → unknown)                   #
# --------------------------------------------------------------------------- #


class TestResolverOrder:
    def test_skill_hit_injects_envelope_into_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_skill(home, "my-skill", "test skill", "EXPERT_BODY_TOKEN")

        captured: list[list[ConversationMessage]] = []
        monkeypatch.setattr(cli_module, "run_query", _make_capturing_run_query(captured))
        _stub_input_sequence(monkeypatch, ["/my-skill task arg", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert len(captured) == 1

        history = captured[0]
        # D38.2 envelope = 3 messages (args non-empty)
        assert len(history) == 3
        # Msg 0: assistant tool_use(LoadSkill) with synth_ id
        assert history[0].role == "assistant"
        tool_use = history[0].content[0]
        assert isinstance(tool_use, ToolUseBlock)
        assert tool_use.name == "LoadSkill"
        assert tool_use.input == {"name": "my-skill"}
        assert tool_use.id.startswith(SYNTH_ID_PREFIX)
        # Msg 1: user tool_result carrying skill body verbatim
        assert history[1].role == "user"
        tool_result = history[1].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert "EXPERT_BODY_TOKEN" in tool_result.content
        assert tool_result.tool_use_id == tool_use.id
        # Msg 2: user TextBlock carrying args verbatim
        assert history[2].role == "user"
        args_block = history[2].content[0]
        assert isinstance(args_block, TextBlock)
        assert args_block.text == "task arg"

    def test_skill_hit_without_args_yields_two_message_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_skill(home, "noargs", "test skill", "body content")

        captured: list[list[ConversationMessage]] = []
        monkeypatch.setattr(cli_module, "run_query", _make_capturing_run_query(captured))
        _stub_input_sequence(monkeypatch, ["/noargs", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        history = captured[0]
        # D38.2 envelope = 2 messages (args empty) — no trailing TextBlock
        assert len(history) == 2
        assert isinstance(history[0].content[0], ToolUseBlock)
        assert isinstance(history[1].content[0], ToolResultBlock)

    def test_command_hit_uses_phase_5b_path_not_synth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When CommandStore has the name, take Phase 5b expand path,
        NOT the synth envelope path — preserves D24.3 contract.
        """
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_command(home, "review", "Review this code: {args}")

        captured: list[list[ConversationMessage]] = []
        monkeypatch.setattr(cli_module, "run_query", _make_capturing_run_query(captured))
        _stub_input_sequence(monkeypatch, ["/review snippet here", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        history = captured[0]
        # Command path: a single user TextBlock with substituted body —
        # no assistant tool_use envelope precedes it.
        assert len(history) == 1
        assert history[0].role == "user"
        text = history[0].content[0]
        assert isinstance(text, TextBlock)
        assert text.text == "Review this code: snippet here"

    def test_collision_command_beats_skill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """D38.1 priority: same name in CommandStore + SkillStore → command wins."""
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_command(home, "review", "CMD_PATH: {args}")
        _write_skill(home, "review", "skill description", "SKILL_BODY")

        captured: list[list[ConversationMessage]] = []
        monkeypatch.setattr(cli_module, "run_query", _make_capturing_run_query(captured))
        _stub_input_sequence(monkeypatch, ["/review xyz", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        history = captured[0]
        # Command path → 1 user text message; the skill envelope (3-msg
        # shape with tool_use/tool_result) must NOT appear.
        assert len(history) == 1
        text = history[0].content[0]
        assert isinstance(text, TextBlock)
        assert text.text == "CMD_PATH: xyz"
        # And the skill body never reached the LLM
        all_content = " ".join(
            getattr(b, "text", "") + getattr(b, "content", "") for m in history for b in m.content
        )
        assert "SKILL_BODY" not in all_content

    def test_unknown_slash_with_close_skill_name_suggests_difflib(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_skill(home, "parse-credit-report", "central-bank report", "body")
        _stub_input_sequence(monkeypatch, ["/parse-credit-reort", "/exit"])  # typo

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Unknown command" in combined
        assert "Did you mean a skill?" in combined
        assert "parse-credit-report" in combined

    def test_unknown_slash_with_no_close_skill_skips_suggestion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        # No skills installed, no commands installed.
        _stub_input_sequence(monkeypatch, ["/totally_unrelated_thing", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Unknown command" in combined
        # No "Did you mean" line when nothing close enough exists
        assert "Did you mean" not in combined


# --------------------------------------------------------------------------- #
# D38.5 — observability forcing function + bypass-by-construction             #
# --------------------------------------------------------------------------- #


class TestObservabilityAndBypass:
    def test_slash_skill_invoked_event_emitted_with_synthetic_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """D38.5 forcing function: synth path emits an INFO event marked
        ``synthetic=true`` so hook authors / auditors can distinguish UI
        actions from LLM-driven LoadSkill calls.

        ``configure_logging()`` rewires the stdlib root handler set, which
        defeats pytest's ``caplog``. Capture structlog output via the CLI's
        JSON stderr stream (``--log-format json --log-level INFO``).
        """
        import json

        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_skill(home, "my-skill", "test", "body")

        captured: list[list[ConversationMessage]] = []
        monkeypatch.setattr(cli_module, "run_query", _make_capturing_run_query(captured))

        _stub_input_sequence(monkeypatch, ["/my-skill 申请号12345", "/exit"])
        result = CliRunner().invoke(
            cli_module.app,
            ["chat", "--log-level", "INFO", "--log-format", "json"],
        )
        assert result.exit_code == 0

        synth_events: list[dict[str, object]] = []
        for line in (result.stderr or "").splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("event") == "slash_skill_invoked":
                synth_events.append(payload)

        assert synth_events, (
            f"slash_skill_invoked event not found in stderr; stderr={result.stderr!r}"
        )
        event = synth_events[0]
        assert event.get("skill_name") == "my-skill"
        assert event.get("synthetic") is True
        # D38.5: audit needs payload, not just event name. args_length lets
        # auditors filter "skill invoked w/ args" vs "skill invoked alone".
        assert event.get("args_length") == len("申请号12345")

    def test_synth_path_does_not_execute_load_skill_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bypass-by-construction: D38.5 says synth envelope does NOT call
        LoadSkillTool.execute. We verify by patching the tool's execute
        method to a sentinel that records calls; after /<skill> dispatch,
        the recorder must be empty. Hooks + permissions sit on top of
        tool execution — by skipping that machinery entirely, the synth
        path can't touch them.
        """
        _set_minimum_env(monkeypatch)
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _ChatStubClient())
        home = _home(monkeypatch)
        _write_skill(home, "my-skill", "test", "body")

        import openharness.tools.load_skill as load_skill_module

        call_count: list[int] = []
        original_execute = load_skill_module.LoadSkillTool.execute

        async def _spy_execute(self: object, input_data: object, context: object) -> object:
            call_count.append(1)
            return await original_execute(self, input_data, context)  # type: ignore[arg-type]

        monkeypatch.setattr(load_skill_module.LoadSkillTool, "execute", _spy_execute)

        captured: list[list[ConversationMessage]] = []
        monkeypatch.setattr(cli_module, "run_query", _make_capturing_run_query(captured))
        _stub_input_sequence(monkeypatch, ["/my-skill", "/exit"])

        result = CliRunner().invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        # D38.5: LoadSkillTool.execute must NOT have been called on the synth path
        assert call_count == []
