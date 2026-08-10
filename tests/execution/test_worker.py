"""In-process coverage for the sandbox filesystem worker protocol."""

from __future__ import annotations

import subprocess
from io import BytesIO, StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from openharness.execution import worker

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def test_read_missing_directory_empty_slice_and_too_large(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    missing = worker.run({"kind": "read", "path": str(tmp_path / "missing")})
    directory = worker.run({"kind": "read", "path": str(tmp_path)})
    empty_path = tmp_path / "empty.txt"
    empty_path.write_text("")
    empty = worker.run({"kind": "read", "path": str(empty_path)})
    text_path = tmp_path / "text.txt"
    text_path.write_text("one\ntwo\nthree\n")
    sliced = worker.run({"kind": "read", "path": str(text_path), "offset": 2, "limit": 1})
    monkeypatch.setattr(worker, "_MAX_READ_BYTES", 1)
    too_large = worker.run({"kind": "read", "path": str(text_path)})

    assert missing["is_error"] is True
    assert directory["is_error"] is True
    assert empty["output"] == "(empty)"
    assert sliced["output"] == "two\n"
    assert too_large["is_error"] is True


def test_write_success_and_validation_errors(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    success = worker.run({"kind": "write", "path": str(target), "content": "é"})
    directory = worker.run({"kind": "write", "path": str(tmp_path), "content": "x"})
    missing_parent = worker.run(
        {"kind": "write", "path": str(tmp_path / "missing" / "x"), "content": "x"}
    )

    assert target.read_text() == "é"
    assert success["metadata"] == {"bytes_written": 2, "path": str(target)}
    assert directory["is_error"] is True
    assert missing_parent["is_error"] is True


def test_edit_first_all_and_error_paths(tmp_path: Path) -> None:
    missing = worker.run(
        {"kind": "edit", "path": str(tmp_path / "missing"), "old_str": "a", "new_str": "b"}
    )
    directory = worker.run({"kind": "edit", "path": str(tmp_path), "old_str": "a", "new_str": "b"})
    invalid = tmp_path / "invalid.bin"
    invalid.write_bytes(b"\xff")
    invalid_result = worker.run(
        {"kind": "edit", "path": str(invalid), "old_str": "a", "new_str": "b"}
    )
    target = tmp_path / "edit.txt"
    target.write_text("a a")
    not_found = worker.run({"kind": "edit", "path": str(target), "old_str": "z", "new_str": "b"})
    first = worker.run({"kind": "edit", "path": str(target), "old_str": "a", "new_str": "b"})
    all_result = worker.run(
        {
            "kind": "edit",
            "path": str(target),
            "old_str": "a",
            "new_str": "c",
            "replace_all": True,
        }
    )

    assert all(result["is_error"] is True for result in (missing, directory, invalid_result))
    assert not_found["is_error"] is True
    assert first["metadata"] == {"replacements": 1, "path": str(target)}
    assert all_result["metadata"] == {"replacements": 1, "path": str(target)}
    assert target.read_text() == "b c"


def test_edit_uses_request_bound_atomic_temp_path(tmp_path: Path) -> None:
    target = tmp_path / "edit.txt"
    target.write_text("old value")
    temp = tmp_path / ".edit.txt.openharness-request.tmp"

    result = worker.run(
        {
            "kind": "edit",
            "path": str(target),
            "old_str": "old",
            "new_str": "new",
            "temp_path": str(temp),
        }
    )

    assert result["is_error"] is False
    assert target.read_text() == "new value"
    assert not temp.exists()


def test_atomic_write_removes_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    target = tmp_path / "edit.txt"
    target.write_text("old")
    temp = tmp_path / ".edit.txt.openharness-request.tmp"

    def fail_rename(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("replace failed")

    monkeypatch.setattr(worker.os, "rename", fail_rename)

    with pytest.raises(OSError, match="replace failed"):
        worker._atomic_write(target, "new", temp_path=temp)

    assert target.read_text() == "old"
    assert not temp.exists()


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    [
        (1, b"", b"", "(no matches)"),
        (2, b"", b"bad pattern", "rg failed: bad pattern"),
        (0, b"a:1:x\na:2:x\n", b"", "truncated to 1 lines"),
    ],
)
def test_search_exit_conventions_and_truncation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    expected: str,
) -> None:
    def fake_popen(command: list[str], **kwargs: object) -> MagicMock:
        assert command[0] == "rg"
        assert kwargs == {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
        process = MagicMock()
        process.stdout = BytesIO(stdout if returncode in (0, 1) else stderr)
        process.wait.return_value = returncode
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = worker.run(
        {
            "kind": "search",
            "path": str(tmp_path),
            "pattern": "x",
            "glob": "*.py",
            "ignore_case": True,
            "hidden": True,
            "line_cap": 1,
        }
    )

    assert expected in str(result["output"])


def test_process_output_is_drained_but_memory_is_bounded() -> None:
    process = MagicMock()
    process.stdout = BytesIO(b"0123456789")
    process.wait.return_value = 0

    output, truncated, returncode = worker._collect_bounded_output(process, max_bytes=4)

    assert output == b"0123"
    assert truncated is True
    assert returncode == 0
    assert process.stdout.read() == b""


def test_process_without_stdout_is_an_explicit_failure() -> None:
    process = MagicMock()
    process.stdout = None

    with pytest.raises(subprocess.SubprocessError, match="no output pipe"):
        worker._collect_bounded_output(process, max_bytes=4)


def test_search_reports_byte_truncation(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    process = MagicMock()
    process.stdout = BytesIO(b"a:1:needle\n")
    process.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(worker, "_MAX_SEARCH_BYTES", 4)

    result = worker.run(
        {
            "kind": "search",
            "path": str(tmp_path),
            "pattern": "needle",
            "line_cap": 10,
        }
    )

    assert result["is_error"] is False
    assert "output truncated at 4 bytes" in str(result["output"])
    assert result["metadata"]["byte_truncated"] is True


def test_unknown_operation_and_os_error_are_payload_errors(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    unknown = worker.run({"kind": "magic"})

    def fail_write(request: dict[str, object]) -> dict[str, object]:
        del request
        raise OSError("denied")

    monkeypatch.setattr(worker, "_write", fail_write)
    # ``run`` creates its dispatch map at call time, so the patched handler is used.
    failed = worker.run({"kind": "write", "path": str(tmp_path / "x"), "content": "x"})

    assert unknown["is_error"] is True
    assert "denied" in str(failed["output"])


@pytest.mark.parametrize(
    ("kind", "dimension"),
    [
        ("read", "filesystem.read"),
        ("write", "filesystem.write"),
        ("edit", "filesystem.write"),
    ],
)
def test_permission_error_is_a_typed_boundary_violation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    kind: str,
    dimension: str,
) -> None:
    def fail(request: dict[str, object]) -> dict[str, object]:
        del request
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(worker, f"_{kind}", fail)
    target = tmp_path / "outside.txt"
    result = worker.run({"kind": kind, "path": str(target), "line_cap": 1})

    assert result["metadata"] == {
        "boundary_violation": {
            "dimension": dimension,
            "requested": str(target),
            "evidence": "OS sandbox denied the filesystem operation",
            "hard_deny": False,
        }
    }


def test_search_launcher_permission_error_falls_back_to_in_process_search(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    target = tmp_path / "tests" / "test_water.py"
    target.parent.mkdir()
    target.write_text("def test_trap():\n    pass\n", encoding="utf-8")

    def fail_launch(command: list[str], **kwargs: object) -> MagicMock:
        del command, kwargs
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(subprocess, "Popen", fail_launch)
    result = worker.run(
        {
            "kind": "search",
            "path": str(tmp_path),
            "pattern": "trap",
            "glob": "**/test*.py",
            "line_cap": 10,
        }
    )

    assert result["is_error"] is False
    assert "test_water.py:1:def test_trap():" in str(result["output"])
    assert result["metadata"] == {
        "match_count": 1,
        "byte_truncated": False,
        "search_engine": "python-fallback",
    }


def test_search_rg_timeout_kills_process_and_falls_back(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    target = tmp_path / "trapping_rain_water.py"
    target.write_text("def trap(height):\n    return 0\n", encoding="utf-8")
    process = MagicMock()
    process.stdout = BytesIO()
    process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="rg", timeout=0.01),
        0,
    ]

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(worker, "_RG_SEARCH_TIMEOUT_SECONDS", 0.01, raising=False)

    result = worker.run(
        {
            "kind": "search",
            "path": str(tmp_path),
            "pattern": "trap",
            "line_cap": 10,
        }
    )

    assert result["is_error"] is False
    assert "trapping_rain_water.py:1:def trap(height):" in str(result["output"])
    assert result["metadata"]["search_engine"] == "python-fallback"
    process.kill.assert_called_once_with()


def test_search_fallback_preserves_hidden_case_and_line_cap(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    visible = tmp_path / "visible.py"
    hidden = tmp_path / ".hidden.py"
    visible.write_text("Needle\nneedle\n", encoding="utf-8")
    hidden.write_text("needle\n", encoding="utf-8")

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    result = worker.run(
        {
            "kind": "search",
            "path": str(tmp_path),
            "pattern": "needle",
            "ignore_case": True,
            "hidden": False,
            "line_cap": 1,
        }
    )

    assert result["is_error"] is False
    assert "visible.py:1:Needle" in str(result["output"])
    assert ".hidden.py" not in str(result["output"])
    assert "truncated to 1 lines; total matches 2" in str(result["output"])


def test_search_fallback_reports_invalid_regex(tmp_path: Path) -> None:
    result = worker._search_with_python({"path": str(tmp_path), "pattern": "[", "line_cap": 10})

    assert result["is_error"] is True
    assert "not a valid regular expression" in str(result["output"])


def test_search_fallback_skips_binary_and_reports_no_matches(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"needle\0binary")
    (tmp_path / "plain.txt").write_text("haystack\n", encoding="utf-8")

    result = worker._search_with_python(
        {"path": str(tmp_path), "pattern": "needle", "line_cap": 10}
    )

    assert result["output"] == "(no matches)"
    assert result["metadata"] == {
        "match_count": 0,
        "byte_truncated": False,
        "search_engine": "python-fallback",
    }


def test_search_fallback_applies_nonmatching_glob(tmp_path: Path) -> None:
    (tmp_path / "plain.txt").write_text("needle\n", encoding="utf-8")

    result = worker._search_with_python(
        {
            "path": str(tmp_path),
            "pattern": "needle",
            "glob": "*.py",
            "line_cap": 10,
        }
    )

    assert result["output"] == "(no matches)"


def test_search_fallback_reports_byte_truncation(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    target = tmp_path / "plain.txt"
    target.write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(worker, "_MAX_SEARCH_BYTES", 1)

    result = worker._search_with_python({"path": str(target), "pattern": "needle", "line_cap": 10})

    assert result["metadata"] == {
        "match_count": 1,
        "byte_truncated": True,
        "search_engine": "python-fallback",
    }
    assert "output truncated at 1 bytes" in str(result["output"])


def test_search_file_iterator_respects_hidden_single_file(tmp_path: Path) -> None:
    hidden = tmp_path / ".hidden.py"
    hidden.write_text("needle\n", encoding="utf-8")

    def files(*, include_hidden: bool) -> list[Path]:
        return list(
            worker._iter_search_files(
                hidden,
                include_hidden=include_hidden,
                ignored_directories=(),
                deadline=float("inf"),
            )
        )

    assert files(include_hidden=False) == []
    assert files(include_hidden=True) == [hidden]


def test_glob_matching_supports_direct_recursive_and_miss() -> None:
    assert worker._glob_matches("test_water.py", "test*.py") is True
    assert worker._glob_matches("tests/test_water.py", "**/test*.py") is True
    assert worker._glob_matches("water.txt", "**/test*.py") is False


def test_bounded_output_handles_chunks_after_capacity(
    monkeypatch: MonkeyPatch,
) -> None:
    process = MagicMock()
    process.stdout = BytesIO(b"abcd")
    process.wait.return_value = 0
    monkeypatch.setattr(worker, "_STREAM_CHUNK_BYTES", 2)

    output, truncated, returncode = worker._collect_bounded_output(process, max_bytes=1)

    assert output == b"a"
    assert truncated is True
    assert returncode == 0


def test_main_translates_json_between_standard_streams(monkeypatch: MonkeyPatch) -> None:
    stdin = SimpleNamespace(buffer=BytesIO(b'{"kind":"magic"}'))
    stdout = StringIO()
    monkeypatch.setattr(worker.sys, "stdin", stdin)
    monkeypatch.setattr(worker.sys, "stdout", stdout)

    worker.main()

    assert stdout.getvalue() == (
        '{"output":"unknown worker operation: magic","is_error":true,"metadata":{}}'
    )


def test_search_fallback_prunes_gitignored_directories(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    visible = tmp_path / "trapping_rain_water.py"
    visible.write_text("def trap(height):\n    return 0\n", encoding="utf-8")
    ignored = tmp_path / "benchmarks" / "workspaces" / "copy.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("def trap(height):\n    return 99\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("benchmarks/workspaces/\n", encoding="utf-8")

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    result = worker.run(
        {
            "kind": "search",
            "path": str(tmp_path),
            "pattern": "trap",
            "line_cap": 10,
        }
    )

    assert result["is_error"] is False
    assert "trapping_rain_water.py" in str(result["output"])
    assert "benchmarks/workspaces" not in str(result["output"])
    assert result["metadata"]["match_count"] == 1
