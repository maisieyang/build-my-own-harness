"""Tests for ``services/run_session.py`` -- loop-runtime Track B T4.

The single new orchestration layer that owns the worktree + sandbox
container ``AsyncExitStack`` lifecycle for an entire repair-loop RUN
(potentially many ``_run_ask`` attempts), instead of each attempt building
and tearing down its own sandbox container. Uses REAL git worktrees
(mirrors ``test_worktree.py``'s style) but a MOCKED sandbox (no Docker
needed for this module's own contract).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, ClassVar

import pytest

import openharness.services.run_session as run_session_module
from openharness.services.run_journal import load_journal
from openharness.services.run_session import RunSession, open_run_session
from openharness.services.worktree import WorktreeHandle

if TYPE_CHECKING:
    from pathlib import Path


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


class _FakeSandbox:
    """Records construction/enter/exit calls; never touches real Docker."""

    instances: ClassVar[list[_FakeSandbox]] = []
    raise_on_aenter: ClassVar[bool] = False
    raise_on_aexit: ClassVar[bool] = False

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.entered = False
        self.exited = False
        _FakeSandbox.instances.append(self)

    async def __aenter__(self) -> _FakeSandbox:
        if _FakeSandbox.raise_on_aenter:
            raise RuntimeError("sandbox construction failed (e.g. Docker down)")
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True
        if _FakeSandbox.raise_on_aexit:
            raise RuntimeError("sandbox teardown failed")


@pytest.fixture(autouse=True)
def _reset_fake_sandbox_instances() -> None:
    _FakeSandbox.instances = []
    _FakeSandbox.raise_on_aenter = False
    _FakeSandbox.raise_on_aexit = False


def _sandbox_config(*, enabled: bool) -> run_session_module.SandboxConfig:
    from openharness.cli import SandboxConfig

    return SandboxConfig(
        enabled=enabled, image="x", network="none", memory="1g", cpus=1.0, runtime="runc"
    )


class TestOpenRunSessionNoOp:
    async def test_no_isolate_no_journal_yields_none(self, tmp_path: Path) -> None:
        async with open_run_session(
            cwd=tmp_path,
            isolate=False,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is None


class TestOpenRunSessionWorktree:
    async def test_isolate_creates_worktree_once(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        async with open_run_session(
            cwd=repo,
            isolate=True,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            assert isinstance(session, RunSession)
            assert isinstance(session.worktree, WorktreeHandle)
            assert session.cwd_override == session.worktree.path
            assert session.worktree.path.exists()
            session.status = "completed"

    async def test_empty_diff_worktree_auto_removed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        worktree_path: Path | None = None
        async with open_run_session(
            cwd=repo,
            isolate=True,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            worktree_path = session.worktree.path  # type: ignore[union-attr]
            session.status = "completed"

        assert worktree_path is not None
        assert not worktree_path.exists()

    async def test_nonempty_diff_worktree_is_kept(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        worktree_path: Path | None = None
        async with open_run_session(
            cwd=repo,
            isolate=True,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            worktree_path = session.worktree.path  # type: ignore[union-attr]
            (worktree_path / "new_file.txt").write_text("uncommitted change\n")
            session.status = "completed"

        assert worktree_path is not None
        assert worktree_path.exists()

    async def test_worktree_with_changes_kept_when_body_raises(self, tmp_path: Path) -> None:
        """Diff-based cleanup applies regardless of success/failure/crash
        (approved design §3.4.4): a crash with uncommitted changes still
        keeps the worktree -- there's something worth inspecting."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        worktree_path: Path | None = None
        with pytest.raises(RuntimeError):
            async with open_run_session(
                cwd=repo,
                isolate=True,
                journal_enabled=False,
                sandbox_config=_sandbox_config(enabled=False),
            ) as session:
                assert session is not None
                worktree_path = session.worktree.path  # type: ignore[union-attr]
                (worktree_path / "new_file.txt").write_text("uncommitted change\n")
                raise RuntimeError("attempt crashed")

        assert worktree_path is not None
        assert worktree_path.exists()

    async def test_worktree_cleaned_up_when_sandbox_setup_fails_after_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review fix: setup steps now run INSIDE the try/finally, so a
        worktree created successfully before a later setup step (sandbox
        __aenter__) fails no longer leaks -- it's still diff-checked and
        cleaned up in finally.

        The generator raises during setup, BEFORE reaching `yield` -- the
        `async with` body never executes at all, so the worktree path is
        captured via a spy on `create_worktree` instead of from inside
        the (unreachable) body."""
        from openharness.services import run_session as rs_module
        from openharness.services import worktree as worktree_module

        monkeypatch.setattr(run_session_module, "SandboxExecution", _FakeSandbox)
        _FakeSandbox.raise_on_aenter = True

        captured_handles: list[object] = []
        real_create_worktree = worktree_module.create_worktree

        async def _spying_create_worktree(*args: object, **kwargs: object) -> object:
            handle = await real_create_worktree(*args, **kwargs)  # type: ignore[arg-type]
            captured_handles.append(handle)
            return handle

        monkeypatch.setattr(rs_module, "create_worktree", _spying_create_worktree)

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        with pytest.raises(RuntimeError, match="sandbox construction failed"):
            async with open_run_session(
                cwd=repo,
                isolate=True,
                journal_enabled=True,
                sandbox_config=_sandbox_config(enabled=True),
            ):
                pytest.fail("should never reach the body -- setup itself raised")

        assert len(captured_handles) == 1
        worktree_path = captured_handles[0].path  # type: ignore[attr-defined]
        assert not worktree_path.exists()  # no changes were ever made -> cleaned up

    async def test_git_status_nonzero_exit_prevents_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review fix: a failing `git status --porcelain` (non-zero exit)
        must NOT be treated as "confirmed clean" even if stdout happens to
        be empty -- fail closed, never force-remove on an unconfirmed
        state."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        class _FakeFailingProcess:
            returncode = 1

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b"fatal: some git error\n"

        async def _fake_create_subprocess_exec(*args: object, **kwargs: object) -> object:
            return _FakeFailingProcess()

        worktree_path: Path | None = None
        async with open_run_session(
            cwd=repo,
            isolate=True,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            worktree_path = session.worktree.path  # type: ignore[union-attr]
            session.status = "completed"
            # Patch AFTER worktree creation succeeded (which itself shells
            # out to git) so only the cleanup-time status check is faked.
            monkeypatch.setattr(
                run_session_module.asyncio,
                "create_subprocess_exec",
                _fake_create_subprocess_exec,
            )

        assert worktree_path is not None
        assert worktree_path.exists()

    async def test_empty_diff_worktree_auto_removed_even_on_crash(self, tmp_path: Path) -> None:
        """Diff-based cleanup applies regardless of success/failure/crash
        (approved design §3.4.4): a crash with NO changes still auto-removes
        the worktree -- nothing was produced, so there's nothing to keep."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        worktree_path: Path | None = None
        with pytest.raises(RuntimeError):
            async with open_run_session(
                cwd=repo,
                isolate=True,
                journal_enabled=False,
                sandbox_config=_sandbox_config(enabled=False),
            ) as session:
                assert session is not None
                worktree_path = session.worktree.path  # type: ignore[union-attr]
                raise RuntimeError("attempt crashed before any edit")

        assert worktree_path is not None
        assert not worktree_path.exists()


class TestOpenRunSessionExistingWorktreeReuse:
    """loop-runtime Track B T7: ``--resume-run`` reopens an ALREADY
    existing worktree (from a prior, crashed/exhausted run) instead of
    creating a new one via ``create_worktree``."""

    async def test_existing_worktree_is_reused_not_recreated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openharness.services import worktree as worktree_module

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        # Simulate a prior run's worktree already on disk (as if created by
        # an earlier open_run_session call that then crashed).
        prior_worktree_root = tmp_path / "prior-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "openharness/run/prior-run", str(prior_worktree_root)],
            cwd=repo,
            check=True,
        )
        existing_handle = WorktreeHandle(
            path=prior_worktree_root,
            branch="openharness/run/prior-run",
            base_ref="",
            repo_root=repo,
        )

        create_worktree_calls: list[object] = []
        real_create_worktree = worktree_module.create_worktree

        async def _spying_create_worktree(*args: object, **kwargs: object) -> object:
            create_worktree_calls.append((args, kwargs))
            return await real_create_worktree(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(run_session_module, "create_worktree", _spying_create_worktree)

        async with open_run_session(
            cwd=repo,
            isolate=True,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=False),
            existing_worktree=existing_handle,
        ) as session:
            assert session is not None
            assert session.worktree is existing_handle
            assert session.cwd_override == prior_worktree_root
            session.status = "completed"

        assert create_worktree_calls == []  # create_worktree never called


class TestOpenRunSessionSandbox:
    async def test_sandbox_enabled_constructs_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(run_session_module, "SandboxExecution", _FakeSandbox)

        async with open_run_session(
            cwd=tmp_path,
            isolate=False,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=True),
        ) as session:
            assert session is not None
            assert len(_FakeSandbox.instances) == 1
            assert _FakeSandbox.instances[0].entered
            assert session.execution_env_override is _FakeSandbox.instances[0]
            session.status = "completed"

        assert _FakeSandbox.instances[0].exited

    async def test_sandbox_disabled_no_construction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(run_session_module, "SandboxExecution", _FakeSandbox)

        async with open_run_session(
            cwd=tmp_path,
            isolate=False,
            journal_enabled=True,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            assert _FakeSandbox.instances == []
            assert session.execution_env_override is None
            session.status = "completed"

    async def test_sandbox_torn_down_even_when_body_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(run_session_module, "SandboxExecution", _FakeSandbox)

        with pytest.raises(RuntimeError):
            async with open_run_session(
                cwd=tmp_path,
                isolate=False,
                journal_enabled=False,
                sandbox_config=_sandbox_config(enabled=True),
            ):
                raise RuntimeError("attempt crashed")

        assert _FakeSandbox.instances[0].exited

    async def test_sandbox_teardown_failure_does_not_block_journal_or_worktree_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review fix: the finally block's three cleanup steps are now
        independently guarded -- a failing sandbox teardown must not
        prevent the journal's run_finished event or the worktree
        diff-check/removal from still happening."""
        monkeypatch.setattr(run_session_module, "SandboxExecution", _FakeSandbox)
        _FakeSandbox.raise_on_aexit = True

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        async with open_run_session(
            cwd=repo,
            isolate=True,
            journal_enabled=True,
            sandbox_config=_sandbox_config(enabled=True),
        ) as session:
            assert session is not None
            worktree_path = session.worktree.path  # type: ignore[union-attr]
            journal_run_dir = session.journal.run_dir  # type: ignore[union-attr]
            session.status = "completed"

        # Sandbox teardown raised internally (caught+swallowed by
        # open_run_session's own best-effort finally step) -- both later
        # cleanup steps still ran despite that.
        assert _FakeSandbox.instances[0].exited
        events = load_journal(journal_run_dir)
        assert any(e["event"] == "run_finished" for e in events)
        assert not worktree_path.exists()  # no changes -> auto-removed


class TestOpenRunSessionJournal:
    async def test_journal_enabled_writes_run_started_and_run_finished(
        self, tmp_path: Path
    ) -> None:
        async with open_run_session(
            cwd=tmp_path,
            isolate=False,
            journal_enabled=True,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            assert session.journal is not None
            session.status = "completed"

        events = load_journal(session.journal.run_dir)  # type: ignore[union-attr]
        event_names = [e["event"] for e in events]
        assert "run_started" in event_names
        assert "run_finished" in event_names

    async def test_journal_records_crashed_status_on_exception(self, tmp_path: Path) -> None:
        captured_session: RunSession | None = None
        with pytest.raises(RuntimeError):
            async with open_run_session(
                cwd=tmp_path,
                isolate=False,
                journal_enabled=True,
                sandbox_config=_sandbox_config(enabled=False),
            ) as session:
                captured_session = session
                raise RuntimeError("attempt crashed")

        assert captured_session is not None
        assert captured_session.journal is not None
        events = load_journal(captured_session.journal.run_dir)
        finished_events = [e for e in events if e["event"] == "run_finished"]
        assert len(finished_events) == 1
        assert finished_events[0]["status"] == "crashed"

    async def test_status_auto_completes_on_clean_exit_without_caller_setting_it(
        self, tmp_path: Path
    ) -> None:
        """Review fix: a normal exit auto-transitions "running" ->
        "completed" -- a caller (future T5) that forgets to set
        session.status explicitly must not leave the journal recording
        status="running" for a run that actually finished cleanly."""
        captured_session: RunSession | None = None
        async with open_run_session(
            cwd=tmp_path,
            isolate=False,
            journal_enabled=True,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            captured_session = session
            # deliberately NOT setting session.status here

        assert captured_session is not None
        assert captured_session.status == "completed"
        assert captured_session.journal is not None
        events = load_journal(captured_session.journal.run_dir)
        finished_events = [e for e in events if e["event"] == "run_finished"]
        assert len(finished_events) == 1
        assert finished_events[0]["status"] == "completed"

    async def test_no_journal_when_disabled(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        async with open_run_session(
            cwd=repo,
            isolate=True,
            journal_enabled=False,
            sandbox_config=_sandbox_config(enabled=False),
        ) as session:
            assert session is not None
            assert session.journal is None
            session.status = "completed"
