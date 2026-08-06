from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import openharness.cli as cli_module
from openharness.execution import (
    BoundaryVerification,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionReviewDecision,
    workspace_runtime_profile,
)
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationCompleteEvent,
    ConversationMessage,
    PermissionParkedEvent,
    TextBlock,
    UsageSnapshot,
)
from openharness.services.goal_judge import GoalJudgeResult, GoalJudgeVerdict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.engine import QueryContext
    from openharness.protocols import ApiStreamEvent


class _Client:
    async def stream_message(self, request: object) -> AsyncIterator[ApiStreamEvent]:
        del request
        raise AssertionError("run_query is stubbed")
        yield  # pragma: no cover


class _Session:
    def __init__(self) -> None:
        self.profile = workspace_runtime_profile()
        self.boundary = EnforcedBoundary(
            profile_fingerprint=self.profile.fingerprint,
            backend="test",
            backend_version="1",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
        )

    async def close(self) -> None:
        return None


def _inputs(monkeypatch: pytest.MonkeyPatch, values: list[str]) -> None:
    iterator = iter(values)

    def _input(prompt: str = "") -> str:
        del prompt
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr("builtins.input", _input)


@pytest.mark.parametrize(
    ("command", "decision"),
    [
        ("/approve", PermissionReviewDecision.APPROVE),
        ("/deny", PermissionReviewDecision.DENY),
    ],
)
def test_park_decision_and_explicit_resume_are_durable_and_skip_judge_until_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
    decision: PermissionReviewDecision,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENHARNESS_SANDBOX_ENABLED", "true")
    monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _Client())
    session = _Session()

    async def _open(**kwargs: object) -> tuple[object, _Session]:
        del kwargs
        return session.profile, session

    monkeypatch.setattr(cli_module, "_open_sandbox_session", _open)
    run_calls = 0

    async def _run(
        initial_messages: list[ConversationMessage], context: QueryContext
    ) -> AsyncIterator[ApiStreamEvent]:
        nonlocal run_calls
        run_calls += 1
        assistant = ConversationMessage(
            role="assistant", content=[TextBlock(text="permission lifecycle")]
        )
        messages = [*initial_messages, assistant]
        if run_calls == 1:
            assert context.permission_runtime is not None
            request = PermissionDeltaRequest.create(
                tool_use_id="tool-1",
                tool_name="WebFetch",
                final_arguments={"url": "https://example.com"},
                profile=context.permission_runtime.profile,
                boundary=context.permission_runtime.boundary,
                delta=PermissionDelta.external_tool("web"),
            )
            context.permission_runtime.park(request, reason="owner decision needed")
            yield PermissionParkedEvent(
                request_id=request.request_id,
                tool_use_id=request.tool_use_id,
                tool_name=request.tool_name,
                delta_kind=request.delta.kind.value,
                delta_value=request.delta.value,
                profile_fingerprint=request.profile_fingerprint,
                boundary_fingerprint=request.boundary_fingerprint,
                reason="owner decision needed",
                messages=messages,
            )
            return
        yield ApiMessageCompleteEvent(
            message=assistant,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )
        yield ConversationCompleteEvent(messages=messages)

    monkeypatch.setattr(cli_module, "run_query", _run)
    judge_calls = 0

    async def _judge(*args: object, **kwargs: object) -> GoalJudgeResult:
        nonlocal judge_calls
        del args, kwargs
        judge_calls += 1
        return GoalJudgeResult(verdict=GoalJudgeVerdict.MET, reason="resumed")

    monkeypatch.setattr(cli_module, "judge_goal_completion", _judge)
    persisted: list[PermissionReviewDecision | None] = []

    def _persist(*, cwd: object, runtime: object) -> None:
        del cwd
        persisted.append(runtime.last_human_decision)  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "openharness.services.snapshot.update_permission_runtime_snapshot", _persist
    )
    _inputs(monkeypatch, ["/goal finish", command, "/resume", "/exit"])

    result = CliRunner().invoke(cli_module.app, ["chat"])

    assert result.exit_code == 0
    assert "goal blocked on permission" in result.stdout
    assert "use /resume" in result.stdout
    assert run_calls == 2
    assert judge_calls == 1
    assert persisted == [decision]


def _snapshot_with_runtime(runtime: object) -> dict[str, object]:
    return {
        "version": 1,
        "schema": "openharness.snapshot.v1",
        "created_at": "2026-08-05T12:00:00+00:00",
        "git_head": None,
        "cwd": str(Path.cwd()),
        "model": "qwen-plus",
        "permission_mode": "default",
        "system_prompt": "test",
        "max_tokens": 1024,
        "messages": [],
        "tool_metadata": {},
        "extra": {
            "permission_runtime": runtime.export_state().model_dump(mode="json")  # type: ignore[attr-defined]
        },
    }


def test_chat_resume_restores_permission_state_under_same_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENHARNESS_SANDBOX_ENABLED", "true")
    monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _Client())
    session = _Session()
    from openharness.permissions import PermissionRuntime

    runtime = PermissionRuntime(profile=session.profile, boundary=session.boundary)
    monkeypatch.setattr(
        cli_module,
        "_load_resume_snapshot",
        lambda *a, **kw: _snapshot_with_runtime(runtime),
    )

    async def _open(**kwargs: object) -> tuple[object, _Session]:
        del kwargs
        return session.profile, session

    monkeypatch.setattr(cli_module, "_open_sandbox_session", _open)
    _inputs(monkeypatch, ["/exit"])

    result = CliRunner().invoke(cli_module.app, ["chat", "--resume"])

    assert result.exit_code == 0
    assert "resumed: 0 messages" in result.stdout


def test_chat_resume_refuses_boundary_drift_and_missing_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr(cli_module, "_build_client", lambda _settings: _Client())
    session = _Session()
    from openharness.permissions import PermissionRuntime

    runtime = PermissionRuntime(profile=session.profile, boundary=session.boundary)
    snapshot = _snapshot_with_runtime(runtime)
    monkeypatch.setattr(cli_module, "_load_resume_snapshot", lambda *a, **kw: snapshot)

    missing = CliRunner().invoke(cli_module.app, ["chat", "--resume"])
    assert missing.exit_code == 1
    assert "no verified sandbox boundary" in missing.stderr

    monkeypatch.setenv("OPENHARNESS_SANDBOX_ENABLED", "true")
    drifted = _Session()
    drifted.boundary = EnforcedBoundary(
        profile_fingerprint=drifted.profile.fingerprint,
        backend="test",
        backend_version="2",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )

    async def _open(**kwargs: object) -> tuple[object, _Session]:
        del kwargs
        return drifted.profile, drifted

    monkeypatch.setattr(cli_module, "_open_sandbox_session", _open)
    drift = CliRunner().invoke(cli_module.app, ["chat", "--resume"])
    assert drift.exit_code == 1
    assert "boundary drift" in drift.stderr
