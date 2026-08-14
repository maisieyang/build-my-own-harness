"""Tests for ``QueryContext.from_snapshot`` factory — P12-T4 (D30.7).

Verifies the agent-state / runtime-state split: snapshot loads
``model`` / ``max_tokens`` / ``system_prompt`` / ``messages`` from disk;
caller passes the canonical profile, registries, hooks,
execution_env / etc. fresh per invocation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from openharness.engine.context import QueryContext
from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    ExternalToolPolicy,
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionRuntime,
    workspace_runtime_profile,
)
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
)
from openharness.protocols.content import ToolResultBlock, ToolUseBlock
from openharness.services.snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    write_session_snapshot,
)
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api import SupportsStreamingMessages


class _StubApiClient:
    async def stream_message(self, request: object) -> object:
        del request
        raise NotImplementedError("not invoked in from_snapshot tests")


def _runtime_kwargs(cwd: Path) -> dict[str, object]:
    return {
        "api_client": cast("SupportsStreamingMessages", _StubApiClient()),
        "tool_registry": create_default_tool_registry(),
        "cwd": cwd,
    }


def _synthesize_snapshot(
    *,
    cwd: Path,
    model: str = "qwen-plus",
    system_prompt: str = "you are helpful",
    max_tokens: int = 1024,
    messages: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "version": SNAPSHOT_VERSION,
        "schema": SNAPSHOT_SCHEMA,
        "created_at": "2026-05-26T10:00:00Z",
        "git_head": None,
        "cwd": str(cwd),
        "model": model,
        "permission_profile_fingerprint": workspace_runtime_profile().fingerprint,
        "system_prompt": system_prompt,
        "max_tokens": max_tokens,
        "messages": messages or [],
        "tool_metadata": {"recent_files": [], "verified_work": [], "task_focus_state": {}},
        "extra": {},
    }


class TestFromSnapshotAgentState:
    def test_loads_model_and_tokens_but_not_runtime_posture(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(
            cwd=tmp_path,
            model="qwen-max",
            max_tokens=2048,
        )
        ctx, messages = QueryContext.from_snapshot(
            snap,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        assert ctx.model == "qwen-max"
        assert ctx.max_tokens == 2048
        assert messages == []

    def test_loads_system_prompt_verbatim(self, tmp_path: Path) -> None:
        # D30.2 contract: system_prompt is stored verbatim, NOT
        # re-rendered from registries. Resume preserves whatever the
        # agent saw at snapshot time.
        snap = _synthesize_snapshot(
            cwd=tmp_path,
            system_prompt="agent's exact prior prompt (skills A,B,C)",
        )
        ctx, _ = QueryContext.from_snapshot(
            snap,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        assert ctx.system_prompt == "agent's exact prior prompt (skills A,B,C)"


class TestFromSnapshotMessagesRoundTrip:
    def test_text_only_messages_parse(self, tmp_path: Path) -> None:
        original = [
            ConversationMessage(role="user", content=[TextBlock(text="hi")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="hello")]),
        ]
        snap = _synthesize_snapshot(
            cwd=tmp_path,
            messages=[m.model_dump(mode="json") for m in original],
        )
        _, messages = QueryContext.from_snapshot(
            snap,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        assert messages == original

    def test_tool_use_and_result_blocks_parse(self, tmp_path: Path) -> None:
        original = [
            ConversationMessage(role="user", content=[TextBlock(text="read x")]),
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="Read", input={"path": "/x"})],
            ),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="t1", content="ok")],
            ),
        ]
        snap = _synthesize_snapshot(
            cwd=tmp_path,
            messages=[m.model_dump(mode="json") for m in original],
        )
        _, messages = QueryContext.from_snapshot(
            snap,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        assert messages == original

    def test_real_write_then_load_then_from_snapshot_round_trip(self, tmp_path: Path) -> None:
        # Write a real snapshot file (T2's writer) → load_snapshot →
        # from_snapshot. Pins the end-to-end shape that --resume uses.
        from dataclasses import dataclass, field

        from openharness.services.snapshot import load_snapshot

        @dataclass
        class _Ctx:
            model: str = "qwen-plus"
            system_prompt: str | None = "system"
            max_tokens: int = 1024
            runtime_permission_profile: object = field(default_factory=workspace_runtime_profile)

        original = [
            ConversationMessage(role="user", content=[TextBlock(text="round trip")]),
        ]

        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=original,
            context=_Ctx(),  # type: ignore[arg-type]
        )

        loaded = load_snapshot(tmp_path)
        ctx, messages = QueryContext.from_snapshot(
            loaded,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        assert ctx.model == "qwen-plus"
        assert ctx.system_prompt == "system"
        assert messages == original


class TestFromSnapshotRuntimeKwargRequirements:
    def test_missing_required_runtime_kwarg_typeerrors(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(cwd=tmp_path)
        # API client remains required runtime state.
        rt = _runtime_kwargs(tmp_path)
        rt.pop("api_client")
        with pytest.raises(TypeError):
            QueryContext.from_snapshot(snap, **rt)  # type: ignore[arg-type]

    def test_optional_runtime_kwargs_default_sane(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(cwd=tmp_path)
        ctx, _ = QueryContext.from_snapshot(
            snap,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        # Defaults: empty hook_registry, EmptySkillStore, _HOST_EXECUTION,
        # memory_store=None. Compaction checkpoints are not runtime state.
        assert ctx.hook_registry.is_empty()
        assert ctx.memory_store is None
        assert not hasattr(ctx, "session_memory_path")
        assert ctx.snapshot_enabled is False


class TestFromSnapshotRuntimePosture:
    def test_current_runtime_postures_are_invocation_state(self, tmp_path: Path) -> None:
        from openharness.permissions import ExecutionPosture, ReviewerPosture

        snap = _synthesize_snapshot(cwd=tmp_path)
        ctx, _ = QueryContext.from_snapshot(
            snap,
            reviewer_posture=ReviewerPosture.AUTO,
            execution_posture=ExecutionPosture.EXECUTE,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        assert ctx.reviewer_posture is ReviewerPosture.AUTO
        assert ctx.execution_posture is ExecutionPosture.EXECUTE

    def test_profile_drift_fails_closed(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(cwd=tmp_path)
        snap["permission_profile_fingerprint"] = "different"
        with pytest.raises(ValueError, match="canonical profile"):
            QueryContext.from_snapshot(
                snap,
                **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
            )


class TestFromSnapshotPermissionRuntime:
    def test_restores_verified_runtime_state(self, tmp_path: Path) -> None:
        profile = workspace_runtime_profile()
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="test",
            backend_version="1",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
        )
        runtime = PermissionRuntime(profile=profile, boundary=boundary)
        snap = _synthesize_snapshot(cwd=tmp_path)
        snap["extra"] = {"permission_runtime": runtime.export_state().model_dump(mode="json")}

        context, _ = QueryContext.from_snapshot(
            snap,
            permission_runtime=runtime,
            runtime_permission_profile=profile,
            enforced_boundary=boundary,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )

        assert context.permission_runtime is not runtime
        assert context.permission_runtime is not None
        assert context.permission_runtime.boundary.fingerprint == boundary.fingerprint

    def test_refuses_permission_state_without_verified_runtime(self, tmp_path: Path) -> None:
        profile = workspace_runtime_profile()
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="test",
            backend_version="1",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
        )
        runtime = PermissionRuntime(profile=profile, boundary=boundary)
        snap = _synthesize_snapshot(cwd=tmp_path)
        snap["extra"] = {"permission_runtime": runtime.export_state().model_dump(mode="json")}

        with pytest.raises(ValueError, match="no current runtime"):
            QueryContext.from_snapshot(
                snap,
                **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
            )

    def test_restores_external_permission_state_without_local_boundary(
        self,
        tmp_path: Path,
    ) -> None:
        profile = workspace_runtime_profile().model_copy(
            update={"external_tools": ExternalToolPolicy(mcp="ask")}
        )
        runtime = PermissionRuntime(profile=profile, boundary=None)
        request = PermissionDeltaRequest.create_external(
            tool_use_id="tool-external",
            tool_name="Github.create_issue",
            final_arguments={"title": "exact issue"},
            profile=profile,
            policy=profile.external_tools,
            surface="mcp",
            effect_kind="mutating",
            trust_source="trusted-server",
            tool_identity="Github.create_issue",
            server_identity="Github",
            delta=PermissionDelta.external_tool("mcp"),
            crossing=BoundaryViolation(
                dimension="external.mcp",
                requested="Github.create_issue",
                evidence="mutating external effect",
            ),
        )
        runtime.park(request, reason="needs a person")
        snap = _synthesize_snapshot(cwd=tmp_path)
        snap["extra"] = {"permission_runtime": runtime.export_state().model_dump(mode="json")}

        context, _ = QueryContext.from_snapshot(
            snap,
            permission_runtime=runtime,
            runtime_permission_profile=profile,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )

        assert context.permission_runtime is not None
        assert context.permission_runtime.boundary is None
        assert context.permission_runtime.parked_request == request
