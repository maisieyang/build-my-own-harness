"""Tests for ``_run_ask``'s cwd and verified sandbox-session overrides.

These two generic overrides are the mechanism `services/run_session.py`
uses to run one request in a worktree and optional sandbox without teaching
the agent engine about task isolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import openharness.cli as cli_module
from openharness.execution import (
    BoundaryVerification,
    EnforcedBoundary,
    ExecutionEffect,
    OneShotOverlaySession,
)
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent

_COMMON_RUN_ASK_KWARGS: dict[str, object] = {
    "model_override": None,
    "max_tokens": 8192,
    "reviewer_posture_override": None,
    "execution_posture_override": None,
    "log_level_override": None,
    "log_format_override": None,
    "tool_result_cap_override": None,
    "auto_truncate_override": None,
}


def _set_minimum_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")


def _hello_world_events(text: str = "hello") -> list[ApiStreamEvent]:
    return [
        ApiTextDeltaEvent(text=text),
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
            usage=UsageSnapshot(input_tokens=3, output_tokens=1),
            stop_reason="end_turn",
        ),
    ]


class _RecordingStubClient:
    def __init__(self, events: list[ApiStreamEvent]) -> None:
        self._events = events

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        for event in self._events:
            yield event


class _CapturedContext:
    """Holds the QueryContext + initial_messages ``_run_ask`` constructs
    (mirrors test_cli.py's own helper of the same name, plus messages --
    kept local per this repo's convention of small per-file test helpers
    rather than cross-file test imports)."""

    def __init__(self) -> None:
        self.context: object | None = None
        self.initial_messages: list[ConversationMessage] | None = None


def _patch_run_query_capture(monkeypatch: pytest.MonkeyPatch, captured: _CapturedContext) -> None:
    async def _capturing_run_query(
        initial_messages: list[ConversationMessage],
        context: object,
    ) -> AsyncIterator[ApiStreamEvent]:
        captured.initial_messages = initial_messages
        captured.context = context
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)


class TestCwdOverride:
    async def test_cwd_override_replaces_detected_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        await cli_module._run_ask("hi", cwd_override=tmp_path, **_COMMON_RUN_ASK_KWARGS)

        ctx = captured.context
        assert ctx.cwd == tmp_path  # type: ignore[attr-defined]

    async def test_no_cwd_override_uses_real_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        await cli_module._run_ask("hi", **_COMMON_RUN_ASK_KWARGS)

        ctx = captured.context
        assert ctx.cwd == Path.cwd()  # type: ignore[attr-defined]

    async def test_cwd_override_reaches_slash_command_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review fix: cwd_override must also reach FilesystemCommandStore's
        project_dir -- previously it only patched env.cwd, so command/
        bundle/hook-plugin discovery silently kept reading the real
        process cwd, breaking --isolate's isolation guarantee for those
        three subsystems."""
        _set_minimum_env(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        real_cwd = tmp_path / "real-cwd"
        real_cwd.mkdir()
        monkeypatch.chdir(real_cwd)

        override_dir = tmp_path / "worktree"
        override_dir.mkdir()
        commands_dir = override_dir / ".openharness" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "review.md").write_text(
            "---\nname: review\ndescription: Review pending changes\n---\n"
            "Please review:\n\n{args}\n",
            encoding="utf-8",
        )

        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        await cli_module._run_ask(
            "/review last 3 commits", cwd_override=override_dir, **_COMMON_RUN_ASK_KWARGS
        )

        assert captured.initial_messages is not None
        user_text = captured.initial_messages[-1].content[0].text  # type: ignore[union-attr]
        assert "Please review:" in user_text
        assert "last 3 commits" in user_text


class TestExecutionEnvironmentCompatibility:
    def test_run_ask_has_no_unverified_execution_environment_override(self) -> None:
        import inspect

        assert "execution_env_override" not in inspect.signature(cli_module._run_ask).parameters

    async def test_no_sandbox_falls_back_to_host_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openharness.execution.host import HostExecution

        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        await cli_module._run_ask("hi", **_COMMON_RUN_ASK_KWARGS)

        ctx = captured.context
        assert isinstance(ctx.execution_env, HostExecution)  # type: ignore[attr-defined]


class TestVerifiedSandboxSession:
    async def test_seatbelt_backend_is_opened_and_injected_into_query_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_minimum_env(monkeypatch)
        stub = _RecordingStubClient(_hello_world_events())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)
        captured = _CapturedContext()
        _patch_run_query_capture(monkeypatch, captured)

        class _Session:
            boundary = EnforcedBoundary(
                profile_fingerprint="a" * 64,
                backend="macos-seatbelt",
                backend_version="1",
                covered_effects=(
                    ExecutionEffect.COMMAND,
                    ExecutionEffect.FILE_READ,
                    ExecutionEffect.FILE_WRITE,
                    ExecutionEffect.FILE_SEARCH,
                ),
                verification=BoundaryVerification.VERIFIED,
            )

            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        session = _Session()
        opened_profiles: list[object] = []

        class _Backend:
            def __init__(self, *, cwd: Path) -> None:
                assert cwd == tmp_path

            def preflight(self, profile: object) -> object:
                del profile
                from openharness.execution import BackendSupport

                return BackendSupport.available(backend="macos-seatbelt")

            async def open(self, profile: object) -> _Session:
                opened_profiles.append(profile)
                session.boundary = EnforcedBoundary(  # type: ignore[misc]
                    profile_fingerprint=profile.fingerprint,  # type: ignore[attr-defined]
                    backend="macos-seatbelt",
                    backend_version="1",
                    covered_effects=(
                        ExecutionEffect.COMMAND,
                        ExecutionEffect.FILE_READ,
                        ExecutionEffect.FILE_WRITE,
                        ExecutionEffect.FILE_SEARCH,
                    ),
                    verification=BoundaryVerification.VERIFIED,
                )
                return session

        monkeypatch.setattr(cli_module, "SeatbeltBackend", _Backend)

        await cli_module._run_ask(
            "hi",
            cwd_override=tmp_path,
            sandbox_override=True,
            sandbox_backend_override="seatbelt",
            **_COMMON_RUN_ASK_KWARGS,
        )

        assert len(opened_profiles) == 1
        assert isinstance(
            captured.context.sandbox_session,
            OneShotOverlaySession,  # type: ignore[attr-defined]
        )
        assert captured.context.sandbox_session.boundary is session.boundary  # type: ignore[attr-defined]
        assert session.closed is True
