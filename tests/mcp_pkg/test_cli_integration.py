"""Tests for P5-T5 — CLI bootstrap + trust_source log field.

This is the **invariant verification** capability of Phase 5. We assert:

1. Built-in tools have ``trust_source == "local"`` (additive default
   on BaseTool;backward-compat).
2. ``oh ask`` bootstraps MCP servers configured via Settings into the
   ToolRegistry alongside built-ins.
3. ``tool_dispatch`` log event carries a ``trust_source`` field.
4. The dispatch path (permissions / hooks / engine loop) does NOT
   special-case MCP adapters — `git diff` on those files is empty
   except for the documented ``trust_source`` log field addition.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import openharness.cli as cli_module
from openharness.mcp import McpServerConfig
from openharness.observability import configure_logging
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent


_TEST_SERVER = Path(__file__).parent / "_test_server.py"


def _python_server_cfg(name: str = "EchoServer") -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command=(sys.executable, str(_TEST_SERVER)),
    )


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


def _configure(stream: io.StringIO) -> None:
    configure_logging(level="DEBUG", format="json", stream=stream)


def _events(stream: io.StringIO, name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
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
# 1. Built-in tools have trust_source == "local"                              #
# --------------------------------------------------------------------------- #


class TestBuiltinToolsTrustSource:
    def test_all_default_tools_marked_local(self) -> None:
        """Built-in Read/Write/Edit/Bash/Grep all carry ``trust_source =
        "local"`` (BaseTool's class-attr default)."""
        registry = create_default_tool_registry()
        for tool in registry.list_tools():
            assert tool.trust_source == "local"


# --------------------------------------------------------------------------- #
# 2. CLI bootstraps MCP pool + 3. trust_source in tool_dispatch log           #
# --------------------------------------------------------------------------- #


class _RecordingStubClient:
    """Captures the request the engine sends so we can inspect the
    tool catalog injected via system prompt + dispatch."""

    def __init__(self, events: list[ApiStreamEvent]) -> None:
        self._events = events
        self.last_request: ApiMessageRequest | None = None

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> Any:
        self.last_request = request
        for event in self._events:
            yield event


def _trivial_end_turn() -> list[ApiStreamEvent]:
    from openharness.protocols.content import TextBlock
    from openharness.protocols.messages import ConversationMessage
    from openharness.protocols.stream_events import ApiMessageCompleteEvent
    from openharness.protocols.usage import UsageSnapshot

    return [
        ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        ),
    ]


class TestCliBootstrapWithMcp:
    def _set_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")

    def test_mcp_tools_appear_in_request_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end:configure one MCP server via Settings,
        run ``oh ask``, verify the request's ``tools`` list includes
        ``EchoServer.echo`` etc."""
        from typer.testing import CliRunner

        self._set_env(monkeypatch)
        # Configure MCP server via env.
        monkeypatch.setenv(
            "OPENHARNESS_MCP_SERVERS",
            json.dumps(
                [
                    {
                        "name": "EchoServer",
                        "command": [sys.executable, str(_TEST_SERVER)],
                    }
                ]
            ),
        )
        monkeypatch.setenv("OPENHARNESS_TRUSTED_MCP_SERVERS", "EchoServer")

        stub = _RecordingStubClient(_trivial_end_turn())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hello"])
        assert result.exit_code == 0, result.stderr

        # The captured request's tool catalog includes our MCP tools.
        assert stub.last_request is not None
        tool_names = {t.name for t in (stub.last_request.tools or [])}
        # Built-ins (5)
        for builtin in {"Read", "Write", "Edit", "Bash", "Grep"}:
            assert builtin in tool_names
        # MCP adapters (2 from _test_server.py with EchoServer namespace)
        assert "EchoServer.echo" in tool_names
        assert "EchoServer.raise_error" in tool_names

    def test_pool_failure_does_not_crash_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One bad MCP server config alongside good — CLI completes
        normally (D15.4 isolation)."""
        from typer.testing import CliRunner

        self._set_env(monkeypatch)
        monkeypatch.setenv(
            "OPENHARNESS_MCP_SERVERS",
            json.dumps(
                [
                    {"name": "Good", "command": [sys.executable, str(_TEST_SERVER)]},
                    {"name": "Bad", "command": ["/nonexistent/binary"]},
                ]
            ),
        )
        monkeypatch.setenv("OPENHARNESS_TRUSTED_MCP_SERVERS", "Good,Bad")

        stub = _RecordingStubClient(_trivial_end_turn())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        # CLI succeeds even with one bad server.
        assert result.exit_code == 0, result.stderr

        # Good's tools appear, Bad's don't.
        assert stub.last_request is not None
        tool_names = {t.name for t in (stub.last_request.tools or [])}
        assert "Good.echo" in tool_names
        assert "Bad.echo" not in tool_names

    def test_no_mcp_servers_configured_means_normal_phase_4_behavior(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without OPENHARNESS_MCP_SERVERS, the CLI behaves exactly like
        Phase 4 — pool is empty, no extra tools, no extra log events."""
        from typer.testing import CliRunner

        self._set_env(monkeypatch)
        # OPENHARNESS_MCP_SERVERS NOT set.
        stub = _RecordingStubClient(_trivial_end_turn())
        monkeypatch.setattr(cli_module, "_build_client", lambda _settings: stub)

        runner = CliRunner()
        result = runner.invoke(cli_module.app, ["ask", "hi"])
        assert result.exit_code == 0

        # Only the default built-ins, no MCP-namespaced tools.
        # P6-T5: ``Agent`` joins the default lineup as the sub-agent
        # primitive — no MCP-namespaced tools still means no servers.
        assert stub.last_request is not None
        tool_names = {t.name for t in (stub.last_request.tools or [])}
        assert tool_names == {"Read", "Write", "Edit", "Bash", "Grep", "Agent"}


# --------------------------------------------------------------------------- #
# 3. tool_dispatch log carries trust_source field                             #
# --------------------------------------------------------------------------- #


class TestToolDispatchTrustSourceField:
    """We test the log field via engine/query.py directly with a stub
    client + a fake registry, rather than through CLI (which would
    require a full Qwen call). Same pattern as
    tests/observability/test_query_integration.py."""

    async def test_trust_source_field_for_local_tool(self, log_stream: io.StringIO) -> None:
        from typing import cast

        from engine.conftest import _AllowAllChecker, _StubApiClient
        from openharness.api import SupportsStreamingMessages  # noqa: TC001
        from openharness.engine import QueryContext, run_query
        from openharness.protocols.content import TextBlock
        from openharness.protocols.messages import ConversationMessage
        from openharness.protocols.stream_events import ApiMessageCompleteEvent
        from openharness.tools import ToolRegistry

        _configure(log_stream)

        # Use a real local tool — _FakeTool from tools test fixtures
        from tools.conftest import _FakeTool

        registry = ToolRegistry()
        registry.register(_FakeTool())

        # Stub 2-turn flow: tool_use → end_turn
        tool_use_event = ApiMessageCompleteEvent.model_validate(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_01",
                            "name": "Fake",
                            "input": {"value": "x"},
                        }
                    ],
                },
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "tool_use",
            }
        )
        end_event = ApiMessageCompleteEvent.model_validate(
            {
                "message": {"role": "assistant", "content": []},
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "end_turn",
            }
        )
        stub = _StubApiClient(events_per_turn=[[tool_use_event], [end_event]])
        ctx = QueryContext(
            api_client=cast("SupportsStreamingMessages", stub),
            tool_registry=registry,
            permission_checker=_AllowAllChecker(),
            system_prompt="t",
            cwd=Path("/tmp"),
            model="qwen-plus",
            max_tokens=64,
        )
        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
            ctx,
        ):
            pass

        dispatches = _events(log_stream, "tool_dispatch")
        assert len(dispatches) == 1
        assert dispatches[0]["tool"] == "Fake"
        # Built-in / test fakes get trust_source="local"
        assert dispatches[0]["trust_source"] == "local"

    async def test_trust_source_field_for_mcp_adapter(self, log_stream: io.StringIO) -> None:
        """When the dispatched tool is an MCP adapter, trust_source
        reflects the trust whitelist at construction time."""
        from typing import cast

        from engine.conftest import _AllowAllChecker, _StubApiClient
        from openharness.api import SupportsStreamingMessages  # noqa: TC001
        from openharness.engine import QueryContext, run_query
        from openharness.mcp import McpClient, McpToolAdapter
        from openharness.protocols.content import TextBlock
        from openharness.protocols.messages import ConversationMessage
        from openharness.protocols.stream_events import ApiMessageCompleteEvent
        from openharness.tools import ToolRegistry

        _configure(log_stream)

        # Spawn real MCP server + build trusted adapter
        async with McpClient(_python_server_cfg("Mcp")) as client:
            raw_tools = await client.list_tools()
            echo_def = next(t for t in raw_tools if t.name == "echo")
            adapter = McpToolAdapter(
                server_name="Mcp",
                raw_tool_def=echo_def,
                client=client,
                trust=True,  # trusted-server
            )
            registry = ToolRegistry()
            registry.register(adapter)

            tool_use_event = ApiMessageCompleteEvent.model_validate(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_01",
                                "name": "Mcp.echo",
                                "input": {"text": "test"},
                            }
                        ],
                    },
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "stop_reason": "tool_use",
                }
            )
            end_event = ApiMessageCompleteEvent.model_validate(
                {
                    "message": {"role": "assistant", "content": []},
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "stop_reason": "end_turn",
                }
            )
            stub = _StubApiClient(events_per_turn=[[tool_use_event], [end_event]])
            ctx = QueryContext(
                api_client=cast("SupportsStreamingMessages", stub),
                tool_registry=registry,
                permission_checker=_AllowAllChecker(),
                system_prompt="t",
                cwd=Path("/tmp"),
                model="qwen-plus",
                max_tokens=64,
            )
            async for _ in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="hi")])],
                ctx,
            ):
                pass

            dispatches = _events(log_stream, "tool_dispatch")
            assert len(dispatches) == 1
            assert dispatches[0]["tool"] == "Mcp.echo"
            assert dispatches[0]["trust_source"] == "trusted-server"
