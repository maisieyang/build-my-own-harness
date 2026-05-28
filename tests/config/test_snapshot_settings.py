"""Tests for ``SnapshotSettings`` — P12-T3-3a.

Three surfaces:

1. Defaults match D30.8
2. Nested env vars via ``__`` delimiter
3. Programmatic construction
"""

from __future__ import annotations

import pytest

from openharness.config.settings import Settings, SnapshotSettings


def _seed_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")


class TestSnapshotSettingsDefaults:
    def test_defaults_match_d30_8(self) -> None:
        s = SnapshotSettings()
        assert s.enabled is True
        assert s.max_age_warn_days == 7

    def test_max_age_accepts_zero(self) -> None:
        # 0 disables age warning per D30.8 docstring
        assert SnapshotSettings(max_age_warn_days=0).max_age_warn_days == 0

    def test_max_age_rejects_negative(self) -> None:
        with pytest.raises(Exception, match="greater than or equal"):
            SnapshotSettings(max_age_warn_days=-1)


class TestSettingsNestedDefaults:
    def test_snapshot_field_uses_default_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        settings = Settings()
        assert isinstance(settings.snapshot, SnapshotSettings)
        assert settings.snapshot.enabled is True
        assert settings.snapshot.max_age_warn_days == 7


class TestSnapshotNestedEnvOverride:
    def test_enabled_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__ENABLED", "false")
        settings = Settings()
        assert settings.snapshot.enabled is False

    def test_max_age_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_SNAPSHOT__MAX_AGE_WARN_DAYS", "30")
        settings = Settings()
        assert settings.snapshot.max_age_warn_days == 30


class TestProgrammaticConstruction:
    def test_pass_snapshot_settings_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        custom = SnapshotSettings(enabled=False, max_age_warn_days=30)
        settings = Settings(snapshot=custom)
        assert settings.snapshot.enabled is False
        assert settings.snapshot.max_age_warn_days == 30
