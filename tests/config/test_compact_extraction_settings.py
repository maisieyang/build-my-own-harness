"""Tests for ``CompactSettings`` — P11-T5.5a.

Three surfaces:

1. Defaults match D29.8
2. Nested env vars via ``__`` delimiter
3. Programmatic construction

**Phase 17 D37.3**: the parallel ``ExtractionSettings`` test classes
this file used to host were removed when the Phase 11 extraction
stack was deleted. CompactSettings is unchanged.
"""

from __future__ import annotations

import pytest

from openharness.config.settings import (
    CompactSettings,
    Settings,
)


def _seed_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")


class TestCompactSettingsDefaults:
    def test_defaults_match_d29_8(self) -> None:
        s = CompactSettings()
        assert s.enabled is True
        assert s.threshold_ratio == 0.83
        assert s.preserve_recent_messages == 12
        assert s.full_compact_max_tokens == 20_000
        assert s.full_compact_timeout_s == 120.0

    def test_threshold_ratio_bounds(self) -> None:
        # 0.0 and 1.0 inclusive per Field(ge=0.0, le=1.0)
        assert CompactSettings(threshold_ratio=0.0).threshold_ratio == 0.0
        assert CompactSettings(threshold_ratio=1.0).threshold_ratio == 1.0
        with pytest.raises(Exception, match="less than or equal to 1"):
            CompactSettings(threshold_ratio=1.5)
        with pytest.raises(Exception, match="greater than or equal to 0"):
            CompactSettings(threshold_ratio=-0.1)


class TestSettingsNestedDefaults:
    def test_compact_field_uses_default_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        settings = Settings()
        assert isinstance(settings.compact, CompactSettings)
        assert settings.compact.threshold_ratio == 0.83


class TestCompactNestedEnvOverride:
    def test_threshold_ratio_via_double_underscore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_COMPACT__THRESHOLD_RATIO", "0.5")
        settings = Settings()
        assert settings.compact.threshold_ratio == 0.5

    def test_enabled_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_COMPACT__ENABLED", "false")
        settings = Settings()
        assert settings.compact.enabled is False

    def test_multiple_fields_simultaneously(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_COMPACT__THRESHOLD_RATIO", "0.7")
        monkeypatch.setenv("OPENHARNESS_COMPACT__FULL_COMPACT_MAX_TOKENS", "10000")
        monkeypatch.setenv("OPENHARNESS_COMPACT__FULL_COMPACT_TIMEOUT_S", "15.0")
        monkeypatch.setenv("OPENHARNESS_COMPACT__PRESERVE_RECENT_MESSAGES", "24")
        settings = Settings()
        assert settings.compact.threshold_ratio == 0.7
        assert settings.compact.preserve_recent_messages == 24
        assert settings.compact.full_compact_max_tokens == 10_000
        assert settings.compact.full_compact_timeout_s == 15.0

    def test_preserve_recent_messages_must_be_positive(self) -> None:
        with pytest.raises(Exception, match="greater than or equal to 1"):
            CompactSettings(preserve_recent_messages=0)


class TestProgrammaticConstruction:
    def test_pass_compact_settings_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        custom = CompactSettings(enabled=False, threshold_ratio=0.5)
        settings = Settings(compact=custom)
        assert settings.compact.enabled is False
        assert settings.compact.threshold_ratio == 0.5
