"""Fixtures for the config test sub-package.

Settings tests must run from a clean environment: any ``OPENHARNESS_*`` env
vars set in the developer's shell — or a project-root ``.env`` file used for
local ``oh ask`` runs — would otherwise contaminate test outcomes. The
``_clean_openharness_env`` fixture is autouse so every test in this package
starts from a blank slate (no env vars + cwd switched to an empty tmp dir,
so ``pydantic-settings`` cannot fall back to a stray ``.env``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_openharness_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip ``OPENHARNESS_*`` env vars and isolate cwd from project ``.env``.

    The ``chdir`` step is what fixes the contamination from a developer-side
    ``.env`` file in the project root: pydantic-settings resolves ``env_file=".env"``
    relative to cwd, so with cwd pointing at an empty tmp dir there is no
    ``.env`` to read.
    """
    for var in ("OPENHARNESS_API_KEY", "OPENHARNESS_BASE_URL", "OPENHARNESS_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
