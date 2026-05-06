"""Fixtures for the config test sub-package.

Settings tests must run from a clean environment: any ``OPENHARNESS_*`` env
vars set in the developer's shell would otherwise contaminate test outcomes.
The ``_clean_openharness_env`` fixture is autouse so every test in this
package starts from a blank slate.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_openharness_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every ``OPENHARNESS_*`` env var before each test."""
    for var in ("OPENHARNESS_API_KEY", "OPENHARNESS_BASE_URL", "OPENHARNESS_MODEL"):
        monkeypatch.delenv(var, raising=False)
