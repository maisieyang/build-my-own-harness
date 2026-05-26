"""Tests for session_memory 5-slot checkpoint — P11-T2.

Three surfaces:

1. **Path resolution** — same cwd → same dir; different cwd → distinct;
   symlinks resolve; lazy mkdir.
2. **Render** — 5-slot section structure stable across empty /
   partial / full tool_metadata; message one-liner format; ordering.
3. **Atomic write + read** — write creates dir; overwrites; read
   returns None when missing; 12k cap enforced.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.content import (
    ImageBlock,
    ImageSource,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.services.session_memory import (
    _render_5_slot,
    get_session_memory_dir,
    read_session_memory,
    update_session_memory_file,
)


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


class TestGetSessionMemoryDir:
    def test_under_home_session_memory(self) -> None:
        d = get_session_memory_dir("/tmp/foo")
        assert d.parent == Path.home() / ".openharness" / "session-memory"

    def test_basename_in_dir_name(self) -> None:
        d = get_session_memory_dir("/tmp/my-project")
        assert d.name.startswith("my-project-")

    def test_sha1_suffix_12_chars(self) -> None:
        d = get_session_memory_dir("/tmp/foo")
        suffix = d.name.rsplit("-", 1)[-1]
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_same_cwd_same_dir(self) -> None:
        assert get_session_memory_dir("/tmp/foo") == get_session_memory_dir("/tmp/foo")

    def test_different_cwd_same_basename_distinct(self) -> None:
        d1 = get_session_memory_dir("/a/foo")
        d2 = get_session_memory_dir("/b/foo")
        assert d1 != d2
        assert d1.name.startswith("foo-")
        assert d2.name.startswith("foo-")

    def test_does_not_create_dir(self, tmp_path: Path) -> None:
        # Resolving a path must not mkdir
        d = get_session_memory_dir(tmp_path / "fresh-project")
        assert not d.exists()

    def test_symlink_resolves_to_canonical(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            os.symlink(real, link)
        except OSError:
            pytest.skip("symlink not supported on this filesystem")
        assert get_session_memory_dir(real) == get_session_memory_dir(link)


class TestRender5Slot:
    def test_all_section_headers_present(self) -> None:
        rendered = _render_5_slot({}, [])
        # All 5 slots + the top-level title
        assert "# Session Memory" in rendered
        assert "## Current State" in rendered
        assert "## Next Step" in rendered
        assert "## Verified Work" in rendered
        assert "## Active Artifacts" in rendered
        assert "## Recent Conversation" in rendered

    def test_empty_metadata_uses_placeholders(self) -> None:
        rendered = _render_5_slot({}, [])
        # Each slot's placeholder text
        assert "(none)" in rendered  # Current State default
        assert "(awaiting user direction)" in rendered  # Next Step default
        assert "(none yet)" in rendered  # Verified Work empty
        assert "(none touched yet)" in rendered  # Active Artifacts empty
        assert "(no turns yet)" in rendered  # Recent Conversation empty

    def test_task_focus_state_populated(self) -> None:
        metadata = {
            "task_focus_state": {
                "goal": "Find bug in charge.py",
                "next_step": "Run tests after fix",
            }
        }
        rendered = _render_5_slot(metadata, [])
        assert "Find bug in charge.py" in rendered
        assert "Run tests after fix" in rendered

    def test_partial_task_focus_state_keeps_default_for_missing(self) -> None:
        # goal present, next_step missing → default used for missing
        metadata = {"task_focus_state": {"goal": "Find bug"}}
        rendered = _render_5_slot(metadata, [])
        assert "Find bug" in rendered
        assert "(awaiting user direction)" in rendered

    def test_verified_work_last_n_only(self) -> None:
        # 15 items → only last 10 surface
        metadata = {"verified_work": [f"item-{i}" for i in range(15)]}
        rendered = _render_5_slot(metadata, [])
        assert "item-14" in rendered  # most recent
        assert "item-5" in rendered  # boundary (15-10=5)
        assert "item-4" not in rendered  # dropped

    def test_recent_files_last_n_only(self) -> None:
        metadata = {"recent_files": [f"file{i}.py" for i in range(15)]}
        rendered = _render_5_slot(metadata, [])
        assert "file14.py" in rendered
        assert "file5.py" in rendered
        assert "file4.py" not in rendered

    def test_message_oneliner_text_block(self) -> None:
        messages = [_user("first message"), _assistant("a reply")]
        rendered = _render_5_slot({}, messages)
        assert "- [user] first message" in rendered
        assert "- [assistant] a reply" in rendered

    def test_message_oneliner_tool_use(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="abc", name="read_file", input={"path": "x"})],
        )
        rendered = _render_5_slot({}, [msg])
        assert "- [assistant/tool] read_file" in rendered

    def test_message_oneliner_tool_result(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="abc", content="file contents here")],
        )
        rendered = _render_5_slot({}, [msg])
        assert "- [user/tool_result] file contents here" in rendered

    def test_message_oneliner_image_block(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[ImageBlock(source=ImageSource(media_type="image/png", data="b64data"))],
        )
        rendered = _render_5_slot({}, [msg])
        assert "- [user/image]" in rendered

    def test_message_oneliner_truncates_text_at_80_chars(self) -> None:
        long_text = "x" * 100
        rendered = _render_5_slot({}, [_user(long_text)])
        assert "x" * 80 in rendered
        assert "x" * 90 not in rendered

    def test_message_oneliner_normalizes_newlines(self) -> None:
        rendered = _render_5_slot({}, [_user("first\nsecond\nthird")])
        # newlines in body become spaces so the one-liner stays on one line
        assert "first second third" in rendered

    def test_recent_conversation_last_80(self) -> None:
        # 100 messages → only last 80 surface
        messages = [_user(f"msg-{i}") for i in range(100)]
        rendered = _render_5_slot({}, messages)
        assert "msg-99" in rendered
        assert "msg-20" in rendered  # boundary (100-80=20)
        assert "msg-19" not in rendered

    def test_non_list_verified_work_treated_as_empty(self) -> None:
        # Defensive: engine might write the wrong type
        rendered = _render_5_slot({"verified_work": "not a list"}, [])
        assert "(none yet)" in rendered

    def test_non_string_items_filtered(self) -> None:
        rendered = _render_5_slot({"verified_work": ["good", 42, "also good", None]}, [])
        assert "good" in rendered
        assert "also good" in rendered
        assert "42" not in rendered


class TestCapAndTruncation:
    def test_under_cap_no_truncation(self) -> None:
        rendered = _render_5_slot({}, [_user("hello")])
        assert len(rendered) < 12_000
        assert "[... truncated" not in rendered

    def test_over_cap_drops_recent_conversation_oldest(self) -> None:
        # Build a metadata with lots of long messages — over 12k
        # Each message one-liner is ~95 chars (80 text + framing)
        # 200 messages * ~95 = ~19k chars
        messages = [_user("x" * 90) for _ in range(200)]
        rendered = _render_5_slot({}, messages)
        assert len(rendered) <= 12_000
        # Most recent still present, oldest dropped
        # (Last 80 from 200 = msgs 120-199; truncation drops from 120 upward)


class TestUpdateAndRead:
    def test_first_write_creates_dir_and_file(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        path = update_session_memory_file(cwd, {}, [])
        assert path.exists()
        assert path.name == "checkpoint.md"
        assert path.parent == get_session_memory_dir(cwd)

    def test_second_write_overwrites(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        path1 = update_session_memory_file(cwd, {"task_focus_state": {"goal": "first"}}, [])
        first = path1.read_text(encoding="utf-8")
        path2 = update_session_memory_file(cwd, {"task_focus_state": {"goal": "second"}}, [])
        second = path2.read_text(encoding="utf-8")
        assert path1 == path2  # same file
        assert "first" in first
        assert "second" in second
        assert "first" not in second  # truly overwritten

    def test_no_orphan_tmp_files_after_success(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        update_session_memory_file(cwd, {}, [])
        files = sorted(p.name for p in get_session_memory_dir(cwd).iterdir())
        assert files == ["checkpoint.md"]

    def test_read_returns_none_when_missing(self, tmp_path: Path) -> None:
        cwd = tmp_path / "fresh-project"
        cwd.mkdir()
        assert read_session_memory(cwd) is None

    def test_read_returns_content_after_write(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        update_session_memory_file(cwd, {"task_focus_state": {"goal": "test goal"}}, [])
        content = read_session_memory(cwd)
        assert content is not None
        assert "test goal" in content
        assert "## Current State" in content


class TestCapTruncationCascade:
    """P11-T7 coverage backfill — exercises stages 2 + 3 of the
    cap-truncation cascade (after Recent Conversation drains, Active
    Artifacts shrink; if still over cap, hard-truncate)."""

    def test_conversation_pop_path(self) -> None:
        # Many huge conversation messages — popping should reduce
        # below cap before draining entirely.
        from openharness.protocols import (
            ConversationMessage as CM,
        )
        from openharness.protocols import (
            TextBlock as TB,
        )
        from openharness.services.session_memory import _render_5_slot

        huge_msgs = [CM(role="user", content=[TB(text="x" * 1500)]) for _ in range(15)]
        rendered = _render_5_slot({}, huge_msgs)
        assert len(rendered) <= 12_000

    def test_artifact_pop_path(self) -> None:
        # Provide artifact lines so long that even after last-N cap
        # the rendered output blows past 12k → cascade must drop
        # artifacts to fit.
        from openharness.services.session_memory import _render_5_slot

        huge_files = [f"src/file_{i}.py - " + ("x" * 1500) for i in range(10)]
        rendered = _render_5_slot(
            {"recent_files": huge_files},
            [],
        )
        assert len(rendered) <= 12_000

    def test_hard_truncate_path(self) -> None:
        # Third stage: task_focus_state.goal is so huge that even
        # after dropping all artifacts + conversation, base sections
        # alone overflow → hard truncate kicks in.
        from openharness.services.session_memory import _render_5_slot

        huge_goal = "x" * 20_000
        rendered = _render_5_slot(
            {"task_focus_state": {"goal": huge_goal}},
            [],
        )
        assert len(rendered) <= 12_000
        assert "truncated to fit cap" in rendered
