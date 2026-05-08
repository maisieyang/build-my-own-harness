"""System prompt assembly -- :func:`build_system_prompt` plus the
:class:`EnvironmentInfo` it consumes.

Per the P2-T5 Three-Axis discussion (D11.1 - D11.6) and ``decisions/06`` D6.5:
this module exposes one public function with a stable signature that later
phases extend without renaming. Phase 3 will inject personalization rules;
Phase 4 will inject memory excerpts; both arrive as additional sections in
the same Markdown structure.

P2-T5 sub-units:

- 5a (this commit): :class:`EnvironmentInfo` + :func:`detect_environment`.
- 5b: :func:`build_system_prompt` -- assembles base instructions, tool
  catalog, and environment block.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


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
