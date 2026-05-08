"""Tests for the Settings configuration layer (P1-T4 sub-unit 4a).

The :class:`Settings` class loads OpenHarness's runtime configuration from
environment variables. It defines four user-facing contracts:

1. **Required**: the user must provide an API key + base URL, or get a clear
   ``ValidationError`` naming the missing field.
2. **Optional default**: ``model`` defaults to ``qwen-plus`` (per
   ``decisions/05-cli.md`` D5.3) but can be overridden via env var.
3. **Provider-neutral prefix**: env vars are namespaced under
   ``OPENHARNESS_`` regardless of which Provider the base URL points to
   (D5.1). Unprefixed vars must not leak into Settings.
4. **.env file support**: settings can be loaded from a ``.env`` file, with
   real environment variables taking precedence over file values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from openharness.config.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path


class TestRequiredFieldsLoading:
    """All three settings populate from ``OPENHARNESS_``-prefixed env vars."""

    def test_loads_all_three_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test-123")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("OPENHARNESS_MODEL", "qwen-max")

        settings = Settings()

        assert settings.api_key == "sk-test-123"
        assert settings.base_url == "https://example.com/v1"
        assert settings.model == "qwen-max"

    def test_only_recognizes_prefixed_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Unprefixed env vars must not populate Settings — otherwise a stray
        # ``API_KEY`` from the user's shell would silently override the
        # ``OPENHARNESS_API_KEY`` contract.
        monkeypatch.setenv("API_KEY", "leaked-from-shell")
        monkeypatch.setenv("OPENHARNESS_API_KEY", "the-real-key")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")

        settings = Settings()

        assert settings.api_key == "the-real-key"


class TestDefaults:
    """``model`` has a sensible default; the user only needs to set the key + URL."""

    def test_default_model_is_qwen_plus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")
        # OPENHARNESS_MODEL deliberately not set.

        settings = Settings()

        assert settings.model == "qwen-plus"

    def test_default_permission_mode_is_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.permissions import PermissionMode

        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")

        settings = Settings()

        assert settings.permission_mode is PermissionMode.DEFAULT


class TestPermissionModeFromEnv:
    """OPENHARNESS_PERMISSION_MODE env var sets the permission policy."""

    @pytest.mark.parametrize(
        ("env_value", "expected"),
        [
            ("default", "DEFAULT"),
            ("auto", "AUTO"),
            ("dry_run", "DRY_RUN"),
        ],
    )
    def test_each_mode_value_loads(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_value: str,
        expected: str,
    ) -> None:
        from openharness.permissions import PermissionMode

        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("OPENHARNESS_PERMISSION_MODE", env_value)

        settings = Settings()

        assert settings.permission_mode is PermissionMode[expected]

    def test_invalid_mode_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("OPENHARNESS_PERMISSION_MODE", "yolo")

        with pytest.raises(ValidationError):
            Settings()


class TestMissingRequiredFields:
    """Missing required fields produce a ``ValidationError`` naming the field."""

    def test_missing_api_key_raises_validation_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")

        with pytest.raises(ValidationError) as exc_info:
            Settings()

        # The error must name the missing field so the user knows which env
        # var to set. We match case-insensitively so we don't bind to the
        # exact rendering format of pydantic's error messages.
        assert "api_key" in str(exc_info.value).lower()

    def test_missing_base_url_raises_validation_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")

        with pytest.raises(ValidationError) as exc_info:
            Settings()

        assert "base_url" in str(exc_info.value).lower()

    def test_completely_unset_env_raises_validation_error(self) -> None:
        # No OPENHARNESS_* vars set at all (autouse fixture cleared them).
        with pytest.raises(ValidationError):
            Settings()


class TestDotEnvFile:
    """A ``.env`` file is honored; real env vars override file values."""

    def test_loads_from_env_file_when_real_env_unset(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENHARNESS_API_KEY=key-from-file\nOPENHARNESS_BASE_URL=https://file.example.com/v1\n"
        )

        settings = Settings(_env_file=str(env_file))

        assert settings.api_key == "key-from-file"
        assert settings.base_url == "https://file.example.com/v1"

    def test_real_env_var_overrides_dotenv_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENHARNESS_API_KEY=key-from-file\nOPENHARNESS_BASE_URL=https://file.example.com/v1\n"
        )
        monkeypatch.setenv("OPENHARNESS_API_KEY", "key-from-real-env")

        settings = Settings(_env_file=str(env_file))

        # Real env wins for the field it sets; file fills in what's missing.
        assert settings.api_key == "key-from-real-env"
        assert settings.base_url == "https://file.example.com/v1"
