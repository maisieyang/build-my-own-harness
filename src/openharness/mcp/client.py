"""``McpClient`` — single-server lifecycle wrapper — P5-T2.

Per ``decisions/11`` D15.1: Phase 5 ships **stdio transport only**, via
the official `mcp` Python SDK. This module wraps
``mcp.client.stdio.stdio_client`` + ``mcp.ClientSession`` behind a
simple async context manager:

::

    async with McpClient(cfg) as client:
        tools = await client.list_tools()
        result = await client.call_tool(name, arguments)

The wrapper adds:

- **Init timeout** (5 s default per Phase 5 boundary sub-decision):
  ``session.initialize()`` is wrapped in ``asyncio.wait_for`` so a hung
  MCP server can't stall ``oh ask`` bootstrap forever.
- **Three log events** (the 9 → 12 inventory addition):
  ``mcp_server_start`` (info) on successful init,
  ``mcp_server_stop`` (info) on clean shutdown,
  ``mcp_server_error`` (warning) on any failure with a ``phase`` field
  ∈ ``{"init", "call", "shutdown"}``.
- **AsyncExitStack composition**:two nested SDK async-context-managers
  (``stdio_client`` for transport, ``ClientSession`` for protocol) are
  combined into one — callers see one ``async with`` instead of nesting.

Failure semantics (per D15.4):

- Init failure (timeout / spawn error / handshake refused) → ``__aenter__``
  raises ``McpInitError`` (subclass of ``OpenHarnessError``). The
  ``AsyncExitStack`` is already cleaned up by ``__aenter__`` itself.
- Mid-call failure (subprocess died, broken pipe) →
  :meth:`list_tools` / :meth:`call_tool` raise ``McpCallError`` (also
  ``OpenHarnessError``). The pool layer (T4) translates these into
  bounded auto-respawn or fail-the-call decisions.

Public surface:

    from openharness.mcp import McpClient, McpInitError, McpCallError
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openharness.errors import OpenHarnessError
from openharness.observability import get_logger

if TYPE_CHECKING:
    from types import TracebackType

    from openharness.mcp.config import McpServerConfig


logger = get_logger("mcp")


# Phase 5 boundary sub-decision: 5 s per-server init timeout. Configurable
# via ``McpClient(init_timeout=...)`` for tests + slow real servers.
_DEFAULT_INIT_TIMEOUT_SECONDS = 5.0


class McpInitError(OpenHarnessError):
    """Raised when an MCP server fails to start or complete its
    ``initialize`` handshake within the timeout.

    Subclass of :class:`OpenHarnessError` so the CLI's root catch-all
    surfaces it with the standard error UX.
    """


class McpCallError(OpenHarnessError):
    """Raised when an MCP ``tools/call`` or ``tools/list`` fails after
    the server is up.

    Pool layer translates this into ``ToolResult(is_error=True, ...)``
    or bounded respawn per D15.4.
    """


class McpClient:
    """Async-context-manager-style wrapper around one MCP server connection.

    Lifecycle:

    1. ``__aenter__`` spawns the subprocess (via ``stdio_client``), opens
       a ``ClientSession`` over the pipes, runs ``initialize`` with a
       timeout. Emits ``mcp_server_start`` on success.
    2. While entered, :meth:`list_tools` / :meth:`call_tool` are usable.
    3. ``__aexit__`` closes the session + subprocess in reverse order.
       Emits ``mcp_server_stop``.

    Any failure path also emits ``mcp_server_error`` with the matching
    ``phase`` before raising.
    """

    def __init__(
        self,
        config: McpServerConfig,
        *,
        init_timeout: float = _DEFAULT_INIT_TIMEOUT_SECONDS,
    ) -> None:
        self._config = config
        self._init_timeout = init_timeout
        # Populated by __aenter__ / cleared by __aexit__.
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    @property
    def name(self) -> str:
        """The server's namespace prefix (from config). Lets callers
        log without reaching through ``client._config``."""
        return self._config.name

    async def __aenter__(self) -> McpClient:
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command=self._config.command[0],
                args=list(self._config.command[1:]),
                # SDK semantics: env=None inherits parent;dict merges with
                # SDK's default-environment dict. Empty dict → None so we
                # don't accidentally clear PATH and friends.
                env=dict(self._config.env) if self._config.env else None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            # Bounded init handshake — pathological servers that hang on
            # initialize won't stall the whole CLI.
            await asyncio.wait_for(session.initialize(), timeout=self._init_timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            await stack.aclose()
            logger.warning(
                "mcp_server_error",
                server=self._config.name,
                phase="init",
                error="TimeoutError",
            )
            raise McpInitError(
                f"MCP server {self._config.name!r} init timed out after {self._init_timeout}s"
            ) from exc
        except Exception as exc:
            await stack.aclose()
            logger.warning(
                "mcp_server_error",
                server=self._config.name,
                phase="init",
                error=type(exc).__name__,
            )
            raise McpInitError(
                f"MCP server {self._config.name!r} failed to initialize: {exc}"
            ) from exc

        self._stack = stack
        self._session = session
        logger.info(
            "mcp_server_start",
            server=self._config.name,
            command=" ".join(self._config.command),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._stack is None:
            return  # never entered (shouldn't happen in well-formed use)
        try:
            await self._stack.aclose()
            logger.info("mcp_server_stop", server=self._config.name, reason="clean")
        except Exception as e:
            logger.warning(
                "mcp_server_error",
                server=self._config.name,
                phase="shutdown",
                error=type(e).__name__,
            )
            # Don't re-raise — we're already exiting. Best-effort cleanup.
        finally:
            self._stack = None
            self._session = None

    async def list_tools(self) -> list[Any]:
        """Return the server's declared tool catalog.

        Result type is the SDK's ``ListToolsResult.tools`` list:elements
        carry ``name`` / ``description`` / ``inputSchema`` / optional
        ``annotations``. T3 (``McpToolAdapter``) consumes these to
        synthesize ``BaseTool`` subclasses.

        Raises :class:`McpCallError` if the session isn't open or the
        SDK call fails.
        """
        session = self._require_session("list_tools")
        try:
            result = await session.list_tools()
        except Exception as exc:
            logger.warning(
                "mcp_server_error",
                server=self._config.name,
                phase="call",
                error=type(exc).__name__,
            )
            raise McpCallError(f"MCP {self._config.name!r} list_tools failed: {exc}") from exc
        return list(result.tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke ``tools/call`` on the server. Returns the SDK's
        ``CallToolResult`` (carries ``content`` list + ``isError`` flag).

        Raises :class:`McpCallError` on transport failures (pipe broken,
        subprocess died). Logical errors (server returned ``isError=True``)
        are **not** raised — caller inspects the result, in line with
        the errors-as-payload principle (D15.4 logical case).
        """
        session = self._require_session("call_tool")
        try:
            return await session.call_tool(name, arguments=arguments or {})
        except Exception as exc:
            logger.warning(
                "mcp_server_error",
                server=self._config.name,
                phase="call",
                error=type(exc).__name__,
            )
            raise McpCallError(
                f"MCP {self._config.name!r} call_tool({name!r}) failed: {exc}"
            ) from exc

    def _require_session(self, where: str) -> ClientSession:
        """Raise a clear error if methods are called outside ``async with``."""
        if self._session is None:
            raise McpCallError(
                f"{where}() called on McpClient that is not entered "
                f"(use 'async with McpClient(cfg) as client:')"
            )
        return self._session
