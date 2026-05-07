"""Fixtures for the CLI test sub-package.

Like ``tests/config/conftest.py``, CLI unit tests must run from a clean
environment. The developer's shell may have ``OPENHARNESS_API_KEY`` set
for the integration test -- if that value reached the unit-test code
path, ``Settings()`` would no longer raise ``ValidationError`` on the
"missing key" tests, and assertions about the default model would flap
depending on shell state. Same for a project-root ``.env`` file used to
run ``oh ask`` locally: pydantic-settings would read it as a fallback,
again hiding the "missing key" path. So we also chdir to an empty tmp
dir for every non-integration test.

The fixture is autouse but **opts out** for ``@pytest.mark.integration``
tests, which require the real env vars + project cwd to talk to a Provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_openharness_env(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if "integration" in request.keywords:
        # Integration tests need the real env intact -- otherwise we'd
        # wipe the API key the test is about to use.
        return
    for var in ("OPENHARNESS_API_KEY", "OPENHARNESS_BASE_URL", "OPENHARNESS_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
