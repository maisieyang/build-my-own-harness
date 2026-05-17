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


class TestDenyPaths:
    """``deny_paths`` (P3-T3.3b) — comma-separated env var for Tier 2 globs.

    Parsed from ``OPENHARNESS_DENY_PATHS``. Empty value (or unset) yields
    an empty tuple — the AuthZ Tier 2 layer treats this as "no user rules".
    """

    def test_default_is_empty_tuple_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        settings = Settings()
        assert settings.deny_paths == ()

    def test_single_pattern_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_DENY_PATHS", "secrets/**")
        settings = Settings()
        assert settings.deny_paths == ("secrets/**",)

    def test_multiple_patterns_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_DENY_PATHS", "secrets/**,*.env,private/")
        settings = Settings()
        assert settings.deny_paths == ("secrets/**", "*.env", "private/")

    def test_whitespace_around_commas_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_DENY_PATHS", " secrets/** , *.env ")
        settings = Settings()
        assert settings.deny_paths == ("secrets/**", "*.env")

    def test_empty_segments_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Trailing comma / double comma / pure whitespace yields no entry.
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_DENY_PATHS", "secrets/**,,*.env,")
        settings = Settings()
        assert settings.deny_paths == ("secrets/**", "*.env")


class TestTrustedMcpServers:
    """P5-T1.1c — env parsing mirrors deny_paths (D15.6 same shape)."""

    def test_default_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        settings = Settings()
        assert settings.trusted_mcp_servers == ()

    def test_single_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_TRUSTED_MCP_SERVERS", "github")
        settings = Settings()
        assert settings.trusted_mcp_servers == ("github",)

    def test_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_TRUSTED_MCP_SERVERS", "github,filesystem,brave-search")
        settings = Settings()
        assert settings.trusted_mcp_servers == ("github", "filesystem", "brave-search")

    def test_whitespace_stripped_and_empty_segments_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_TRUSTED_MCP_SERVERS", " github , , filesystem ,")
        settings = Settings()
        assert settings.trusted_mcp_servers == ("github", "filesystem")


class TestMcpServers:
    """P5-T1.1b — JSON-blob env parsing into tuple[McpServerConfig, ...]."""

    def test_default_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        settings = Settings()
        assert settings.mcp_servers == ()

    def test_single_server_from_json_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.mcp import McpServerConfig

        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv(
            "OPENHARNESS_MCP_SERVERS",
            '[{"name":"fs","command":["npx","-y","@modelcontextprotocol/server-filesystem","/tmp"]}]',
        )
        settings = Settings()
        assert len(settings.mcp_servers) == 1
        cfg = settings.mcp_servers[0]
        assert isinstance(cfg, McpServerConfig)
        assert cfg.name == "fs"
        assert cfg.command == (
            "npx",
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/tmp",
        )
        assert cfg.env == {}

    def test_multi_server_with_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv(
            "OPENHARNESS_MCP_SERVERS",
            (
                '[{"name":"fs","command":["x"]},'
                '{"name":"gh","command":["node","gh.js"],'
                '"env":{"GITHUB_TOKEN":"ghp_xxx"}}]'
            ),
        )
        settings = Settings()
        assert len(settings.mcp_servers) == 2
        fs, gh = settings.mcp_servers
        assert fs.name == "fs"
        assert fs.env == {}
        assert gh.name == "gh"
        assert gh.env == {"GITHUB_TOKEN": "ghp_xxx"}

    def test_programmatic_construction_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openharness.mcp import McpServerConfig

        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        cfg = McpServerConfig(name="programmatic", command=("y",))
        settings = Settings(mcp_servers=(cfg,))
        assert settings.mcp_servers == (cfg,)

    def test_invalid_json_blob_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_MCP_SERVERS", "not-valid-json{")
        with pytest.raises(ValidationError, match="not valid JSON"):
            Settings()

    def test_invalid_server_name_in_blob_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        # The name "1invalid" starts with a digit — McpServerConfig rejects it.
        monkeypatch.setenv(
            "OPENHARNESS_MCP_SERVERS",
            '[{"name":"1invalid","command":["x"]}]',
        )
        with pytest.raises(ValidationError, match="invalid MCP server name"):
            Settings()


class TestMaxAgentDepth:
    """P6-T1 (D16.5) — env-driven sub-agent recursion cap."""

    def test_default_is_three(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        settings = Settings()
        assert settings.max_agent_depth == 3

    @pytest.mark.parametrize("value", ["0", "1", "5", "20"])
    def test_env_var_parses_to_int(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_MAX_AGENT_DEPTH", value)
        settings = Settings()
        assert settings.max_agent_depth == int(value)

    def test_zero_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # ``0`` disables spawning entirely — depth 0 + 1 > 0 refuses.
        # Useful as a "no sub-agents" hard kill-switch.
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_MAX_AGENT_DEPTH", "0")
        settings = Settings()
        assert settings.max_agent_depth == 0

    def test_negative_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_MAX_AGENT_DEPTH", "-1")
        with pytest.raises(ValidationError):
            Settings()

    def test_non_int_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_MAX_AGENT_DEPTH", "not-a-number")
        with pytest.raises(ValidationError):
            Settings()


class TestSandboxFields:
    """P7b-T2 (D18.x) — Docker sandbox configuration."""

    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        settings = Settings()
        assert settings.sandbox_enabled is False  # default: host execution
        assert settings.sandbox_image == "python:3.12-slim"
        assert settings.sandbox_network == "none"
        assert settings.sandbox_memory == "1g"
        assert settings.sandbox_cpus == 1.0
        assert settings.sandbox_pids == 256

    def test_enable_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_SANDBOX_ENABLED", "true")
        settings = Settings()
        assert settings.sandbox_enabled is True

    def test_custom_image_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_SANDBOX_IMAGE", "ubuntu:latest")
        settings = Settings()
        assert settings.sandbox_image == "ubuntu:latest"

    def test_network_must_be_none_or_bridge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_SANDBOX_NETWORK", "host")
        with pytest.raises(ValidationError):
            Settings()

    @pytest.mark.parametrize(("env_value", "expected"), [("none", "none"), ("bridge", "bridge")])
    def test_network_valid_values(
        self, env_value: str, expected: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_SANDBOX_NETWORK", env_value)
        settings = Settings()
        assert settings.sandbox_network == expected

    def test_cpus_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_SANDBOX_CPUS", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_pids_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_SANDBOX_PIDS", "0")
        with pytest.raises(ValidationError):
            Settings()


class TestEnablePluginHooks:
    """P5e-T3 (D20.3) — opt-in plugin hook discovery."""

    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        settings = Settings()
        assert settings.enable_plugin_hooks is False

    def test_enable_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_ENABLE_PLUGIN_HOOKS", "true")
        settings = Settings()
        assert settings.enable_plugin_hooks is True

    def test_false_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        monkeypatch.setenv("OPENHARNESS_ENABLE_PLUGIN_HOOKS", "false")
        settings = Settings()
        assert settings.enable_plugin_hooks is False
