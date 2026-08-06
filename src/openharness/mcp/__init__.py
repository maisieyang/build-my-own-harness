"""MCP (Model Context Protocol) client subsystem — Phase 5.

Federated tool registry: ``oh ask`` can consume tools from external
MCP servers in addition to the 5 built-in tools (Read / Write / Edit /
Bash / Grep). Per ``decisions/11`` boundary:

- D15.1: stdio transport only (via official ``mcp`` Python SDK).
- D15.3: tools registered as ``<Server>.<Tool>`` (PascalCase).
- D15.4: bounded once-per-query auto-respawn on subprocess crash.
- D15.6: per-server trust whitelist (``Settings.trusted_mcp_servers``)
  gates ``is_read_only`` — untrusted servers force strict path.

Cross-cutting invariant: **MCP integration must NOT modify**
``permissions/checker.py``, ``hooks/executor.py``, or ``engine/query.py``
dispatch logic. MCP tools satisfy the same ``BaseTool`` contract as
built-in ones;dispatch / permission / hook layers don't know about MCP.

Sub-units land incrementally (see ``tasks/phase-5-plan.md``):

- T1 (this commit area):``McpServerConfig`` + ``Settings`` fields
- T2:``McpClient`` — single-server lifecycle via ``mcp`` SDK
- T3:``McpToolAdapter`` — BaseTool subclass synthesized from
  ``inputSchema``
- T4:``McpClientPool`` — N-server orchestration + respawn
- T5:CLI bootstrap wiring + ``trust_source`` log field
- T6:end-to-end smoke (real ``@modelcontextprotocol/server-filesystem``)
- T7:coverage + retro

Public API (so far):

    from openharness.mcp import McpServerConfig
"""

from __future__ import annotations

from openharness.mcp.adapter import McpToolAdapter
from openharness.mcp.client import McpCallError, McpClient, McpInitError
from openharness.mcp.config import McpEnvironmentPosture, McpServerConfig
from openharness.mcp.pool import McpClientPool

__all__ = [
    "McpCallError",
    "McpClient",
    "McpClientPool",
    "McpEnvironmentPosture",
    "McpInitError",
    "McpServerConfig",
    "McpToolAdapter",
]
