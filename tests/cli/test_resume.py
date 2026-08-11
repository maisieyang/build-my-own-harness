"""Tests for ``oh ask --resume`` / ``oh chat --resume`` — P12-T5.

Covers:

1. ``oh ask --resume "follow-up"`` loads snapshot + appends prompt
2. ``oh ask "fresh"`` (no flag) ignores snapshot
3. ``--resume-id <prefix>`` selects by git-HEAD prefix
4. ``--resume-id <bad>`` exits 1
5. ``--resume`` without snapshot → warn + start fresh
6. ``--help`` mentions new flags on both ask + chat
7. ``oh chat --resume`` prints banner
8. Snapshot cwd mismatch (synthesized) → exit 1
9. Snapshot version > supported → exit 1
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.services.snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    get_snapshot_dir,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


def _set_min_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
    # Snapshot writes are noisy in stub tests; disable for these tests
    # unless explicitly enabled per-test.
    monkeypatch.setenv("OPENHARNESS_SNAPSHOT__ENABLED", "false")


class _RecordingStub:
    """Stub api_client recording the last request. Returns end_turn."""

    def __init__(self) -> None:
        self.last_request: ApiMessageRequest | None = None
        self.calls = 0

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        self.calls += 1
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def _write_snapshot(
    cwd: Path,
    *,
    messages: list[dict[str, object]] | None = None,
    cwd_field: str | None = None,
    version: int = SNAPSHOT_VERSION,
    git_head: str | None = "abc1234",
) -> Path:
    """Hand-author a snapshot file at the resolved snapshot path for ``cwd``.

    Used instead of write_session_snapshot for tests that need to
    synthesize specific schema variants (wrong cwd, future version,
    etc.) — write_session_snapshot would put the *current* cwd in
    the cwd field.
    """
    snapshot_dir = get_snapshot_dir(cwd)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "schema": SNAPSHOT_SCHEMA,
        "created_at": "2026-05-26T14:00:00Z",
        "git_head": git_head,
        "cwd": cwd_field if cwd_field is not None else str(cwd.resolve()),
        "model": "qwen-plus",
        "permission_mode": "default",
        "system_prompt": "prior system prompt",
        "max_tokens": 1024,
        "messages": messages or [],
        "tool_metadata": {"recent_files": [], "verified_work": [], "task_focus_state": {}},
        "extra": {},
    }
    path = snapshot_dir / "current.json"
    path.write_text(json.dumps(payload))
    return path


# --------------------------------------------------------------------------- #
# `oh ask --resume`                                                           #
# --------------------------------------------------------------------------- #


class TestAskResumeHappyPath:
    def test_resume_loads_snapshot_and_appends_prompt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_min_env(monkeypatch)
        # Snapshot has 2 prior messages (user + assistant)
        prior = [
            ConversationMessage(role="user", content=[TextBlock(text="prior")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="reply")]),
        ]
        # Snapshot under the per-test isolated cwd
        import os as _os

        cwd_iso = _os.getcwd()
        from pathlib import Path as _P

        _write_snapshot(
            _P(cwd_iso),
            messages=[m.model_dump(mode="json") for m in prior],
        )

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "--resume", "follow-up"])
        assert result.exit_code == 0, result.stderr
        # First (and only) LLM request includes prior history + new prompt
        assert stub.last_request is not None
        assert len(stub.last_request.messages) == 3
        # Last message is the new user prompt
        last = stub.last_request.messages[-1]
        assert last.role == "user"
        first_block = last.content[0]
        from openharness.protocols.content import TextBlock as _TB

        assert isinstance(first_block, _TB)
        assert first_block.text == "follow-up"

    def test_ask_without_resume_ignores_snapshot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Snapshot exists but no --resume flag → fresh single-message request
        _set_min_env(monkeypatch)
        import os as _os
        from pathlib import Path as _P

        prior = [ConversationMessage(role="user", content=[TextBlock(text="prior")])]
        _write_snapshot(
            _P(_os.getcwd()),
            messages=[m.model_dump(mode="json") for m in prior],
        )

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "fresh"])
        assert result.exit_code == 0, result.stderr
        assert stub.last_request is not None
        # Only the fresh prompt — no prior history
        assert len(stub.last_request.messages) == 1


class TestAskResumeWithoutSnapshot:
    def test_resume_no_snapshot_warns_and_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "--resume", "fresh"])
        # Exit 0 (warn, don't error)
        assert result.exit_code == 0
        assert "starting fresh" in result.stderr.lower()
        # Falls through to fresh-session path with just the new prompt
        assert stub.last_request is not None
        assert len(stub.last_request.messages) == 1

    def test_resume_id_no_match_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        import os as _os
        from pathlib import Path as _P

        # Snapshot exists with git_head="abc1234"; user asks for "ffff"
        _write_snapshot(_P(_os.getcwd()), git_head="abc1234")

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "--resume-id", "ffff", "follow-up"])
        assert result.exit_code == 1
        assert "ffff" in result.stderr or "id=" in result.stderr.lower()


class TestAskResumeStaleness:
    def test_cwd_mismatch_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        import os as _os
        from pathlib import Path as _P

        # Snapshot says it belongs to another project
        _write_snapshot(_P(_os.getcwd()), cwd_field="/some/other/project")

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "--resume", "follow-up"])
        assert result.exit_code == 1
        assert "cwd" in result.stderr.lower()

    def test_version_too_new_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        import os as _os
        from pathlib import Path as _P

        _write_snapshot(_P(_os.getcwd()), version=999)

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "--resume", "follow-up"])
        assert result.exit_code == 1
        assert "version" in result.stderr.lower()


class TestAskResumeIdImpliesResume:
    def test_resume_id_alone_loads_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        import os as _os
        from pathlib import Path as _P

        prior = [ConversationMessage(role="user", content=[TextBlock(text="prior")])]
        _write_snapshot(
            _P(_os.getcwd()),
            messages=[m.model_dump(mode="json") for m in prior],
            git_head="abc1234",
        )

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)

        runner = CliRunner()
        # No --resume flag, just --resume-id
        result = runner.invoke(cli_module.headless_app, ["run", "--resume-id", "abc", "follow-up"])
        assert result.exit_code == 0, result.stderr
        assert stub.last_request is not None
        # Prior + new prompt
        assert len(stub.last_request.messages) == 2


# --------------------------------------------------------------------------- #
# `oh chat --resume` (REPL banner)                                            #
# --------------------------------------------------------------------------- #


def _stub_chat_inputs(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    it = iter(lines)

    def _next(prompt: str = "") -> str:
        del prompt
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError from exc

    import builtins

    monkeypatch.setattr(builtins, "input", _next)


class TestChatResumeBanner:
    def test_chat_resume_prints_banner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        import os as _os
        from pathlib import Path as _P

        prior = [
            ConversationMessage(role="user", content=[TextBlock(text="prior 1")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="prior 2")]),
            ConversationMessage(role="user", content=[TextBlock(text="prior 3")]),
        ]
        _write_snapshot(
            _P(_os.getcwd()),
            messages=[m.model_dump(mode="json") for m in prior],
            git_head="abc1234",
        )

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
        _stub_chat_inputs(monkeypatch, ["/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat", "--resume"])
        assert result.exit_code == 0
        assert "resumed:" in result.stdout
        assert "3 messages" in result.stdout
        assert "abc1234" in result.stdout

    def test_chat_no_resume_no_banner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_min_env(monkeypatch)
        # No snapshot, no --resume → no banner
        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _s: stub)
        _stub_chat_inputs(monkeypatch, ["/exit"])

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat"])
        assert result.exit_code == 0
        assert "resumed:" not in result.stdout


# --------------------------------------------------------------------------- #
# --help mentions the new flags                                               #
# --------------------------------------------------------------------------- #


class TestHelpMentionsResume:
    def test_ask_help_lists_resume_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Wide terminal so the flags don't wrap; strip ANSI because under
        # force_terminal (CI sets GITHUB_ACTIONS) Rich styles the help text.
        monkeypatch.setenv("COLUMNS", "200")
        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
        assert "--resume" in plain
        assert "--resume-id" in plain

    def test_chat_help_lists_resume_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLUMNS", "200")
        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["chat", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
        assert "--resume" in plain
        assert "--resume-id" in plain
