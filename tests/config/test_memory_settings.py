"""Tests for :class:`MemorySettings` + ``Settings.enable_memory`` — P10-T4.4e.

Two surfaces:

1. **Defaults match D28.10**: ``enable_memory=True`` (opt-OUT),
   nested ``memory`` defaults per :class:`MemorySettings` field
   declarations.
2. **Env-var overrides**:
   - Single field: ``OPENHARNESS_ENABLE_MEMORY=false``
   - Nested field: ``OPENHARNESS_MEMORY__MAX_FILES=10`` (double
     underscore delimiter, the ``env_nested_delimiter`` setting).
"""

from __future__ import annotations

import pytest

from openharness.config.settings import MemorySettings, Settings


def _seed_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the two required Settings fields so construction succeeds."""
    monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://example.com/v1")


class TestMemorySettingsDefaults:
    def test_defaults_match_d28_10(self) -> None:
        m = MemorySettings()
        # D28.10 — these defaults are the spec
        assert m.max_files == 5
        assert m.max_entrypoint_bytes == 8_000
        assert m.max_body_chars == 8_000
        assert m.max_claude_md_chars == 12_000

    def test_each_field_accepts_zero(self) -> None:
        # ``ge=0`` validator allows zero (degenerate disabled state)
        m = MemorySettings(
            max_files=0,
            max_entrypoint_bytes=0,
            max_body_chars=0,
            max_claude_md_chars=0,
        )
        assert m.max_files == 0

    def test_negative_value_rejected(self) -> None:
        # Pydantic raises ValidationError on ge=0 violation
        with pytest.raises(Exception, match=r"greater than or equal to 0"):
            MemorySettings(max_files=-1)


class TestSettingsEnableMemoryDefault:
    def test_enable_memory_defaults_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D28.10 opt-OUT — default ON. Deviation from plugins' opt-in.
        _seed_required_env(monkeypatch)
        settings = Settings()
        assert settings.enable_memory is True

    def test_project_instructions_are_enabled_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_required_env(monkeypatch)

        settings = Settings(enable_memory=False)

        assert settings.enable_project_instructions is True
        assert settings.max_project_instruction_chars == 12_000

    def test_memory_nested_uses_default_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        settings = Settings()
        assert isinstance(settings.memory, MemorySettings)
        assert settings.memory.max_files == 5


class TestEnableMemoryEnvOverride:
    def test_env_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_ENABLE_MEMORY", "false")
        settings = Settings()
        assert settings.enable_memory is False

    def test_env_0_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_ENABLE_MEMORY", "0")
        settings = Settings()
        assert settings.enable_memory is False

    def test_env_true_keeps_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Redundant with default but pins the pattern
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_ENABLE_MEMORY", "true")
        settings = Settings()
        assert settings.enable_memory is True


class TestMemoryNestedEnvOverride:
    def test_max_files_via_double_underscore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # OPENHARNESS_MEMORY__MAX_FILES — double underscore is the
        # env_nested_delimiter convention. Single underscore would
        # collide with field names that contain underscores.
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MEMORY__MAX_FILES", "10")
        settings = Settings()
        assert settings.memory.max_files == 10

    def test_max_body_chars_via_double_underscore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MEMORY__MAX_BODY_CHARS", "4000")
        settings = Settings()
        assert settings.memory.max_body_chars == 4000

    def test_max_claude_md_chars_via_double_underscore(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MEMORY__MAX_CLAUDE_MD_CHARS", "20000")
        settings = Settings()
        assert settings.memory.max_claude_md_chars == 20000

    def test_multiple_nested_fields_simultaneously(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MEMORY__MAX_FILES", "3")
        monkeypatch.setenv("OPENHARNESS_MEMORY__MAX_BODY_CHARS", "5000")
        monkeypatch.setenv("OPENHARNESS_MEMORY__MAX_ENTRYPOINT_BYTES", "4000")
        settings = Settings()
        assert settings.memory.max_files == 3
        assert settings.memory.max_body_chars == 5000
        assert settings.memory.max_entrypoint_bytes == 4000
        # Untouched field keeps default
        assert settings.memory.max_claude_md_chars == 12_000

    def test_single_underscore_does_NOT_resolve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # OPENHARNESS_MEMORY_MAX_FILES (single underscore) would be
        # ambiguous — could mean "field named ``memory_max_files``" OR
        # "nested ``memory.max_files``". The double-underscore
        # delimiter resolves the ambiguity. Verify single-underscore
        # form does NOT silently take effect.
        _seed_required_env(monkeypatch)
        monkeypatch.setenv("OPENHARNESS_MEMORY_MAX_FILES", "99")
        settings = Settings()
        # The default holds — single underscore wasn't recognized
        assert settings.memory.max_files == 5


class TestProgrammaticConstruction:
    def test_pass_memory_settings_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        custom = MemorySettings(max_files=2, max_body_chars=1000)
        settings = Settings(memory=custom)
        assert settings.memory.max_files == 2
        assert settings.memory.max_body_chars == 1000

    def test_pass_enable_memory_false_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_required_env(monkeypatch)
        settings = Settings(enable_memory=False)
        assert settings.enable_memory is False
