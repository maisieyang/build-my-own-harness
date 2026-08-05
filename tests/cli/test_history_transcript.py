"""Goal-scoped conversation evidence renderer tests."""

from __future__ import annotations

from openharness._stream_render import (
    _TRANSCRIPT_TOOL_OUTPUT_PREVIEW,
    render_history_transcript,
)
from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant(*blocks: TextBlock | ToolUseBlock) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=list(blocks))


class TestBlockRendering:
    def test_assistant_text_appears(self) -> None:
        out = render_history_transcript([_user("hi"), _assistant(TextBlock(text="hello back"))])
        assert "hello back" in out
        assert "hi" in out

    def test_tool_call_rendered_with_name_and_input(self) -> None:
        msg = _assistant(ToolUseBlock(id="tu_1", name="Bash", input={"command": "uv run pytest"}))
        out = render_history_transcript([_user("run tests"), msg])
        assert "[tool call: Bash(" in out
        assert "uv run pytest" in out

    def test_tool_result_rendered_with_status(self) -> None:
        ok = ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="tu_1", content="2717 passed", is_error=False)],
        )
        err = ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="tu_2", content="boom", is_error=True)],
        )
        out = render_history_transcript([ok, err])
        assert "[tool result (ok): 2717 passed]" in out
        assert "[tool result (error): boom]" in out

    def test_tool_result_preview_preserves_head_and_tail(self) -> None:
        middle = "x" * (_TRANSCRIPT_TOOL_OUTPUT_PREVIEW + 500)
        long_output = f"START-DIAGNOSTIC\n{middle}\nFINAL: 2717 passed"
        msg = ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="tu_1", content=long_output, is_error=False)],
        )
        out = render_history_transcript([msg])
        assert "START-DIAGNOSTIC" in out
        assert "FINAL: 2717 passed" in out
        assert "chars omitted" in out
        assert middle not in out


class TestTurnBoundaries:
    def test_assistant_turns_are_numbered(self) -> None:
        out = render_history_transcript(
            [
                _user("a"),
                _assistant(TextBlock(text="r1")),
                _user("b"),
                _assistant(TextBlock(text="r2")),
            ]
        )
        assert "[assistant turn 1]" in out
        assert "[assistant turn 2]" in out
        # 判官可数 turn(D48.5):标记数 = assistant 消息数.
        assert out.count("[assistant turn ") == 2


class TestEdgeCases:
    def test_empty_history_renders_empty(self) -> None:
        assert render_history_transcript([]) == ""

    def test_user_text_framed_as_user(self) -> None:
        out = render_history_transcript([_user("please fix the bug")])
        assert "[user]: please fix the bug" in out
