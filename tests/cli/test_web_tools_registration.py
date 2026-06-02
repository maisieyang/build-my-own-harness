"""Tests for ``_maybe_register_web_tools`` (P14-T4).

The helper is the conditional bridge from CLI flag → tool registry.
When ``--enable-web`` is OFF (or unset), it must be a no-op so the
tool catalog stays byte-identical to v0.2.0 (invariant T14-* in
``decisions/29``). When ON, it must construct a provider, register
both tools, and surface a clear remediation error when the API key
is missing.

We exercise the helper directly rather than through a full ``oh ask``
invocation so the test stays focused on the registration semantics
and doesn't pull in the engine + LLM dispatch chain.
"""

from __future__ import annotations

import pytest
import typer
from pydantic import SecretStr

from openharness.cli import _maybe_register_web_tools
from openharness.config.settings import Settings
from openharness.tools import create_default_tool_registry


def _settings(
    *,
    enabled: bool = False,
    api_key: str | None = None,
) -> Settings:
    """Build a Settings instance with web fields configured for the test."""
    s = Settings(api_key="placeholder", base_url="https://example/", model="qwen-plus")
    s.web.enabled = enabled
    if api_key is not None:
        s.web.api_key = SecretStr(api_key)
    return s


class TestNoOpWhenWebDisabled:
    def test_enable_web_false_does_not_touch_registry(self) -> None:
        registry = create_default_tool_registry()
        before = set({t.name for t in registry.list_tools()})
        _maybe_register_web_tools(registry, enable_web=False, settings=_settings())
        after = set({t.name for t in registry.list_tools()})
        assert after == before
        assert "WebSearch" not in after
        assert "WebFetch" not in after

    def test_enable_web_false_with_api_key_still_noop(self) -> None:
        """Even if the API key IS set, --enable-web=False means no
        registration (user explicitly opted out)."""
        registry = create_default_tool_registry()
        before = set({t.name for t in registry.list_tools()})
        _maybe_register_web_tools(
            registry, enable_web=False, settings=_settings(api_key="tvly-xxx")
        )
        assert set({t.name for t in registry.list_tools()}) == before


class TestExitWhenKeyMissing:
    def test_enable_web_true_no_api_key_raises_typer_exit(self) -> None:
        registry = create_default_tool_registry()
        with pytest.raises(typer.Exit) as excinfo:
            _maybe_register_web_tools(registry, enable_web=True, settings=_settings())
        # typer.Exit doesn't carry the message; the helper prints to
        # stderr. Verify the registry was NOT mutated regardless.
        assert excinfo.value.exit_code == 1
        assert "WebSearch" not in {t.name for t in registry.list_tools()}


class TestRegistrationWhenEnabledAndKeyed:
    def test_enable_web_true_with_key_registers_both_tools(self) -> None:
        registry = create_default_tool_registry()
        _maybe_register_web_tools(
            registry,
            enable_web=True,
            settings=_settings(api_key="tvly-test-key"),
        )
        names = set({t.name for t in registry.list_tools()})
        assert "WebSearch" in names
        assert "WebFetch" in names
        # The 6 original tools must still be there — registration is
        # additive.
        for original in ("Read", "Write", "Edit", "Bash", "Grep", "Agent"):
            assert original in names
