"""SWE-bench drives the private headless API, never the public ``oh`` CLI."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from openharness.swebench.model import SWEBenchInstance
from openharness.swebench.runner import (
    Invocation,
    InvocationResult,
    RunConfig,
    build_env,
    default_invoker,
    run_instance,
)

if TYPE_CHECKING:
    from pathlib import Path

GOLD_SENTINEL = "GOLD-PATCH-SENTINEL"


def make_instance() -> SWEBenchInstance:
    return SWEBenchInstance.model_validate(
        {
            "instance_id": "acme__widget-1",
            "repo": "acme/widget",
            "base_commit": "a" * 40,
            "problem_statement": "widget frobnicates twice",
            "patch": GOLD_SENTINEL,
            "FAIL_TO_PASS": '["tests/test_w.py::F2P-SENTINEL"]',
        }
    )


def good_envelope() -> str:
    return json.dumps(
        {
            "type": "result",
            "result": "fixed it",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            "cost_usd": None,
            "num_turns": 4,
            "session_id": "run-x",
        }
    )


class TestInternalInvocation:
    def test_run_config_has_no_public_cli_command(self) -> None:
        assert not hasattr(RunConfig(), "oh_command")

    def test_invocation_carries_typed_runtime_inputs(self, tmp_path: Path) -> None:
        seen: list[Invocation] = []

        def invoke(inv: Invocation) -> InvocationResult:
            seen.append(inv)
            return InvocationResult(exit_code=0, stdout=good_envelope(), stderr="")

        run_instance(
            make_instance(),
            tmp_path,
            RunConfig(model="deepseek-v3.2", sandbox=True, max_turns=60),
            invoke=invoke,
            extract=lambda ws: "",
        )

        invocation = seen[0]
        assert invocation.model == "deepseek-v3.2"
        assert invocation.sandbox is True
        assert invocation.max_turns == 60
        assert "widget frobnicates twice" in invocation.prompt

    def test_default_invoker_calls_private_runtime(self, tmp_path: Path) -> None:
        result = default_invoker(
            Invocation(
                prompt="hello",
                cwd=tmp_path,
                env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
                timeout_s=10,
                model=None,
                sandbox=False,
                max_turns=1,
            )
        )

        assert result.exit_code == 1
        assert "Configuration error" in result.stderr


class TestBuildEnv:
    def test_write_tools_allowed_bash_denied_without_sandbox(self) -> None:
        env = build_env({"PATH": "/usr/bin"}, sandbox=False)

        allow = env["OPENHARNESS_PERMISSIONS__ALLOW"]
        assert "Edit(**)" in allow
        assert "Write(**)" in allow
        assert "Bash" not in allow

    def test_bash_allowed_only_with_sandbox(self) -> None:
        env = build_env({}, sandbox=True)

        assert "Bash(*)" in env["OPENHARNESS_PERMISSIONS__ALLOW"]

    def test_memory_and_snapshot_disabled(self) -> None:
        env = build_env({}, sandbox=False)

        assert env["OPENHARNESS_ENABLE_MEMORY"] == "false"
        assert env["OPENHARNESS_SNAPSHOT__ENABLED"] == "false"

    def test_base_env_preserved(self) -> None:
        env = build_env({"PATH": "/usr/bin", "OPENHARNESS_API_KEY": "sk-test"}, sandbox=False)

        assert env["PATH"] == "/usr/bin"
        assert env["OPENHARNESS_API_KEY"] == "sk-test"


class TestRunInstance:
    def test_happy_path_parses_envelope_and_extracts_patch(self, tmp_path: Path) -> None:
        seen: list[Invocation] = []

        def invoke(inv: Invocation) -> InvocationResult:
            seen.append(inv)
            return InvocationResult(exit_code=0, stdout=good_envelope() + "\n", stderr="")

        result = run_instance(
            make_instance(),
            tmp_path,
            RunConfig(model="m1"),
            invoke=invoke,
            extract=lambda ws: "diff --git a/x b/x\n",
        )

        assert result.status == "completed"
        assert result.stop_reason == "end_turn"
        assert result.num_turns == 4
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.model_patch.startswith("diff --git")
        assert seen[0].cwd == tmp_path
        assert result.exit_code == 0

    def test_firewall_gold_fields_never_in_prompt_or_env(self, tmp_path: Path) -> None:
        """D40.3 红线: 泄题字段不进内部请求和 env."""
        seen: list[Invocation] = []

        def invoke(inv: Invocation) -> InvocationResult:
            seen.append(inv)
            return InvocationResult(exit_code=0, stdout=good_envelope(), stderr="")

        run_instance(
            make_instance(),
            tmp_path,
            RunConfig(),
            invoke=invoke,
            extract=lambda ws: "",
            base_env={"PATH": "/usr/bin"},
        )

        inv = seen[0]
        assert GOLD_SENTINEL not in inv.prompt
        assert "F2P-SENTINEL" not in inv.prompt
        for value in inv.env.values():
            assert GOLD_SENTINEL not in value
            assert "F2P-SENTINEL" not in value

    def test_timeout_still_extracts_partial_patch(self, tmp_path: Path) -> None:
        def invoke(inv: Invocation) -> InvocationResult:
            return InvocationResult(exit_code=None, stdout="", stderr="timed out")

        result = run_instance(
            make_instance(),
            tmp_path,
            RunConfig(timeout_s=1.0),
            invoke=invoke,
            extract=lambda ws: "partial diff\n",
        )

        assert result.status == "timeout"
        assert result.model_patch == "partial diff\n"

    def test_api_transport_failure_gets_own_status(self, tmp_path: Path) -> None:
        """全量前发现: DashScope mid-stream disconnects were lumped into
        invalid-envelope — taxonomy needs environment noise separated from
        parse problems. The oh CLI renders every API-layer death as
        'Request failed (HTTP ...' on stderr; key on that."""

        def invoke(inv: Invocation) -> InvocationResult:
            return InvocationResult(
                exit_code=1,
                stdout="",
                stderr=(
                    "Request failed (HTTP unknown): Unexpected error: peer "
                    "closed connection without sending complete message body"
                ),
            )

        result = run_instance(
            make_instance(), tmp_path, RunConfig(), invoke=invoke, extract=lambda ws: ""
        )

        assert result.status == "api-failed"
        assert "peer closed" in (result.detail or "")

    def test_invalid_envelope_is_differentiated_not_raised(self, tmp_path: Path) -> None:
        def invoke(inv: Invocation) -> InvocationResult:
            return InvocationResult(exit_code=1, stdout="Error: boom, no json here", stderr="trace")

        result = run_instance(
            make_instance(),
            tmp_path,
            RunConfig(),
            invoke=invoke,
            extract=lambda ws: "",
        )

        assert result.status == "invalid-envelope"
        assert result.detail is not None
        assert result.model_patch == ""

    def test_extraction_failure_is_differentiated(self, tmp_path: Path) -> None:
        from openharness.swebench.workspace import WorkspaceError

        def invoke(inv: Invocation) -> InvocationResult:
            return InvocationResult(exit_code=0, stdout=good_envelope(), stderr="")

        def broken_extract(ws: Path) -> str:
            raise WorkspaceError("diff", "corrupt index")

        result = run_instance(
            make_instance(), tmp_path, RunConfig(), invoke=invoke, extract=broken_extract
        )

        assert result.status == "patch-extraction-failed"
        assert result.model_patch == ""
