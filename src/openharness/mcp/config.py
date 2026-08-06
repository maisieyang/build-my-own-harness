"""``McpServerConfig`` — declarative configuration for one MCP server — P5-T1.

Per ``decisions/11`` D15.2:Phase 5 ships **two** registration paths —
``Settings.mcp_servers`` (env-driven) and direct Python construction
(``McpClientPool(configs=[...])`` for tests / programmatic use). This
module defines the dataclass both paths feed into.

Shape:

::

    @dataclass(frozen=True)
    class McpServerConfig:
        name: str                  # "github" — also serves as namespace prefix
        command: tuple[str, ...]   # ("npx", "@modelcontextprotocol/server-github")
        env: dict[str, str]        # API tokens etc., merged into subprocess env

Design choices:

- **Frozen dataclass**:configs are read-only after construction. The
  pool spawns one subprocess per config and never mutates the spec.
- **``command`` as tuple, not list**:tuple matches the immutability
  intent of frozen dataclass(list inside frozen dataclass is mutable
  via methods, surprising). ``subprocess.exec`` / ``mcp.client.stdio``
  accept any iterable;tuple suffices.
- **``env`` stays as ``dict``**:Python typing convention. Mutation is
  technically possible but discouraged;the pool copies it into the
  subprocess's ``env`` and never reads it again.
- **``name`` validation regex**:per ``tasks/phase-5-plan.md`` T1
  acceptance, names must be safe identifiers because they're used as
  namespace prefixes (``"github"`` → ``"github.tool_x"``). Disallow
  characters that would break tool name parsing or shell injection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# Server names map directly into tool namespaces (D15.3:
# ``<Server>.<Tool>``). Restrict to safe identifier characters so the
# namespace prefix can never collide with tool-name dots or be
# misinterpreted by downstream parsing.
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class McpEnvironmentPosture(str, Enum):
    """Environment authority granted to a stdio MCP subprocess."""

    MINIMAL = "minimal"


@dataclass(frozen=True)
class McpServerConfig:
    """Configuration to spawn + connect to one MCP server."""

    name: str
    command: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    sandbox: bool = False
    environment_posture: McpEnvironmentPosture = McpEnvironmentPosture.MINIMAL

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid MCP server name {self.name!r}: must match "
                f"{_NAME_PATTERN.pattern} (alphanumeric + ``_-``, starts with letter)"
            )
        if not self.command:
            raise ValueError(
                "MCP server command must be non-empty "
                "(e.g., ('npx', '-y', '@modelcontextprotocol/server-filesystem'))"
            )
