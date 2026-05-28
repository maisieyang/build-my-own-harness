"""Tests for ``engine.messages.collect_turn_metadata`` — P12-T1-1a.

Single producer feeds two consumers (session_memory checkpoint +
snapshot writer) per D30.6. Deterministic — no LLM call.

Surfaces:

1. ``recent_files`` extraction from Read/Write/Edit tool_use blocks
2. Non-file tools (Bash/Grep/etc.) excluded from recent_files
3. ``verified_work`` summarizes successful tool_results;
   error tool_results excluded
4. Dedupe across multiple Read of same path
5. ``task_focus_state`` always has goal=None / next_step=None placeholder
6. ``prior_metadata`` extends recent_files across turns at resume time
"""

from __future__ import annotations

from openharness.engine.messages import collect_turn_metadata
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from openharness.protocols.content import ToolResultBlock


def _user_msg(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant_tool_use(
    *blocks: ToolUseBlock,
) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=list(blocks))


def _user_tool_result(*blocks: ToolResultBlock) -> ConversationMessage:
    from openharness.protocols import ContentBlock

    bundled: list[ContentBlock] = list(blocks)
    return ConversationMessage(role="user", content=bundled)


class TestRecentFiles:
    def test_read_tool_use_populates_recent_files(self) -> None:
        msgs = [
            _user_msg("hi"),
            _assistant_tool_use(ToolUseBlock(id="t1", name="Read", input={"path": "/x.py"})),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["recent_files"] == ["/x.py"]

    def test_write_tool_use_populates_recent_files(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Write", input={"file_path": "/y.py", "content": "..."})
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["recent_files"] == ["/y.py"]

    def test_edit_tool_use_populates_recent_files(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(
                    id="t1",
                    name="Edit",
                    input={"file_path": "/z.py", "old_string": "a", "new_string": "b"},
                ),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["recent_files"] == ["/z.py"]

    def test_bash_and_grep_excluded_from_recent_files(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
                ToolUseBlock(id="t2", name="Grep", input={"pattern": "foo"}),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["recent_files"] == []

    def test_dedupe_within_turn(self) -> None:
        # Two Read of same path → one entry
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/x.py"}),
                ToolUseBlock(id="t2", name="Read", input={"path": "/x.py"}),
                ToolUseBlock(id="t3", name="Read", input={"path": "/y.py"}),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["recent_files"] == ["/x.py", "/y.py"]

    def test_caps_at_10_most_recent(self) -> None:
        # 15 unique paths → only last 10 kept
        blocks = [
            ToolUseBlock(id=f"t{i}", name="Read", input={"path": f"/f{i}.py"}) for i in range(15)
        ]
        msgs = [_assistant_tool_use(*blocks)]
        meta = collect_turn_metadata(msgs)
        assert len(meta["recent_files"]) == 10
        # Last 10 kept: f5..f14
        assert meta["recent_files"][0] == "/f5.py"
        assert meta["recent_files"][-1] == "/f14.py"

    def test_missing_path_input_safely_skipped(self) -> None:
        # Defensive: Read with no path key shouldn't crash
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={}),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["recent_files"] == []

    def test_non_string_path_skipped(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": 42}),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["recent_files"] == []


class TestPriorMetadataAccumulation:
    def test_prior_metadata_seeds_recent_files(self) -> None:
        # Resume scenario: prior turn touched ["/a.py", "/b.py"];
        # this turn touches "/c.py" → accumulate.
        prior = {"recent_files": ["/a.py", "/b.py"]}
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/c.py"}),
            ),
        ]
        meta = collect_turn_metadata(msgs, prior)
        assert meta["recent_files"] == ["/a.py", "/b.py", "/c.py"]

    def test_dedupe_across_prior_and_current(self) -> None:
        prior = {"recent_files": ["/a.py", "/b.py"]}
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/b.py"}),
                ToolUseBlock(id="t2", name="Read", input={"path": "/c.py"}),
            ),
        ]
        meta = collect_turn_metadata(msgs, prior)
        # /b.py already in prior → not duplicated
        assert meta["recent_files"] == ["/a.py", "/b.py", "/c.py"]

    def test_accumulation_still_capped(self) -> None:
        prior_files = [f"/old{i}.py" for i in range(8)]
        prior = {"recent_files": prior_files}
        blocks = [
            ToolUseBlock(id=f"t{i}", name="Read", input={"path": f"/new{i}.py"}) for i in range(5)
        ]
        msgs = [_assistant_tool_use(*blocks)]
        meta = collect_turn_metadata(msgs, prior)
        # 8 + 5 = 13 → capped at 10 most recent → keeps last 10
        assert len(meta["recent_files"]) == 10
        # The newest 5 are guaranteed kept
        for new in [f"/new{i}.py" for i in range(5)]:
            assert new in meta["recent_files"]

    def test_prior_metadata_none_safe(self) -> None:
        # Default arg is None — same shape as fresh start
        meta = collect_turn_metadata([])
        assert meta["recent_files"] == []
        assert meta["verified_work"] == []

    def test_invalid_prior_metadata_safely_ignored(self) -> None:
        # Defensive: malformed prior shouldn't crash
        meta = collect_turn_metadata([], {"recent_files": "not a list"})
        assert meta["recent_files"] == []


class TestVerifiedWork:
    def test_successful_tool_result_summarized(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/x.py"}),
            ),
            _user_tool_result(
                ToolResultBlock(tool_use_id="t1", content="print('hello')"),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["verified_work"] == ["Read: print('hello')"]

    def test_error_tool_result_excluded(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/missing"}),
            ),
            _user_tool_result(
                ToolResultBlock(tool_use_id="t1", content="File not found", is_error=True),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["verified_work"] == []

    def test_long_content_truncated_to_60_chars(self) -> None:
        long_text = "x" * 100
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/x"}),
            ),
            _user_tool_result(
                ToolResultBlock(tool_use_id="t1", content=long_text),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        # "Read: " (6 chars) + 60 chars of content = 66 chars
        assert len(meta["verified_work"][0]) == 6 + 60

    def test_newlines_collapsed_to_spaces(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/x"}),
            ),
            _user_tool_result(
                ToolResultBlock(tool_use_id="t1", content="line1\nline2\nline3"),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert "\n" not in meta["verified_work"][0]
        assert "line1 line2 line3" in meta["verified_work"][0]

    def test_caps_at_10_per_turn(self) -> None:
        # 15 successful tool_results → only last 10 kept
        msgs: list[ConversationMessage] = []
        for i in range(15):
            msgs.append(
                _assistant_tool_use(
                    ToolUseBlock(id=f"t{i}", name="Bash", input={"command": f"echo {i}"}),
                )
            )
            msgs.append(
                _user_tool_result(
                    ToolResultBlock(tool_use_id=f"t{i}", content=f"output{i}"),
                )
            )
        meta = collect_turn_metadata(msgs)
        assert len(meta["verified_work"]) == 10
        # Last 10 kept
        assert "Bash: output5" in meta["verified_work"][0]
        assert "Bash: output14" in meta["verified_work"][-1]

    def test_unknown_tool_use_id_handled_as_unknown(self) -> None:
        # A tool_result with a tool_use_id that doesn't match any
        # tool_use block (shouldn't happen in practice but defensive)
        msgs = [
            _user_tool_result(
                ToolResultBlock(tool_use_id="t_unknown", content="orphan"),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["verified_work"] == ["unknown: orphan"]


class TestTaskFocusStatePlaceholder:
    def test_always_present_with_none_values(self) -> None:
        # Phase 12 ships the schema field present but unpopulated;
        # session_memory render fills with "(none)" placeholder text.
        meta = collect_turn_metadata([])
        assert meta["task_focus_state"] == {"goal": None, "next_step": None}

    def test_present_even_with_complex_turn(self) -> None:
        msgs = [
            _user_msg("complex task"),
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/x.py"}),
            ),
            _user_tool_result(
                ToolResultBlock(tool_use_id="t1", content="content"),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        assert meta["task_focus_state"] == {"goal": None, "next_step": None}


class TestPurityAndOutputShape:
    def test_input_messages_not_mutated(self) -> None:
        msgs = [
            _assistant_tool_use(
                ToolUseBlock(id="t1", name="Read", input={"path": "/x.py"}),
            ),
        ]
        meta = collect_turn_metadata(msgs)
        meta["recent_files"].append("/y.py")  # mutate output
        # Re-collect — should produce same recent_files (output mutation
        # didn't leak back to messages → recomputation stable)
        meta2 = collect_turn_metadata(msgs)
        assert meta2["recent_files"] == ["/x.py"]

    def test_empty_messages_returns_skeleton(self) -> None:
        meta = collect_turn_metadata([])
        assert meta == {
            "recent_files": [],
            "verified_work": [],
            "task_focus_state": {"goal": None, "next_step": None},
        }
