"""Tests for prompts.py — P2-T5.

Sub-unit 5a covers ``EnvironmentInfo`` + ``detect_environment``:

1. ``EnvironmentInfo`` is a frozen dataclass (matches D11.1).
2. ``detect_environment`` returns a populated instance using stdlib only.
3. ``SHELL`` env var falls back to ``/bin/sh`` when unset.
4. The function never raises (per D11.2 -- callers should not need try/except).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openharness.prompts import EnvironmentInfo, detect_environment


class TestEnvironmentInfo:
    def test_round_trip_all_fields(self) -> None:
        env = EnvironmentInfo(
            os_name="Darwin",
            os_version="25.4.0",
            shell="/bin/zsh",
            cwd=Path("/tmp"),
            python_version="3.12.3",
        )
        assert env.os_name == "Darwin"
        assert env.os_version == "25.4.0"
        assert env.shell == "/bin/zsh"
        assert env.cwd == Path("/tmp")
        assert env.python_version == "3.12.3"

    def test_frozen_field_assignment_raises(self) -> None:
        env = EnvironmentInfo(
            os_name="Linux",
            os_version="6.0.0",
            shell="/bin/bash",
            cwd=Path("/"),
            python_version="3.11.0",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.os_name = "Windows"  # type: ignore[misc]


class TestDetectEnvironment:
    def test_returns_populated_instance(self) -> None:
        env = detect_environment()
        # Don't assert exact values (host-dependent) -- just that each field
        # is non-empty / well-typed. The host running the test is implicitly
        # the test fixture.
        assert env.os_name != ""
        assert env.os_version != ""
        assert env.shell != ""
        assert isinstance(env.cwd, Path)
        assert env.python_version.count(".") >= 1  # X.Y or X.Y.Z

    def test_shell_falls_back_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # D11.2 fallback: missing SHELL env var -> /bin/sh.
        monkeypatch.delenv("SHELL", raising=False)
        env = detect_environment()
        assert env.shell == "/bin/sh"

    def test_shell_uses_env_var_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
        env = detect_environment()
        assert env.shell == "/usr/local/bin/fish"

    def test_does_not_raise_under_normal_conditions(self) -> None:
        # detect_environment is documented to never raise (D11.2).
        # Call it a couple of times to surface any transient issues.
        for _ in range(3):
            detect_environment()
