"""Tests for ``LoadSkillTool`` — P5c-T2.

Four surfaces:

1. **Static class attributes** — name, description, is_read_only flag,
   input model. These are the LLM-facing contract.
2. **Happy path round-trip** — registered skill → ``execute`` returns
   ``ToolResult(output=skill.body)``.
3. **Error paths** — unknown name → ``is_error=True`` with the catalog
   surfaced(errors-as-payload,framing §4.2). The LLM reads the
   catalog and corrects itself.
4. **Invariant verification** — no authorization module branches on the
   LoadSkill implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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
    def test_no_permissions_module_names_load_skill_tool(self) -> None:
        from pathlib import Path

        source = Path(__file__).parents[2] / "src" / "openharness" / "permissions"
        assert all("LoadSkillTool" not in path.read_text() for path in source.glob("*.py"))
