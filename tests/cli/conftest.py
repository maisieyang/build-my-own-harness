"""Fixtures for the CLI test sub-package.

Like ``tests/config/conftest.py``, CLI unit tests must run from a clean
environment. The developer's shell may have ``OPENHARNESS_API_KEY`` set
for the integration test -- if that value reached the unit-test code
path, ``Settings()`` would no longer raise ``ValidationError`` on the
"missing key" tests, and assertions about the default model would flap
depending on shell state.

The fixture is autouse but **opts out** for ``@pytest.mark.integration``
tests, which require the real env vars to talk to a Provider. Without
this carve-out, the autouse fixture would wipe the credentials right
before the integration test ran -- producing a confusing "missing key"
failure even though the user did set the env correctly.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_openharness_env(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if "integration" in request.keywords:
        # Integration tests need the real env intact -- otherwise we'd
        # wipe the API key the test is about to use.
        return
    for var in ("OPENHARNESS_API_KEY", "OPENHARNESS_BASE_URL", "OPENHARNESS_MODEL"):
        monkeypatch.delenv(var, raising=False)
