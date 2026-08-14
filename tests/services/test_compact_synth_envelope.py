"""Compaction transparency verification for slash-skill synth
envelopes — Phase 18 T3.

Per D38 §六 wiring audit, ``services/compact.py`` was marked
``requires verification``. Synth envelopes (assistant tool_use +
user tool_result + optional user TextBlock) must flow through compaction
**identically** to envelopes produced by real LLM tool calls, with
**zero** ``if id.startswith("synth_")`` special-casing in
``compact.py``.

If any of these tests fail, the right response is **not** to patch
``compact.py`` — it's to escalate as a new D38.8 sub-decision. Adding
a synth-aware branch to ``compact.py`` would leak the synth concept
into a layer that has no business knowing about it (per D38 §六
closing rule).

Coverage:

- L0 ``estimate_message_tokens`` — synth tool_use/tool_result blocks
  contribute to token count exactly like real ones
- L1 ``TruncateToolResultHook`` — by D38.5 it doesn't run on synth
  envelopes (synth path bypasses tool execution → no PostToolUse
  fires); but the hook's tokenizer also doesn't crash if it ever
  *did* encounter a synth_ id
- L2 ``try_context_collapse`` — long synth tool_result body collapses
  identically to a real tool_result body
- L4 ``full_compact`` — older slice containing synth envelopes is
  passed to ``summarize()`` without filtering, summary splices into
  the canonical ``[boundary, summary, *recent]`` shape
- Source-level forcing function: ``compact.py`` must contain no
  ``synth`` identifier anywhere — guards against a "helpful" leak
  during future maintenance.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openharness.engine.slash_skill import synthesize_skill_envelope
from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.content import ToolResultBlock, ToolUseBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot
from openharness.services.compact import (
    estimate_message_tokens,
    full_compact,
    try_context_collapse,
)
from openharness.skills.model import Skill

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_skill(body: str, name: str = "parse-credit-report") -> Skill:
    return Skill(
        name=name,
        description="test skill",
        body=body,
        version=None,
        source_path=Path("/tmp/fake.md"),
    )


def _synth_envelope(body: str, args: str = "") -> list[ConversationMessage]:
    """Construct a synth envelope via the production helper so the test
    exercises the real wire shape, not a fixture."""
    return synthesize_skill_envelope(_make_skill(body), args)


class _SummarizingStubClient:
    """LLM stub used by L4 — yields a canonical summary response."""

    def __init__(self, response: str) -> None:
        self._response = response
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


# --------------------------------------------------------------------------- #
# L0 — estimate_message_tokens (and by extension, L1 cap math)                #
# --------------------------------------------------------------------------- #


class TestL0SynthTransparency:
    def test_estimate_includes_synth_tool_use_and_tool_result_body(self) -> None:
        # A medium body ensures non-trivial token count.
        body = "expert guidance " * 200  # ~3200 chars
        envelope = _synth_envelope(body, args="some args")

        synth_tokens = estimate_message_tokens(envelope, model="qwen-plus")

        # Also estimate the body alone as a user TextBlock baseline.
        baseline = estimate_message_tokens(
            [ConversationMessage(role="user", content=[TextBlock(text=body)])],
            model="qwen-plus",
        )
        # Synth envelope tokens must be >= baseline (it carries the body
        # plus tool_use overhead + args TextBlock). If estimate_message_tokens
        # silently filtered synth_ blocks, the result would be tiny.
        assert synth_tokens >= baseline
        assert synth_tokens > 0

    def test_estimate_synth_matches_real_tool_envelope(self) -> None:
        """Synth and real-id tool envelopes must produce *identical* token
        counts — proves no ID-prefix discrimination in the L0 path. Post-
        D38.8 the synth envelope is always 3 messages (placeholder trailing
        TextBlock when args empty); the hand-built reference mirrors that
        shape exactly so token-count parity is testable.
        """
        from openharness.engine.slash_skill import DEFAULT_EMPTY_ARGS_PLACEHOLDER

        body = "X" * 5_000
        synth = _synth_envelope(body)  # args="" defaults the placeholder

        # Hand-build a structurally identical 3-message envelope with a
        # non-synth id and the same placeholder trailing TextBlock.
        real_id = "toolu_real_001"
        real = [
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id=real_id,
                        name="LoadSkill",
                        input={"name": "parse-credit-report"},
                    )
                ],
            ),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(type="tool_result", tool_use_id=real_id, content=body)],
            ),
            ConversationMessage(
                role="user",
                content=[TextBlock(text=DEFAULT_EMPTY_ARGS_PLACEHOLDER)],
            ),
        ]
        assert estimate_message_tokens(synth, model="qwen-plus") == estimate_message_tokens(
            real, model="qwen-plus"
        )


# --------------------------------------------------------------------------- #
# L2 — try_context_collapse                                                   #
# --------------------------------------------------------------------------- #


class TestL2SynthTransparency:
    def test_long_synth_tool_result_body_collapses(self) -> None:
        # L2 collapses > 2400-char ToolResultBlock content.
        body = "Y" * 5_000
        envelope = _synth_envelope(body)

        new_messages, changed = try_context_collapse(envelope)

        assert changed is True
        # The synth tool_result is at index 1; its content should now
        # carry the "[collapsed N chars]" marker.
        collapsed_result = new_messages[1].content[0]
        assert isinstance(collapsed_result, ToolResultBlock)
        assert "[collapsed" in collapsed_result.content
        # tool_use_id is preserved verbatim (so the message stream stays
        # well-formed after collapse — same invariant as for real envelopes).
        original_id = envelope[1].content[0].tool_use_id  # type: ignore[union-attr]
        assert collapsed_result.tool_use_id == original_id

    def test_short_synth_tool_result_passes_through_unchanged(self) -> None:
        # Below threshold → no change.
        envelope = _synth_envelope("short body")
        new_messages, changed = try_context_collapse(envelope)

        assert changed is False
        # Returned messages structurally identical (Pydantic equality).
        assert new_messages == envelope


# --------------------------------------------------------------------------- #
# L4 — full_compact                                                           #
# --------------------------------------------------------------------------- #


class TestL4SynthTransparency:
    @pytest.mark.asyncio
    async def test_full_compact_summarizes_history_containing_synth_envelopes(
        self,
    ) -> None:
        # 6 synth envelopes (12 messages) + 13 plain message pairs (26 messages).
        history: list[ConversationMessage] = []
        for i in range(6):
            history.extend(_synth_envelope(f"body for skill {i}"))
        for i in range(13):
            history.append(ConversationMessage(role="user", content=[TextBlock(text=f"q {i}")]))
            history.append(
                ConversationMessage(role="assistant", content=[TextBlock(text=f"a {i}")])
            )

        client = _SummarizingStubClient(
            response=(
                "<analysis>noting key facts</analysis><summary>9-slot summary content</summary>"
            )
        )
        new_messages, applied = await full_compact(history, model="qwen-plus", api_client=client)

        assert applied is True
        # Shape: [boundary, summary, *recent (=12 messages)]
        assert len(new_messages) == 2 + 12
        boundary_block = new_messages[0].content[0]
        summary_block = new_messages[1].content[0]
        assert isinstance(boundary_block, TextBlock)
        assert isinstance(summary_block, TextBlock)
        assert "summarized below" in boundary_block.text
        assert "9-slot summary content" in summary_block.text
        # summarize() was called exactly once — synth envelopes weren't
        # filtered or skipped at the L4 producer level.
        assert client.call_count == 1


# --------------------------------------------------------------------------- #
# Forcing function — compact.py source must not mention "synth"               #
# --------------------------------------------------------------------------- #


class TestCompactPyZeroLeakage:
    def test_compact_module_has_no_synth_envelope_branching(self) -> None:
        """D38 §六 closing rule: any branch in ``compact.py`` keying off
        the synth-envelope marker is a concept leak. This guard fires the
        moment a maintainer adds ``if id.startswith("synth_")``, a
        ``SYNTH_ID_PREFIX`` reference, or imports from
        ``engine.slash_skill`` — forcing a D38.8 ratification before any
        special-case branch lands.

        We forbid the ``synth_`` prefix + ``slash_skill`` import — the
        markers unique to D38.2.
        """
        import openharness.services.compact as compact_module

        src_path = Path(compact_module.__file__)  # type: ignore[arg-type]
        source = src_path.read_text(encoding="utf-8")

        leaks = [
            marker
            for marker in ('"synth_"', "'synth_'", "SYNTH_ID_PREFIX", "slash_skill")
            if marker in source
        ]
        assert not leaks, (
            "services/compact.py must remain synth-unaware (D38 §六 "
            f"closing rule). Found leak markers: {leaks}. Escalate to "
            "D38.8 before adding a special-case branch."
        )
