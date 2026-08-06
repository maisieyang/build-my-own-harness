"""Tests for ``McpServerConfig`` — P5-T1.1a.

Three surfaces:

1. **Happy construction** — typical configs build cleanly and freeze
   their fields.
2. **Name validation** — invalid identifiers are rejected at construction
   time (namespace safety per D15.3).
3. **Command validation** — empty command is rejected (spawning nothing
   makes no sense).
"""

from __future__ import annotations

import dataclasses

import pytest

from openharness.mcp import McpEnvironmentPosture, McpServerConfig


class TestConstruction:
    def test_minimal_config(self) -> None:
        cfg = McpServerConfig(name="fs", command=("npx", "server-filesystem"))
        assert cfg.name == "fs"
        assert cfg.command == ("npx", "server-filesystem")
        assert cfg.env == {}
        assert cfg.sandbox is False
        assert cfg.environment_posture is McpEnvironmentPosture.MINIMAL

    def test_with_env_vars(self) -> None:
        cfg = McpServerConfig(
            name="github",
            command=("node", "./gh.js"),
            env={"GITHUB_TOKEN": "ghp_abc123"},
        )
        assert cfg.env == {"GITHUB_TOKEN": "ghp_abc123"}

    def test_stdio_sandbox_is_explicit_opt_in(self) -> None:
        cfg = McpServerConfig(name="local", command=("node", "server.js"), sandbox=True)
        assert cfg.sandbox is True

    def test_is_frozen(self) -> None:
        cfg = McpServerConfig(name="x", command=("y",))
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.name = "z"  # type: ignore[misc]

    def test_env_defaults_to_empty_dict_not_shared(self) -> None:
        # The classic mutable-default bug: two instances must not share env.
        a = McpServerConfig(name="a", command=("x",))
        b = McpServerConfig(name="b", command=("y",))
        a.env["key"] = "value"
        assert "key" not in b.env

    def test_command_as_tuple_preserved(self) -> None:
        cfg = McpServerConfig(name="t", command=("a", "b", "c"))
        # command is a tuple, not a list — immutable
        assert isinstance(cfg.command, tuple)


class TestNameValidation:
    @pytest.mark.parametrize(
        "name",
        [
            "fs",
            "github",
            "Filesystem",
            "server_v2",
            "a-b-c",
            "X1",
            "a",
        ],
    )
    def test_valid_names_accepted(self, name: str) -> None:
        cfg = McpServerConfig(name=name, command=("x",))
        assert cfg.name == name

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            " ",  # whitespace
            "1abc",  # starts with digit
            "_underscore_start",  # starts with underscore
            "-dash-start",  # starts with dash
            "with space",
            "with.dot",  # dot would collide with `Server.Tool` separator
            "with/slash",
            "with:colon",
            "with;semicolon",  # shell injection-ish
            "name$dollar",
        ],
    )
    def test_invalid_names_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="invalid MCP server name"):
            McpServerConfig(name=name, command=("x",))


class TestCommandValidation:
    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be non-empty"):
            McpServerConfig(name="x", command=())

    def test_single_arg_command_accepted(self) -> None:
        # Just the executable, no args — legal.
        cfg = McpServerConfig(name="x", command=("uvx",))
        assert cfg.command == ("uvx",)
