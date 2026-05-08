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

from openharness.prompts import EnvironmentInfo, build_system_prompt, detect_environment
from openharness.protocols import ToolSpec


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


class TestBuildSystemPrompt:
    """5b: build_system_prompt assembly contract.

    Asserts on *structure* (section markers, presence of names) -- not on
    exact wording -- so future phases can extend the body without test
    churn.
    """

    @pytest.fixture
    def env(self) -> EnvironmentInfo:
        return EnvironmentInfo(
            os_name="Darwin",
            os_version="25.4.0",
            shell="/bin/zsh",
            cwd=Path("/work/project"),
            python_version="3.12.3",
        )

    @pytest.fixture
    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="Read",
                description="Read a text file's contents.",
                input_schema={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="Bash",
                description="Execute a shell command in the project's cwd.",
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    def test_returns_non_empty_string(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env)
        assert prompt != ""
        assert len(prompt) > 50  # base instructions alone are ~50 chars

    def test_contains_section_markers(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        # D11.3: Markdown sections; tests assert markers, not full text.
        prompt = build_system_prompt(tools, env)
        assert "## Tools" in prompt
        assert "## Environment" in prompt

    def test_contains_every_tool_name(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env)
        for tool in tools:
            assert tool.name in prompt
            assert tool.description in prompt  # D11.4: full description

    def test_contains_environment_fields(self, tools: list[ToolSpec], env: EnvironmentInfo) -> None:
        prompt = build_system_prompt(tools, env)
        assert env.os_name in prompt
        assert env.os_version in prompt
        assert env.shell in prompt
        assert str(env.cwd) in prompt
        assert env.python_version in prompt

    def test_section_order_is_base_then_tools_then_environment(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        prompt = build_system_prompt(tools, env)
        tools_idx = prompt.index("## Tools")
        env_idx = prompt.index("## Environment")
        # Base instructions live before the Tools section header.
        assert prompt.index("OpenHarness") < tools_idx
        assert tools_idx < env_idx

    def test_empty_tools_list_renders_sentinel(self, env: EnvironmentInfo) -> None:
        # D11.6: empty registry still has a `## Tools` section.
        prompt = build_system_prompt([], env)
        assert "## Tools" in prompt
        assert "(no tools registered)" in prompt

    def test_base_instructions_mention_error_recovery(
        self, tools: list[ToolSpec], env: EnvironmentInfo
    ) -> None:
        # D11.5: base instructions encode the D8.5 recovery contract in
        # natural language so the LLM knows is_error=True isn't fatal.
        prompt = build_system_prompt(tools, env)
        assert "recoverable" in prompt
