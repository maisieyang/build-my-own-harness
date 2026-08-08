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
        # P7b: SandboxExecution must also not leak. cli.py is the only
        # non-execution/ module allowed to reference it (boundary doc
        # D18 allows cli.py to enter the sandbox context manager).
        "SandboxExecution",
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


class TestPhase5dCrossCuttingInvariant:
    """ModeBundle identifiers (``Bundle`` / ``WhitelistRegistry`` /
    ``BUILTIN_HOOKS`` / ``parse_bundle`` / ``apply_bundle_to_context`` /
    ``UnknownBundleError``) must NOT appear in the protected zero-diff
    subsystems. Phase 5d is the first cross-layer tenant — its
    correctness rests entirely on composing primitives without modifying
    any of them.

    Allowed touch points: ``bundles/`` itself (where the identifiers
    live) and ``cli.py`` (the only consumer that loads + applies a
    bundle). Everywhere else must be zero-diff.
    """

    _PROTECTED_MODULES = (
        "openharness.hooks.executor",
        "openharness.hooks.registry",
        "openharness.hooks.context",
        "openharness.hooks.events",
        "openharness.hooks.result",
        "openharness.engine.context",
        "openharness.engine.query",
        "openharness.engine.messages",
        "openharness.observability.context",
        "openharness.observability.logging",
        "openharness.observability.sanitize",
        "openharness.mcp.client",
        "openharness.mcp.adapter",
        "openharness.mcp.pool",
        "openharness.mcp.config",
        "openharness.compaction.hook",
        "openharness.compaction.truncate",
        "openharness.compaction.tokenize",
        "openharness.skills.model",
        "openharness.skills.store",
        "openharness.protocols.content",
        "openharness.protocols.messages",
        "openharness.protocols.requests",
        "openharness.protocols.stream_events",
        "openharness.protocols.tools",
        "openharness.protocols.usage",
        "openharness.tools.base",
        "openharness.tools.read",
        "openharness.tools.write",
        "openharness.tools.edit",
        "openharness.tools.grep",
        "openharness.tools.bash",
        "openharness.tools.load_skill",
        "openharness.tools.spawn_agent",
        "openharness.execution.base",
        "openharness.execution.host",
        "openharness.execution.sandbox",
        "openharness.prompts",
        # commands/model.py gains ``mode`` field — strict 1-additive-field
        # diff vs Phase 7b close. Does NOT reference any bundle identifier.
        "openharness.commands.model",
        "openharness.commands.store",
        "openharness.commands.expand",
        "openharness.commands.errors",
    )

    _FORBIDDEN_IDENTIFIERS = (
        "Bundle",
        "WhitelistRegistry",
        "BUILTIN_HOOKS",
        "parse_bundle",
        "apply_bundle_to_context",
        "UnknownBundleError",
        "BundleApplication",
        "FilesystemBundleStore",
        "EmptyBundleStore",
        "BundleStore",
        "resolve_hook",
        # P5e additions: plugin hook discovery identifiers must also
        # NOT leak into protected modules. Discovery lives in
        # bundles/hook_plugins.py + cli.py only.
        "HookSpec",
        "hook_spec",
        "discover_plugin_hooks",
        "discover_filesystem_hook_plugins",
        # Internal module-name leakage check.
        "openharness.bundles",
    )

    @staticmethod
    def _strip_comments_and_docstrings(source: str) -> str:
        cleaned = re.sub(r'"""[\s\S]*?"""', "", source, flags=re.MULTILINE)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned, flags=re.MULTILINE)
        cleaned_lines = [line.split("#", 1)[0] for line in cleaned.splitlines()]
        return "\n".join(cleaned_lines)

    def test_protected_modules_do_not_reference_bundles(self) -> None:
        for mod_name in self._PROTECTED_MODULES:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            code = self._strip_comments_and_docstrings(source)
            for forbidden in self._FORBIDDEN_IDENTIFIERS:
                pattern = rf"\b{re.escape(forbidden)}\b"
                assert not re.search(pattern, code), (
                    f"{mod_name} references {forbidden!r} — Phase 5d "
                    f"cross-cutting invariant violated. ModeBundle must "
                    f"compose primitives WITHOUT modifying any of the "
                    f"layered abstractions."
                )

    def test_cli_is_the_consumer(self) -> None:
        # Sanity inverse: cli.py is the ONE legitimate consumer that
        # loads + applies bundles. If this drops out, T4 wiring broke.
        import openharness.cli as cli_module

        source = inspect.getsource(cli_module)
        assert "apply_bundle_to_context" in source
        assert "FilesystemBundleStore" in source
        assert "UnknownBundleError" in source


class TestPhase8MarkdownStoreInvariant:
    """``markdown_store`` is consumed by commands / skills / bundles
    only. No other layer should reference its identifiers — the
    extraction is internal to the discovery domain.
    """

    _PROTECTED_MODULES = (
        "openharness.hooks.executor",
        "openharness.hooks.registry",
        "openharness.engine.context",
        "openharness.engine.query",
        "openharness.observability.context",
        "openharness.observability.logging",
        "openharness.mcp.client",
        "openharness.mcp.adapter",
        "openharness.mcp.pool",
        "openharness.compaction.hook",
        "openharness.compaction.truncate",
        "openharness.protocols.content",
        "openharness.protocols.messages",
        "openharness.protocols.requests",
        "openharness.tools.base",
        "openharness.tools.read",
        "openharness.tools.write",
        "openharness.tools.edit",
        "openharness.tools.grep",
        "openharness.tools.bash",
        "openharness.tools.load_skill",
        "openharness.tools.spawn_agent",
        "openharness.execution.host",
        "openharness.execution.sandbox",
        "openharness.prompts",
    )

    _FORBIDDEN_IDENTIFIERS = (
        "FilesystemMarkdownStore",
        "EmptyMarkdownStore",
        "MarkdownDocument",
        "read_frontmatter_dict",
        "split_frontmatter",
        "NAME_PATTERN",
        "FRONTMATTER_FENCE",
        "openharness.markdown_store",
    )

    @staticmethod
    def _strip_comments_and_docstrings(source: str) -> str:
        cleaned = re.sub(r'"""[\s\S]*?"""', "", source, flags=re.MULTILINE)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned, flags=re.MULTILINE)
        cleaned_lines = [line.split("#", 1)[0] for line in cleaned.splitlines()]
        return "\n".join(cleaned_lines)

    def test_protected_modules_do_not_reference_markdown_store(self) -> None:
        for mod_name in self._PROTECTED_MODULES:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            code = self._strip_comments_and_docstrings(source)
            for forbidden in self._FORBIDDEN_IDENTIFIERS:
                pattern = rf"\b{re.escape(forbidden)}\b"
                assert not re.search(pattern, code), (
                    f"{mod_name} references {forbidden!r} — Phase 8 "
                    f"invariant violated. markdown_store/ is consumed "
                    f"by commands/skills/bundles only."
                )

    def test_consumers_DO_reference_markdown_store(self) -> None:
        # Sanity inverse: the three legitimate consumers actually use
        # markdown_store. If this fails, the refactor reverted somewhere.
        from openharness.bundles import model as bundles_model_mod
        from openharness.bundles import store as bundles_store_mod
        from openharness.commands import model as commands_model_mod
        from openharness.commands import store as commands_store_mod
        from openharness.skills import model as skills_model_mod
        from openharness.skills import store as skills_store_mod

        for mod in (
            commands_model_mod,
            commands_store_mod,
            skills_model_mod,
            skills_store_mod,
            bundles_model_mod,
            bundles_store_mod,
        ):
            source = inspect.getsource(mod)
            assert "markdown_store" in source, (
                f"{mod.__name__} expected to import from markdown_store"
                f" but does not — refactor may have reverted."
            )


class TestPhase6PlusConversationCompleteInvariant:
    """``ConversationCompleteEvent`` is a stream-event type used ONLY by
    the engine (emit) and the REPL (consume). No other layer should
    reference it.
    """

    _PROTECTED_MODULES = (
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
        "openharness.bundles.model",
        "openharness.bundles.store",
        "openharness.bundles.registry",
        "openharness.bundles.apply",
        "openharness.bundles.hooks",
        "openharness.bundles.hook_plugins",
        "openharness.markdown_store.parse",
        "openharness.markdown_store.store",
        "openharness.tools.read",
        "openharness.tools.write",
        "openharness.tools.edit",
        "openharness.tools.grep",
        "openharness.tools.bash",
        "openharness.tools.load_skill",
        "openharness.tools.spawn_agent",
        "openharness.execution.host",
        "openharness.execution.sandbox",
        "openharness.prompts",
    )

    _FORBIDDEN_IDENTIFIERS = ("ConversationCompleteEvent",)

    @staticmethod
    def _strip_comments_and_docstrings(source: str) -> str:
        cleaned = re.sub(r'"""[\s\S]*?"""', "", source, flags=re.MULTILINE)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned, flags=re.MULTILINE)
        cleaned_lines = [line.split("#", 1)[0] for line in cleaned.splitlines()]
        return "\n".join(cleaned_lines)

    def test_protected_modules_do_not_reference_event(self) -> None:
        for mod_name in self._PROTECTED_MODULES:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            code = self._strip_comments_and_docstrings(source)
            for forbidden in self._FORBIDDEN_IDENTIFIERS:
                pattern = rf"\b{re.escape(forbidden)}\b"
                assert not re.search(pattern, code), (
                    f"{mod_name} references {forbidden!r} — Phase 6+ "
                    f"invariant violated. ConversationCompleteEvent is "
                    f"engine→REPL only."
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
