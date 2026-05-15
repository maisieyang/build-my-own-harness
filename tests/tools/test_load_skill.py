"""Tests for ``LoadSkillTool`` — P5c-T2.

Four surfaces:

1. **Static class attributes** — name, description, is_read_only flag,
   input model. These are the LLM-facing contract.
2. **Happy path round-trip** — registered skill → ``execute`` returns
   ``ToolResult(output=skill.body)``.
3. **Error paths** — unknown name → ``is_error=True`` with the catalog
   surfaced(errors-as-payload,framing §4.2). The LLM reads the
   catalog and corrects itself.
4. **Invariant verification** — ``is_read_only=True`` propagates
   through :class:`TierBasedPermissionChecker` Tier 3 → ``Decision.ALLOW``
   without any LoadSkill-aware branching in ``permissions/``. This is
   the third tenant test of Phase 3 abstractions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openharness.config.settings import Settings
from openharness.permissions.checker import Decision
from openharness.permissions.tier_based import TierBasedPermissionChecker
from openharness.skills.store import EmptySkillStore, FilesystemSkillStore
from openharness.tools import LoadSkillInput, LoadSkillTool, ToolRegistry
from openharness.tools.base import ToolExecutionContext

if TYPE_CHECKING:
    from pathlib import Path


def _write_skill(directory: Path, name: str, description: str, body: str = "default body") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def store_with_two_skills(tmp_path: Path) -> FilesystemSkillStore:
    """Filesystem store with ``react-testing`` and ``sql-tuning`` pre-loaded."""
    _write_skill(tmp_path, "react-testing", "React tests", body="React patterns here")
    _write_skill(tmp_path, "sql-tuning", "Postgres perf", body="EXPLAIN ANALYZE")
    store = FilesystemSkillStore(global_dir=tmp_path)
    store.discover()  # warm cache
    return store


@pytest.fixture
def exec_ctx(tmp_path: Path) -> ToolExecutionContext:
    """LoadSkill is cwd-independent, but the ABC signature needs one."""
    return ToolExecutionContext(cwd=tmp_path)


class TestLoadSkillToolStatic:
    """Class-level contract — these never change after construction."""

    def test_name_is_LoadSkill(self) -> None:
        assert LoadSkillTool.name == "LoadSkill"

    def test_is_read_only_true(self) -> None:
        # The L5 invariant: this attribute is what makes the AuthZ Tier 3
        # lax path apply automatically. If this flips to False, permission
        # would route LoadSkill through the strict write/exec path.
        assert LoadSkillTool.is_read_only is True

    def test_input_model_is_LoadSkillInput(self) -> None:
        assert LoadSkillTool.input_model is LoadSkillInput

    def test_description_mentions_catalog(self) -> None:
        # LLM uses this string to decide when to call. Must reference the
        # catalog so the LLM knows where ``name`` comes from.
        assert "catalog" in LoadSkillTool.description.lower()
        assert "skill" in LoadSkillTool.description.lower()

    def test_to_api_schema_round_trip(self, store_with_two_skills: FilesystemSkillStore) -> None:
        # Register in a fresh registry → verify ToolSpec emission works
        # (Pydantic input model schema generation must not crash).
        registry = ToolRegistry()
        registry.register(LoadSkillTool(store_with_two_skills))
        schemas = registry.to_api_schema()
        assert len(schemas) == 1
        spec = schemas[0]
        assert spec.name == "LoadSkill"
        assert "name" in spec.input_schema.get("properties", {})


class TestLoadSkillToolHappyPath:
    """Round-trip: known skill → body returned as ToolResult."""

    async def test_returns_skill_body(
        self,
        store_with_two_skills: FilesystemSkillStore,
        exec_ctx: ToolExecutionContext,
    ) -> None:
        tool = LoadSkillTool(store_with_two_skills)
        result = await tool.execute(
            LoadSkillInput(name="react-testing"),
            exec_ctx,
        )
        assert result.is_error is False
        assert "React patterns here" in result.output

    async def test_works_with_second_skill(
        self,
        store_with_two_skills: FilesystemSkillStore,
        exec_ctx: ToolExecutionContext,
    ) -> None:
        # Independent fetch — the store isn't single-shot.
        tool = LoadSkillTool(store_with_two_skills)
        result = await tool.execute(LoadSkillInput(name="sql-tuning"), exec_ctx)
        assert result.is_error is False
        assert "EXPLAIN ANALYZE" in result.output


class TestLoadSkillToolErrorPaths:
    """Unknown name surfaces catalog so the LLM can self-correct."""

    async def test_unknown_name_returns_is_error(
        self,
        store_with_two_skills: FilesystemSkillStore,
        exec_ctx: ToolExecutionContext,
    ) -> None:
        tool = LoadSkillTool(store_with_two_skills)
        result = await tool.execute(LoadSkillInput(name="nope"), exec_ctx)
        assert result.is_error is True

    async def test_unknown_name_includes_catalog(
        self,
        store_with_two_skills: FilesystemSkillStore,
        exec_ctx: ToolExecutionContext,
    ) -> None:
        tool = LoadSkillTool(store_with_two_skills)
        result = await tool.execute(LoadSkillInput(name="nope"), exec_ctx)
        # Catalog must surface both available names so the LLM can pivot.
        assert "react-testing" in result.output
        assert "sql-tuning" in result.output
        assert "nope" in result.output  # the queried name shows up in the error

    async def test_empty_store_unknown_name(self, exec_ctx: ToolExecutionContext) -> None:
        # Empty store → catalog is "(none)" so LLM knows there are no
        # skills, not just that this one is missing.
        tool = LoadSkillTool(EmptySkillStore())
        result = await tool.execute(LoadSkillInput(name="anything"), exec_ctx)
        assert result.is_error is True
        assert "(none)" in result.output


class TestLoadSkillToolInvariant:
    """L5 / cross-cutting: ``is_read_only=True`` propagates through
    :class:`TierBasedPermissionChecker` to ``Decision.ALLOW`` **without any
    LoadSkill-aware branching in ``permissions/``** — verifying the third
    tenant of the Phase 3 abstraction invariant.
    """

    def test_tier3_returns_allow_for_LoadSkill(
        self,
        store_with_two_skills: FilesystemSkillStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Construct a real TierBasedPermissionChecker the way the CLI does,
        # then ask it about LoadSkill — should return ALLOW because the
        # tool's ``is_read_only=True`` routes it through Tier 3 lax path.
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        registry = ToolRegistry()
        registry.register(LoadSkillTool(store_with_two_skills))
        settings = Settings()
        checker = TierBasedPermissionChecker(registry, settings)

        result = checker.evaluate(
            tool_name="LoadSkill",
            args=LoadSkillInput(name="react-testing"),
            context=ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision == Decision.ALLOW

    def test_tier3_allow_even_for_unknown_skill_name(
        self,
        store_with_two_skills: FilesystemSkillStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Permission cares about ``tool.is_read_only``,not whether the
        # particular ``name`` exists — that's the tool's job. The dispatch
        # path is uniform.
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-x")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://x/v1")
        registry = ToolRegistry()
        registry.register(LoadSkillTool(store_with_two_skills))
        settings = Settings()
        checker = TierBasedPermissionChecker(registry, settings)

        result = checker.evaluate(
            tool_name="LoadSkill",
            args=LoadSkillInput(name="hallucinated-name"),
            context=ToolExecutionContext(cwd=tmp_path),
        )
        assert result.decision == Decision.ALLOW

    def test_no_permissions_module_imports_LoadSkillTool(self) -> None:
        # The invariant in code: permissions/ must NOT know about LoadSkill.
        # If permissions/ ever imports it, the abstraction has leaked.
        import openharness.permissions.checker as checker_mod
        import openharness.permissions.tier_based as tier_mod

        for mod in (checker_mod, tier_mod):
            assert "LoadSkillTool" not in dir(mod), (
                f"{mod.__name__} unexpectedly references LoadSkillTool — "
                "the cross-cutting invariant has been violated."
            )
