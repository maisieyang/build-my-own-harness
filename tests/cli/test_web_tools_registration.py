"""Tests for ``_maybe_register_web_tools`` (P14-T4 + Phase 14.5 revision).

The helper is the conditional bridge from CLI flag → tool registry,
and now also encodes the **graceful no-key degrade** path:

- ``--enable-web=False`` (or default + explicit OFF) → no-op, return
  False.
- ``--enable-web=True`` AND ``api_key is None`` AND
  ``explicit_flag=True`` (user typed ``--enable-web``) →
  ``typer.Exit(1)`` with remediation.
- ``--enable-web=True`` AND ``api_key is None`` AND
  ``explicit_flag=False`` (default-ON path) → skip silently with a
  brief stderr hint, return False (system prompt picks up the
  anti-substitution paragraph).
- ``--enable-web=True`` AND ``api_key`` set → register both tools,
  return True.

We exercise the helper directly so the test stays focused on
registration semantics and doesn't pull in the engine + LLM
dispatch chain.
"""

from __future__ import annotations

import pytest
import typer
from pydantic import SecretStr

from openharness.cli import _maybe_register_web_tools
from openharness.config.settings import Settings, WebSettings
from openharness.tools import create_default_tool_registry


def _settings(
    *,
    enabled: bool = True,  # Phase 14.5: default ON
    api_key: str | None = None,
) -> Settings:
    """Build a Settings instance with web fields configured for the test."""
    s = Settings(api_key="placeholder", base_url="https://example/", model="qwen-plus")
    s.web.enabled = enabled
    if api_key is not None:
        s.web.api_key = SecretStr(api_key)
    return s


class TestDefaults:
    """Phase 14.5: WebSettings.enabled default flipped to True."""

    def test_websettings_default_enabled_is_true(self) -> None:
        s = WebSettings()
        assert s.enabled is True

    def test_websettings_default_api_key_is_none(self) -> None:
        s = WebSettings()
        assert s.api_key is None


class TestNoOpWhenWebDisabled:
    def test_enable_web_false_returns_false_and_no_op(self) -> None:
        registry = create_default_tool_registry()
        before = set({t.name for t in registry.list_tools()})
        result = _maybe_register_web_tools(
            registry, enable_web=False, settings=_settings(), explicit_flag=True
        )
        assert result is False
        after = set({t.name for t in registry.list_tools()})
        assert after == before
        assert "WebSearch" not in after
        assert "WebFetch" not in after

    def test_enable_web_false_with_api_key_still_noop(self) -> None:
        """--no-enable-web explicit override beats both settings + key."""
        registry = create_default_tool_registry()
        before = set({t.name for t in registry.list_tools()})
        result = _maybe_register_web_tools(
            registry,
            enable_web=False,
            settings=_settings(api_key="tvly-xxx"),
            explicit_flag=True,
        )
        assert result is False
        assert set({t.name for t in registry.list_tools()}) == before


class TestGracefulDegradeWhenDefaultOnNoKey:
    """Phase 14.5: default-ON + no key = silent skip, NOT exit."""

    def test_default_on_no_key_returns_false_no_exit_silently(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        registry = create_default_tool_registry()
        # explicit_flag=False simulates "user didn't type --enable-web,
        # default ON kicked in" → silent graceful degrade.
        result = _maybe_register_web_tools(
            registry, enable_web=True, settings=_settings(), explicit_flag=False
        )
        assert result is False
        # Stderr is silent — emitting on every ``oh ask`` invocation
        # would be unacceptable UX. The system prompt's
        # anti-substitution paragraph carries the contextual hint.
        captured = capsys.readouterr()
        assert captured.err == ""
        # Registry NOT mutated.
        assert "WebSearch" not in {t.name for t in registry.list_tools()}


class TestExplicitFlagNoKey:
    """Phase 14.5: --enable-web explicit + no key = hard fail."""

    def test_explicit_enable_web_no_key_raises_typer_exit(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        registry = create_default_tool_registry()
        with pytest.raises(typer.Exit) as excinfo:
            _maybe_register_web_tools(
                registry, enable_web=True, settings=_settings(), explicit_flag=True
            )
        assert excinfo.value.exit_code == 1
        # Hard-fail message must still surface the same remediation.
        captured = capsys.readouterr()
        assert "OPENHARNESS_WEB__API_KEY" in captured.err
        assert "tavily.com" in captured.err
        assert "WebSearch" not in {t.name for t in registry.list_tools()}


class TestRegistrationWhenEnabledAndKeyed:
    def test_default_on_with_key_registers_both_tools(self) -> None:
        registry = create_default_tool_registry()
        result = _maybe_register_web_tools(
            registry,
            enable_web=True,
            settings=_settings(api_key="tvly-test-key"),
            explicit_flag=False,
        )
        assert result is True
        names = set({t.name for t in registry.list_tools()})
        assert "WebSearch" in names
        assert "WebFetch" in names
        # The 6 original tools must still be there — registration is
        # additive.
        for original in ("Read", "Write", "Edit", "Bash", "Grep", "Agent"):
            assert original in names

    def test_explicit_enable_web_with_key_also_registers(self) -> None:
        """Explicit ``--enable-web`` + key set behaves identically to
        default-ON + key set: both register, both return True."""
        registry = create_default_tool_registry()
        result = _maybe_register_web_tools(
            registry,
            enable_web=True,
            settings=_settings(api_key="tvly-test-key"),
            explicit_flag=True,
        )
        assert result is True
        names = set({t.name for t in registry.list_tools()})
        assert "WebSearch" in names
        assert "WebFetch" in names
