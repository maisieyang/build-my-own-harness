"""Manually trigger the live Default, Plan, and Goal dogfood cases.

The runner drives the real ``oh`` process and configured model. It deliberately
uses no model mocks and is never collected as an eval or CI test.
"""

# The case prompts intentionally use Chinese punctuation byte-for-byte.
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import json
import os
import pty
import selectors
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SOURCE = REPO_ROOT / "dogfood" / "fixtures" / "pricing"
RUNTIME_ROOT = REPO_ROOT / ".dogfood"
WORK_ROOT = RUNTIME_ROOT / "work"
ARTIFACT_ROOT = RUNTIME_ROOT / "artifacts"

CORE_WORK = WORK_ROOT / "repl-core-workflows"
DEPTH_WORK = WORK_ROOT / "repl-controller-depth"

CORE_TASK = (
    "检查并修复 .dogfood/work/repl-core-workflows/pricing.py 中的折扣计算。"
    "要求 discount_percent 按百分比计算，只接受 0 到 100，非法值抛出 ValueError，"
    "补充边界测试，并运行 uv run pytest "
    ".dogfood/work/repl-core-workflows/test_pricing.py -q --no-cov。"
    "只修改这个 dogfood 目录。"
)
CORE_GOAL = f"/goal {CORE_TASK}"

DEPTH_PLAN_TASK = (
    "检查 .dogfood/work/repl-controller-depth/pricing.py 中的折扣计算问题，"
    "给出修复与验证计划，只规划，不修改代码。"
)
DEPTH_PLAN_REFINEMENT = (
    "继续规划：补充输入边界、精确验证命令，以及验证失败时如何定位；仍然不要执行。"
)
DEPTH_GOAL = (
    "/goal 严格分阶段完成：第一个 assistant turn 只读取 "
    ".dogfood/work/repl-controller-depth/pricing.py 和 test_pricing.py，"
    "解释当前失败原因，不修改文件、不运行 Bash，然后结束这一轮；"
    "后续 assistant turn 才修复百分比计算，只接受 0 到 100，非法值抛出 "
    "ValueError，补充 0%、100% 和非法百分比测试，并成功运行 uv run pytest "
    ".dogfood/work/repl-controller-depth/test_pricing.py -q --no-cov。"
    "只修改这个 dogfood 目录。"
)


class DogfoodFailure(RuntimeError):
    """A case failed one of its externally observable acceptance criteria."""


class InteractiveProcess:
    """Drive a long-running process line-by-line without shell interpolation."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        default_timeout: float,
        env: dict[str, str] | None = None,
    ) -> None:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        self.default_timeout = default_timeout
        self._output = bytearray()
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        try:
            self._process = subprocess.Popen(
                command,
                cwd=cwd,
                env=process_env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
        except Exception:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._master_fd, selectors.EVENT_READ)

    def __enter__(self) -> InteractiveProcess:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    @property
    def transcript(self) -> str:
        return self._output.decode("utf-8", errors="replace")

    @property
    def returncode(self) -> int | None:
        return self._process.poll()

    def mark(self) -> int:
        """Return a byte offset for matching only future output."""
        return len(self._output)

    def transcript_since(self, marker: int) -> str:
        return self._output[marker:].decode("utf-8", errors="replace")

    def send_line(self, value: str) -> None:
        if self._process.poll() is not None:
            raise DogfoodFailure(
                f"REPL exited before input {value!r}; return code {self._process.returncode}"
            )
        os.write(self._master_fd, value.encode("utf-8") + b"\n")

    def wait_for(
        self,
        expected: str,
        *,
        since: int = 0,
        timeout: float | None = None,
    ) -> None:
        needle = expected.encode("utf-8")
        deadline = time.monotonic() + (timeout or self.default_timeout)
        while needle not in self._output[since:]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tail = self.transcript[-4000:]
                raise DogfoodFailure(
                    f"timed out waiting for {expected!r}; transcript tail:\n{tail}"
                )
            events = self._selector.select(min(remaining, 0.5))
            if events:
                chunk = self._read_chunk()
                if chunk:
                    self._output.extend(chunk)
                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
                    continue
            if self._process.poll() is not None:
                self._drain()
                if needle in self._output[since:]:
                    return
                tail = self.transcript[-4000:]
                raise DogfoodFailure(
                    f"REPL exited with {self._process.returncode} before {expected!r}; "
                    f"transcript tail:\n{tail}"
                )

    def _read_chunk(self) -> bytes:
        try:
            return os.read(self._master_fd, 65536)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return b""
            raise

    def _drain(self) -> None:
        while True:
            events = self._selector.select(0)
            if not events:
                break
            chunk = self._read_chunk()
            if not chunk:
                break
            self._output.extend(chunk)

    def close(self) -> None:
        if self._process.poll() is None:
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self._process.pid, signal.SIGTERM)
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self._process.pid, signal.SIGKILL)
                    self._process.wait(timeout=2)
        self._drain()
        self._selector.close()
        with contextlib.suppress(OSError):
            os.close(self._master_fd)


def reset_fixture(*, source: Path, target: Path, runtime_root: Path) -> None:
    """Replace one disposable runtime fixture from its canonical source."""
    root = runtime_root.resolve()
    resolved_target = target.resolve()
    if resolved_target == root or not resolved_target.is_relative_to(root):
        raise ValueError(f"refusing to reset target outside dogfood runtime root: {target}")
    if not source.is_dir():
        raise FileNotFoundError(f"dogfood fixture source is missing: {source}")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def hash_fixture(path: Path) -> dict[str, str]:
    """Hash source files while ignoring generated Python caches."""
    hashes: dict[str, str] = {}
    for candidate in sorted(path.rglob("*")):
        relative = candidate.relative_to(path)
        if not candidate.is_file() or "__pycache__" in relative.parts:
            continue
        hashes[relative.as_posix()] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return hashes


def assert_expected_baseline(*, returncode: int, output: str) -> None:
    """Require the intentional one-failure/one-pass pricing baseline."""
    if returncode == 0 or "1 failed, 1 passed" not in output:
        raise DogfoodFailure(
            "expected planted baseline '1 failed, 1 passed', "
            f"got return code {returncode}:\n{output}"
        )


@dataclass(frozen=True)
class VerifierResult:
    returncode: int
    output: str


def run_verifier(work_dir: Path) -> VerifierResult:
    relative_test = (work_dir / "test_pricing.py").relative_to(REPO_ROOT)
    process_env = os.environ.copy()
    process_env.setdefault("UV_CACHE_DIR", str(REPO_ROOT / ".cache" / "uv"))
    completed = subprocess.run(
        ["uv", "run", "pytest", str(relative_test), "-q", "--no-cov"],
        cwd=REPO_ROOT,
        env=process_env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return VerifierResult(returncode=completed.returncode, output=completed.stdout)


def git_status() -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DogfoodFailure(message)


def launch_repl(timeout: float) -> InteractiveProcess:
    return InteractiveProcess(
        ["uv", "run", "oh", "--auto", "--sandbox"],
        cwd=REPO_ROOT,
        default_timeout=timeout,
        env={
            "NO_COLOR": "1",
            "PYTHONUNBUFFERED": "1",
            "UV_CACHE_DIR": str(REPO_ROOT / ".cache" / "uv"),
        },
    )


def _send_after_prompt(process: InteractiveProcess, value: str) -> int:
    marker = process.mark()
    process.send_line(value)
    return marker


def _exit_repl(process: InteractiveProcess) -> None:
    process.send_line("/exit")
    deadline = time.monotonic() + 10
    while process.returncode is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.returncode is None:
        raise DogfoodFailure("REPL did not exit after /exit")


def _assert_no_tracked_changes(before: str) -> None:
    require(git_status() == before, "files outside the ignored dogfood runtime changed")


def _case_artifact_dir(run_id: str, case_id: str) -> Path:
    path = ARTIFACT_ROOT / run_id / case_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_artifacts(
    *,
    run_id: str,
    case_id: str,
    transcript: str,
    before_hash: dict[str, str],
    after_hash: dict[str, str],
    baseline: VerifierResult,
    verification: VerifierResult,
    status: str,
    error: str | None = None,
) -> None:
    path = _case_artifact_dir(run_id, case_id)
    (path / "transcript.txt").write_text(transcript, encoding="utf-8")
    (path / "before.sha256").write_text(
        json.dumps(before_hash, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (path / "after.sha256").write_text(
        json.dumps(after_hash, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (path / "verification.txt").write_text(
        "# baseline\n"
        f"exit={baseline.returncode}\n{baseline.output}\n"
        "# final\n"
        f"exit={verification.returncode}\n{verification.output}",
        encoding="utf-8",
    )
    result: dict[str, Any] = {
        "case_id": case_id,
        "status": status,
        "launch_command": ["uv", "run", "oh", "--auto", "--sandbox"],
        "error": error,
    }
    (path / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _prepare(work_dir: Path) -> tuple[dict[str, str], VerifierResult, str]:
    reset_fixture(source=FIXTURE_SOURCE, target=work_dir, runtime_root=RUNTIME_ROOT)
    before_hash = hash_fixture(work_dir)
    baseline = run_verifier(work_dir)
    assert_expected_baseline(returncode=baseline.returncode, output=baseline.output)
    return before_hash, baseline, git_status()


def run_dpg001(*, run_id: str, timeout: float) -> None:
    before_hash, baseline, status_before = _prepare(CORE_WORK)
    transcript = ""
    try:
        with launch_repl(timeout) as process:
            process.wait_for(">>> ")
            marker = _send_after_prompt(process, CORE_TASK)
            process.wait_for(">>> ", since=marker)
            segment = process.transcript_since(marker)
            require("plan mode — approve this plan?" not in segment, "Default showed Plan menu")
            require("goal met" not in segment and "goal not met" not in segment, "Default ran Goal")
            _exit_repl(process)
            transcript = process.transcript
        verification = run_verifier(CORE_WORK)
        require(verification.returncode == 0, "DPG-001 final verifier failed")
        _assert_no_tracked_changes(status_before)
        write_artifacts(
            run_id=run_id,
            case_id="DPG-001",
            transcript=transcript,
            before_hash=before_hash,
            after_hash=hash_fixture(CORE_WORK),
            baseline=baseline,
            verification=verification,
            status="passed",
        )
    except Exception as exc:
        verification = run_verifier(CORE_WORK)
        write_artifacts(
            run_id=run_id,
            case_id="DPG-001",
            transcript=transcript,
            before_hash=before_hash,
            after_hash=hash_fixture(CORE_WORK),
            baseline=baseline,
            verification=verification,
            status="failed",
            error=str(exc),
        )
        raise


def run_dpg002(*, run_id: str, timeout: float) -> None:
    before_hash, baseline, status_before = _prepare(CORE_WORK)
    transcript = ""
    try:
        with launch_repl(timeout) as process:
            process.wait_for(">>> ")
            marker = _send_after_prompt(process, "/plan")
            process.wait_for(">>> ", since=marker)
            marker = _send_after_prompt(process, CORE_TASK)
            process.wait_for("plan mode — approve this plan?", since=marker)
            plan_segment = process.transcript_since(marker)
            for forbidden in ("[Bash]", "[Edit]", "[Write]", "[Agent]"):
                require(forbidden not in plan_segment, f"Plan exposed forbidden action {forbidden}")
            require(hash_fixture(CORE_WORK) == before_hash, "Plan changed the fixture")
            marker = _send_after_prompt(process, "1")
            process.wait_for("(plan approved — back to default mode)", since=marker)
            process.wait_for(">>> ", since=marker)
            require(hash_fixture(CORE_WORK) == before_hash, "Plan approval executed the plan")
            _exit_repl(process)
            transcript = process.transcript
        verification = run_verifier(CORE_WORK)
        assert_expected_baseline(returncode=verification.returncode, output=verification.output)
        _assert_no_tracked_changes(status_before)
        write_artifacts(
            run_id=run_id,
            case_id="DPG-002",
            transcript=transcript,
            before_hash=before_hash,
            after_hash=hash_fixture(CORE_WORK),
            baseline=baseline,
            verification=verification,
            status="passed",
        )
    except Exception as exc:
        verification = run_verifier(CORE_WORK)
        write_artifacts(
            run_id=run_id,
            case_id="DPG-002",
            transcript=transcript,
            before_hash=before_hash,
            after_hash=hash_fixture(CORE_WORK),
            baseline=baseline,
            verification=verification,
            status="failed",
            error=str(exc),
        )
        raise


def run_dpg003(*, run_id: str, timeout: float) -> None:
    before_hash, baseline, status_before = _prepare(CORE_WORK)
    transcript = ""
    try:
        with launch_repl(timeout) as process:
            process.wait_for(">>> ")
            marker = _send_after_prompt(process, CORE_GOAL)
            process.wait_for("(goal set:", since=marker)
            process.wait_for("goal met after", since=marker)
            process.wait_for(">>> ", since=marker)
            marker = _send_after_prompt(process, "/goal")
            process.wait_for("(no goal set — /goal <condition> to set one)", since=marker)
            process.wait_for(">>> ", since=marker)
            _exit_repl(process)
            transcript = process.transcript
        verification = run_verifier(CORE_WORK)
        require(verification.returncode == 0, "DPG-003 final verifier failed")
        _assert_no_tracked_changes(status_before)
        write_artifacts(
            run_id=run_id,
            case_id="DPG-003",
            transcript=transcript,
            before_hash=before_hash,
            after_hash=hash_fixture(CORE_WORK),
            baseline=baseline,
            verification=verification,
            status="passed",
        )
    except Exception as exc:
        verification = run_verifier(CORE_WORK)
        write_artifacts(
            run_id=run_id,
            case_id="DPG-003",
            transcript=transcript,
            before_hash=before_hash,
            after_hash=hash_fixture(CORE_WORK),
            baseline=baseline,
            verification=verification,
            status="failed",
            error=str(exc),
        )
        raise


def run_depth(*, run_id: str, timeout: float) -> None:
    before_hash, baseline, status_before = _prepare(DEPTH_WORK)
    dpg004_transcript = ""
    with launch_repl(timeout) as process:
        try:
            process.wait_for(">>> ")
            marker = _send_after_prompt(process, "/plan")
            process.wait_for(">>> ", since=marker)
            dpg004_start = process.mark()
            marker = _send_after_prompt(process, DEPTH_PLAN_TASK)
            process.wait_for("plan mode — approve this plan?", since=marker)
            marker = _send_after_prompt(process, "2")
            process.wait_for(">>> ", since=marker)
            marker = _send_after_prompt(process, DEPTH_PLAN_REFINEMENT)
            process.wait_for("plan mode — approve this plan?", since=marker)
            plan_segment = process.transcript_since(dpg004_start)
            for forbidden in ("[Bash]", "[Edit]", "[Write]", "[Agent]"):
                require(forbidden not in plan_segment, f"Plan exposed forbidden action {forbidden}")
            require(hash_fixture(DEPTH_WORK) == before_hash, "iterative Plan changed fixture")
            marker = _send_after_prompt(process, "3")
            process.wait_for("(plan discarded — back to default mode)", since=marker)
            process.wait_for(">>> ", since=marker)
            require(hash_fixture(DEPTH_WORK) == before_hash, "discarded Plan changed fixture")
            dpg004_transcript = process.transcript_since(dpg004_start)
            dpg004_verification = run_verifier(DEPTH_WORK)
            assert_expected_baseline(
                returncode=dpg004_verification.returncode,
                output=dpg004_verification.output,
            )
            write_artifacts(
                run_id=run_id,
                case_id="DPG-004",
                transcript=dpg004_transcript,
                before_hash=before_hash,
                after_hash=hash_fixture(DEPTH_WORK),
                baseline=baseline,
                verification=dpg004_verification,
                status="passed",
            )
        except Exception as exc:
            verification = run_verifier(DEPTH_WORK)
            write_artifacts(
                run_id=run_id,
                case_id="DPG-004",
                transcript=process.transcript,
                before_hash=before_hash,
                after_hash=hash_fixture(DEPTH_WORK),
                baseline=baseline,
                verification=verification,
                status="failed",
                error=str(exc),
            )
            raise

        dpg005_start = process.mark()
        try:
            marker = _send_after_prompt(process, DEPTH_GOAL)
            process.wait_for("(goal set:", since=marker)
            process.wait_for("goal not met — continuing", since=marker)
            process.wait_for("goal met after", since=marker)
            process.wait_for(">>> ", since=marker)
            marker = _send_after_prompt(process, "/goal")
            process.wait_for("(no goal set — /goal <condition> to set one)", since=marker)
            process.wait_for(">>> ", since=marker)
            _exit_repl(process)
            verification = run_verifier(DEPTH_WORK)
            require(verification.returncode == 0, "DPG-005 final verifier failed")
            _assert_no_tracked_changes(status_before)
            write_artifacts(
                run_id=run_id,
                case_id="DPG-005",
                transcript=process.transcript_since(dpg005_start),
                before_hash=before_hash,
                after_hash=hash_fixture(DEPTH_WORK),
                baseline=baseline,
                verification=verification,
                status="passed",
            )
        except Exception as exc:
            verification = run_verifier(DEPTH_WORK)
            write_artifacts(
                run_id=run_id,
                case_id="DPG-005",
                transcript=process.transcript_since(dpg005_start),
                before_hash=before_hash,
                after_hash=hash_fixture(DEPTH_WORK),
                baseline=baseline,
                verification=verification,
                status="failed",
                error=str(exc),
            )
            raise


def prepare_only() -> None:
    for work_dir in (CORE_WORK, DEPTH_WORK):
        _, baseline, _ = _prepare(work_dir)
        print(f"{work_dir.relative_to(REPO_ROOT)}: {baseline.output.strip()}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("core", "depth", "all"))
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="reset both fixtures and verify their planted failures without calling a model",
    )
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds per REPL wait")
    parser.add_argument(
        "--run-id",
        default=datetime.now().strftime("%Y%m%d-%H%M%S"),
        help="artifact directory name",
    )
    args = parser.parse_args(argv)
    if not args.prepare_only and args.suite is None:
        parser.error("--suite is required unless --prepare-only is used")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.prepare_only:
        prepare_only()
        return 0
    try:
        if args.suite in {"core", "all"}:
            run_dpg001(run_id=args.run_id, timeout=args.timeout)
            run_dpg002(run_id=args.run_id, timeout=args.timeout)
            run_dpg003(run_id=args.run_id, timeout=args.timeout)
        if args.suite in {"depth", "all"}:
            run_depth(run_id=args.run_id, timeout=args.timeout)
    except (DogfoodFailure, OSError, subprocess.SubprocessError) as exc:
        print(f"dogfood failed: {exc}", file=sys.stderr)
        return 1
    print(f"dogfood passed; artifacts: {ARTIFACT_ROOT / args.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
