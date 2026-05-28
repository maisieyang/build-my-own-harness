"""Tests for ``QueryContext.from_snapshot`` factory — P12-T4 (D30.7).

Verifies the agent-state / runtime-state split: snapshot loads
``model`` / ``max_tokens`` / ``permission_mode`` / ``system_prompt``
/ ``messages`` from disk; caller passes registries / hooks /
execution_env / etc. fresh per invocation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from openharness.engine.context import QueryContext
from openharness.permissions import PermissionMode
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


class _AllowAllChecker:
    def evaluate(self, *_a: object, **_kw: object) -> object:
        from openharness.permissions import DecisionResult

        return DecisionResult.allow()


def _runtime_kwargs(cwd: Path) -> dict[str, object]:
    return {
        "api_client": cast("SupportsStreamingMessages", _StubApiClient()),
        "tool_registry": create_default_tool_registry(),
        "permission_checker": _AllowAllChecker(),
        "cwd": cwd,
    }


def _synthesize_snapshot(
    *,
    cwd: Path,
    model: str = "qwen-plus",
    permission_mode: str = "default",
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
        "permission_mode": permission_mode,
        "system_prompt": system_prompt,
        "max_tokens": max_tokens,
        "messages": messages or [],
        "tool_metadata": {"recent_files": [], "verified_work": [], "task_focus_state": {}},
        "extra": {},
    }


class TestFromSnapshotAgentState:
    def test_loads_model_max_tokens_permission_mode(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(
            cwd=tmp_path,
            model="qwen-max",
            permission_mode="auto",
            max_tokens=2048,
        )
        ctx, messages = QueryContext.from_snapshot(
            snap,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        assert ctx.model == "qwen-max"
        assert ctx.max_tokens == 2048
        assert ctx.permission_mode == PermissionMode.AUTO
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
        from dataclasses import dataclass

        from openharness.services.snapshot import load_snapshot

        @dataclass
        class _Ctx:
            model: str = "qwen-plus"
            permission_mode: PermissionMode = PermissionMode.DEFAULT
            system_prompt: str | None = "system"
            max_tokens: int = 1024

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
        assert ctx.permission_mode == PermissionMode.DEFAULT
        assert ctx.system_prompt == "system"
        assert messages == original


class TestFromSnapshotRuntimeKwargRequirements:
    def test_missing_required_runtime_kwarg_typeerrors(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(cwd=tmp_path)
        # Drop ``permission_checker`` from runtime kwargs → TypeError
        rt = _runtime_kwargs(tmp_path)
        rt.pop("permission_checker")
        with pytest.raises(TypeError):
            QueryContext.from_snapshot(snap, **rt)  # type: ignore[arg-type]

    def test_optional_runtime_kwargs_default_sane(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(cwd=tmp_path)
        ctx, _ = QueryContext.from_snapshot(
            snap,
            **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
        )
        # Defaults: empty hook_registry, EmptySkillStore, _HOST_EXECUTION,
        # memory_store=None, session_memory_path=None
        assert ctx.hook_registry.is_empty()
        assert ctx.memory_store is None
        assert ctx.session_memory_path is None
        assert ctx.snapshot_enabled is False


class TestFromSnapshotPermissionModeRoundTrip:
    def test_each_mode_round_trips(self, tmp_path: Path) -> None:
        for mode_value in ("default", "auto", "dry_run"):
            snap = _synthesize_snapshot(cwd=tmp_path, permission_mode=mode_value)
            ctx, _ = QueryContext.from_snapshot(
                snap,
                **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
            )
            assert ctx.permission_mode == PermissionMode(mode_value)

    def test_invalid_permission_mode_raises(self, tmp_path: Path) -> None:
        snap = _synthesize_snapshot(cwd=tmp_path, permission_mode="not_a_mode")
        with pytest.raises(ValueError):
            QueryContext.from_snapshot(
                snap,
                **_runtime_kwargs(tmp_path),  # type: ignore[arg-type]
            )
