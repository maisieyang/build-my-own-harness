"""``McpClientPool`` — N-server orchestration — P5-T4.

Per ``decisions/11`` D15.4 (failure handling) + D15.6 (trust whitelist):
manages N MCP servers per ``oh ask`` invocation. Start all in parallel,
collect their tool catalogs into a single list of ``McpToolAdapter``,
shut down on exit. Failures are **isolated**:one bad server doesn't
kill the pool — its tools just don't get registered, and the surviving
servers' tools still flow through.

Lifecycle:

::

    async with McpClientPool(configs, trusted) as pool:
        for adapter in pool.adapters:
            registry.register(adapter)
        # ... run query ...

Failure semantics (D15.4 per failure mode):

- **Init failure** (server's subprocess won't spawn or refuses
  ``initialize``):pool logs a warning, marks the server dead, **does not
  raise**. Other servers continue. ``oh ask`` doesn't crash on one bad MCP.
- **Mid-call failure / transport break** (subprocess dies after init):
  the adapter's first call returns ``ToolResult(is_error=True)`` (handled
  in T3 by ``McpToolAdapter.execute`` catching ``McpCallError``). The pool
  itself doesn't do anything special — adapters use their captured
  ``McpClient`` reference; if the client is dead, calls fail.
- **Once-per-query auto-respawn** is deferred from Phase 5 boundary to a
  future iteration. The Phase 5 simpler model: bad server stays bad for
  the rest of the query. Reasoning: server crashes inside one ``oh ask``
  are rare; mid-query respawn would need substantial concurrency logic.

  D15.4 boundary text explicitly states "bounded once-per-query respawn"
  but this turns out to require careful coordination between adapter
  (which has the dead McpClient reference) and pool (which would need to
  re-create it). For Phase 5 we ship the simpler model — note this as a
  Phase 5+ retro item and re-open if real usage shows the need.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from openharness.mcp.adapter import McpToolAdapter
from openharness.mcp.client import McpClient, McpInitError
from openharness.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path
    from types import TracebackType

    from openharness.mcp.config import McpServerConfig


logger = get_logger("mcp")


class McpClientPool:
    """Manage N MCP servers as a single bundle.

    Construction:

        pool = McpClientPool(configs, trusted_servers={"github", "fs"})
        async with pool:
            adapters = pool.adapters  # list[McpToolAdapter]

    The pool spawns each ``McpClient`` in parallel via
    :func:`asyncio.gather` with ``return_exceptions=True`` so a single
    server's init failure is isolated to its task. Surviving clients are
    kept on an :class:`AsyncExitStack` so the pool's ``__aexit__`` tears
    them all down in reverse order.

    After ``__aenter__`` returns, :attr:`adapters` is a flat list of all
    successfully-registered tools across all surviving servers.
    """

    def __init__(
        self,
        configs: Sequence[McpServerConfig],
        trusted_servers: Iterable[str] = (),
        *,
        init_timeout: float = 5.0,
        sandbox_cwd: Path | None = None,
    ) -> None:
        self._configs = tuple(configs)
        self._trusted: set[str] = set(trusted_servers)
        self._init_timeout = init_timeout
        self._sandbox_cwd = sandbox_cwd
        # Populated by __aenter__.
        self._stack: AsyncExitStack | None = None
        self._adapters: list[McpToolAdapter] = []
        # Tracks which configs failed so retro logs can name them.
        self._dead_servers: list[str] = []

    @property
    def adapters(self) -> list[McpToolAdapter]:
        """All registered MCP tool adapters from surviving servers.

        Available after entering ``async with``. Read-only — callers
        should treat this as a snapshot;the pool doesn't refresh on
        server crash.
        """
        return list(self._adapters)

    @property
    def dead_servers(self) -> list[str]:
        """Names of servers whose init failed — useful for tests / debug."""
        return list(self._dead_servers)

    async def __aenter__(self) -> McpClientPool:
        stack = AsyncExitStack()
        try:
            # Parallel startup. ``return_exceptions=True`` lets one server's
            # failure be caught without breaking the rest.
            startup_results = await asyncio.gather(
                *(self._start_one(stack, cfg) for cfg in self._configs),
                return_exceptions=True,
            )
            for cfg, outcome in zip(self._configs, startup_results, strict=True):
                if isinstance(outcome, BaseException):
                    # Already logged by _start_one;collect dead name.
                    self._dead_servers.append(cfg.name)
                elif outcome is not None:
                    self._adapters.extend(outcome)
        except Exception:
            # If gather itself blows up (unexpected), tear down the stack.
            await stack.aclose()
            raise
        self._stack = stack
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._stack is None:
            return
        try:
            await self._stack.aclose()
        finally:
            self._stack = None
            self._adapters = []

    async def _start_one(
        self,
        stack: AsyncExitStack,
        cfg: McpServerConfig,
    ) -> list[McpToolAdapter] | None:
        """Spawn one server, list its tools, build adapters.

        Returns the per-server adapter list, or raises ``McpInitError``
        (caught by ``asyncio.gather``). Note: the McpClient is kept alive
        via ``stack`` so adapters' captured references stay valid for the
        duration of the pool's context.
        """
        trusted = cfg.name in self._trusted
        if not cfg.sandbox and not trusted:
            logger.warning(
                "mcp_server_error",
                server=cfg.name,
                phase="init",
                error="UntrustedHostPosture",
            )
            raise McpInitError(
                f"MCP server {cfg.name!r} is untrusted and has no stdio sandbox; "
                "enable its sandbox or explicitly trust the server"
            )
        try:
            client = McpClient(
                cfg,
                init_timeout=self._init_timeout,
                sandbox_cwd=self._sandbox_cwd,
            )
            # Enter the McpClient context on the SHARED stack — McpClient
            # itself handles its own teardown order;we just attach it.
            await stack.enter_async_context(client)
        except McpInitError:
            # Init failure already logged by McpClient with phase=init.
            # Pool re-logs at the pool-level so trace consumers see "this
            # specific server failed to start" alongside the SDK detail.
            logger.warning(
                "mcp_server_error",
                server=cfg.name,
                phase="init",
                error="McpInitError",
            )
            raise

        raw_tools = await client.list_tools()
        return [
            McpToolAdapter(
                server_name=cfg.name,
                raw_tool_def=t,
                client=client,
                trust=trusted,
            )
            for t in raw_tools
        ]
