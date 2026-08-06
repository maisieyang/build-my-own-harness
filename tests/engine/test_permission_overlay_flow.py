from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from engine.conftest import _AllowAllChecker, _StubApiClient
from openharness.engine import QueryContext, run_query
from openharness.engine.query import (
    _boundary_violation_metadata,
    _dataflow_for_violation,
    _delta_for_violation,
    extract_authorization_context,
)
from openharness.execution import (
    BackendSupport,
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
    OneShotOverlaySession,
    ProcessCompleted,
)
from openharness.permissions import (
    PermissionDeltaRequest,
    PermissionFilesystemAccess,
    PermissionReviewDecision,
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
    ToolResultBlock,
    UsageSnapshot,
)
from openharness.tools import Bash, ToolRegistry
from openharness.tools.base import ToolResult

if TYPE_CHECKING:
    from openharness.api import OpenAICompatibleApiClient
    from openharness.execution import DataPlaneOperation


def _boundary(profile: RuntimePermissionProfile) -> EnforcedBoundary:
    return EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="fake",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )


@dataclass
class _BaseSession:
    profile: RuntimePermissionProfile
    closed: bool = False

    @property
    def boundary(self) -> EnforcedBoundary:
        return _boundary(self.profile)

    async def execute(self, operation: DataPlaneOperation) -> BoundaryViolation:
        del operation
        return BoundaryViolation(
            dimension="network.disabled",
            requested="example.com:443",
            evidence="network access is disabled by the active profile",
        )

    async def close(self) -> None:
        self.closed = True


@dataclass
class _OverlaySession:
    profile: RuntimePermissionProfile
    closed: bool = False

    @property
    def boundary(self) -> EnforcedBoundary:
        return _boundary(self.profile)

    async def execute(self, operation: DataPlaneOperation) -> ProcessCompleted:
        del operation
        return ProcessCompleted(output="downloaded\n", exit_code=0)

    async def close(self) -> None:
        self.closed = True


@dataclass
class _Backend:
    opened: list[_OverlaySession] = field(default_factory=list)

    def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
        assert profile.network.allow_domains == ("example.com",)
        return BackendSupport.available(backend="fake")

    async def open(self, profile: RuntimePermissionProfile) -> _OverlaySession:
        session = _OverlaySession(profile)
        self.opened.append(session)
        return session


@dataclass
class _Reviewer:
    calls: list[PermissionDeltaRequest] = field(default_factory=list)

    async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict:
        self.calls.append(request)
        return PermissionReviewVerdict.approve("exact domain once")


def _tool_turn() -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": "curl https://example.com"},
                    }
                ],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "tool_use",
        }
    )


def _multi_tool_turn() -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": "curl https://example.com"},
                    },
                    {
                        "type": "tool_use",
                        "id": "tool-2",
                        "name": "Bash",
                        "input": {"command": "printf should-not-run"},
                    },
                ],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "tool_use",
        }
    )


async def test_boundary_review_installs_and_consumes_one_verified_overlay() -> None:
    profile = workspace_runtime_profile()
    base = _BaseSession(profile)
    backend = _Backend()
    sandbox = OneShotOverlaySession(backend=backend, profile=profile, base=base)
    reviewer = _Reviewer()
    runtime = PermissionRuntime(profile=profile, boundary=base.boundary, reviewer=reviewer)
    registry = ToolRegistry()
    registry.register(Bash())
    end = ApiMessageCompleteEvent(
        message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn()], [end]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        sandbox_session=sandbox,
        runtime_permission_profile=profile,
        enforced_boundary=base.boundary,
        permission_runtime=runtime,
    )

    user_message = ConversationMessage(
        role="user",
        content=[TextBlock(text="Download this one public URL so we can inspect it locally.")],
    )
    events = [event async for event in run_query([user_message], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert completed.is_error is False
    assert completed.output == "downloaded\n"
    violation = next(event for event in events if isinstance(event, BoundaryViolationEvent))
    assert violation.requested == "example.com:443"
    assert reviewer.calls[0].final_arguments == {
        "command": "curl https://example.com",
        "timeout_seconds": None,
    }
    assert reviewer.calls[0].authorization_context == (
        "Download this one public URL so we can inspect it locally.",
    )
    assert reviewer.calls[0].profile_facts == profile.normalized()
    assert reviewer.calls[0].boundary_facts["network_rules"] == []
    assert len(backend.opened) == 1
    assert backend.opened[0].closed is True
    assert runtime.parked_request is None


async def test_explicit_network_deny_never_reaches_reviewer() -> None:
    class _ExplicitDeny(_BaseSession):
        async def execute(self, operation: DataPlaneOperation) -> BoundaryViolation:
            del operation
            return BoundaryViolation(
                dimension="network.domain",
                requested="blocked.example:443",
                evidence="domain is explicitly denied",
                hard_deny=True,
            )

    profile = workspace_runtime_profile()
    base = _ExplicitDeny(profile)
    reviewer = _Reviewer()
    runtime = PermissionRuntime(profile=profile, boundary=base.boundary, reviewer=reviewer)
    registry = ToolRegistry()
    registry.register(Bash())
    end = ApiMessageCompleteEvent(
        message=ConversationMessage(role="assistant", content=[TextBlock(text="cannot")]),
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn()], [end]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        sandbox_session=OneShotOverlaySession(backend=_Backend(), profile=profile, base=base),
        runtime_permission_profile=profile,
        enforced_boundary=base.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert completed.is_error is True
    assert "hard deny" in completed.output
    assert reviewer.calls == []


async def test_local_reviewer_defer_parks_before_next_model_turn() -> None:
    @dataclass
    class _DeferReviewer:
        calls: int = 0

        async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict:
            del request
            self.calls += 1
            return PermissionReviewVerdict(
                decision=PermissionReviewDecision.DEFER,
                reason="needs owner",
            )

    profile = workspace_runtime_profile()
    base = _BaseSession(profile)
    reviewer = _DeferReviewer()
    runtime = PermissionRuntime(profile=profile, boundary=base.boundary, reviewer=reviewer)
    registry = ToolRegistry()
    registry.register(Bash())
    client = _StubApiClient(events_per_turn=[[_tool_turn()]])
    context = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", client),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        sandbox_session=OneShotOverlaySession(backend=_Backend(), profile=profile, base=base),
        runtime_permission_profile=profile,
        enforced_boundary=base.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    assert any(isinstance(event, PermissionParkedEvent) for event in events)
    assert reviewer.calls == 1
    assert len(client.captured_requests) == 1


async def test_parked_multi_tool_turn_persists_a_well_formed_tool_result_pair() -> None:
    @dataclass
    class _DeferReviewer:
        async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict:
            del request
            return PermissionReviewVerdict.defer("needs owner")

    profile = workspace_runtime_profile()
    base = _BaseSession(profile)
    runtime = PermissionRuntime(
        profile=profile,
        boundary=base.boundary,
        reviewer=_DeferReviewer(),
    )
    registry = ToolRegistry()
    registry.register(Bash())
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_multi_tool_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        sandbox_session=OneShotOverlaySession(backend=_Backend(), profile=profile, base=base),
        runtime_permission_profile=profile,
        enforced_boundary=base.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    parked = next(event for event in events if isinstance(event, PermissionParkedEvent))
    result_blocks = [
        block for block in parked.messages[-1].content if isinstance(block, ToolResultBlock)
    ]
    assert [block.tool_use_id for block in result_blocks] == ["tool-1", "tool-2"]
    assert result_blocks[0].is_error is True
    assert result_blocks[1].is_error is True
    assert result_blocks[1].content == "not executed: permission request parked"


async def test_approved_delta_without_overlay_executor_parks_fail_closed() -> None:
    profile = workspace_runtime_profile()
    base = _BaseSession(profile)
    runtime = PermissionRuntime(
        profile=profile,
        boundary=base.boundary,
        reviewer=_Reviewer(),
    )
    registry = ToolRegistry()
    registry.register(Bash())
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        sandbox_session=base,
        runtime_permission_profile=profile,
        enforced_boundary=base.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert completed.output == (
        "permission parked: approved delta has no verified overlay executor"
    )
    assert runtime.parked_request is not None


async def test_approved_delta_parks_when_backend_cannot_compile_overlay() -> None:
    class _UnsupportedBackend(_Backend):
        def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
            del profile
            return BackendSupport.unsupported(
                backend="fake",
                features=("network",),
                reason="network overlay unsupported",
            )

    profile = workspace_runtime_profile()
    base = _BaseSession(profile)
    runtime = PermissionRuntime(
        profile=profile,
        boundary=base.boundary,
        reviewer=_Reviewer(),
    )
    registry = ToolRegistry()
    registry.register(Bash())
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        sandbox_session=OneShotOverlaySession(
            backend=_UnsupportedBackend(),
            profile=profile,
            base=base,
        ),
        runtime_permission_profile=profile,
        enforced_boundary=base.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert (
        "approved overlay could not be installed: network overlay unsupported" in completed.output
    )
    assert runtime.parked_request is not None


async def test_approved_overlay_that_still_violates_boundary_parks_once() -> None:
    @dataclass
    class _UnsatisfiedBackend:
        def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport:
            del profile
            return BackendSupport.available(backend="fake")

        async def open(self, profile: RuntimePermissionProfile) -> _BaseSession:
            return _BaseSession(profile)

    profile = workspace_runtime_profile()
    base = _BaseSession(profile)
    runtime = PermissionRuntime(
        profile=profile,
        boundary=base.boundary,
        reviewer=_Reviewer(),
    )
    registry = ToolRegistry()
    registry.register(Bash())
    context = QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            _StubApiClient(events_per_turn=[[_tool_turn()]]),
        ),
        tool_registry=registry,
        permission_checker=_AllowAllChecker(),
        system_prompt="test",
        cwd=Path("/tmp"),
        model="qwen-plus",
        sandbox_session=OneShotOverlaySession(
            backend=_UnsatisfiedBackend(),
            profile=profile,
            base=base,
        ),
        runtime_permission_profile=profile,
        enforced_boundary=base.boundary,
        permission_runtime=runtime,
    )

    events = [event async for event in run_query([], context)]

    completed = next(event for event in events if isinstance(event, ToolExecutionCompletedEvent))
    assert completed.output == (
        "permission parked: one-shot overlay did not satisfy the exact boundary request"
    )
    assert runtime.parked_request is not None


def test_boundary_metadata_and_minimum_filesystem_deltas_are_typed() -> None:
    assert _boundary_violation_metadata(ToolResult(output="x")) is None
    assert (
        _boundary_violation_metadata(
            ToolResult(output="x", metadata={"boundary_violation": {"dimension": 1}})
        )
        is None
    )
    assert (
        _boundary_violation_metadata(
            ToolResult(
                output="x",
                metadata={
                    "boundary_violation": {
                        "dimension": "filesystem.read",
                        "requested": "/outside/in",
                        "evidence": "denied",
                        "hard_deny": "false",
                    }
                },
            )
        )
        is None
    )

    read = BoundaryViolation("filesystem.read", "/outside/in", "denied")
    search = BoundaryViolation("filesystem.search", "/outside", "denied")
    write = BoundaryViolation("filesystem.write", "/outside/out", "denied")
    hard_write = BoundaryViolation(
        "filesystem.write", "/workspace/.git/config", "denied", hard_deny=True
    )
    assert _delta_for_violation(read).filesystem_access is PermissionFilesystemAccess.READ
    assert _delta_for_violation(search).filesystem_access is PermissionFilesystemAccess.SEARCH
    assert _delta_for_violation(write).filesystem_access is PermissionFilesystemAccess.WRITE
    assert _delta_for_violation(hard_write).hard_deny is True
    assert _delta_for_violation(BoundaryViolation("process.signal", "123", "denied")) is None
    assert _dataflow_for_violation(read) == (("/outside/in",), ("model context",))
    assert _dataflow_for_violation(write) == (
        ("final tool arguments",),
        ("/outside/out",),
    )
    assert _dataflow_for_violation(
        BoundaryViolation("network.domain", "example.com:443", "denied")
    ) == (("sandbox-visible data",), ("example.com:443",))


def test_authorization_context_excludes_machine_generated_goal_turns() -> None:
    messages = [
        ConversationMessage(
            role="user",
            content=[TextBlock(text="Please update the dependency.")],
        ),
        ConversationMessage(
            role="user",
            content=[TextBlock(text="[goal-status] set: tests pass")],
        ),
        ConversationMessage(
            role="user",
            content=[TextBlock(text="[goal set] Work toward this goal now: tests pass")],
        ),
        ConversationMessage(
            role="user",
            content=[TextBlock(text="[goal checker] not met: run tests again")],
        ),
    ]

    assert extract_authorization_context(messages) == (
        "Please update the dependency.",
        "[goal-status] set: tests pass",
    )
