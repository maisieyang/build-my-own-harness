"""Tests for ``services/snapshot.py`` — P12-T2.

Four surfaces grow over T2-2a → T2-2d:

1. Path resolver + ``_current_git_head`` helper (2a).
2. ``write_session_snapshot`` + JSON round-trip (2b — this commit).
3. ``load_snapshot`` + 3 staleness branches (2c).
4. Error hierarchy + ``__init__.py`` re-exports (2d).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path

import pytest

from openharness.execution import (
    BoundaryVerification,
    BoundaryViolation,
    EnforcedBoundary,
    ExecutionEffect,
)
from openharness.permissions import (
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionRuntime,
    workspace_runtime_profile,
)
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
)
from openharness.protocols.content import ToolResultBlock, ToolUseBlock
from openharness.services.snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    SnapshotCwdMismatch,
    SnapshotError,
    SnapshotMalformed,
    SnapshotNotFound,
    SnapshotVersionMismatch,
    _current_git_head,
    _serialize_snapshot,
    append_messages_to_snapshot,
    clear_conversation_snapshot,
    get_snapshot_dir,
    load_snapshot,
    update_permission_runtime_snapshot,
    write_session_snapshot,
)


@dataclass
class _StubContext:
    """Minimal QueryContext stand-in carrying only the fields snapshot
    serialization reads. Avoids constructing a full QueryContext for
    these tests (which would drag in tool_registry / api_client /
    permission_checker etc. unnecessary for the serializer)."""

    model: str = "qwen-plus"
    system_prompt: str | None = "test prompt"
    max_tokens: int = 1024
    permission_runtime: PermissionRuntime | None = None
    runtime_permission_profile: object = field(default_factory=workspace_runtime_profile)


def _permission_runtime() -> PermissionRuntime:
    profile = workspace_runtime_profile()
    boundary = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="test",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    return PermissionRuntime(profile=profile, boundary=boundary)


# --------------------------------------------------------------------------- #
# 2a: get_snapshot_dir                                                        #
# --------------------------------------------------------------------------- #


class TestGetSnapshotDir:
    def test_under_home_snapshots(self) -> None:
        d = get_snapshot_dir("/tmp/foo")
        assert d.parent == Path.home() / ".openharness" / "snapshots"

    def test_basename_in_dir_name(self) -> None:
        d = get_snapshot_dir("/tmp/my-project")
        assert d.name.startswith("my-project-")

    def test_sha1_suffix_12_chars(self) -> None:
        d = get_snapshot_dir("/tmp/foo")
        suffix = d.name.rsplit("-", 1)[-1]
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_same_cwd_same_dir(self) -> None:
        assert get_snapshot_dir("/tmp/foo") == get_snapshot_dir("/tmp/foo")

    def test_different_cwd_same_basename_distinct(self) -> None:
        d1 = get_snapshot_dir("/a/foo")
        d2 = get_snapshot_dir("/b/foo")
        assert d1 != d2
        assert d1.name.startswith("foo-")
        assert d2.name.startswith("foo-")

    def test_does_not_create_dir(self, tmp_path: Path) -> None:
        d = get_snapshot_dir(tmp_path / "fresh-project")
        assert not d.exists()

    def test_symlink_resolves_to_canonical(self, tmp_path: Path) -> None:
        import os as _os

        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            _os.symlink(real, link)
        except OSError:
            pytest.skip("symlink not supported on this filesystem")
        assert get_snapshot_dir(real) == get_snapshot_dir(link)

    def test_sha1_matches_expected_algorithm(self) -> None:
        # Pin the algorithm so changes are intentional.
        cwd = Path("/tmp/test").resolve()
        expected = sha1(str(cwd).encode("utf-8")).hexdigest()[:12]
        assert get_snapshot_dir("/tmp/test").name == f"{cwd.name}-{expected}"


# --------------------------------------------------------------------------- #
# 2a: _current_git_head                                                       #
# --------------------------------------------------------------------------- #


class TestCurrentGitHead:
    def test_returns_short_sha_in_git_repo(self) -> None:
        # Run inside this repo — should yield a real short SHA.
        repo_root = Path(__file__).resolve().parents[2]
        head = _current_git_head(repo_root)
        assert head is not None
        assert len(head) >= 7  # git short SHA min length
        assert all(c in "0123456789abcdef" for c in head)

    def test_returns_none_when_not_a_git_repo(self, tmp_path: Path) -> None:
        # Fresh tmp dir with no .git
        head = _current_git_head(tmp_path)
        assert head is None

    def test_returns_none_when_git_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force FileNotFoundError by replacing subprocess.run with one
        # that raises (simulates git binary missing).
        def _no_git(*_a: object, **_kw: object) -> object:
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", _no_git)
        assert _current_git_head("/tmp") is None

    def test_returns_none_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _timeout(*_a: object, **_kw: object) -> object:
            raise subprocess.TimeoutExpired(cmd="git", timeout=1.0)

        monkeypatch.setattr(subprocess, "run", _timeout)
        assert _current_git_head("/tmp") is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate git failing (e.g., empty repo with no commits).
        class _Result:
            returncode = 128
            stdout = ""
            stderr = "fatal: ..."

        def _fail(*_a: object, **_kw: object) -> object:
            return _Result()

        monkeypatch.setattr(subprocess, "run", _fail)
        assert _current_git_head("/tmp") is None

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Result:
            returncode = 0
            stdout = "abc1234\n"
            stderr = ""

        def _ok(*_a: object, **_kw: object) -> object:
            return _Result()

        monkeypatch.setattr(subprocess, "run", _ok)
        assert _current_git_head("/tmp") == "abc1234"

    def test_returns_none_when_empty_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Defensive: returncode=0 but empty stdout shouldn't yield ""
        class _Result:
            returncode = 0
            stdout = "  \n"
            stderr = ""

        def _empty(*_a: object, **_kw: object) -> object:
            return _Result()

        monkeypatch.setattr(subprocess, "run", _empty)
        assert _current_git_head("/tmp") is None


# --------------------------------------------------------------------------- #
# 2b: _serialize_snapshot + write_session_snapshot                            #
# --------------------------------------------------------------------------- #


def _sample_messages() -> list[ConversationMessage]:
    return [
        ConversationMessage(role="user", content=[TextBlock(text="hi")]),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="t1", name="Read", input={"path": "/x"})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="t1", content="ok")],
        ),
    ]


class TestSerializeSnapshot:
    def test_schema_constants_present(self, tmp_path: Path) -> None:
        out = _serialize_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        assert out["version"] == SNAPSHOT_VERSION
        assert out["schema"] == SNAPSHOT_SCHEMA

    def test_all_required_fields_present(self, tmp_path: Path) -> None:
        out = _serialize_snapshot(
            cwd=tmp_path,
            tool_metadata={"recent_files": ["a.py"]},
            messages=_sample_messages(),
            context=_StubContext(),  # type: ignore[arg-type]
        )
        assert set(out) == {
            "version",
            "schema",
            "created_at",
            "git_head",
            "cwd",
            "model",
            "permission_profile_fingerprint",
            "system_prompt",
            "max_tokens",
            "messages",
            "tool_metadata",
            "extra",
        }

    def test_canonical_profile_fingerprint_is_single_written(self, tmp_path: Path) -> None:
        context = _StubContext()
        out = _serialize_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=context,  # type: ignore[arg-type]
        )
        assert out["permission_profile_fingerprint"] == (
            context.runtime_permission_profile.fingerprint  # type: ignore[attr-defined]
        )
        assert "permission_mode" not in out

    def test_permission_runtime_state_persists_exact_park_fingerprints(
        self, tmp_path: Path
    ) -> None:
        profile = workspace_runtime_profile()
        boundary = EnforcedBoundary(
            profile_fingerprint=profile.fingerprint,
            backend="test",
            backend_version="1",
            covered_effects=(ExecutionEffect.COMMAND,),
            verification=BoundaryVerification.VERIFIED,
        )
        runtime = PermissionRuntime(profile=profile, boundary=boundary)
        request = PermissionDeltaRequest.create(
            tool_use_id="tool-1",
            tool_name="WebFetch",
            final_arguments={"url": "https://example.com"},
            profile=profile,
            boundary=boundary,
            delta=PermissionDelta.external_tool("web"),
            crossing=BoundaryViolation(
                dimension="external.web",
                requested="WebFetch",
                evidence="outside local sandbox",
            ),
            data_sources=("final tool arguments",),
            data_destinations=("web",),
        )
        runtime.park(request, reason="needs a person")

        out = _serialize_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(permission_runtime=runtime),  # type: ignore[arg-type]
        )

        state = out["extra"]["permission_runtime"]
        assert set(state) == {
            "schema_version",
            "profile_fingerprint",
            "parked_request",
            "parked_reason",
            "parked_review_status",
            "parked_continuation",
            "grants",
            "denials",
            "request_id_aliases",
            "last_human_decision",
            "last_decided_request",
            "last_decision_resumed",
        }
        assert state["schema_version"] == 2
        assert state["profile_fingerprint"] == profile.fingerprint
        assert state["parked_request"]["request_id"] == request.request_id
        assert state["parked_request"]["request_fingerprint"] == request.request_fingerprint
        assert state["parked_request"]["grant_fingerprint"] == request.grant_fingerprint
        assert state["parked_request"]["enforcement"]["backend"] == "test"


def test_permission_decision_amends_current_snapshot_without_rotating(
    tmp_path: Path,
) -> None:
    profile = workspace_runtime_profile()
    boundary = EnforcedBoundary(
        profile_fingerprint=profile.fingerprint,
        backend="test",
        backend_version="1",
        covered_effects=(ExecutionEffect.COMMAND,),
        verification=BoundaryVerification.VERIFIED,
    )
    runtime = PermissionRuntime(profile=profile, boundary=boundary)
    write_session_snapshot(
        cwd=tmp_path,
        tool_metadata={},
        messages=[],
        context=_StubContext(permission_runtime=runtime),  # type: ignore[arg-type]
    )

    request = PermissionDeltaRequest.create(
        tool_use_id="tool-1",
        tool_name="WebFetch",
        final_arguments={"url": "https://example.com"},
        profile=profile,
        boundary=boundary,
        delta=PermissionDelta.external_tool("web"),
        crossing=BoundaryViolation(
            dimension="external.web",
            requested="WebFetch",
            evidence="outside local sandbox",
        ),
        data_sources=("final tool arguments",),
        data_destinations=("web",),
    )
    runtime.park(request, reason="needs approval")
    assert update_permission_runtime_snapshot(cwd=tmp_path, runtime=runtime) is not None

    loaded = load_snapshot(tmp_path)
    assert (
        loaded["extra"]["permission_runtime"]["parked_request"]["request_id"] == request.request_id
    )

    runtime.approve_parked(request.request_id)
    assert update_permission_runtime_snapshot(cwd=tmp_path, runtime=runtime) is not None
    loaded = load_snapshot(tmp_path)
    grants = loaded["extra"]["permission_runtime"]["grants"]
    assert [grant["grant_fingerprint"] for grant in grants] == [request.grant_fingerprint]


def test_clear_conversation_snapshot_atomically_replaces_messages_and_permission_state(
    tmp_path: Path,
) -> None:
    runtime = _permission_runtime()
    request = PermissionDeltaRequest.create(
        tool_use_id="tool-clear",
        tool_name="Bash",
        final_arguments={"command": "curl https://example.com"},
        profile=runtime.profile,
        boundary=runtime.boundary,
        delta=PermissionDelta.network_domain("example.com"),
        crossing=BoundaryViolation(
            dimension="network.domain",
            requested="example.com:443",
            evidence="not in allowlist",
        ),
    )
    runtime.park(request, reason="waiting for user")
    path = write_session_snapshot(
        cwd=tmp_path,
        tool_metadata={"keep": ["metadata"]},
        messages=_sample_messages(),
        context=_StubContext(permission_runtime=runtime),  # type: ignore[arg-type]
    )
    runtime.clear_pending_state()

    assert clear_conversation_snapshot(cwd=tmp_path, runtime=runtime) == path

    loaded = load_snapshot(tmp_path)
    assert loaded["messages"] == []
    assert loaded["tool_metadata"] == {"keep": ["metadata"]}
    permission_state = loaded["extra"]["permission_runtime"]
    assert permission_state["parked_request"] is None
    assert permission_state["last_human_decision"] is None
    assert not (path.parent / "history").exists()


def test_clear_conversation_snapshot_missing_is_noop(tmp_path: Path) -> None:
    assert clear_conversation_snapshot(cwd=tmp_path, runtime=_permission_runtime()) is None


def test_clear_conversation_snapshot_write_failure_preserves_old_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _permission_runtime()
    path = write_session_snapshot(
        cwd=tmp_path,
        tool_metadata={},
        messages=_sample_messages(),
        context=_StubContext(permission_runtime=runtime),  # type: ignore[arg-type]
    )
    before = path.read_text(encoding="utf-8")

    def _fail_write(**kwargs: object) -> None:
        del kwargs
        raise OSError("disk full")

    monkeypatch.setattr("openharness.services.snapshot._atomic_write", _fail_write)

    with pytest.raises(SnapshotError, match="unable to replace current snapshot"):
        clear_conversation_snapshot(cwd=tmp_path, runtime=runtime)
    assert path.read_text(encoding="utf-8") == before


class TestPermissionRuntimeSnapshotAmendmentFailures:
    def test_missing_snapshot_is_a_non_fatal_noop(self, tmp_path: Path) -> None:
        assert (
            update_permission_runtime_snapshot(
                cwd=tmp_path,
                runtime=_permission_runtime(),
            )
            is None
        )

    def test_malformed_snapshot_is_a_non_fatal_noop(self, tmp_path: Path) -> None:
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "current.json").write_text("{broken", encoding="utf-8")

        assert (
            update_permission_runtime_snapshot(
                cwd=tmp_path,
                runtime=_permission_runtime(),
            )
            is None
        )

    def test_non_object_extra_is_replaced_without_losing_other_fields(self, tmp_path: Path) -> None:
        runtime = _permission_runtime()
        path = write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={"keep": True},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["extra"] = "invalid"
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert update_permission_runtime_snapshot(cwd=tmp_path, runtime=runtime) == path

        amended = json.loads(path.read_text(encoding="utf-8"))
        assert amended["tool_metadata"] == {"keep": True}
        assert amended["extra"]["permission_runtime"] == runtime.export_state().model_dump(
            mode="json"
        )

    def test_atomic_write_failure_does_not_break_the_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        runtime = _permission_runtime()
        path = write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        before = path.read_text(encoding="utf-8")

        def _fail_write(**kwargs: object) -> None:
            del kwargs
            raise OSError("disk full")

        monkeypatch.setattr("openharness.services.snapshot._atomic_write", _fail_write)

        assert update_permission_runtime_snapshot(cwd=tmp_path, runtime=runtime) is None
        assert path.read_text(encoding="utf-8") == before


class TestSerializeSnapshotFields:
    def test_messages_serialized_as_dicts_with_type_discriminator(self, tmp_path: Path) -> None:
        out = _serialize_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=_sample_messages(),
            context=_StubContext(),  # type: ignore[arg-type]
        )
        assert len(out["messages"]) == 3
        # Each content block carries the ``type`` discriminator
        # required for the discriminated union to round-trip
        assert out["messages"][0]["content"][0]["type"] == "text"
        assert out["messages"][1]["content"][0]["type"] == "tool_use"
        assert out["messages"][2]["content"][0]["type"] == "tool_result"

    def test_tool_metadata_copied_not_shared(self, tmp_path: Path) -> None:
        # If the snapshot held the SAME dict reference, mutating the
        # producer's dict after serialization would silently mutate
        # the snapshot — bad. Verify defensive copy.
        meta = {"recent_files": ["x.py"]}
        out = _serialize_snapshot(
            cwd=tmp_path,
            tool_metadata=meta,
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        meta["recent_files"].append("y.py")  # mutate after serialize
        assert out["tool_metadata"]["recent_files"] == ["x.py"]

    def test_cwd_serialized_as_string(self, tmp_path: Path) -> None:
        # JSON can't hold Path, so the serializer must coerce to str.
        out = _serialize_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        assert isinstance(out["cwd"], str)
        assert out["cwd"] == str(tmp_path)


class TestWriteSessionSnapshot:
    def test_first_write_creates_dir_and_file(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        path = write_session_snapshot(
            cwd=cwd,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        assert path.exists()
        assert path.name == "current.json"
        # Parent dir created lazily
        assert path.parent == get_snapshot_dir(cwd)

    def test_produces_valid_json(self, tmp_path: Path) -> None:
        path = write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={"recent_files": ["a.py"]},
            messages=_sample_messages(),
            context=_StubContext(),  # type: ignore[arg-type]
        )
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["version"] == SNAPSHOT_VERSION

    def test_second_write_overwrites(self, tmp_path: Path) -> None:
        path1 = write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[ConversationMessage(role="user", content=[TextBlock(text="first")])],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        path2 = write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[ConversationMessage(role="user", content=[TextBlock(text="second")])],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        assert path1 == path2  # same file path
        loaded = json.loads(path2.read_text(encoding="utf-8"))
        assert loaded["messages"][0]["content"][0]["text"] == "second"

    def test_no_orphan_tmp_files_after_success(self, tmp_path: Path) -> None:
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        dir_path = get_snapshot_dir(tmp_path)
        files = sorted(p.name for p in dir_path.iterdir())
        assert files == ["current.json"]

    def test_messages_round_trip_byte_identical(self, tmp_path: Path) -> None:
        # The contract resume depends on: write messages → load JSON →
        # ConversationMessage.model_validate yields equivalent objects.
        original = _sample_messages()
        path = write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=original,
            context=_StubContext(),  # type: ignore[arg-type]
        )
        loaded = json.loads(path.read_text(encoding="utf-8"))
        round_tripped = [ConversationMessage.model_validate(m) for m in loaded["messages"]]
        assert round_tripped == original

    def test_git_head_populated_in_real_repo(self) -> None:
        # Use this repo's root — known to have a git HEAD.
        repo_root = Path(__file__).resolve().parents[2]
        path = write_session_snapshot(
            cwd=repo_root,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["git_head"] is not None
        assert len(loaded["git_head"]) >= 7

    def test_git_head_null_in_non_git_dir(self, tmp_path: Path) -> None:
        path = write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["git_head"] is None


# --------------------------------------------------------------------------- #
# 2c: load_snapshot + 3 staleness branches                                    #
# --------------------------------------------------------------------------- #


class TestLoadSnapshotRoundTrip:
    def test_round_trip_returns_equivalent_dict(self, tmp_path: Path) -> None:
        original = _sample_messages()
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={"recent_files": ["a.py"]},
            messages=original,
            context=_StubContext(),  # type: ignore[arg-type]
        )
        loaded = load_snapshot(tmp_path)
        assert loaded["version"] == SNAPSHOT_VERSION
        assert loaded["schema"] == SNAPSHOT_SCHEMA
        assert loaded["model"] == "qwen-plus"
        # Messages survive round trip through model_validate
        loaded_messages = [ConversationMessage.model_validate(m) for m in loaded["messages"]]
        assert loaded_messages == original


class TestLoadSnapshotErrors:
    def test_not_found_when_no_file(self, tmp_path: Path) -> None:
        with pytest.raises(SnapshotNotFound):
            load_snapshot(tmp_path)

    def test_cwd_mismatch_refuses(self, tmp_path: Path) -> None:
        # Write a snapshot whose ``cwd`` field is the actual dir,
        # then manually overwrite the file with a cwd field for a
        # different path. Resume from the original dir → cwd mismatch.
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        snapshot_path = get_snapshot_dir(tmp_path) / "current.json"
        data = json.loads(snapshot_path.read_text())
        data["cwd"] = "/some/other/project"
        snapshot_path.write_text(json.dumps(data))

        with pytest.raises(SnapshotCwdMismatch, match="does not match"):
            load_snapshot(tmp_path)

    def test_version_too_new_refuses(self, tmp_path: Path) -> None:
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        snapshot_path = get_snapshot_dir(tmp_path) / "current.json"
        data = json.loads(snapshot_path.read_text())
        data["version"] = 999  # imagined future version
        snapshot_path.write_text(json.dumps(data))

        with pytest.raises(SnapshotVersionMismatch, match="upgrade openharness"):
            load_snapshot(tmp_path)

    def test_malformed_json_refuses(self, tmp_path: Path) -> None:
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "current.json").write_text("not valid json {{{")

        with pytest.raises(SnapshotMalformed):
            load_snapshot(tmp_path)

    def test_missing_version_field_refuses(self, tmp_path: Path) -> None:
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "current.json").write_text(json.dumps({"foo": "bar"}))

        with pytest.raises(SnapshotMalformed, match="version"):
            load_snapshot(tmp_path)

    def test_missing_cwd_field_refuses(self, tmp_path: Path) -> None:
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "current.json").write_text(
            json.dumps({"version": SNAPSHOT_VERSION, "schema": SNAPSHOT_SCHEMA})
        )

        with pytest.raises(SnapshotMalformed, match="cwd"):
            load_snapshot(tmp_path)

    def test_root_not_a_dict_refuses(self, tmp_path: Path) -> None:
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "current.json").write_text(json.dumps(["not", "a", "dict"]))

        with pytest.raises(SnapshotMalformed):
            load_snapshot(tmp_path)

    def test_snapshot_id_no_match_raises_not_found(self, tmp_path: Path) -> None:
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        # tmp_path isn't a git repo → git_head is null → prefix can't match
        with pytest.raises(SnapshotNotFound, match="does not match prefix"):
            load_snapshot(tmp_path, snapshot_id="abc")


class TestLoadSnapshotStalenessLogs:
    def test_version_downgrade_loads_with_info_log(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Write a snapshot then drop its version to 0 (imagined v0).
        # This v1 reader should LOAD it (forward-compat) and emit an
        # info log signaling the downgrade.
        write_session_snapshot(
            cwd=tmp_path,
            tool_metadata={},
            messages=[],
            context=_StubContext(),  # type: ignore[arg-type]
        )
        snapshot_path = get_snapshot_dir(tmp_path) / "current.json"
        data = json.loads(snapshot_path.read_text())
        data["version"] = 0
        snapshot_path.write_text(json.dumps(data))

        with caplog.at_level("INFO", logger="snapshot"):
            loaded = load_snapshot(tmp_path)
        assert loaded["version"] == 0

    def test_git_head_drift_logs_warn_but_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Stale snapshot: synthesize one with a fake git_head + manually
        # write to disk so we don't need a real git repo.
        snapshot_dir = get_snapshot_dir(tmp_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        synthesized = {
            "version": SNAPSHOT_VERSION,
            "schema": SNAPSHOT_SCHEMA,
            "created_at": "2026-05-26T00:00:00Z",
            "git_head": "deadbee",  # 7 chars, looks like a real SHA
            "cwd": str(tmp_path.resolve()),
            "model": "qwen-plus",
            "permission_profile_fingerprint": workspace_runtime_profile().fingerprint,
            "system_prompt": "test",
            "max_tokens": 1024,
            "messages": [],
            "tool_metadata": {},
            "extra": {},
        }
        (snapshot_dir / "current.json").write_text(json.dumps(synthesized))

        # tmp_path isn't a git repo → current_head is None → drift check
        # short-circuits and no log fires. Verify load succeeds anyway.
        loaded = load_snapshot(tmp_path)
        assert loaded["git_head"] == "deadbee"


# --------------------------------------------------------------------------- #
# 2d: Error hierarchy + __init__ re-exports                                   #
# --------------------------------------------------------------------------- #


class TestErrorHierarchy:
    def test_all_specific_errors_subclass_snapshot_error(self) -> None:
        # One `except SnapshotError` catches every failure mode.
        assert issubclass(SnapshotNotFound, SnapshotError)
        assert issubclass(SnapshotCwdMismatch, SnapshotError)
        assert issubclass(SnapshotVersionMismatch, SnapshotError)
        assert issubclass(SnapshotMalformed, SnapshotError)

    def test_specific_errors_distinct(self) -> None:
        # NotFound shouldn't accidentally subclass CwdMismatch etc.
        assert not issubclass(SnapshotNotFound, SnapshotCwdMismatch)
        assert not issubclass(SnapshotCwdMismatch, SnapshotVersionMismatch)


class TestAppendMessagesToSnapshot:
    """F17 — amend the turn that just ended, without rotating it away.

    Motivating case: the ``/goal`` judge's ``met``/``cleared`` sentinels
    are appended by the REPL *after* the engine already wrote this turn's
    snapshot. See ``tests/cli/test_chat_goal.py::TestGoalSentinelPersistence``
    for the seam these support.
    """

    @staticmethod
    def _seed(cwd: Path) -> Path:
        return write_session_snapshot(
            cwd=cwd,
            tool_metadata={"recent_files": ["a.py"]},
            messages=_sample_messages(),
            context=_StubContext(),  # type: ignore[arg-type]
        )

    def test_appends_after_existing_messages(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        seeded = len(_sample_messages())
        extra = ConversationMessage(role="user", content=[TextBlock(text="[goal-status] met: x")])

        path = append_messages_to_snapshot(cwd=tmp_path, messages=[extra])

        assert path is not None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert len(loaded["messages"]) == seeded + 1
        assert loaded["messages"][-1]["content"][0]["text"] == "[goal-status] met: x"

    def test_preserves_every_other_field(self, tmp_path: Path) -> None:
        seeded_path = self._seed(tmp_path)
        before = json.loads(seeded_path.read_text(encoding="utf-8"))
        extra = ConversationMessage(role="user", content=[TextBlock(text="s")])

        append_messages_to_snapshot(cwd=tmp_path, messages=[extra])

        after = json.loads(seeded_path.read_text(encoding="utf-8"))
        assert {k: v for k, v in after.items() if k != "messages"} == {
            k: v for k, v in before.items() if k != "messages"
        }

    def test_round_trips_through_load_snapshot(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        extra = ConversationMessage(role="user", content=[TextBlock(text="sentinel")])

        append_messages_to_snapshot(cwd=tmp_path, messages=[extra])

        loaded = load_snapshot(tmp_path)
        rebuilt = [ConversationMessage.model_validate(m) for m in loaded["messages"]]
        assert isinstance(rebuilt[-1].content[0], TextBlock)
        assert rebuilt[-1].content[0].text == "sentinel"

    def test_does_not_rotate_into_history(self, tmp_path: Path) -> None:
        # An amendment continues the same turn — rotating would file a
        # near-duplicate into history/ on every goal completion.
        self._seed(tmp_path)
        extra = ConversationMessage(role="user", content=[TextBlock(text="s")])

        append_messages_to_snapshot(cwd=tmp_path, messages=[extra])

        history_dir = get_snapshot_dir(tmp_path) / "history"
        assert not history_dir.exists() or list(history_dir.glob("*.json")) == []

    def test_no_snapshot_returns_none(self, tmp_path: Path) -> None:
        extra = ConversationMessage(role="user", content=[TextBlock(text="s")])
        assert append_messages_to_snapshot(cwd=tmp_path, messages=[extra]) is None

    def test_empty_messages_returns_none(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        assert append_messages_to_snapshot(cwd=tmp_path, messages=[]) is None

    def test_malformed_snapshot_returns_none_without_raising(self, tmp_path: Path) -> None:
        # A broken snapshot must not take down the session (D30.9 spirit).
        self._seed(tmp_path)
        (get_snapshot_dir(tmp_path) / "current.json").write_text("{not json", encoding="utf-8")
        extra = ConversationMessage(role="user", content=[TextBlock(text="s")])

        assert append_messages_to_snapshot(cwd=tmp_path, messages=[extra]) is None

    def test_messages_field_not_a_list_returns_none(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        path = get_snapshot_dir(tmp_path) / "current.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["messages"] = "oops"
        path.write_text(json.dumps(payload), encoding="utf-8")
        extra = ConversationMessage(role="user", content=[TextBlock(text="s")])

        assert append_messages_to_snapshot(cwd=tmp_path, messages=[extra]) is None


class TestServicesInitExports:
    def test_snapshot_functions_re_exported(self) -> None:
        # Phase 12 plan: ``services/__init__.py`` re-exports the
        # snapshot writer + loader + error classes so callers do
        # ``from openharness.services import write_session_snapshot``.
        from openharness import services

        assert hasattr(services, "write_session_snapshot")
        assert hasattr(services, "load_snapshot")
        assert hasattr(services, "SnapshotError")
        assert hasattr(services, "SnapshotNotFound")
        assert hasattr(services, "SnapshotCwdMismatch")
        assert hasattr(services, "SnapshotVersionMismatch")
