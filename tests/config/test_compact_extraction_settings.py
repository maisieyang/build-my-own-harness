"""Tests for ``CompactSettings`` + ``ExtractionSettings`` — P11-T5.5a.

Three surfaces:

1. Defaults match D29.8
2. Nested env vars via ``__`` delimiter
3. Programmatic construction
"""

from __future__ import annotations

import pytest

from openharness.config.settings import (
    CompactSettings,
    ExtractionSettings,
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
        assert s.full_compact_max_tokens == 20_000
        assert s.full_compact_timeout_s == 25.0

    def test_threshold_ratio_bounds(self) -> None:
        # 0.0 and 1.0 inclusive per Field(ge=0.0, le=1.0)
        assert CompactSettings(threshold_ratio=0.0).threshold_ratio == 0.0
        assert CompactSettings(threshold_ratio=1.0).threshold_ratio == 1.0
        with pytest.raises(Exception, match="less than or equal to 1"):
            CompactSettings(threshold_ratio=1.5)
        with pytest.raises(Exception, match="greater than or equal to 0"):
            CompactSettings(threshold_ratio=-0.1)


class TestExtractionSettingsDefaults:
    def test_defaults_match_d29_8(self) -> None:
        s = ExtractionSettings()
        assert s.enabled is True
        assert s.max_records_per_turn == 3
        assert s.model is None  # default → use main convo model
        assert s.timeout_s == 30.0

    def test_max_records_accepts_zero(self) -> None:
        assert ExtractionSettings(max_records_per_turn=0).max_records_per_turn == 0


class TestSettingsNestedDefaults:
    def test_compact_field_uses_default_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        settings = Settings()
        assert isinstance(settings.compact, CompactSettings)
        assert settings.compact.threshold_ratio == 0.83

    def test_extraction_field_uses_default_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        # Conftest sets OPENHARNESS_EXTRACTION__ENABLED=false for test
        # isolation (stub LLMs can't satisfy extraction's JSON-output
        # prompt). Undo to verify production default.
        monkeypatch.delenv("OPENHARNESS_EXTRACTION__ENABLED")
        settings = Settings()
        assert isinstance(settings.extraction, ExtractionSettings)
        assert settings.extraction.enabled is True


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
        settings = Settings()
        assert settings.compact.threshold_ratio == 0.7
        assert settings.compact.full_compact_max_tokens == 10_000
        assert settings.compact.full_compact_timeout_s == 15.0


class TestExtractionNestedEnvOverride:
    def test_enabled_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_EXTRACTION__ENABLED", "false")
        settings = Settings()
        assert settings.extraction.enabled is False

    def test_model_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_EXTRACTION__MODEL", "qwen-turbo")
        settings = Settings()
        assert settings.extraction.model == "qwen-turbo"

    def test_max_records_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_EXTRACTION__MAX_RECORDS_PER_TURN", "5")
        settings = Settings()
        assert settings.extraction.max_records_per_turn == 5


class TestProgrammaticConstruction:
    def test_pass_compact_settings_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        custom = CompactSettings(enabled=False, threshold_ratio=0.5)
        settings = Settings(compact=custom)
        assert settings.compact.enabled is False
        assert settings.compact.threshold_ratio == 0.5

    def test_pass_extraction_settings_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        custom = ExtractionSettings(enabled=False, model="qwen-turbo")
        settings = Settings(extraction=custom)
        assert settings.extraction.enabled is False
        assert settings.extraction.model == "qwen-turbo"
