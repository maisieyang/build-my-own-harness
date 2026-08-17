"""Typed tools for harness-owned durable project memory."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from openharness.markdown_store import NAME_PATTERN
from openharness.memory import (
    FilesystemMemoryStore,
    Memory,
    MemoryScope,
    MemoryType,
    compute_memory_signature,
)
from openharness.tools.base import (
    BaseTool,
    ExecutionDomain,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)


class MemoryListInput(BaseModel):
    """No arguments."""


class MemoryShowInput(BaseModel):
    name: str = Field(description="Exact memory name from MemoryList.")


class MemoryUpsertInput(BaseModel):
    name: str = Field(
        min_length=1,
        pattern=NAME_PATTERN.pattern,
        description="Stable kebab-case memory name.",
    )
    description: str = Field(
        min_length=1,
        description="One-line hook used to decide whether to recall this memory.",
    )
    type: MemoryType = Field(description="Memory category: user, feedback, project, or reference.")
    body: str = Field(min_length=1, description="Durable fact and the context needed to apply it.")


class MemoryDeleteInput(BaseModel):
    name: str = Field(description="Exact memory name to forget.")


class _MemoryTool:
    execution_domain = ExecutionDomain.TRUSTED_CONTROL
    trust_source = "local"

    def __init__(self, store: FilesystemMemoryStore) -> None:
        self._store = store


class MemoryListTool(_MemoryTool, BaseTool[MemoryListInput]):
    name = "MemoryList"
    description = "List durable memories for the current project as a concise catalog."
    input_model = MemoryListInput
    is_read_only = True

    async def execute(self, args: MemoryListInput, context: ToolExecutionContext) -> ToolResult:
        del args, context
        index = self._store.render_index()
        return ToolResult(output=index or "(no memories)")


class MemoryShowTool(_MemoryTool, BaseTool[MemoryShowInput]):
    name = "MemoryShow"
    description = "Load one durable project memory by its exact catalog name."
    input_model = MemoryShowInput
    is_read_only = True

    async def execute(self, args: MemoryShowInput, context: ToolExecutionContext) -> ToolResult:
        del context
        memory = self._store.get(args.name)
        if memory is None:
            available = ", ".join(sorted(self._store.discover())) or "(none)"
            return ToolResult(
                is_error=True,
                output=f"no memory named {args.name!r}; available memories: {available}",
            )
        return ToolResult(
            output=(
                f"name: {memory.name}\n"
                f"type: {memory.type.value}\n"
                f"description: {memory.description}\n\n"
                f"{memory.body.rstrip()}"
            )
        )


class MemoryUpsertTool(_MemoryTool, BaseTool[MemoryUpsertInput]):
    name = "MemoryUpsert"
    description = (
        "Create or replace one durable project memory. Root session only; "
        "OpenHarness owns storage paths and index updates."
    )
    input_model = MemoryUpsertInput
    is_read_only = False
    root_session_only = True

    async def execute(self, args: MemoryUpsertInput, context: ToolExecutionContext) -> ToolResult:
        if _is_subagent(context):
            return _root_only_error()
        now = datetime.now(timezone.utc)
        existing = self._store.get(args.name)
        memory = Memory(
            id=existing.id if existing is not None else uuid4().hex[:16],
            name=args.name,
            description=" ".join(args.description.split()),
            type=args.type,
            scope=MemoryScope.PRIVATE,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            body=args.body.rstrip() + "\n",
            signature=compute_memory_signature(args.body, args.type, MemoryScope.PRIVATE),
            source_path=(
                existing.source_path
                if existing is not None
                else self._store.project_dir / f"{args.name}.md"
            ),
        )
        path = self._store.upsert_memory(memory)
        return ToolResult(
            output=f"saved memory {args.name!r}",
            metadata={"name": args.name, "path": str(path)},
        )


class MemoryDeleteTool(_MemoryTool, BaseTool[MemoryDeleteInput]):
    name = "MemoryDelete"
    description = "Forget one durable project memory by exact name. Root session only."
    input_model = MemoryDeleteInput
    is_read_only = False
    root_session_only = True

    async def execute(self, args: MemoryDeleteInput, context: ToolExecutionContext) -> ToolResult:
        if _is_subagent(context):
            return _root_only_error()
        if not self._store.delete_memory(args.name):
            available = ", ".join(sorted(self._store.discover())) or "(none)"
            return ToolResult(
                is_error=True,
                output=f"no memory named {args.name!r}; available memories: {available}",
            )
        return ToolResult(output=f"deleted memory {args.name!r}")


def register_memory_tools(registry: ToolRegistry, store: FilesystemMemoryStore) -> None:
    """Register the complete typed memory surface in stable order."""
    registry.register(MemoryListTool(store))
    registry.register(MemoryShowTool(store))
    registry.register(MemoryUpsertTool(store))
    registry.register(MemoryDeleteTool(store))


def _is_subagent(context: ToolExecutionContext) -> bool:
    parent = context.parent_query
    return parent is not None and parent.agent_depth > 0


def _root_only_error() -> ToolResult:
    return ToolResult(
        is_error=True,
        output="project memory mutations are owned by the root session",
    )
