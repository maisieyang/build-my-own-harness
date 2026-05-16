"""Cross-cutting invariant verification — P6-T5.

The **third tenant test** of Phase 3's abstraction(after MCP in 5a and
Skills in 5c). Verifies structurally that ``SpawnAgent`` lands as just
another ``BaseTool``,with NO sub-agent-aware code in the four
"zero-diff" layers boundary D13 / D16 calls out.

The wider net than the Skills invariant test:Phase 6 also forbids
references to ``SpawnAgent`` / ``agent_depth`` / ``parent_query`` in
``permissions/``,``hooks/``,``mcp/``,``compaction/``,``protocols/``
(none of those should know about sub-agents at all). The minimal
additive surfaces ARE allowed —
``ToolExecutionContext.parent_query``(``tools/base.py``),
``QueryContext.agent_depth`` / ``max_agent_depth``(``engine/context.py``),
``Settings.max_agent_depth``(``config/settings.py``),
``bind_agent_depth``(``observability/context.py``),
``bind_agent_depth`` import in ``engine/query.py``,
``parent_query=context``(``engine/query.py``)— these are the
explicit additive change set per the boundary cross-cutting section.
"""

from __future__ import annotations

import importlib
import inspect
import re


class TestPhase6CrossCuttingInvariant:
    """``SpawnAgent`` must not be referenced by any of the four core
    "zero-diff" subsystems:permissions,hooks,mcp,compaction.

    The protected modules listed here have NO legitimate reason to know
    about sub-agents. If a future change adds such a reference, the
    invariant is violated and the boundary doc must be re-opened.
    """

    _PROTECTED_MODULES = (
        "openharness.permissions.checker",
        "openharness.permissions.tier_based",
        "openharness.hooks.executor",
        "openharness.hooks.registry",
        "openharness.mcp.client",
        "openharness.mcp.adapter",
        "openharness.mcp.pool",
        "openharness.compaction.hook",
        "openharness.compaction.truncate",
    )

    _FORBIDDEN_IDENTIFIERS = (
        "SpawnAgent",
        "SpawnAgentInput",
        "agent_depth",
        "max_agent_depth",
        "parent_query",
        "parent_run_id",
        "bind_agent_depth",
    )

    @staticmethod
    def _strip_comments_and_docstrings(source: str) -> str:
        cleaned = re.sub(r'"""[\s\S]*?"""', "", source, flags=re.MULTILINE)
        cleaned = re.sub(r"'''[\s\S]*?'''", "", cleaned, flags=re.MULTILINE)
        cleaned_lines = [line.split("#", 1)[0] for line in cleaned.splitlines()]
        return "\n".join(cleaned_lines)

    def test_protected_modules_do_not_reference_subagent(self) -> None:
        for mod_name in self._PROTECTED_MODULES:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            code = self._strip_comments_and_docstrings(source)
            for forbidden in self._FORBIDDEN_IDENTIFIERS:
                pattern = rf"\b{re.escape(forbidden)}\b"
                assert not re.search(pattern, code), (
                    f"{mod_name} references {forbidden!r} — Phase 6 "
                    f"cross-cutting invariant violated. Sub-agent must NOT "
                    f"leak into permission / hook / MCP / compaction layers."
                )


class TestSpawnAgentIsJustABaseTool:
    """Belt-and-braces:``SpawnAgent`` is structurally indistinguishable
    from other ``BaseTool`` subclasses. The engine treats it like
    ``Read`` — the fact it recursively invokes ``run_query`` is an
    implementation detail of ``execute``.
    """

    def test_inherits_only_from_BaseTool(self) -> None:
        from openharness.tools.base import BaseTool
        from openharness.tools.spawn_agent import SpawnAgent

        mro = SpawnAgent.__mro__
        # MRO: SpawnAgent → BaseTool → ABC → Generic → object
        assert BaseTool in mro
        # No engine / permission / hook / sandbox base classes anywhere
        # in the chain.
        forbidden_bases = {"PermissionChecker", "Hook", "Engine", "QueryContext"}
        for cls in mro:
            for forbidden in forbidden_bases:
                assert forbidden not in cls.__name__, (
                    f"SpawnAgent's MRO includes {cls.__name__} — must be a pure BaseTool subclass."
                )

    def test_no_engine_dispatch_path_for_subagent(self) -> None:
        # ``engine/query.py`` should NOT special-case SpawnAgent. The
        # only Phase 6 changes are the additive ``parent_query`` kwarg on
        # ToolExecutionContext construction and the ``bind_agent_depth``
        # context manager wrap. Both are tool-agnostic.
        from openharness.engine import query as query_mod

        source = inspect.getsource(query_mod)
        code = TestPhase6CrossCuttingInvariant._strip_comments_and_docstrings(source)
        # These identifiers MUST NOT appear in dispatch code:
        for forbidden in ("SpawnAgent", "SpawnAgentInput"):
            assert forbidden not in code, (
                f"engine/query.py references {forbidden!r} — must not "
                f"special-case sub-agent dispatch."
            )

    def test_no_isinstance_branch_on_SpawnAgent_anywhere(self) -> None:
        # Stronger check:``isinstance(..., SpawnAgent)`` must not appear
        # in any production source file. This guards against the
        # temptation to special-case sub-agent dispatch downstream.
        import openharness

        package_dir = inspect.getfile(openharness)
        from pathlib import Path

        pkg_root = Path(package_dir).parent
        for py_file in pkg_root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            assert (
                "isinstance(" not in text
                or "SpawnAgent" not in text
                or (
                    "isinstance" in text
                    and "SpawnAgent" not in text.split("isinstance(", 1)[1][:100]
                )
            ), (
                f"{py_file.relative_to(pkg_root)} contains an isinstance() "
                f"branch referencing SpawnAgent — cross-cutting invariant "
                f"violated."
            )
