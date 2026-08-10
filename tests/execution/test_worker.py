"""In-process coverage for the sandbox filesystem worker protocol."""

from __future__ import annotations

import subprocess
from io import BytesIO
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

    output, truncated = worker._collect_bounded_output(process, max_bytes=4)

    assert output == b"0123"
    assert truncated is True
    assert process.stdout.read() == b""


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
