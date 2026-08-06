"""Tests for ``McpClientPool`` — P5-T4.

Strategy:spawn 1 or 2 real subprocess MCP servers (via the same
``_test_server.py`` from T2). The pool starts them in parallel and
hands back ``McpToolAdapter`` lists.

Three surfaces:

1. **Happy multi-server path** — N servers all start, all tools collected
   into ``pool.adapters`` with correct namespacing.
2. **Trust threading** — adapters get the right ``trust_source`` per
   the pool's trusted-servers set.
3. **Isolated failure** — one bad config alongside good ones — pool
   yields the good's adapters, marks bad as dead, ``oh ask`` doesn't crash.
"""

from __future__ import annotations

import sys
from pathlib import Path

from openharness.mcp import McpClientPool, McpServerConfig

_TEST_SERVER = Path(__file__).parent / "_test_server.py"


def _good_cfg(name: str) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command=(sys.executable, str(_TEST_SERVER)),
    )


def _bad_cfg(name: str) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command=("/nonexistent/binary-that-should-not-exist",),
    )


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


class TestHappyMultiServer:
    async def test_single_server_yields_adapters(self) -> None:
        async with McpClientPool(
            [_good_cfg("Echo")], trusted_servers={"Echo"}, init_timeout=10.0
        ) as pool:
            assert pool.dead_servers == []
            # _test_server.py has 2 tools (echo + raise_error).
            assert len(pool.adapters) == 2
            names = {a.name for a in pool.adapters}
            assert "Echo.echo" in names
            assert "Echo.raise_error" in names

    async def test_two_servers_start_in_parallel(self) -> None:
        async with McpClientPool(
            [_good_cfg("A"), _good_cfg("B")],
            trusted_servers={"A", "B"},
            init_timeout=10.0,
        ) as pool:
            assert pool.dead_servers == []
            names = {a.name for a in pool.adapters}
            # 2 servers x 2 tools each
            assert names == {"A.echo", "A.raise_error", "B.echo", "B.raise_error"}

    async def test_empty_configs_yields_empty_pool(self) -> None:
        async with McpClientPool([]) as pool:
            assert pool.adapters == []
            assert pool.dead_servers == []


# --------------------------------------------------------------------------- #
# Trust gating threaded through pool                                          #
# --------------------------------------------------------------------------- #


class TestTrustThreading:
    async def test_trusted_server_adapters_marked_trusted_source(self) -> None:
        async with McpClientPool(
            [_good_cfg("Trusted"), _good_cfg("Untrusted")],
            trusted_servers={"Trusted"},
            init_timeout=10.0,
        ) as pool:
            trusted_adapters = [a for a in pool.adapters if a.name.startswith("Trusted.")]
            untrusted_adapters = [a for a in pool.adapters if a.name.startswith("Untrusted.")]
            assert all(a.trust_source == "trusted-server" for a in trusted_adapters)
            assert all(a.trust_source == "strict-default" for a in untrusted_adapters)

    async def test_untrusted_unsandboxed_server_fails_closed_before_spawn(self) -> None:
        async with McpClientPool([_good_cfg("X")], init_timeout=10.0) as pool:
            assert pool.adapters == []
            assert pool.dead_servers == ["X"]


# --------------------------------------------------------------------------- #
# Isolated failure                                                            #
# --------------------------------------------------------------------------- #


class TestIsolatedFailure:
    async def test_one_bad_server_does_not_kill_pool(self) -> None:
        """The classic D15.4 init-failure isolation: a single misconfigured
        MCP server must NOT prevent ``oh ask`` from running with the
        survivors."""
        async with McpClientPool(
            [_good_cfg("Good"), _bad_cfg("Bad")],
            trusted_servers={"Good", "Bad"},
            init_timeout=2.0,
        ) as pool:
            # Good's tools all present.
            good_names = {a.name for a in pool.adapters if a.name.startswith("Good.")}
            assert good_names == {"Good.echo", "Good.raise_error"}
            # Bad is in dead_servers.
            assert "Bad" in pool.dead_servers
            # No adapter from Bad.
            assert not any(a.name.startswith("Bad.") for a in pool.adapters)

    async def test_all_servers_fail_yields_empty_adapters_but_no_crash(self) -> None:
        async with McpClientPool([_bad_cfg("Bad1"), _bad_cfg("Bad2")], init_timeout=2.0) as pool:
            assert pool.adapters == []
            assert set(pool.dead_servers) == {"Bad1", "Bad2"}
