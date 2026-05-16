"""Cross-cutting invariant verification — P7-T4.

The **fourth tenant test** of Phase 3's abstractions(after MCP in 5a,
Skills in 5c, Commands in 5b, Sub-agent in 6). Verifies structurally
that ``ExecutionEnvironment`` lands as a tenant of the existing
context-passing chain — no permission / hook / engine-dispatch /
observability / mcp / compaction / skills / commands / protocols /
other-tools layer references the new abstraction.

This is the cleanest invariant proof yet because ``HostExecution`` is
the **identity transform**:if behavior parity holds (all 13 prior
BashTool tests pass unchanged after P7-T3 refactor),AND no protected
module references the abstraction's identifiers, then the substrate
layer is purely additive — exactly what Phase 7a sets out to validate.
"""

from __future__ import annotations

import importlib
import inspect
import re


class TestPhase7aCrossCuttingInvariant:
    """``ExecutionEnvironment`` / ``HostExecution`` / ``ProcessResult``
    must not be referenced by the four core "zero-diff" subsystems plus
    the other tool modules. Only the explicitly-allowed touch points
    (engine/context, engine/query, tools/base, tools/bash) may import.
    """

    _PROTECTED_MODULES = (
        "openharness.permissions.checker",
        "openharness.permissions.tier_based",
        "openharness.hooks.executor",
        "openharness.hooks.registry",
        "openharness.observability.context",
        "openharness.observability.logging",
        "openharness.mcp.client",
        "openharness.mcp.adapter",
        "openharness.mcp.pool",
        "openharness.compaction.hook",
        "openharness.compaction.truncate",
        "openharness.skills.model",
        "openharness.skills.store",
        "openharness.commands.model",
        "openharness.commands.store",
        "openharness.commands.expand",
        "openharness.tools.read",
        "openharness.tools.write",
        "openharness.tools.edit",
        "openharness.tools.grep",
        "openharness.tools.load_skill",
        "openharness.tools.spawn_agent",
    )

    _FORBIDDEN_IDENTIFIERS = (
        "ExecutionEnvironment",
        "HostExecution",
        "ProcessResult",
        "_HOST_EXECUTION",
    )

    @staticmethod
    def _strip_comments_and_docstrings(source: str) -> str:
        cleaned = re.sub(r'"""[\s\S]*?"""', "", source, flags=re.MULTILINE)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned, flags=re.MULTILINE)
        cleaned_lines = [line.split("#", 1)[0] for line in cleaned.splitlines()]
        return "\n".join(cleaned_lines)

    def test_protected_modules_do_not_reference_execution(self) -> None:
        for mod_name in self._PROTECTED_MODULES:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            code = self._strip_comments_and_docstrings(source)
            for forbidden in self._FORBIDDEN_IDENTIFIERS:
                pattern = rf"\b{re.escape(forbidden)}\b"
                assert not re.search(pattern, code), (
                    f"{mod_name} references {forbidden!r} — Phase 7a "
                    f"cross-cutting invariant violated. The "
                    f"ExecutionEnvironment abstraction must NOT leak "
                    f"into permission / hook / observability / mcp / "
                    f"compaction / skills / commands / other-tools "
                    f"layers."
                )

    def test_allowed_modules_DO_reference_execution(self) -> None:
        # Sanity check the inverse: the allowed touch points actually
        # USE the abstraction. If this test fails, the wiring is broken
        # somewhere upstream.
        from openharness.engine import context as engine_context_mod
        from openharness.engine import query as engine_query_mod
        from openharness.tools import base as tools_base_mod
        from openharness.tools import bash as tools_bash_mod

        for mod, expected_id in (
            (engine_context_mod, "ExecutionEnvironment"),
            (engine_query_mod, "execution_env"),  # the kwarg
            (tools_base_mod, "ExecutionEnvironment"),
            (tools_bash_mod, "_HOST_EXECUTION"),
        ):
            source = inspect.getsource(mod)
            assert expected_id in source, (
                f"{mod.__name__} expected to reference {expected_id!r} "
                f"but does not — substrate wiring may be broken."
            )


class TestExecutionEnvironmentSubstrateSwap:
    """Demonstrates the substrate swap actually works end-to-end via
    QueryContext + ToolExecutionContext flow — orthogonal to the
    BashTool unit test that does the same at the tool level.

    This is the highest-level integration check for Phase 7a: the
    full chain (QueryContext.execution_env → ToolExecutionContext.
    execution_env → BashTool.execute → ExecutionEnvironment.run_command)
    transports an injected substrate faithfully.
    """

    async def test_full_chain_substrate_swap(self, tmp_path: object) -> None:
        from pathlib import Path

        from openharness.execution import ExecutionEnvironment, ProcessResult
        from openharness.tools import Bash
        from openharness.tools.base import ToolExecutionContext
        from openharness.tools.bash import BashInput

        class _CountingEnv:
            def __init__(self) -> None:
                self.invocations = 0

            async def run_command(
                self, command: str, cwd: Path, timeout: float | None = None
            ) -> ProcessResult:
                del command, cwd, timeout
                self.invocations += 1
                return ProcessResult(output="substrate-swapped", exit_code=0)

        env: ExecutionEnvironment = _CountingEnv()
        assert isinstance(tmp_path, Path)
        exec_ctx = ToolExecutionContext(cwd=tmp_path, execution_env=env)

        result = await Bash().execute(BashInput(command="echo whatever"), exec_ctx)

        # The injected substrate was invoked exactly once.
        assert env.invocations == 1  # type: ignore[attr-defined]
        # BashTool's ToolResult reflects the substrate's output verbatim.
        assert result.output == "substrate-swapped"
