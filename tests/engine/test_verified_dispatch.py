"""Verified local dispatch bypasses the legacy permission checker."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel

from engine.conftest import _StubApiClient
from openharness.engine import QueryContext
from openharness.engine.errors import AutonomousBoundaryError
from openharness.engine.query import (
    _dispatch_one,
    _verified_dispatch_authorization,
    run_query,
)
from openharness.execution import (
    BoundaryVerification,
    EnforcedBoundary,
    ExecutionEffect,
    OperationCompleted,
    ProcessCompleted,
)
from openharness.permissions import ExternalToolPolicy, workspace_runtime_profile
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
    UsageSnapshot,
)
from openharness.tools import (
    BaseTool,
    Bash,
    ExecutionDomain,
    ExternalEffectKind,
    ExternalEffectSurface,
    Read,
    SpawnAgent,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api import OpenAICompatibleApiClient
    from openharness.execution import DataPlaneOperation, ExecutionResult


class _ExplodingChecker:
    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> None:
        del tool_name, args, context
        raise AssertionError("legacy checker must not run in verified dispatch")


class _NoArgs(BaseModel):
    pass


class _TrustedControlTool(BaseTool[_NoArgs]):
    execution_domain = ExecutionDomain.TRUSTED_CONTROL
    name = "Control"
    description = "test trusted control"
    input_model = _NoArgs

    async def execute(self, args: _NoArgs, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="controlled")


class _ExternalReadTool(BaseTool[_NoArgs]):
    execution_domain = ExecutionDomain.EXTERNAL_EFFECT
    external_effect_surface = ExternalEffectSurface.WEB
    external_effect_kind = ExternalEffectKind.READ_ONLY
    external_effect_trusted = True
    name = "ExternalRead"
    description = "test external read"
    input_model = _NoArgs

    async def execute(self, args: _NoArgs, context: ToolExecutionContext) -> ToolResult:
        del args, context
        return ToolResult(output="external")


class _Session:
    def __init__(self, covered_effects: tuple[ExecutionEffect, ...]) -> None:
        profile = workspace_runtime_profile()
        self._boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="verified-test",
            backend_version="1",
            covered_effects=covered_effects,
            verification=BoundaryVerification.VERIFIED,
        )
        self.operations: list[DataPlaneOperation] = []

    @property
    def boundary(self) -> EnforcedBoundary:
        return self._boundary

    async def execute(self, operation: DataPlaneOperation) -> ExecutionResult:
        self.operations.append(operation)
        if operation.required_effect is ExecutionEffect.COMMAND:
            return ProcessCompleted(output="sandboxed\n", exit_code=0)
        return OperationCompleted(output="sandboxed", metadata={})

    async def close(self) -> None:
        return None


def _context(
    tmp_path: Path,
    registry: ToolRegistry,
    session: _Session,
    *,
    client: _StubApiClient | None = None,
) -> QueryContext:
    return QueryContext(
        api_client=cast(
            "OpenAICompatibleApiClient",
            client if client is not None else _StubApiClient(events_per_turn=[]),
        ),
        tool_registry=registry,
        system_prompt="test",
        cwd=tmp_path,
        model="test",
        sandbox_session=session,
        runtime_permission_profile=workspace_runtime_profile(),
        enforced_boundary=session.boundary,
    )


@pytest.mark.parametrize(
    ("tool", "tool_input", "effect"),
    [
        (Read(), {"path": "README.md"}, ExecutionEffect.FILE_READ),
        (Bash(), {"command": "printf ok"}, ExecutionEffect.COMMAND),
    ],
)
async def test_verified_local_dispatch_never_calls_legacy_checker(
    tmp_path: Path,
    tool: Read | Bash,
    tool_input: dict[str, str],
    effect: ExecutionEffect,
) -> None:
    registry = ToolRegistry()
    registry.register(tool)
    session = _Session((effect,))
    context = _context(tmp_path, registry, session)
    use = ToolUseBlock(id="tool-1", name=tool.name, input=tool_input)

    outcome = await _dispatch_one(
        use,
        context,
        ToolExecutionContext(
            cwd=tmp_path,
            parent_query=context,
            sandbox_session=session,
        ),
    )

    assert outcome.is_error is False
    assert len(session.operations) == 1
    assert session.operations[0].required_effect is effect


async def test_verified_dispatch_denies_missing_effect_without_checker_or_execution(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(Read())
    session = _Session((ExecutionEffect.COMMAND,))
    context = _context(tmp_path, registry, session)

    outcome = await _dispatch_one(
        ToolUseBlock(id="tool-1", name="Read", input={"path": "README.md"}),
        context,
        ToolExecutionContext(
            cwd=tmp_path,
            parent_query=context,
            sandbox_session=session,
        ),
    )

    assert outcome.is_error is True
    assert outcome.output == "verified dispatch denied: boundary does not cover file_read"
    assert session.operations == []


def _child_read_turn() -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "child-read",
                        "name": "Read",
                        "input": {"path": "README.md"},
                    }
                ],
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "tool_use",
        }
    )


def _child_end_turn() -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent(
        message=ConversationMessage(
            role="assistant",
            content=[TextBlock(text="inspected")],
        ),
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )


async def test_delegated_dispatch_inherits_verified_runtime_and_bypasses_checker(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(Read())
    registry.register(SpawnAgent())
    session = _Session((ExecutionEffect.FILE_READ,))
    client = _StubApiClient(events_per_turn=[[_child_read_turn()], [_child_end_turn()]])
    context = _context(tmp_path, registry, session, client=client)

    outcome = await _dispatch_one(
        ToolUseBlock(
            id="parent-agent",
            name="Agent",
            input={"description": "inspect", "prompt": "read the project README"},
        ),
        context,
        ToolExecutionContext(
            cwd=tmp_path,
            parent_query=context,
            sandbox_session=session,
        ),
    )

    assert outcome.is_error is False
    assert outcome.output == "inspected"
    assert len(session.operations) == 1
    assert session.operations[0].required_effect is ExecutionEffect.FILE_READ


async def test_delegated_dispatch_denies_incomplete_child_coverage_before_model_call(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(Read())
    registry.register(SpawnAgent())
    session = _Session((ExecutionEffect.COMMAND,))
    client = _StubApiClient(events_per_turn=[])
    context = _context(tmp_path, registry, session, client=client)

    outcome = await _dispatch_one(
        ToolUseBlock(
            id="parent-agent",
            name="Agent",
            input={"description": "inspect", "prompt": "read the project README"},
        ),
        context,
        ToolExecutionContext(
            cwd=tmp_path,
            parent_query=context,
            sandbox_session=session,
        ),
    )

    assert outcome.is_error is True
    assert outcome.output == (
        "verified dispatch denied: delegated tool Read lacks file_read coverage"
    )
    assert client.captured_requests == []


async def test_autonomous_local_catalog_requires_verified_boundary_before_model_call(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(Read())
    client = _StubApiClient(events_per_turn=[])
    context = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", client),
        tool_registry=registry,
        system_prompt="test",
        cwd=tmp_path,
        model="test",
        autonomous=True,
    )

    with pytest.raises(AutonomousBoundaryError, match="Read"):
        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="inspect")])],
            context,
        ):
            pass

    assert client.captured_requests == []


def test_local_dispatch_without_verified_boundary_fails_closed(tmp_path: Path) -> None:
    registry = ToolRegistry()
    tool = Read()
    registry.register(tool)
    context = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", _StubApiClient(events_per_turn=[])),
        tool_registry=registry,
        system_prompt="test",
        cwd=tmp_path,
        model="test",
    )

    handled, failure = _verified_dispatch_authorization(tool, context)

    assert handled is True
    assert failure == (
        "verified dispatch denied: local execution requires a verified sandbox boundary",
        True,
    )


async def test_autonomous_verified_catalog_passes_gate(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(Read())
    session = _Session((ExecutionEffect.FILE_READ,))
    client = _StubApiClient(events_per_turn=[[_child_end_turn()]])
    context = _context(tmp_path, registry, session, client=client)
    context = dataclasses.replace(context, autonomous=True)

    events = [
        event
        async for event in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="inspect")])],
            context,
        )
    ]

    assert events
    assert len(client.captured_requests) == 1


@pytest.mark.parametrize(
    ("tool", "expected"),
    [(_TrustedControlTool(), "controlled"), (_ExternalReadTool(), "external")],
)
async def test_external_and_trusted_control_domains_never_call_legacy_checker(
    tmp_path: Path,
    tool: _TrustedControlTool | _ExternalReadTool,
    expected: str,
) -> None:
    registry = ToolRegistry()
    registry.register(tool)
    client = _StubApiClient(events_per_turn=[])
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy(web="allow")}
    )
    context = QueryContext(
        api_client=cast("OpenAICompatibleApiClient", client),
        tool_registry=registry,
        system_prompt="test",
        cwd=tmp_path,
        model="test",
        runtime_permission_profile=profile,
    )

    outcome = await _dispatch_one(
        ToolUseBlock(id="tool-1", name=tool.name, input={}),
        context,
        ToolExecutionContext(cwd=tmp_path, parent_query=context),
    )

    assert outcome.is_error is False
    assert outcome.output == expected
