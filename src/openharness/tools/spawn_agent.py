"""``SpawnAgent`` — recursive tool dispatch — P6-T3 (D16.1).

The capability Phase 6 unlocks: the LLM can call ``Agent(description=...,
prompt=...)`` like any other tool. ``execute`` builds a sub-:class:`QueryContext`
via ``dataclasses.replace`` (inheriting every runtime field by default and
incrementing ``agent_depth``), drives the **same**
:func:`run_query` against the same engine, collects the sub-agent's final
assistant text, and returns it as a single :class:`ToolResult`.

The cross-cutting invariant: this is the **third tenant test** of
Phase 3's abstraction (after MCP / Skills). Engine dispatch loop /
authorization boundary / hook chain / observability layer all see ``Agent``
exactly like they see ``Read`` — the fact that ``execute`` internally
re-enters ``run_query`` is an implementation detail of this one
``BaseTool`` subclass.

Runtime contract:

- inherit all QueryContext fields by default, including the parent's optional
  ``max_turns`` circuit breaker; a specialized variant may explicitly override
  ``system_prompt`` or ``max_turns``
- D16.4: parent's conversation grows by exactly one tool_use/tool_result
  pair regardless of sub-agent length (internal events not surfaced)
- D16.5: depth check lives ENTIRELY in ``execute`` — engine is
  depth-agnostic
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.engine.errors import LoopLimitExceeded
from openharness.engine.query import run_query
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
)
from openharness.tools.base import BaseTool, ExecutionDomain, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from openharness.tools.base import ToolExecutionContext


class SpawnAgentInput(BaseModel):
    """LLM-facing input for ``Agent`` tool calls."""

    description: str = Field(
        description=(
            "Short label for the sub-task — appears in trace logs for "
            "debuggability. Example: 'research async patterns'."
        ),
    )
    prompt: str = Field(
        description=(
            "The full task description sent to the sub-agent as its initial "
            "user message. It inherits the parent's task tools, excluding "
            "root-session-only control operations."
        ),
    )


class SpawnAgent(BaseTool[SpawnAgentInput]):
    """Delegate a sub-task to a fresh agent loop with its own conversation context."""

    execution_domain = ExecutionDomain.DELEGATED_RUNTIME
    name = "Agent"
    description = (
        "Delegate a sub-task to a fresh agent loop with its own conversation "
        "context. The sub-agent receives `prompt` as its initial user message, "
        "runs independently through tool dispatch (inheriting your optional "
        "turn circuit breaker), "
        "and returns its final text response as the tool result. Use when a "
        "sub-task warrants isolated context — e.g., focused research that "
        "would clutter your main conversation, or a multi-step investigation "
        "you want compartmentalized. The sub-agent inherits task tools but not "
        "root-session-only control operations; it retains this `Agent` tool "
        "(bounded recursion: see "
        "OPENHARNESS_MAX_AGENT_DEPTH)."
    )
    input_model = SpawnAgentInput
    is_read_only = False  # Sub-agent can use mutating tools — AuthZ Tier 3 strict
    trust_source = "local"  # P5-T5 provenance

    def __init__(
        self,
        *,
        name: str = "Agent",
        description: str | None = None,
        system_prompt: str | None = None,
        max_turns: int | None = None,
        tool_filter: set[str] | None = None,
    ) -> None:
        """Construct a configurable :class:`SpawnAgent` variant.

        Phase 6 ships a single default ``Agent`` instance(`cli.py`
        registers it). Programmatic users can construct additional
        variants with differentiated ``system_prompt`` /``max_turns``
        — e.g., a ``ResearchAgent`` with a focused system prompt and
        a tighter turn budget.

        ``tool_filter`` optionally restricts the inherited catalog by exact
        name. Root-session-only control tools are always removed.
        """
        # Class attributes are overridable on the instance per-variant.
        if name != "Agent":
            self.name = name
        if description is not None:
            self.description = description
        self._sub_system_prompt = system_prompt
        self._max_turns_override = max_turns
        self._tool_filter = frozenset(tool_filter) if tool_filter is not None else None

    async def execute(
        self,
        args: SpawnAgentInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        # 1. Defensive: parent_query must be present (engine sets it per P6-T2).
        # In practice this branch only fires if a tool author manually constructs
        # a ToolExecutionContext without going through the engine.
        parent = context.parent_query
        if parent is None:
            return ToolResult(
                is_error=True,
                output="SpawnAgent invoked outside an active query context",
            )

        # 2. Depth check (D16.5). Top-level runs start at depth 0; default cap
        # of 3 supports supervisor → research → leaf chains. Refusal surfaces
        # to the parent LLM as is_error=True so it can adapt (errors-as-payload).
        if parent.agent_depth + 1 > parent.max_agent_depth:
            return ToolResult(
                is_error=True,
                output=(
                    f"max agent depth ({parent.max_agent_depth}) reached; "
                    f"cannot spawn further sub-agents"
                ),
            )

        # 3. Build sub-context. ``dataclasses.replace`` inherits every parent
        # field unless explicitly overridden — same api_client, same
        # task tools, same canonical profile/boundary, same hook_registry, same
        # skill_store, same cwd, same model, same max_tokens, same
        # reviewer/execution postures. Persistence and mutable permission
        # lifecycle are explicitly isolated: this Agent is an ephemeral tool
        # invocation, not an independently resumable user session. Only the
        # Agent depth always changes. System prompt and max_turns change only
        # for an explicitly configured specialized variant; the default Agent
        # inherits the parent's optional circuit breaker (including None).
        child_permission_runtime = (
            parent.permission_runtime.fork_for_subagent()
            if parent.permission_runtime is not None
            else None
        )
        child_registry = ToolRegistry()
        for tool in parent.tool_registry.list_tools():
            if tool.root_session_only:
                continue
            if self._tool_filter is not None and tool.name not in self._tool_filter:
                continue
            child_registry.register(tool)
        sub_context = dataclasses.replace(
            parent,
            system_prompt=self._sub_system_prompt
            if self._sub_system_prompt is not None
            else parent.system_prompt,
            max_turns=parent.max_turns
            if self._max_turns_override is None
            else self._max_turns_override,
            agent_depth=parent.agent_depth + 1,
            tool_registry=child_registry,
            permission_runtime=child_permission_runtime,
            snapshot_enabled=False,
            llm_focus_state_enabled=False,
        )

        # 4. Sub-agent receives `args.prompt` as its initial user message —
        # totally isolated from the parent's conversation.
        initial_messages: list[ConversationMessage] = [
            ConversationMessage(
                role="user",
                content=[TextBlock(text=args.prompt)],
            ),
        ]

        # 5. Drive run_query and collect the final assistant message.
        # Per D16.4: internal events are NOT surfaced — we just consume them
        # to drive the loop and capture the final ApiMessageCompleteEvent.
        final_event: ApiMessageCompleteEvent | None = None
        try:
            async for event in run_query(initial_messages, sub_context):
                if isinstance(event, ApiMessageCompleteEvent):
                    final_event = event
        except LoopLimitExceeded:
            # D16.4: turn-budget exhaustion surfaces as is_error so parent LLM
            # can pivot. Other OpenHarnessError types propagate (engine wraps
            # as ToolError in the parent's dispatch loop).
            return ToolResult(
                is_error=True,
                output=(f"sub-agent exceeded max_turns={sub_context.max_turns} without completing"),
            )

        if final_event is None:
            # Defensive: run_query should always emit a terminal
            # ApiMessageCompleteEvent. If it doesn't, something upstream broke.
            return ToolResult(
                is_error=True,
                output="sub-agent produced no completion event",
            )

        # 6. Extract text content from the final assistant message. Tool-use
        # blocks at the end of a sub-agent's run are unusual (would mean the
        # sub-agent stopped mid-dispatch); but if present, ignore them — the
        # parent only consumes the text response.
        text_parts = [
            block.text for block in final_event.message.content if isinstance(block, TextBlock)
        ]
        output = "\n".join(text_parts) if text_parts else "(no text response)"
        return ToolResult(output=output)
