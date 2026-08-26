"""``_build_client`` must hand ``settings.extra_body`` to the API client —
the last hop of the OPENHARNESS_EXTRA_BODY chain (RUNLOG 节点 8):
env → Settings → client → SDK ``extra_body``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import openharness.cli as cli_module
from openharness.config import Settings

if TYPE_CHECKING:
    import pytest


class TestBuildClientExtraBody:
    def test_extra_body_reaches_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.invalid/v1")
        monkeypatch.setenv("OPENHARNESS_EXTRA_BODY", '{"enable_thinking": false}')

        client = cli_module._build_client(Settings())

        assert client._extra_body == {"enable_thinking": False}
        assert client._sdk.max_retries == 0

    def test_default_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.invalid/v1")

        client = cli_module._build_client(Settings())

        assert client._extra_body is None
