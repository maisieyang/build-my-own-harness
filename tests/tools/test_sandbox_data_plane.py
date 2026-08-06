"""S4: core local tools route through one active sandbox session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openharness.execution import (
    BoundaryViolation,
    CommandOperation,
    ExecutionFailed,
    FileEditOperation,
    FileReadOperation,
    FileSearchOperation,
    FileWriteOperation,
    OperationCompleted,
    ProcessCompleted,
    TimedOut,
)
from openharness.tools import Bash, Edit, Grep, Read, Write
from openharness.tools.base import ToolExecutionContext, tool_result_from_operation
from openharness.tools.bash import BashInput
from openharness.tools.edit import EditInput
from openharness.tools.grep import GrepInput
from openharness.tools.read import ReadInput
from openharness.tools.write import WriteInput

if TYPE_CHECKING:
    from pathlib import Path


class _RecordingSession:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.operations: list[Any] = []

    async def execute(self, operation: Any) -> Any:
        self.operations.append(operation)
        return self.result


async def test_read_uses_session_even_when_host_path_does_not_exist(tmp_path: Path) -> None:
    session = _RecordingSession(
        OperationCompleted(output="sandbox contents", metadata={"size_bytes": 16})
    )
    context = ToolExecutionContext(cwd=tmp_path, sandbox_session=session)  # type: ignore[arg-type]

    result = await Read().execute(ReadInput(path="missing-on-host.txt"), context)

    assert result.output == "sandbox contents"
    assert isinstance(session.operations[0], FileReadOperation)


async def test_write_and_edit_use_session_without_touching_host(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    session = _RecordingSession(OperationCompleted(output="ok", metadata={}))
    context = ToolExecutionContext(cwd=tmp_path, sandbox_session=session)  # type: ignore[arg-type]

    await Write().execute(WriteInput(path="file.txt", content="new"), context)
    await Edit().execute(EditInput(path="file.txt", old_str="a", new_str="b"), context)

    assert not target.exists()
    assert isinstance(session.operations[0], FileWriteOperation)
    assert isinstance(session.operations[1], FileEditOperation)


async def test_grep_uses_same_session_instead_of_host_rg(tmp_path: Path) -> None:
    session = _RecordingSession(
        OperationCompleted(output="a.py:1:hit", metadata={"match_count": 1})
    )
    context = ToolExecutionContext(cwd=tmp_path, sandbox_session=session)  # type: ignore[arg-type]

    result = await Grep().execute(GrepInput(pattern="hit"), context)

    assert result.output == "a.py:1:hit"
    assert isinstance(session.operations[0], FileSearchOperation)


async def test_bash_uses_session_instead_of_legacy_execution_environment(
    tmp_path: Path,
) -> None:
    session = _RecordingSession(ProcessCompleted(output="sandbox\n", exit_code=0))
    context = ToolExecutionContext(cwd=tmp_path, sandbox_session=session)  # type: ignore[arg-type]

    result = await Bash().execute(BashInput(command="echo host-must-not-run"), context)

    assert result.output == "sandbox\n"
    assert isinstance(session.operations[0], CommandOperation)


def test_file_tool_result_translation_covers_structured_failures() -> None:
    violation = tool_result_from_operation(
        BoundaryViolation(dimension="filesystem", requested="/outside", evidence="denied")
    )
    timed_out = tool_result_from_operation(TimedOut())
    failed = tool_result_from_operation(ExecutionFailed(reason="worker died"))

    assert violation.is_error and "boundary violation" in violation.output
    assert timed_out.is_error and "timed out" in timed_out.output
    assert failed.is_error and "worker died" in failed.output


def test_file_tool_rejects_a_process_result_without_crashing() -> None:
    result = tool_result_from_operation(ProcessCompleted(output="wrong domain", exit_code=0))

    assert result.is_error is True
    assert "invalid result" in result.output
