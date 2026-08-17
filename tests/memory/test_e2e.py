"""End-to-end integration for typed durable project memory.

Seed a typed record → run the private non-interactive entry → assert the
runtime-generated catalog and typed Memory tools reach the query context.

The test stubs out the LLM client (so no API calls) and intercepts
``run_query`` so we can inspect the ``QueryContext.system_prompt``
the engine receives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from openharness import cli as cli_module
from openharness.memory.model import parse_memory
from openharness.memory.paths import get_project_memory_dir
from openharness.protocols import ConversationMessage, TextBlock
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.usage import UsageSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


_STRIPE_MEMORY = """\
---
id: 01HE2ESTRIPE000
name: stripe-sdk-version
description: Project uses Stripe SDK 8.x with the legacy refund API
type: project
scope: private
created_at: 2026-05-26T10:00:00+00:00
updated_at: 2026-05-26T10:00:00+00:00
use_count: 0
---

Stripe SDK pinned to 8.x. Use ``stripe.Refund.create`` with
metadata.original_charge_id for the audit trail. Webhook signature
tolerance bumped to 600s.
"""


class _CapturedContext:
    def __init__(self) -> None:
        self.context: object | None = None


class _StubClient:
    """Minimal LLM stub — yields one assistant message + end_turn."""

    def __init__(self) -> None:
        self.last_request: ApiMessageRequest | None = None

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.last_request = request
        yield ApiTextDeltaEvent(text="ok")
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text="ok")],
            ),
            usage=UsageSnapshot(input_tokens=10, output_tokens=1),
            stop_reason="end_turn",
        )


def _patch_run_query(monkeypatch: pytest.MonkeyPatch, captured: _CapturedContext) -> None:
    """Stub out the engine so we can inspect the QueryContext + system_prompt."""

    async def _capturing(
        initial_messages: list[ConversationMessage],
        context: object,
    ) -> AsyncIterator[ApiStreamEvent]:
        del initial_messages
        captured.context = context
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(cli_module, "run_query", _capturing)


def _seed_env_and_stub(monkeypatch: pytest.MonkeyPatch) -> _CapturedContext:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
    monkeypatch.setattr(cli_module, "_build_client", lambda _s: _StubClient())
    captured = _CapturedContext()
    _patch_run_query(monkeypatch, captured)
    return captured


def _seed_stripe_memory() -> Path:
    """Write the stripe memory into the per-cwd HOME-isolated memory dir."""
    from pathlib import Path

    memory_dir = get_project_memory_dir(Path.cwd())
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / "stripe-sdk-version.md"
    path.write_text(_STRIPE_MEMORY)
    return path


class TestMemoryE2E:
    """End-to-end: hand-written memory → oh ask → injected + use_count++."""

    def test_query_match_injects_memory_into_system_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The generated catalog exposes hooks, not full memory bodies."""
        captured = _seed_env_and_stub(monkeypatch)
        _seed_stripe_memory()

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "how do I issue a stripe refund"])

        assert result.exit_code == 0, result.stderr
        assert captured.context is not None
        prompt = captured.context.system_prompt  # type: ignore[attr-defined]
        # New ## Memory section with rules block present
        assert "## Memory" in prompt
        assert "You have a persistent" in prompt
        # The index is derived from typed records; no hand-maintained
        # MEMORY.md is required.
        assert "### Memory Index" in prompt
        assert "[stripe-sdk-version](stripe-sdk-version.md)" in prompt
        # Memory body is NOT auto-injected anymore — LLM expected to
        # Read it on demand. Verify the legacy ## Relevant Memories
        # section is gone.
        assert "## Relevant Memories" not in prompt
        assert "Stripe SDK pinned to 8.x" not in prompt

    # Phase 16 T2 (D36.7) retired both the use_count side-effect and the
    # zero-token relevance filter. The replaced tests asserted Phase 10
    # behavior that the new architecture deliberately drops — the LLM is
    # now responsible for deciding which memory body to Read after
    # scanning the MEMORY.md index, so neither use_count tracking nor
    # query-side relevance filtering is part of the contract anymore.
    # Algorithm-level coverage for the deprecated relevance + use_count
    # paths lives in ``tests/memory/test_relevance.py`` and
    # ``tests/memory/test_usage.py`` against the still-functional
    # module-level functions.

    def test_disable_memory_flag_skips_injection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Project instructions and durable memory are independent layers.
        captured = _seed_env_and_stub(monkeypatch)
        path = _seed_stripe_memory()
        from pathlib import Path

        (Path.cwd() / "AGENTS.md").write_text(
            "Run project tests with uv.\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(
            cli_module.headless_app,
            ["run", "--no-enable-memory", "how do I issue a stripe refund"],
        )

        assert result.exit_code == 0
        prompt = captured.context.system_prompt  # type: ignore[attr-defined]
        # No memory-related section
        assert "## Relevant Memories" not in prompt
        assert "## Memory" not in prompt
        assert "## Project Instructions" in prompt
        assert "Run project tests with uv." in prompt
        # use_count untouched — store wasn't even scanned
        reparsed = parse_memory(path)
        assert reparsed is not None
        assert reparsed.use_count == 0

    def test_handwritten_index_does_not_override_generated_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pathlib import Path

        captured = _seed_env_and_stub(monkeypatch)
        _seed_stripe_memory()
        memory_dir = get_project_memory_dir(Path.cwd())
        (memory_dir / "MEMORY.md").write_text("- [stale](missing.md)\n")

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "anything"])

        assert result.exit_code == 0
        prompt = captured.context.system_prompt  # type: ignore[attr-defined]
        assert "## Memory" in prompt
        assert "- [stripe-sdk-version]" in prompt
        assert "- [stale]" not in prompt

    def test_claude_md_cascade_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path

        captured = _seed_env_and_stub(monkeypatch)
        # Write a CLAUDE.md in cwd
        Path("CLAUDE.md").write_text("Use uv, not pip. Always.\n")

        runner = CliRunner()
        result = runner.invoke(cli_module.headless_app, ["run", "anything"])

        assert result.exit_code == 0
        prompt = captured.context.system_prompt  # type: ignore[attr-defined]
        # CLAUDE.md is one supported project-instruction format.
        assert "## Project Instructions" in prompt
        assert "Use uv, not pip" in prompt
