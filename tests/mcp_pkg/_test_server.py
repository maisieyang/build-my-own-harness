"""Tiny FastMCP server used as a subprocess by ``test_client.py``.

Two tools:

- ``echo(text)``:returns the text verbatim (happy-path round-trip).
- ``raise_error()``:returns ``isError=True`` content (logical-error path).

Designed to start fast (<100 ms) so tests run quickly.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("openharness-test-server")


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the text."""
    return f"echo: {text}"


@mcp.tool()
def raise_error() -> str:
    """Always fails — used to test logical-error path."""
    raise ValueError("intentional test failure")


if __name__ == "__main__":
    # FastMCP serves stdio by default.
    mcp.run(transport="stdio")
    sys.exit(0)
