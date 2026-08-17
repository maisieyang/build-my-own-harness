"""Typed durable-memory control surface.

The model chooses memory semantics; OpenHarness owns paths, atomic writes,
index maintenance, and the root-session write boundary.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from openharness.memory import FilesystemMemoryStore
from openharness.tools import ExecutionDomain
from openharness.tools.base import ToolExecutionContext, ToolRegistry
from openharness.tools.memory import (
    MemoryDeleteInput,
    MemoryDeleteTool,
    MemoryListInput,
    MemoryListTool,
    MemoryShowInput,
    MemoryShowTool,
    MemoryUpsertInput,
    MemoryUpsertTool,
    register_memory_tools,
)


def _root_context(tmp_path: Any) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=tmp_path)


def _child_context(tmp_path: Any) -> ToolExecutionContext:
    parent_query = cast("Any", SimpleNamespace(agent_depth=1))
    return ToolExecutionContext(cwd=tmp_path, parent_query=parent_query)


class TestMemoryToolContract:
    def test_registers_four_trusted_control_tools(self, tmp_path: Any) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path / "memory")
        registry = ToolRegistry()

        register_memory_tools(registry, store)

        tools = registry.list_tools()
        assert [tool.name for tool in tools] == [
            "MemoryList",
            "MemoryShow",
            "MemoryUpsert",
            "MemoryDelete",
        ]
        assert all(tool.execution_domain is ExecutionDomain.TRUSTED_CONTROL for tool in tools)
        assert [tool.is_read_only for tool in tools] == [True, True, False, False]


class TestMemoryTools:
    async def test_list_empty_store(self, tmp_path: Any) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path / "memory")

        result = await MemoryListTool(store).execute(MemoryListInput(), _root_context(tmp_path))

        assert result.is_error is False
        assert result.output == "(no memories)"

    async def test_upsert_creates_storage_and_generated_index(self, tmp_path: Any) -> None:
        memory_dir = tmp_path / "memory"
        store = FilesystemMemoryStore(project_dir=memory_dir)

        result = await MemoryUpsertTool(store).execute(
            MemoryUpsertInput(
                name="preferred-test-command",
                description="Use the targeted unit-test command first",
                type="feedback",
                body="Run the targeted test before the full suite.",
            ),
            _root_context(tmp_path),
        )

        assert result.is_error is False
        assert "preferred-test-command" in result.output
        assert (memory_dir / "preferred-test-command.md").is_file()
        index = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert (
            "- [preferred-test-command](preferred-test-command.md) — "
            "Use the targeted unit-test command first"
        ) in index

    async def test_show_returns_typed_memory_body(self, tmp_path: Any) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path / "memory")
        root = _root_context(tmp_path)
        await MemoryUpsertTool(store).execute(
            MemoryUpsertInput(
                name="review-style",
                description="State the evidence before the conclusion",
                type="feedback",
                body="Lead reviews with concrete evidence.",
            ),
            root,
        )

        result = await MemoryShowTool(store).execute(MemoryShowInput(name="review-style"), root)

        assert result.is_error is False
        assert "type: feedback" in result.output
        assert "Lead reviews with concrete evidence." in result.output

    async def test_upsert_same_name_replaces_body_without_duplicate(self, tmp_path: Any) -> None:
        memory_dir = tmp_path / "memory"
        store = FilesystemMemoryStore(project_dir=memory_dir)
        root = _root_context(tmp_path)
        tool = MemoryUpsertTool(store)
        common = {
            "name": "review-style",
            "description": "How to structure reviews",
            "type": "feedback",
        }

        await tool.execute(MemoryUpsertInput(**common, body="Old guidance."), root)
        await tool.execute(MemoryUpsertInput(**common, body="Current guidance."), root)

        memories = store.discover()
        assert list(memories) == ["review-style"]
        assert memories["review-style"].body.strip() == "Current guidance."
        assert sorted(path.name for path in memory_dir.glob("review-style*.md")) == [
            "review-style.md"
        ]

    async def test_delete_removes_memory_and_refreshes_index(self, tmp_path: Any) -> None:
        memory_dir = tmp_path / "memory"
        store = FilesystemMemoryStore(project_dir=memory_dir)
        root = _root_context(tmp_path)
        await MemoryUpsertTool(store).execute(
            MemoryUpsertInput(
                name="obsolete",
                description="An obsolete preference",
                type="feedback",
                body="Do the old thing.",
            ),
            root,
        )

        result = await MemoryDeleteTool(store).execute(MemoryDeleteInput(name="obsolete"), root)

        assert result.is_error is False
        assert not (memory_dir / "obsolete.md").exists()
        assert "obsolete" not in (memory_dir / "MEMORY.md").read_text(encoding="utf-8")

    async def test_unknown_show_and_delete_are_recoverable_errors(self, tmp_path: Any) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path / "memory")
        root = _root_context(tmp_path)

        shown = await MemoryShowTool(store).execute(MemoryShowInput(name="missing"), root)
        deleted = await MemoryDeleteTool(store).execute(MemoryDeleteInput(name="missing"), root)

        assert shown.is_error is True
        assert deleted.is_error is True
        assert "missing" in shown.output
        assert "missing" in deleted.output

    @pytest.mark.parametrize("tool_name", ["upsert", "delete"])
    async def test_subagent_cannot_mutate_project_memory(
        self, tmp_path: Any, tool_name: str
    ) -> None:
        store = FilesystemMemoryStore(project_dir=tmp_path / "memory")
        child = _child_context(tmp_path)
        if tool_name == "upsert":
            result = await MemoryUpsertTool(store).execute(
                MemoryUpsertInput(
                    name="child-note",
                    description="Should not be persisted",
                    type="project",
                    body="Private subagent observation.",
                ),
                child,
            )
        else:
            result = await MemoryDeleteTool(store).execute(
                MemoryDeleteInput(name="anything"), child
            )

        assert result.is_error is True
        assert "root session" in result.output
        assert store.discover() == {}
