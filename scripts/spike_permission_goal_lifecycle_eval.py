"""Live/record/replay dogfood for the permission + goal lifecycle.

Replay (default) scores the committed live observation without API access.
Record drives the real non-TTY ``oh chat`` front door, answers dynamic parked
request ids, verifies snapshots and filesystem effects, and writes a new
observation. Run record mode from a normal macOS terminal, not from inside an
existing Seatbelt boundary.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from openharness.config.settings import Settings
from openharness.eval.permission_goal_lifecycle import (
    LifecycleObservation,
    LifecycleSample,
    load_lifecycle_dataset,
    normalize_status_line,
    run_permission_goal_lifecycle_eval,
    status_line_contains,
)
from openharness.eval.results import get_git_info
from openharness.protocols import ConversationMessage
from openharness.repl import find_active_goal
from openharness.services.snapshot import load_snapshot

_ROOT = Path(__file__).resolve().parent.parent
_EVAL_ROOT = _ROOT / "evals" / "permission_goal_lifecycle"
_DATASET = _EVAL_ROOT / "dataset.yaml"
_COMMITTED_OBSERVATION = _EVAL_ROOT / "observations" / "qwen3.7-max-live-2026-08-06.yaml"
_PARKED_RE = re.compile(r"\[permission parked ([0-9a-f]{12})\b")
_PROCESS_TIMEOUT_SECONDS = 300.0


@dataclass
class _Trace:
    checkpoints: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    pending_request_id: str | None = None
    parks_seen: int = 0
    awaiting_decision: bool = False
    judge_calls_while_parked: int = 0


async def _send(process: asyncio.subprocess.Process, command: str) -> None:
    if process.stdin is None:
        raise RuntimeError("chat subprocess has no stdin")
    process.stdin.write(f"{command}\n".encode())
    await process.stdin.drain()


def _append_once(trace: _Trace, checkpoint: str) -> None:
    if checkpoint not in trace.checkpoints:
        trace.checkpoints.append(checkpoint)


async def _drive_process(
    sample: LifecycleSample,
    trace: _Trace,
    *,
    resume: bool,
    exit_after_first_park: bool,
) -> None:
    argv = [
        sys.executable,
        "-m",
        "openharness",
        "chat",
        "--auto",
        "--sandbox",
        "--sandbox-backend",
        "seatbelt",
    ]
    if resume:
        argv.insert(4, "--resume")
    environment = dict(os.environ)
    environment["OPENHARNESS_PERMISSION_AUTO_REVIEW"] = "false"
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=_ROOT,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if process.stdout is None:
        raise RuntimeError("chat subprocess has no stdout")

    if not resume:
        await _send(process, "/permissions")
        await _send(process, f"/goal {sample.goal}")

    async def _consume() -> None:
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            trace.transcript.append(line)
            print(line, end="", flush=True)

            if status_line_contains(line, "verified boundary:") and "(verified)" in line:
                _append_once(trace, "boundary_verified")
            if resume and status_line_contains(line, "(resumed:"):
                _append_once(trace, "snapshot_permission_restored")
            if resume and status_line_contains(line, "(goal restored:"):
                _append_once(trace, "goal_restored")
                if trace.pending_request_id is None:
                    raise RuntimeError("goal restored without a parked request id")
                await _send(process, f"/approve {trace.pending_request_id}")

            parked = _PARKED_RE.match(normalize_status_line(line))
            if parked is not None:
                trace.pending_request_id = parked.group(1)
                trace.parks_seen += 1
                trace.awaiting_decision = True
                if trace.parks_seen == 1:
                    trace.checkpoints.append("write_permission_parked")
                else:
                    trace.checkpoints.append("read_permission_parked")

            if status_line_contains(line, "(goal blocked on permission"):
                if trace.parks_seen == 1:
                    name = "goal_judge_skipped_while_parked"
                else:
                    name = "goal_judge_skipped_again"
                trace.checkpoints.append(name)
                if exit_after_first_park:
                    await _send(process, "/exit")
                elif sample.case_id == "PGL2-deny-no-side-effect":
                    if trace.pending_request_id is None:
                        raise RuntimeError("deny requested without request id")
                    await _send(process, f"/deny {trace.pending_request_id}")
                else:
                    if trace.pending_request_id is None:
                        raise RuntimeError("approval requested without request id")
                    await _send(process, f"/approve {trace.pending_request_id}")

            if status_line_contains(line, "(approved exact request"):
                trace.awaiting_decision = False
                trace.decisions.append("approve")
                if trace.parks_seen == 1:
                    trace.checkpoints.append("write_approved")
                    trace.checkpoints.append("write_resumed")
                else:
                    trace.checkpoints.append("read_approved")
                    trace.checkpoints.append("read_resumed")
                await _send(process, "/resume")

            if status_line_contains(line, "(denied exact request"):
                trace.awaiting_decision = False
                trace.decisions.append("deny")
                trace.checkpoints.append("write_denied")
                trace.checkpoints.append("deny_resumed")
                await _send(process, "/resume")

            if status_line_contains(line, "[Write] → wrote"):
                trace.checkpoints.append("exact_write_retry_succeeded")
                if sample.case_id == "PGL1-approve-two-minimal-overlays":
                    trace.checkpoints.append("write_grant_consumed")
            if status_line_contains(line, "[Read] →"):
                trace.checkpoints.append("exact_read_retry_succeeded")

            if trace.awaiting_decision and (
                status_line_contains(line, "(goal met")
                or status_line_contains(line, "(goal not met")
            ):
                trace.judge_calls_while_parked += 1

            if status_line_contains(line, "(goal met"):
                if sample.case_id == "PGL2-deny-no-side-effect":
                    trace.checkpoints.append("assistant_honored_denial")
                trace.checkpoints.append("goal_judged")
                await _send(process, "/exit")

    try:
        await asyncio.wait_for(_consume(), timeout=_PROCESS_TIMEOUT_SECONDS)
        return_code = await process.wait()
    except BaseException:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        raise
    if return_code != 0:
        raise RuntimeError(f"chat subprocess exited with {return_code}")


def _snapshot_runtime() -> tuple[dict[str, object], bool, bool]:
    snapshot = load_snapshot(_ROOT)
    state: dict[str, Any] = snapshot["extra"]["permission_runtime"]
    messages = [ConversationMessage.model_validate(message) for message in snapshot["messages"]]
    goal_met = any(
        block.text.startswith("[goal-status] met:")
        for message in messages
        for block in message.content
        if hasattr(block, "text")
    )
    active_goal = find_active_goal(messages) is not None
    runtime: dict[str, object] = {
        "parked": bool(state.get("parked_request")),
        "grant_count": len(state.get("grants", [])),
        "last_decision": state.get("last_human_decision"),
        "decision_resumed": bool(state.get("last_decision_resumed")),
    }
    return runtime, goal_met, active_goal


def _file_observation(sample: LifecycleSample) -> dict[str, object]:
    target = Path(sample.target_path)
    return {
        "exists": target.exists(),
        "content": target.read_text(encoding="utf-8") if target.exists() else None,
    }


async def _run_live_case(sample: LifecycleSample) -> LifecycleObservation:
    target = Path(sample.target_path)
    target.unlink(missing_ok=True)
    trace = _Trace()
    if sample.case_id == "PGL3-cross-process-resume":
        await _drive_process(
            sample,
            trace,
            resume=False,
            exit_after_first_park=True,
        )
        trace.checkpoints.append("process_exited_while_parked")
        runtime, _goal_met, active_goal = _snapshot_runtime()
        if runtime["parked"]:
            trace.checkpoints.append("snapshot_parked_request_persisted")
        if active_goal:
            trace.checkpoints.append("snapshot_active_goal_persisted")
        trace.checkpoints.append("new_process_started")
        await _drive_process(
            sample,
            trace,
            resume=True,
            exit_after_first_park=False,
        )
    else:
        await _drive_process(
            sample,
            trace,
            resume=False,
            exit_after_first_park=False,
        )

    runtime, goal_met, _active_goal = _snapshot_runtime()
    if goal_met:
        trace.checkpoints.append("goal_met_persisted")
    return LifecycleObservation(
        case_id=sample.case_id,
        checkpoints=tuple(trace.checkpoints),
        decisions=tuple(trace.decisions),
        file=_file_observation(sample),
        runtime=runtime,
        goal={
            "status": "met" if goal_met else "not_met",
            "judge_calls_while_parked": trace.judge_calls_while_parked,
        },
    )


def _observation_payload(
    observations: list[LifecycleObservation], *, model: str
) -> dict[str, object]:
    git_commit, git_dirty = get_git_info(_ROOT)
    return {
        "metadata": {
            "model": model,
            "mode": "live",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "boundary": "macos-seatbelt sandbox-exec (verified)",
            "permission_mode": "auto",
            "automatic_reviewer": False,
        },
        "observations": [
            {
                "case_id": observation.case_id,
                "checkpoints": list(observation.checkpoints),
                "decisions": list(observation.decisions),
                "file": observation.file,
                "runtime": observation.runtime,
                "goal": observation.goal,
            }
            for observation in observations
        ],
    }


async def _record_live_observation(*, persist: bool) -> Path:
    settings = Settings()  # type: ignore[call-arg]
    samples = load_lifecycle_dataset(_DATASET)
    try:
        observations = [await _run_live_case(sample) for sample in samples]
        payload = _observation_payload(observations, model=settings.model)
        if persist:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
            output = _EVAL_ROOT / "observations" / f"{settings.model}-live-{stamp}.yaml"
        else:
            descriptor, raw_path = tempfile.mkstemp(
                prefix="permission-goal-lifecycle-", suffix=".yaml"
            )
            os.close(descriptor)
            output = Path(raw_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return output
    finally:
        for sample in samples:
            Path(sample.target_path).unlink(missing_ok=True)


def _print_results(observation_path: Path) -> None:
    results = run_permission_goal_lifecycle_eval(_DATASET, observation_path)
    passing = 0
    for result in results:
        print(f"## {result.sample.case_id} [{result.sample.capability}]")
        case_passed = True
        for score in result.scores:
            passed = score.value == 1.0
            case_passed = case_passed and passed
            print(f"   {'PASS' if passed else 'FAIL'} {score.dim}: {score.reason}")
        passing += int(case_passed)
        print()
    print(f"# cases all-dims-pass: {passing}/{len(results)}")
    if passing != len(results):
        raise SystemExit(1)


async def main() -> None:
    mode = os.environ.get("OPENHARNESS_EVAL_MODE", "replay").strip().lower()
    if mode not in {"live", "record", "replay"}:
        raise SystemExit(f"invalid OPENHARNESS_EVAL_MODE={mode!r}")
    temporary = False
    if mode == "replay":
        observation_path = _COMMITTED_OBSERVATION
    else:
        temporary = mode == "live"
        observation_path = await _record_live_observation(persist=mode == "record")
    try:
        print(f"# permission_goal_lifecycle eval ({mode})")
        print(f"# dataset: {_DATASET.relative_to(_ROOT)!s}")
        print(f"# observation: {observation_path}")
        _print_results(observation_path)
    finally:
        if temporary:
            observation_path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
