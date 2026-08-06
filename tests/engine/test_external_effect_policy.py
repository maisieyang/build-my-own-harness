"""S6: external surfaces use independent policy, never local sandbox claims."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from engine.conftest import _AllowAllChecker, _StubApiClient
from openharness.engine import QueryContext, run_query
from openharness.execution import (
    BoundaryVerification,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    ExternalToolMode,
    ExternalToolPolicy,
    PermissionDeltaRequest,
    PermissionReviewVerdict,
    PermissionRuntime,
    RuntimePermissionProfile,
    workspace_runtime_profile,
)
from openharness.protocols import (
    ApiMessageCompleteEvent,
    BoundaryViolationEvent,
    ConversationMessage,
    PermissionParkedEvent,
    TextBlock,
    ToolExecutionCompletedEvent,
    UsageSnapshot,
)
from openharness.tools import (
    BaseTool,
    ExecutionDomain,
    ExternalEffectSurface,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)

if TYPE_CHECKING:
    from openharness.api import OpenAICompatibleApiClient
    from openharness.protocols import ApiStreamEvent


class _Input(BaseModel):
    value: str


class _ExternalTool(BaseTool[_Input]):
    name = "RemoteMutation"
    description = "Mutate a remote system."
    input_model = _Input
    execution_domain = ExecutionDomain.EXTERNAL_EFFECT
    external_effect_surface = ExternalEffectSurface.MCP

    def __init__(self) -> None:
        self.executed = False

    async def execute(self, args: _Input, context: ToolExecutionContext) -> ToolResult:
        del args, context
        self.executed = True
        return ToolResult(output="executed")


class _BoundaryTool(_ExternalTool):
    name = "BoundaryTool"
    execution_domain = ExecutionDomain.LOCAL_DATA
    external_effect_surface = None

    async def execute(self, args: _Input, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(
            output="blocked",
            is_error=True,
            metadata={
                "boundary_violation": {
                    "dimension": "filesystem.write",
                    "requested": "/outside",
                    "evidence": "seatbelt deny",
                }
            },
        )


@dataclass
class _Reviewer:
    verdict: PermissionReviewVerdict
    calls: list[PermissionDeltaRequest] = field(default_factory=list)

    async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict:
        self.calls.append(request)
        return self.verdict


def _runtime(reviewer: _Reviewer) -> tuple[RuntimePermissionProfile, PermissionRuntime]:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    boundary = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="test",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    return profile, PermissionRuntime(profile=profile, boundary=boundary, reviewer=reviewer)


def _tool_turn() -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "RemoteMutation",
                        "input": {"value": "x"},
                    }
                ],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "tool_use",
        }
    )


def _end_turn() -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent(
        message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )


async def _run(mode: ExternalToolMode) -> tuple[_ExternalTool, list[ApiStreamEvent]]:
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    client = _StubApiClient(events_per_turn=[[_tool_turn()], [_end_turn()]])
    context = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", client),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        max_tokens=32,
        runtime_permission_profile=RuntimePermissionProfile(
            name="external",
            external_tools=ExternalToolPolicy(mcp=mode),
        ),
    )
    events = [event async for event in run_query([], context)]
    return tool, events


async def test_external_deny_is_hard_even_when_legacy_checker_allows() -> None:
    tool, events = await _run(ExternalToolMode.DENY)

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert tool.executed is False
    assert completed.is_error is True
    assert "external effect denied" in completed.output


async def test_external_ask_does_not_inherit_local_auto_or_sandbox_trust() -> None:
    tool, events = await _run(ExternalToolMode.ASK)

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert tool.executed is False
    assert completed.is_error is True
    assert "external approval required" in completed.output


async def test_explicit_external_allow_executes() -> None:
    tool, events = await _run(ExternalToolMode.ALLOW)

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert tool.executed is True
    assert completed.is_error is False
    assert completed.output == "executed"


async def test_ask_uses_reviewer_for_exact_call_and_executes_once() -> None:
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    reviewer = _Reviewer(PermissionReviewVerdict.approve("allow exact call"))
    profile, runtime = _runtime(reviewer)
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn()], [_end_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        runtime_permission_profile=profile,
        enforced_boundary=runtime.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert completed.is_error is False
    assert tool.executed is True
    assert len(reviewer.calls) == 1
    assert reviewer.calls[0].final_arguments == {"value": "x"}


async def test_reviewer_defer_emits_typed_park_and_stops_before_next_model_turn() -> None:
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    reviewer = _Reviewer(PermissionReviewVerdict.defer("human must decide"))
    profile, runtime = _runtime(reviewer)
    client = _StubApiClient(events_per_turn=[[_tool_turn()], [_end_turn()]])
    context = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", client),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        runtime_permission_profile=profile,
        enforced_boundary=runtime.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    parked = next(event for event in events if isinstance(event, PermissionParkedEvent))
    assert parked.request_id == runtime.parked_request.request_id
    assert parked.reason == "human must decide"
    assert tool.executed is False
    assert len(client.captured_requests) == 1


async def test_local_boundary_violation_emits_structured_event() -> None:
    tool = _BoundaryTool()
    registry = ToolRegistry()
    registry.register(tool)
    turn = _tool_turn().model_copy(
        update={
            "message": ConversationMessage.model_validate(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "BoundaryTool",
                            "input": {"value": "x"},
                        }
                    ],
                }
            )
        }
    )
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[turn], [_end_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
    )

    events = [event async for event in run_query([], context)]

    violation = next(event for event in events if isinstance(event, BoundaryViolationEvent))
    assert violation.dimension == "filesystem.write"
    assert violation.requested == "/outside"
    assert violation.evidence == "seatbelt deny"
