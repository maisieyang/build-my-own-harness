"""Tests for the completion-policy-neutral run isolation lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import openharness.services.run_session as run_session_module
from openharness.cli import SandboxConfig
from openharness.services.run_session import RunSession, open_run_session
from openharness.services.worktree import WorktreeHandle

if TYPE_CHECKING:
    from pathlib import Path


def _config(*, enabled: bool = False) -> SandboxConfig:
    return SandboxConfig(
        enabled=enabled,
        image="python:3.12-slim",
        network="none",
        memory="1g",
        cpus=1.0,
        runtime="runc",
    )


class _StatusProcess:
    def __init__(self, output: bytes = b"", *, returncode: int = 0) -> None:
        self.returncode = returncode
        self._output = output

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._output, b""


def _handle(tmp_path: Path) -> WorktreeHandle:
    path = tmp_path / "worktree"
    path.mkdir()
    return WorktreeHandle(path=path, branch="oh/run-1", base_ref="abc", repo_root=tmp_path)


@pytest.mark.asyncio
async def test_untracked_files_returns_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _subprocess(*args: object, **kwargs: object) -> _StatusProcess:
        return _StatusProcess(b"one.txt\n\ntwo.txt\n")

    monkeypatch.setattr(run_session_module.asyncio, "create_subprocess_exec", _subprocess)

    assert await run_session_module._untracked_files(tmp_path) == {"one.txt", "two.txt"}


@pytest.mark.asyncio
async def test_untracked_files_fails_closed_on_git_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _nonzero(*args: object, **kwargs: object) -> _StatusProcess:
        return _StatusProcess(returncode=1)

    monkeypatch.setattr(run_session_module.asyncio, "create_subprocess_exec", _nonzero)
    assert await run_session_module._untracked_files(tmp_path) is None

    async def _missing_git(*args: object, **kwargs: object) -> _StatusProcess:
        raise OSError("git missing")

    monkeypatch.setattr(run_session_module.asyncio, "create_subprocess_exec", _missing_git)
    assert await run_session_module._untracked_files(tmp_path) is None


@pytest.mark.asyncio
async def test_noop_without_isolation_or_sandbox(tmp_path: Path) -> None:
    async with open_run_session(cwd=tmp_path, isolate=False, sandbox_config=_config()) as session:
        assert session is None


@pytest.mark.asyncio
async def test_clean_isolated_run_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle = _handle(tmp_path)
    removed: list[WorktreeHandle] = []

    async def _create(cwd: Path, *, run_id: str) -> WorktreeHandle:
        return handle

    async def _remove(value: WorktreeHandle, *, force: bool = False) -> None:
        assert force is True
        removed.append(value)

    async def _subprocess(*args: object, **kwargs: object) -> _StatusProcess:
        return _StatusProcess()

    monkeypatch.setattr(run_session_module, "create_worktree", _create)
    monkeypatch.setattr(run_session_module, "remove_worktree", _remove)
    monkeypatch.setattr(run_session_module.asyncio, "create_subprocess_exec", _subprocess)

    async with open_run_session(
        cwd=tmp_path, isolate=True, sandbox_config=_config(), run_id="run-1"
    ) as session:
        assert isinstance(session, RunSession)
        assert session.cwd_override == handle.path
        assert session.status == "running"

    assert session.status == "completed"
    assert removed == [handle]


@pytest.mark.asyncio
async def test_dirty_isolated_run_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    handle = _handle(tmp_path)
    removed: list[WorktreeHandle] = []

    async def _create(cwd: Path, *, run_id: str) -> WorktreeHandle:
        return handle

    async def _remove(value: WorktreeHandle, *, force: bool = False) -> None:
        removed.append(value)

    async def _subprocess(*args: object, **kwargs: object) -> _StatusProcess:
        return _StatusProcess(b" M README.md\n")

    monkeypatch.setattr(run_session_module, "create_worktree", _create)
    monkeypatch.setattr(run_session_module, "remove_worktree", _remove)
    monkeypatch.setattr(run_session_module.asyncio, "create_subprocess_exec", _subprocess)

    async with open_run_session(cwd=tmp_path, isolate=True, sandbox_config=_config()):
        pass

    assert removed == []


@pytest.mark.asyncio
async def test_existing_worktree_is_verified_not_recreated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle = _handle(tmp_path)
    verified: list[WorktreeHandle] = []

    async def _verify(value: WorktreeHandle) -> None:
        verified.append(value)

    async def _create(cwd: Path, *, run_id: str) -> WorktreeHandle:
        raise AssertionError("existing worktree must be reused")

    async def _subprocess(*args: object, **kwargs: object) -> _StatusProcess:
        return _StatusProcess(b" M kept.txt\n")

    monkeypatch.setattr(run_session_module, "verify_existing_worktree", _verify)
    monkeypatch.setattr(run_session_module, "create_worktree", _create)
    monkeypatch.setattr(run_session_module.asyncio, "create_subprocess_exec", _subprocess)

    async with open_run_session(
        cwd=tmp_path,
        isolate=True,
        sandbox_config=_config(),
        existing_worktree=handle,
    ) as session:
        assert session is not None
        assert session.worktree is handle

    assert verified == [handle]


@pytest.mark.asyncio
async def test_exception_marks_session_crashed_and_closes_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle = _handle(tmp_path)
    closed: list[bool] = []
    captured: RunSession | None = None

    class _FakeSandbox:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeSandbox:
            return self

        async def __aexit__(self, *args: object) -> None:
            closed.append(True)

    async def _create(cwd: Path, *, run_id: str) -> WorktreeHandle:
        return handle

    async def _subprocess(*args: object, **kwargs: object) -> _StatusProcess:
        return _StatusProcess(b" M kept.txt\n")

    monkeypatch.setattr(run_session_module, "SandboxExecution", _FakeSandbox)
    monkeypatch.setattr(run_session_module, "create_worktree", _create)
    monkeypatch.setattr(run_session_module.asyncio, "create_subprocess_exec", _subprocess)

    with pytest.raises(RuntimeError, match="boom"):
        async with open_run_session(
            cwd=tmp_path, isolate=True, sandbox_config=_config(enabled=True)
        ) as session:
            assert session is not None
            captured = session
            assert session.execution_env_override is not None
            raise RuntimeError("boom")

    assert captured is not None
    assert captured.status == "crashed"
    assert closed == [True]
