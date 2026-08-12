"""Deterministic tests for the manually triggered live dogfood runner."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from dogfood.repl_runner import (
    DogfoodFailure,
    InteractiveProcess,
    assert_expected_baseline,
    hash_fixture,
    reset_fixture,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_reset_fixture_replaces_mutated_worktree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pricing.py").write_text("baseline\n", encoding="utf-8")
    (source / "test_pricing.py").write_text("tests\n", encoding="utf-8")

    runtime_root = tmp_path / "runtime"
    target = runtime_root / "case"
    target.mkdir(parents=True)
    (target / "pricing.py").write_text("mutated\n", encoding="utf-8")
    (target / "extra.py").write_text("unexpected\n", encoding="utf-8")

    reset_fixture(source=source, target=target, runtime_root=runtime_root)

    assert (target / "pricing.py").read_text(encoding="utf-8") == "baseline\n"
    assert (target / "test_pricing.py").read_text(encoding="utf-8") == "tests\n"
    assert not (target / "extra.py").exists()
    assert set(hash_fixture(target)) == {"pricing.py", "test_pricing.py"}


def test_reset_fixture_refuses_target_outside_runtime_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pricing.py").write_text("baseline\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside dogfood runtime root"):
        reset_fixture(
            source=source,
            target=tmp_path / "unrelated",
            runtime_root=tmp_path / "runtime",
        )


def test_expected_baseline_requires_the_planted_failure_summary() -> None:
    assert_expected_baseline(
        returncode=1,
        output="FAILED test_pricing.py::test_applies_percentage_discount\n1 failed, 1 passed",
    )

    with pytest.raises(DogfoodFailure, match="expected planted baseline"):
        assert_expected_baseline(returncode=0, output="2 passed")


def test_interactive_process_waits_and_sends_lines(tmp_path: Path) -> None:
    program = (
        "import os\n"
        "print(f'tty:{os.isatty(0)}/{os.isatty(1)}', flush=True)\n"
        "first = input('>>> ')\n"
        "print('reply:' + first, flush=True)\n"
        "second = input('plan> ')\n"
        "print('choice:' + second, flush=True)\n"
    )

    with InteractiveProcess(
        [sys.executable, "-u", "-c", program],
        cwd=tmp_path,
        default_timeout=2.0,
    ) as process:
        process.wait_for("tty:True/True")
        process.wait_for(">>> ")
        marker = process.mark()
        process.send_line("hello")
        process.wait_for("reply:hello", since=marker)
        process.wait_for("plan> ", since=marker)
        marker = process.mark()
        process.send_line("2")
        process.wait_for("choice:2", since=marker)

    assert process.returncode == 0
    assert "reply:hello" in process.transcript
    assert "choice:2" in process.transcript
