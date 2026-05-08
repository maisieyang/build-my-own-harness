"""System prompt assembly -- :func:`build_system_prompt` plus the
:class:`EnvironmentInfo` it consumes.

Per the P2-T5 Three-Axis discussion (D11.1 - D11.6) and ``decisions/06`` D6.5:
this module exposes one public function with a stable signature that later
phases extend without renaming. Phase 3 will inject personalization rules;
Phase 4 will inject memory excerpts; both arrive as additional sections in
the same Markdown structure.

P2-T5 sub-units:

- 5a: :class:`EnvironmentInfo` + :func:`detect_environment`.
- 5b (this commit): :func:`build_system_prompt` -- assembles base
  instructions, tool catalog, and environment block.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openharness.protocols import ToolSpec


@dataclass(frozen=True)
class EnvironmentInfo:
    """Runtime context the LLM benefits from knowing.

    Per D11.1: five fields. Skipped (intentionally) -- git_branch (changes
    mid-session), venv name (detection complexity), locale (not actionable).
    Phase 3 personalization may add fields when concrete needs surface.
    """

    os_name: str
    os_version: str
    shell: str
    cwd: Path
    python_version: str


def detect_environment() -> EnvironmentInfo:
    """Sample the current environment and build an :class:`EnvironmentInfo`.

    Per D11.2: pure stdlib reads, no exceptions. Each source has a fallback:
    ``SHELL`` defaults to ``/bin/sh`` if the env var is unset; the
    ``platform.*`` calls return empty strings rather than raising on weird
    platforms.
    """
    return EnvironmentInfo(
        os_name=platform.system(),
        os_version=platform.release(),
        shell=os.environ.get("SHELL", "/bin/sh"),
        cwd=Path.cwd(),
        python_version=platform.python_version(),
    )


# Per D11.5: short base instructions with a D8.5-aligned line about errors
# being recoverable (matches how run_query feeds is_error=True back to LLM).
_BASE_INSTRUCTIONS = (
    "You are OpenHarness, an LLM agent. You can request tool calls to interact "
    "with the user's filesystem and shell. Tools execute in the working "
    "directory shown in the Environment section. When a tool returns an error "
    "result, read the message and adapt -- most errors are recoverable."
)

# Per D11.6: empty registry still gets a `## Tools` section so the prompt
# structure is stable across configurations and snapshot tests don't flake.
_NO_TOOLS_SENTINEL = "(no tools registered)"


def build_system_prompt(
    tools: list[ToolSpec],
    env: EnvironmentInfo,
) -> str:
    """Assemble the system prompt from base instructions, tool catalog, and
    environment block.

    Per ``decisions/06`` D6.5: this function's signature is the load-bearing
    contract; Phase 3 personalization and Phase 4 memory both extend the
    body by appending more sections, not by changing the call surface.

    Sections are joined by blank lines and use Markdown ``##`` headers
    (D11.3) so future additions read naturally and snapshot tests can match
    on section markers.
    """
    return "\n\n".join(
        [
            _BASE_INSTRUCTIONS,
            _format_tools_section(tools),
            _format_environment_section(env),
        ]
    )


def _format_tools_section(tools: list[ToolSpec]) -> str:
    """Per D11.4: full ``ToolSpec.description`` verbatim (not truncated to a
    single line). Tools are rendered as a Markdown bullet list."""
    if not tools:
        body = _NO_TOOLS_SENTINEL
    else:
        body = "\n".join(f"- **{tool.name}** -- {tool.description}" for tool in tools)
    return f"## Tools\n\n{body}"


def _format_environment_section(env: EnvironmentInfo) -> str:
    """Render :class:`EnvironmentInfo` as a Markdown bullet list."""
    body = (
        f"- OS: {env.os_name} {env.os_version}\n"
        f"- Shell: {env.shell}\n"
        f"- cwd: {env.cwd}\n"
        f"- Python: {env.python_version}"
    )
    return f"## Environment\n\n{body}"
