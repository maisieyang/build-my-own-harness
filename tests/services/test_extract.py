"""Tests for ``extract_memories_from_turn`` + ``has_memory_writes_since`` — P11-T4.4d/4e.

Four surfaces:

1. ``has_memory_writes_since`` — skip-detect logic
2. ``ExtractionResult`` dataclass
3. ``extract_memories_from_turn`` skip conditions
4. ``extract_memories_from_turn`` happy + failure paths
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openharness.api.errors import RequestFailure
from openharness.memory.store import FilesystemMemoryStore
from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.content import ToolUseBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.services.extract import (
    EXTRACTION_SYSTEM_PROMPT,
    ExtractionResult,
    extract_memories_from_turn,
    has_memory_writes_since,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------


class _JsonResponseStub:
    """Returns a fixed JSON string. Records the request issued."""

    def __init__(self, payload: dict) -> None:
        self._response = json.dumps(payload)
        self.last_request: ApiMessageRequest | None = None
        self.call_count = 0

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        self.call_count += 1
        yield ApiTextDeltaEvent(text=self._response)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text=self._response)],
            ),
            usage=UsageSnapshot(input_tokens=10, output_tokens=2),
            stop_reason="end_turn",
        )


class _MalformedResponseStub:
    """Returns non-JSON text."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        yield ApiTextDeltaEvent(text=self._text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text=self._text)],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class _FailingStub:
    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]
        raise RequestFailure("simulated")


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


# ---------------------------------------------------------------------------
# 1. has_memory_writes_since
# ---------------------------------------------------------------------------


class TestHasMemoryWritesSince:
    def test_no_tool_use_returns_false(self, tmp_path: Path) -> None:
        messages = [_user("hello"), _assistant("hi")]
        assert has_memory_writes_since(messages, tmp_path) is False

    def test_unrelated_tool_use_returns_false(self, tmp_path: Path) -> None:
        # write_file to a non-memory path → False
        msg = ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="x", name="write_file", input={"path": "/tmp/other.txt"})],
        )
        assert has_memory_writes_since([msg], tmp_path) is False

    def test_write_to_memory_dir_returns_true(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        msg = ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    id="x",
                    name="write_file",
                    input={"path": str(memory_dir / "new.md")},
                )
            ],
        )
        assert has_memory_writes_since([msg], memory_dir) is True

    def test_edit_to_memory_dir_returns_true(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        existing = memory_dir / "existing.md"
        existing.write_text("x")
        msg = ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="x", name="edit_file", input={"path": str(existing)})],
        )
        assert has_memory_writes_since([msg], memory_dir) is True

    def test_path_outside_memory_dir_returns_false(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        other = tmp_path / "other.txt"
        other.write_text("x")
        msg = ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="x", name="write_file", input={"path": str(other)})],
        )
        assert has_memory_writes_since([msg], memory_dir) is False

    def test_non_string_path_safely_skipped(self, tmp_path: Path) -> None:
        # Defensive: input.path of wrong type shouldn't crash
        msg = ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="x", name="write_file", input={"path": 42})],
        )
        assert has_memory_writes_since([msg], tmp_path) is False


# ---------------------------------------------------------------------------
# 2. ExtractionResult dataclass
# ---------------------------------------------------------------------------


class TestExtractionResult:
    def test_skip_factory(self) -> None:
        r = ExtractionResult.skip("no messages")
        assert r.skipped is True
        assert r.reason == "no messages"
        assert r.written == ()
        assert r.error is None

    def test_default_empty_state(self) -> None:
        r = ExtractionResult()
        assert r.skipped is False
        assert r.written == ()
        assert r.error is None


# ---------------------------------------------------------------------------
# 3. Skip conditions
# ---------------------------------------------------------------------------


class TestExtractSkipConditions:
    @pytest.mark.asyncio
    async def test_disabled_skips(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub({"memories": []})
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("hello"), _assistant("hi")],
            memory_store=store,
            enabled=False,
        )
        assert result.skipped is True
        assert "disabled" in result.reason  # type: ignore[operator]
        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_too_few_messages_skips(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub({"memories": []})
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("only one")],
            memory_store=store,
        )
        assert result.skipped is True
        assert "messages" in result.reason  # type: ignore[operator]
        assert client.call_count == 0

    @pytest.mark.asyncio
    async def test_main_wrote_memory_skips(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memdir"
        memory_dir.mkdir()
        store = FilesystemMemoryStore(project_dir=memory_dir)
        client = _JsonResponseStub({"memories": []})
        messages = [
            _user("save this fact"),
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="x",
                        name="write_file",
                        input={"path": str(memory_dir / "fact.md")},
                    )
                ],
            ),
        ]
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=messages,
            memory_store=store,
        )
        assert result.skipped is True
        assert "already wrote" in result.reason  # type: ignore[operator]
        assert client.call_count == 0


# ---------------------------------------------------------------------------
# 4. Happy + failure paths
# ---------------------------------------------------------------------------


class TestExtractHappyPath:
    @pytest.mark.asyncio
    async def test_writes_single_record(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "stripe-sdk-version",
                        "description": "Project uses Stripe SDK 8.x",
                        "body": "Stripe pinned to 8.x with legacy API",
                    }
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("we use stripe 8"), _assistant("noted")],
            memory_store=store,
        )
        assert result.skipped is False
        assert result.error is None
        assert len(result.written) == 1
        assert result.written[0].name == "stripe-sdk-version"
        # File exists on disk
        assert (tmp_path / "stripe-sdk-version.md").exists()

    @pytest.mark.asyncio
    async def test_max_records_cap_enforced(self, tmp_path: Path) -> None:
        # 5 records returned → only 3 written (D29.5 cap)
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "name": f"rec-{i}",
                        "description": f"description {i}",
                        "body": f"body {i}",
                    }
                    for i in range(5)
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
            max_records=3,
        )
        assert len(result.written) == 3

    @pytest.mark.asyncio
    async def test_team_scope_written_to_team_subdir(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "team",
                        "name": "team-convention",
                        "description": "Team-wide rule",
                        "body": "Always use uv, never pip",
                    }
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert len(result.written) == 1
        # Written to team subdir
        assert (tmp_path / "team" / "team-convention.md").exists()
        assert not (tmp_path / "team-convention.md").exists()

    @pytest.mark.asyncio
    async def test_team_scope_with_secret_dropped(self, tmp_path: Path) -> None:
        # team-scope record with a secret in body → silently dropped
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "reference",
                        "scope": "team",
                        "name": "leaky-mem",
                        "description": "should be blocked",
                        "body": "API key: AKIAIOSFODNN7EXAMPLE for production",
                    },
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "clean-mem",
                        "description": "should write",
                        "body": "clean content",
                    },
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        # Leaky record dropped; clean record written
        assert len(result.written) == 1
        assert result.written[0].name == "clean-mem"
        assert not (tmp_path / "team" / "leaky-mem.md").exists()

    @pytest.mark.asyncio
    async def test_invalid_record_dropped_others_proceed(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "bogus_type",
                        "scope": "private",
                        "name": "x",
                        "description": "d",
                        "body": "b",
                    },
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "good",
                        "description": "d",
                        "body": "b",
                    },
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        # Bogus dropped, good written
        assert len(result.written) == 1
        assert result.written[0].name == "good"


class TestExtractFailureIsolation:
    @pytest.mark.asyncio
    async def test_llm_failure_returns_error_not_raise(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _FailingStub()
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.error is not None
        assert result.written == ()

    @pytest.mark.asyncio
    async def test_malformed_json_returns_error(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _MalformedResponseStub("not json {{{")
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.error is not None
        assert "json" in result.error.lower()

    @pytest.mark.asyncio
    async def test_json_root_not_dict_returns_error(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub({"memories": []})  # placeholder
        # Override response to a JSON array (not dict)
        client._response = json.dumps(["not", "a", "dict"])
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.error is not None


class TestExtractionPromptIntegrity:
    """Lightweight sanity that EXTRACTION_SYSTEM_PROMPT is what we
    expect — guards against accidental edits."""

    def test_prompt_demands_json_output(self) -> None:
        assert "JSON" in EXTRACTION_SYSTEM_PROMPT or "json" in EXTRACTION_SYSTEM_PROMPT

    def test_prompt_mentions_max_3_records(self) -> None:
        assert "AT MOST 3" in EXTRACTION_SYSTEM_PROMPT or "3 records" in EXTRACTION_SYSTEM_PROMPT

    def test_prompt_includes_no_secrets_guidance(self) -> None:
        assert "Secrets" in EXTRACTION_SYSTEM_PROMPT or "secrets" in EXTRACTION_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 5. Per-field validation branches (P11-T7 coverage backfill)
# ---------------------------------------------------------------------------


class TestExtractInvalidRecordBranches:
    """Each invalid-record reason path exercised individually so the
    ``_build_memory_from_record`` validation branches stay covered as
    the schema evolves."""

    @pytest.mark.asyncio
    async def test_record_not_dict(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub({"memories": ["not a dict"]})
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.written == ()
        assert result.error is None

    @pytest.mark.asyncio
    async def test_record_invalid_scope(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "bogus_scope",
                        "name": "x",
                        "description": "d",
                        "body": "b",
                    }
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.written == ()

    @pytest.mark.asyncio
    async def test_record_missing_name(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "description": "d",
                        "body": "b",
                    }
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.written == ()

    @pytest.mark.asyncio
    async def test_record_missing_description(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "x",
                        "body": "b",
                    }
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.written == ()

    @pytest.mark.asyncio
    async def test_record_missing_body(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "x",
                        "description": "d",
                    }
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.written == ()

    @pytest.mark.asyncio
    async def test_record_name_fails_post_init_regex(self, tmp_path: Path) -> None:
        # Memory name regex is restrictive (e.g. "name with spaces" fails)
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub(
            {
                "memories": [
                    {
                        "type": "project",
                        "scope": "private",
                        "name": "Has Spaces And Capitals",
                        "description": "d",
                        "body": "b",
                    }
                ]
            }
        )
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.written == ()

    @pytest.mark.asyncio
    async def test_memories_not_a_list(self, tmp_path: Path) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub({"memories": "should be list"})
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=[_user("u"), _assistant("a")],
            memory_store=store,
        )
        assert result.error is not None
        assert "memories not a list" in result.error

    @pytest.mark.asyncio
    async def test_render_handles_tool_use_and_result_blocks(self, tmp_path: Path) -> None:
        # Exercises the tool_use / tool_result branches inside
        # _render_messages_for_extraction.
        from openharness.protocols.content import ToolResultBlock

        store = FilesystemMemoryStore(project_dir=tmp_path)
        client = _JsonResponseStub({"memories": []})
        messages = [
            _user("hi"),
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="Read", input={"path": "/x"})],
            ),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="t1", content="ok content " * 100)],
            ),
        ]
        result = await extract_memories_from_turn(
            cwd=tmp_path,
            api_client=client,
            model="qwen-plus",
            messages=messages,
            memory_store=store,
        )
        # Did not crash; memories list empty so no writes
        assert result.written == ()
        # The prompt sent included tool_use / tool_result rendering
        assert client.last_request is not None
