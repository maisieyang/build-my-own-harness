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
from openharness.hooks import HookContext, HookRegistry, HookResult
from openharness.permissions import (
    ExternalPolicyEvidence,
    ExternalToolMode,
    ExternalToolPolicy,
    PermissionDeltaRequest,
    PermissionMode,
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
    ExternalEffectKind,
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
    external_effect_kind = ExternalEffectKind.MUTATING
    external_effect_trusted = True

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
    external_effect_kind = None

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


class _WebReadTool(_ExternalTool):
    name = "WebRead"
    external_effect_surface = ExternalEffectSurface.WEB
    external_effect_kind = ExternalEffectKind.NETWORK_READ


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


def _tool_turn(tool_name: str = "RemoteMutation") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": tool_name,
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
        external_tool_policy=ExternalToolPolicy(mcp=mode),
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


async def test_explicit_surface_allow_does_not_bypass_mutating_approval() -> None:
    tool, events = await _run(ExternalToolMode.ALLOW)

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert tool.executed is False
    assert completed.is_error is True
    assert "external approval required" in completed.output


async def test_trusted_read_only_external_call_can_use_explicit_surface_allow() -> None:
    class _TrustedRead(_ExternalTool):
        external_effect_kind = ExternalEffectKind.READ_ONLY

    tool = _TrustedRead()
    registry = ToolRegistry()
    registry.register(tool)
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
        external_tool_policy=ExternalToolPolicy(mcp="allow"),
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert tool.executed is True
    assert completed.is_error is False


async def test_untrusted_external_call_cannot_use_broad_surface_allow() -> None:
    class _UntrustedRead(_ExternalTool):
        external_effect_kind = ExternalEffectKind.READ_ONLY
        external_effect_trusted = False

    tool = _UntrustedRead()
    registry = ToolRegistry()
    registry.register(tool)
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
        external_tool_policy=ExternalToolPolicy(mcp="allow"),
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert tool.executed is False
    assert completed.is_error is True
    assert "external approval required" in completed.output


async def test_web_network_policy_applies_without_a_local_sandbox_profile() -> None:
    tool = _WebReadTool()
    registry = ToolRegistry()
    registry.register(tool)
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn("WebRead")], [_end_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        external_tool_policy=ExternalToolPolicy(web="deny"),
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert tool.executed is False
    assert completed.is_error is True
    assert "denied by web policy" in completed.output


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


async def test_no_sandbox_external_ask_uses_same_exact_runtime() -> None:
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    reviewer = _Reviewer(PermissionReviewVerdict.approve("allow exact external call"))
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    runtime = PermissionRuntime(profile=profile, boundary=None, reviewer=reviewer)
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
        external_tool_policy=profile.external_tools,
        runtime_permission_profile=profile,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert completed.is_error is False
    assert tool.executed is True
    assert len(reviewer.calls) == 1
    assert isinstance(reviewer.calls[0].enforcement, ExternalPolicyEvidence)


async def test_no_sandbox_external_defer_emits_typed_park() -> None:
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    reviewer = _Reviewer(PermissionReviewVerdict.defer("human must decide"))
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )
    runtime = PermissionRuntime(profile=profile, boundary=None, reviewer=reviewer)
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
        external_tool_policy=profile.external_tools,
        runtime_permission_profile=profile,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    parked = next(event for event in events if isinstance(event, PermissionParkedEvent))
    assert parked.enforcement["kind"] == "external_policy"
    assert parked.boundary_fingerprint is None
    assert parked.backend is None
    assert tool.executed is False


async def test_manual_and_auto_review_receive_byte_equivalent_external_request() -> None:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(mcp="ask")}
    )

    async def run_once(
        *,
        mode: PermissionMode,
        runtime: PermissionRuntime,
    ) -> None:
        registry = ToolRegistry()
        registry.register(_ExternalTool())
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
            permission_mode=mode,
            external_tool_policy=profile.external_tools,
            runtime_permission_profile=profile,
            permission_runtime=runtime,
            authorization_context=("Create the one requested issue.",),
        )
        async for _ in run_query([], context):
            pass

    manual = PermissionRuntime(profile=profile, boundary=None)
    reviewer = _Reviewer(PermissionReviewVerdict.defer("human must decide"))
    automatic = PermissionRuntime(profile=profile, boundary=None, reviewer=reviewer)

    await run_once(mode=PermissionMode.DEFAULT, runtime=manual)
    await run_once(mode=PermissionMode.AUTO, runtime=automatic)

    assert manual.parked_request is not None
    assert reviewer.calls == [automatic.parked_request]
    assert automatic.parked_request is not None
    assert manual.parked_request.model_dump(mode="json") == automatic.parked_request.model_dump(
        mode="json"
    )


async def test_hook_modified_final_arguments_are_what_external_reviewer_authorizes() -> None:
    async def rewrite(context: HookContext) -> HookResult | None:
        del context
        return HookResult.modify_input({"value": "rewritten"})

    hooks = HookRegistry()
    hooks.register("PreToolUse", rewrite)
    tool = _ExternalTool()
    registry = ToolRegistry()
    registry.register(tool)
    reviewer = _Reviewer(PermissionReviewVerdict.approve("allow exact rewritten call"))
    profile, runtime = _runtime(reviewer)
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn()], [_end_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        hook_registry=hooks,
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        external_tool_policy=profile.external_tools,
        runtime_permission_profile=profile,
        enforced_boundary=runtime.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert completed.is_error is False
    assert reviewer.calls[0].final_arguments == {"value": "rewritten"}


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
