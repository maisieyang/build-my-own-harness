"""Tests for ``McpClient`` — P5-T2.

Strategy:spawn a tiny in-process FastMCP server (`_test_server.py`)
as the subprocess. Avoids depending on `npx` or network reachability;
real-world server smoke is T6.

Four surfaces:

1. **Happy lifecycle** — enter / list_tools / call_tool / exit clean.
2. **Init failure paths** — bad command / init timeout → McpInitError.
3. **Call failure paths** — call outside `async with` / subprocess died
   mid-call → McpCallError.
4. **Log events** — 3 events fire at correct phases.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openharness.mcp import McpCallError, McpClient, McpInitError, McpServerConfig
from openharness.mcp.client import _build_stdio_parameters
from openharness.observability import configure_logging

if TYPE_CHECKING:
    from collections.abc import Iterator


_TEST_SERVER = Path(__file__).parent / "_test_server.py"


def _python_server_cfg(
    name: str = "Echo",
    extra_args: tuple[str, ...] = (),
) -> McpServerConfig:
    """Build a config that spawns ``_test_server.py`` with the test venv's Python."""
    return McpServerConfig(
        name=name,
        command=(sys.executable, str(_TEST_SERVER), *extra_args),
    )


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _configure(stream: io.StringIO) -> None:
    configure_logging(level="DEBUG", format="json", stream=stream)


def _events(stream: io.StringIO, name: str) -> list[dict[str, object]]:
    """Extract structlog JSON records with ``event == name``.

    Tolerates non-JSON lines (asyncio's DEBUG output, third-party stdlib
    loggers that bypass our renderer) — the stream contract is "JSON
    where we configured it, raw text otherwise"."""
    out: list[dict[str, object]] = []
    for line in stream.getvalue().splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if parsed.get("event") == name:
            out.append(parsed)
    return out


# --------------------------------------------------------------------------- #
# Happy lifecycle                                                             #
# --------------------------------------------------------------------------- #


class TestHappyLifecycle:
    async def test_list_tools_and_call_round_trip(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        async with McpClient(_python_server_cfg()) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert "echo" in names
            assert "raise_error" in names

            result = await client.call_tool("echo", arguments={"text": "hello"})
            # CallToolResult.content is a list of content blocks (TextContent etc.)
            assert result.isError is False
            text_parts = [c.text for c in result.content if hasattr(c, "text")]
            assert any("echo: hello" in t for t in text_parts)

    async def test_name_property_exposes_config_name(self) -> None:
        async with McpClient(_python_server_cfg(name="MyServer")) as client:
            assert client.name == "MyServer"

    async def test_start_and_stop_log_events_fire(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        async with McpClient(_python_server_cfg(name="LogTest")) as _:
            pass

        starts = _events(log_stream, "mcp_server_start")
        stops = _events(log_stream, "mcp_server_stop")
        assert len(starts) == 1
        assert starts[0]["server"] == "LogTest"
        assert "command" in starts[0]
        assert starts[0]["level"] == "info"

        assert len(stops) == 1
        assert stops[0]["server"] == "LogTest"
        assert stops[0]["reason"] == "clean"


# --------------------------------------------------------------------------- #
# Init failures                                                               #
# --------------------------------------------------------------------------- #


class TestInitFailures:
    def test_sandboxed_stdio_server_is_wrapped_and_gets_filtered_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_PASSWORD", "ambient-secret")
        cfg = McpServerConfig(
            name="Local",
            command=("node", "server.js"),
            env={"EXPLICIT_TOKEN": "user-authorized"},
            sandbox=True,
        )

        params = _build_stdio_parameters(cfg, sandbox_cwd=tmp_path)

        assert params.command == "/usr/bin/sandbox-exec"
        assert params.args[0] == "-p"
        assert "(deny network*)" in params.args[1]
        assert params.args[2:] == ["node", "server.js"]
        assert params.cwd == tmp_path
        assert params.env is not None
        assert "DATABASE_PASSWORD" not in params.env
        assert params.env["EXPLICIT_TOKEN"] == "user-authorized"

    def test_sandboxed_stdio_server_without_cwd_fails_closed(self) -> None:
        cfg = McpServerConfig(name="Local", command=("node",), sandbox=True)

        with pytest.raises(McpInitError, match="sandbox cwd"):
            _build_stdio_parameters(cfg, sandbox_cwd=None)

    def test_unsandboxed_stdio_server_still_gets_minimal_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_PASSWORD", "ambient-secret")
        cfg = McpServerConfig(
            name="TrustedLocal",
            command=("node", "server.js"),
            env={"EXPLICIT_TOKEN": "user-authorized"},
        )

        params = _build_stdio_parameters(cfg, sandbox_cwd=None)

        assert params.env is not None
        assert "DATABASE_PASSWORD" not in params.env
        assert params.env["EXPLICIT_TOKEN"] == "user-authorized"

    async def test_nonexistent_command_raises_mcp_init_error(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        bad_cfg = McpServerConfig(
            name="DoesNotExist",
            command=("/nonexistent/binary-that-should-not-exist",),
        )
        with pytest.raises(McpInitError):
            async with McpClient(bad_cfg, init_timeout=2.0):
                pass

        errors = _events(log_stream, "mcp_server_error")
        assert any(e["phase"] == "init" for e in errors)

    async def test_init_timeout_raises_mcp_init_error(self, log_stream: io.StringIO) -> None:
        _configure(log_stream)
        # `sleep` will spawn fine but never respond to JSON-RPC initialize.
        # Set a tiny timeout (0.5 s) so the test finishes fast.
        hang_cfg = McpServerConfig(
            name="Hanging",
            command=("sleep", "30"),
        )
        with pytest.raises(McpInitError, match="timed out"):
            async with McpClient(hang_cfg, init_timeout=0.5):
                pass

        errors = _events(log_stream, "mcp_server_error")
        timeout_errors = [
            e for e in errors if e["phase"] == "init" and e["error"] == "TimeoutError"
        ]
        assert len(timeout_errors) == 1


# --------------------------------------------------------------------------- #
# Call failures                                                               #
# --------------------------------------------------------------------------- #


class TestCallFailures:
    async def test_methods_outside_async_with_raise(self) -> None:
        client = McpClient(_python_server_cfg())
        # Not entered — list_tools / call_tool both raise.
        with pytest.raises(McpCallError, match="not entered"):
            await client.list_tools()
        with pytest.raises(McpCallError, match="not entered"):
            await client.call_tool("echo", arguments={"text": "x"})

    async def test_call_tool_with_server_side_logical_error_returns_result(self) -> None:
        """Tools that raise on the server side return ``CallToolResult`` with
        ``isError=True`` — they do NOT raise McpCallError. Per D15.4:logical
        errors flow as payload through the BaseTool layer (errors-as-payload)."""
        async with McpClient(_python_server_cfg()) as client:
            result = await client.call_tool("raise_error", arguments={})
            assert result.isError is True
            # The error text is in result.content
            text_parts = [c.text for c in result.content if hasattr(c, "text")]
            assert any("intentional test failure" in t.lower() for t in text_parts)

    async def test_list_tools_wraps_transport_error(
        self, monkeypatch: pytest.MonkeyPatch, log_stream: io.StringIO
    ) -> None:
        # A live session that raises mid-call (transport drop / server crash)
        # is wrapped in McpCallError and logged as mcp_server_error.
        _configure(log_stream)

        async def _raise(*_a: object, **_k: object) -> object:
            raise RuntimeError("session exploded")

        async with McpClient(_python_server_cfg(name="Boom")) as client:
            assert client._session is not None
            monkeypatch.setattr(client._session, "list_tools", _raise)
            with pytest.raises(McpCallError, match="list_tools failed"):
                await client.list_tools()
        assert any(e["phase"] == "call" for e in _events(log_stream, "mcp_server_error"))

    async def test_call_tool_wraps_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _raise(*_a: object, **_k: object) -> object:
            raise RuntimeError("session exploded")

        async with McpClient(_python_server_cfg(name="Boom")) as client:
            assert client._session is not None
            monkeypatch.setattr(client._session, "call_tool", _raise)
            with pytest.raises(McpCallError, match=r"call_tool.*failed"):
                await client.call_tool("echo", arguments={"text": "x"})


# --------------------------------------------------------------------------- #
# Smoke: re-enterability & isolation                                          #
# --------------------------------------------------------------------------- #


class TestReentry:
    async def test_two_clients_can_run_concurrently(self) -> None:
        """Two independent McpClient instances should both work — no shared
        global state breakage."""
        async with (
            McpClient(_python_server_cfg(name="A")) as client_a,
            McpClient(_python_server_cfg(name="B")) as client_b,
        ):
            a_tools = await client_a.list_tools()
            b_tools = await client_b.list_tools()
            assert {t.name for t in a_tools} == {t.name for t in b_tools}
