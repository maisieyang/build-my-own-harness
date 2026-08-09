"""Real-API integration test for ``oh ask`` (P1-T4 sub-unit 4d).

This is the only Phase 1 test that opens a network socket. It is
gated two ways:

1. ``@pytest.mark.integration`` -- registered in ``pyproject.toml``;
   CI excludes the marker by default with ``-m "not integration"``.
2. ``skipif`` on the absence of ``OPENHARNESS_API_KEY`` -- so a local
   ``pytest`` run without credentials silently skips instead of
   failing on a network call.

Run explicitly with::

    OPENHARNESS_API_KEY=... \\
    OPENHARNESS_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \\
    uv run pytest -m integration

The assertion deliberately stays loose -- the goal is "did we get
*some* text back", not "did the model say a specific thing". Provider
behavior shifts from week to week; brittle assertions about model
output are not what we are testing here.
"""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from openharness.cli import headless_app


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("OPENHARNESS_API_KEY"),
    reason="OPENHARNESS_API_KEY not set — integration test skipped",
)
@pytest.mark.skipif(
    not os.environ.get("OPENHARNESS_BASE_URL"),
    reason="OPENHARNESS_BASE_URL not set — integration test skipped",
)
def test_real_qwen_streaming_e2e() -> None:
    runner = CliRunner()
    result = runner.invoke(
        headless_app,
        ["run", "Reply with the single word OK and nothing else."],
    )

    assert result.exit_code == 0, (
        f"oh ask exited {result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Streamed text reached stdout. Loose assertion: at least one
    # non-whitespace character. Provider may pad with punctuation,
    # markdown, etc.; we are only proving the wire path works.
    assert result.stdout.strip(), "no text streamed back from Provider"
