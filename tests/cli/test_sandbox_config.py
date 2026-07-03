"""loop-runtime Track B — T0: extract ``_resolve_sandbox_config`` as a pure
helper out of ``_run_ask``'s inline sandbox-override resolution.

Pure unit test of the override-precedence logic in isolation (CLI-flag
override > Settings default), independent of the CLI-level
``TestSandboxFlags`` end-to-end tests in ``test_cli.py`` (those must keep
passing unchanged -- this file additionally pins down the extracted
function's own contract).
"""

from __future__ import annotations

from openharness.cli import SandboxConfig, _resolve_sandbox_config
from openharness.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(api_key="sk-fake", base_url="https://fake.example.com/v1", **overrides)  # type: ignore[arg-type]


class TestResolveSandboxConfig:
    def test_all_overrides_none_uses_settings_defaults(self) -> None:
        settings = _settings()
        config = _resolve_sandbox_config(
            settings,
            sandbox_override=None,
            sandbox_image_override=None,
            sandbox_network_override=None,
            sandbox_memory_override=None,
            sandbox_cpus_override=None,
            sandbox_runtime_override=None,
        )
        assert config == SandboxConfig(
            enabled=False,
            image="python:3.12-slim",
            network="none",
            memory="1g",
            cpus=1.0,
            runtime="runc",
        )

    def test_cli_overrides_win_over_settings(self) -> None:
        settings = _settings(
            sandbox_enabled=True,
            sandbox_image="ubuntu:latest",
            sandbox_network="bridge",
            sandbox_memory="512m",
            sandbox_cpus=0.5,
            sandbox_runtime="runsc",
        )
        config = _resolve_sandbox_config(
            settings,
            sandbox_override=False,
            sandbox_image_override="alpine:latest",
            sandbox_network_override="none",
            sandbox_memory_override="2g",
            sandbox_cpus_override=2.0,
            sandbox_runtime_override="runc",
        )
        assert config == SandboxConfig(
            enabled=False,
            image="alpine:latest",
            network="none",
            memory="2g",
            cpus=2.0,
            runtime="runc",
        )

    def test_sandbox_override_true_wins_even_when_settings_false(self) -> None:
        settings = _settings(sandbox_enabled=False)
        config = _resolve_sandbox_config(
            settings,
            sandbox_override=True,
            sandbox_image_override=None,
            sandbox_network_override=None,
            sandbox_memory_override=None,
            sandbox_cpus_override=None,
            sandbox_runtime_override=None,
        )
        assert config.enabled is True

    def test_config_is_frozen(self) -> None:
        import dataclasses

        import pytest

        config = SandboxConfig(
            enabled=False, image="x", network="none", memory="1g", cpus=1.0, runtime="runc"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.enabled = True  # type: ignore[misc]
