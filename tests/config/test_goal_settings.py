"""D48 T3 — goal settings 字段(默认值 + env 覆盖)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.config import Settings

if TYPE_CHECKING:
    import pytest


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")


class TestGoalSettingsDefaults:
    def test_interactive_loop_has_no_default_turn_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _base_env(monkeypatch)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.max_turns is None

    def test_judge_model_defaults_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _base_env(monkeypatch)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.goal_judge_model is None

    def test_max_auto_turns_defaults_to_unbounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _base_env(monkeypatch)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.goal_max_auto_turns is None


class TestGoalSettingsEnvOverride:
    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _base_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_GOAL_JUDGE_MODEL", "qwen-turbo")
        monkeypatch.setenv("OPENHARNESS_GOAL_MAX_AUTO_TURNS", "5")
        monkeypatch.setenv("OPENHARNESS_MAX_TURNS", "50")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.goal_judge_model == "qwen-turbo"
        assert settings.goal_max_auto_turns == 5
        assert settings.max_turns == 50
