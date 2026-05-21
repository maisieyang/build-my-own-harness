"""Fixtures for the bundles test sub-package.

Bundles tests focus on the **bundle composition** logic — the layered
``Settings`` / ``ToolRegistry`` / ``HookRegistry`` mutation flow. They
construct ``Settings(deny_paths=...)`` without explicit ``api_key`` /
``base_url`` kwargs because **those fields are irrelevant to what the
test is asserting** (no API call is made, no Provider is contacted).

The repo-root ``_clean_openharness_env`` fixture wipes ``OPENHARNESS_*``
env vars and isolates cwd + ``HOME`` so no stray ``.env`` slips in. But
that leaves required ``api_key`` / ``base_url`` fields unset → Settings
``ValidationError`` on construction. Locally this masked itself because
a project-root ``.env`` happened to satisfy the requirement; on CI the
mask is gone and the bundle tests fail.

Fix:provide fake-but-valid placeholders via env var so any
``Settings()`` construction in this sub-package succeeds. The autouse
ordering pytest gives us (root fixture → subpackage fixture) means
this fixture runs **after** ``_clean_openharness_env`` has cleared the
env, so we're setting from a known-empty baseline rather than risking
a leak from the dev's shell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _provide_bundle_test_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Provide placeholder credentials so ``Settings()`` constructs cleanly.

    Bundles tests don't exercise the auth path — they just need a valid
    Settings instance to compose layered overrides against.
    """
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-fake-bundle-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
