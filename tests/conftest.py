"""Shared pytest fixtures.

The repo-root ``_clean_openharness_env`` autouse fixture isolates every
non-integration test from three sources of environment contamination:

1. ``OPENHARNESS_*`` env vars set in the developer's shell — would
   leak into ``Settings()`` and hide ``ValidationError`` paths the
   tests are designed to exercise (or, conversely, succeed where the
   test expects to fail).
2. **Project-level** ``./.env`` — ``pydantic-settings`` resolves
   ``env_file=".env"`` relative to cwd, so a dev-side ``.env`` would
   silently satisfy required Settings fields on a local run while CI
   (with no ``.env``) would fail. ``chdir(tmp_path)`` redirects cwd
   to an empty tmp dir, removing this path.
3. **User-global** ``~/.openharness/{.env,commands,bundles,hooks}`` —
   read by ``cli._load_settings()`` (`P7-T2` D25.2) and by the
   ``commands`` / ``bundles`` / ``hooks`` filesystem discovery paths,
   all of which call ``Path.home()`` directly. Pointing ``HOME`` at
   ``tmp_path`` ensures these directories don't exist for tests, so
   tests against an "empty user environment" don't pick up the dev's
   real ``~/.openharness/`` content.

Integration tests opt out via ``@pytest.mark.integration`` — they
need the real env + real ``~/.openharness/`` to talk to a Provider
and discover real user-installed bundles/skills/hooks.

This fixture lives at repo root rather than per-subpackage
``tests/{config,cli,bundles}/conftest.py`` because the underlying
isolation guarantee is the same for every Settings-touching test —
duplicating per-subpackage created the gap that surfaced as 15
failing tests in ``tests/bundles/`` on CI (commit ``e75c721`` post-
mortem). One root fixture, integration opt-out, no per-subpackage
override.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_openharness_env(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Strip OPENHARNESS_* env vars + isolate cwd + isolate user-global home.

    Uses ``tmp_path_factory`` (not the per-test ``tmp_path``) so the
    isolation dirs are **completely disjoint** from any tmp dirs a test
    might create for its own purposes. If we shared ``tmp_path`` between
    isolation and the test's working area, ``sanitize_path("~/.ssh/...",
    cwd=tmp_path)`` would resolve the literal ``~`` as a subdirectory of
    process cwd, end up inside ``tmp_path``, and break the "outside cwd
    ⇒ <redacted>" assertion.

    Integration tests (`@pytest.mark.integration`) opt out — see module
    docstring for the reasoning.
    """
    if "integration" in request.keywords:
        return
    for var in ("OPENHARNESS_API_KEY", "OPENHARNESS_BASE_URL", "OPENHARNESS_MODEL"):
        monkeypatch.delenv(var, raising=False)
    cwd_dir = tmp_path_factory.mktemp("isolation_cwd")
    home_dir = tmp_path_factory.mktemp("isolation_home")
    monkeypatch.chdir(cwd_dir)
    monkeypatch.setenv("HOME", str(home_dir))
    # P11-T5: extraction defaults ON in production (D29.5 + D29.8) but
    # tests using a stub LLM client get spurious ``memory_extract_failed``
    # warnings since the stub can't satisfy the JSON-output extraction
    # prompt. Default OFF for tests; tests that exercise extraction
    # explicitly set ``OPENHARNESS_EXTRACTION__ENABLED=true``.
    monkeypatch.setenv("OPENHARNESS_EXTRACTION__ENABLED", "false")
