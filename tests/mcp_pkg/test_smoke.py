"""End-to-end smoke against a real community MCP server — P5-T6.

Skipped automatically when ``npx`` is not on PATH (CI without Node /
contributors without npm don't need to install Node just to run our
test suite).

Two smokes:

1. **Filesystem server lifecycle**:spawn ``@modelcontextprotocol/server-
   filesystem`` against a tmp directory, ``list_tools`` returns the
   real catalog (read_file, list_directory, etc.), ``call_tool`` round-
   trips correctly.
2. **CLI integration via filesystem server**:configure the server via
   ``OPENHARNESS_MCP_SERVERS`` env, run ``oh ask``, verify the request
   catalog includes the ``Filesystem.*`` namespaced tools.

These tests are the real test of Phase 5's stdio transport against a
non-trivial external process. The npm package fetches on first run
(takes a few seconds) — subsequent runs use the npx cache.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

import pytest

import openharness.cli as cli_module
from openharness.mcp import McpClient, McpServerConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


# Skip everything in this module when npx is missing.
pytestmark = pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="npx not installed — MCP filesystem smoke requires Node.js",
)


def _filesystem_cfg(tmp_path: Path) -> McpServerConfig:
    """Build a config for the community filesystem server."""
    return McpServerConfig(
        name="Filesystem",
        command=(
            "npx",
            "-y",
            "@modelcontextprotocol/server-filesystem",
            str(tmp_path),
        ),
    )


class TestFilesystemServerLifecycle:
    async def test_list_and_call_round_trip(self, tmp_path: Path) -> None:
        """Real filesystem MCP server lists its tools + we round-trip
        a ``read_file`` call on a real file."""
        # Create a file inside the allowed directory.
        target = tmp_path / "hello.txt"
        target.write_text("Hello from MCP smoke test\n")

        # 30s init timeout because first ``npx`` invocation may need to
        # download the package.
        async with McpClient(_filesystem_cfg(tmp_path), init_timeout=30.0) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            # The filesystem server exposes a known set of tools — assert
            # on the high-confidence subset.
            assert "read_text_file" in tool_names or "read_file" in tool_names
            assert any("list" in n.lower() for n in tool_names)

            # Read the file we created and verify the content.
            read_tool = "read_text_file" if "read_text_file" in tool_names else "read_file"
            result = await client.call_tool(read_tool, arguments={"path": str(target)})
            assert result.isError is False
            text_parts = [c.text for c in result.content if hasattr(c, "text")]
            assert any("Hello from MCP smoke test" in t for t in text_parts)


class TestCliWithFilesystemServer:
    def _set_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
        monkeypatch.setenv(
            "OPENHARNESS_MCP_SERVERS",
            json.dumps(
                [
                    {
                        "name": "Filesystem",
                        "command": [
                            "npx",
                            "-y",
                            "@modelcontextprotocol/server-filesystem",
                            str(tmp_path),
                        ],
                    }
                ]
            ),
        )

    def test_cli_request_includes_filesystem_namespaced_tools(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The oh ask flow registers Filesystem.* tools into the catalog
        sent to the LLM."""
        from typer.testing import CliRunner

        from openharness.protocols.content import TextBlock
        from openharness.protocols.messages import ConversationMessage
        from openharness.protocols.stream_events import ApiMessageCompleteEvent
        from openharness.protocols.usage import UsageSnapshot

        self._set_env(monkeypatch, tmp_path)

        # Capture the request the engine sends, return a trivial end_turn.
        class _RecordingStub:
            def __init__(self) -> None:
                self.last_request: ApiMessageRequest | None = None

            async def stream_message(
                self,
                request: ApiMessageRequest,
            ) -> AsyncIterator[ApiStreamEvent]:
                self.last_request = request
                yield ApiMessageCompleteEvent(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                    stop_reason="end_turn",
                )

        stub = _RecordingStub()
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0, result.stderr

        assert stub.last_request is not None
        tool_names = {t.name for t in (stub.last_request.tools or [])}
        # Built-ins still present.
        assert "Read" in tool_names
        # At least one Filesystem.* tool registered.
        fs_namespaced = [n for n in tool_names if n.startswith("Filesystem.")]
        assert len(fs_namespaced) >= 1, f"no Filesystem.* tools in {tool_names}"
