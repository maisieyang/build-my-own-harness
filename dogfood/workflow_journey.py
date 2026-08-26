"""Prepare, launch, and record the manual OpenHarness workflow dogfood.

The module deliberately does not send prompts, inspect transcript markers, or
decide whether a case passed. The person running the dogfood owns interaction
and judgment. The runner only provides a repeatable fixture, launches and
records both REPL processes, and captures inspectable state.
"""

# The suggested natural-language prompts use Chinese punctuation intentionally.
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pty
import shlex
import shutil
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values

from dogfood.context_inspector import collect_context_artifact, render_text_report
from dogfood.repl_runner import hash_fixture
from openharness.config import Settings
from openharness.execution import CommandOperation, ProcessCompleted, SeatbeltBackend
from openharness.memory import FilesystemMemoryStore
from openharness.memory.paths import get_project_memory_dir
from openharness.permissions import workspace_runtime_profile
from openharness.services.snapshot import get_snapshot_dir

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / ".dogfood"
WORK_ROOT = RUNTIME_ROOT / "work"
ARTIFACT_ROOT = RUNTIME_ROOT / "artifacts"
FIXTURE_SOURCE = REPO_ROOT / "dogfood" / "fixtures" / "workflow-journey"
WORK_DIR = WORK_ROOT / "workflow-journey"

SCHEMA = "openharness.dogfood.workflow-journey.manual.v2"

DEFAULT_INVESTIGATION = (
    "帮我看看 pricing.py 和 test_pricing.py，解释为什么测试会失败。先不要修改代码，也不要运行测试。"
)
DEFAULT_MEMORY = (
    "另外，请记住我的协作习惯：先给我计划，等我明确同意后再执行；"
    "每轮结束时告诉我实际运行了什么验证。"
)
PLAN_INPUT = (
    "/plan 请为这个折扣计算问题制定修复方案。折扣百分比只应该接受 0 到 100，"
    "非法值需要明确报错；也请考虑边界测试和验证方式。"
)
VALIDATION_COMMAND = (
    "python",
    "-m",
    "pytest",
    "-c",
    "pytest.ini",
    "test_pricing.py",
    "-q",
    "--no-cov",
)
VALIDATION_COMMAND_TEXT = shlex.join(VALIDATION_COMMAND)
GOAL_INPUT = (
    "/goal 按刚才批准的计划执行，修复折扣计算：discount_percent 只接受 0 到 100，"
    "并按百分比计算折后金额；补齐 0%、100%、非法范围和小数百分比测试。只有在 "
    f"`{VALIDATION_COMMAND_TEXT}` 成功后才算完成。若该命令被权限阻止，请报告阻塞，"
    "不要用替代命令宣告完成；最多检查 8 轮。"
)
COMPACT_FOLLOW_UP = "请简要告诉我，我们刚才完成了什么，还有没有未完成的工作？"
RESUME_INPUT = "我们刚才做到哪里了？修改和验证已经完成了吗？"
MEMORY_INPUT = "你还记得我之前告诉你的协作习惯吗？"


class JourneyFailure(RuntimeError):
    """The manual journey could not be prepared or safely launched."""


@dataclass(frozen=True)
class ManualStep:
    """A natural user interaction plus questions for human observation."""

    case_id: str
    phase: str
    title: str
    inputs: tuple[str, ...]
    observe: tuple[str, ...]
    new_process: bool = False


JOURNEY_STEPS = (
    ManualStep(
        case_id="DPG-016",
        phase="default",
        title="Default 调查与自然记忆请求",
        inputs=(DEFAULT_INVESTIGATION, DEFAULT_MEMORY),
        observe=(
            "Agent 是否直接理解当前 fixture，而不是把简单任务扩大成仓库探索。",
            "在不修改文件、不运行测试的前提下，失败原因是否解释清楚。",
            "自然的“请记住”请求是否形成了合适的 durable Memory。",
        ),
    ),
    ManualStep(
        case_id="DPG-017",
        phase="plan",
        title="Plan 制定方案并由人批准",
        inputs=(PLAN_INPUT, "1"),
        observe=(
            "Plan 是否保持只读，并覆盖计算公式、输入边界和验证方法。",
            "选择批准后是否只回到 Default，而没有擅自执行。",
            "规划过程是否自然，用户是否知道下一步应该做什么。",
        ),
    ),
    ManualStep(
        case_id="DPG-018",
        phase="goal",
        title="Goal 使用刚批准的计划完成工作",
        inputs=(GOAL_INPUT,),
        observe=(
            "Goal 是否真正利用已有计划，而不是要求用户重述全部任务。",
            "执行、测试和 Judge 收口过程是否可信且容易理解。",
            "结束时是否清楚报告实际运行过的验证。",
        ),
    ),
    ManualStep(
        case_id="DPG-019",
        phase="compact-snapshot",
        title="Compact 形成可恢复的会话交接",
        inputs=("/compact", COMPACT_FOLLOW_UP),
        observe=(
            "Compact 是否明显缩短上下文，同时保留任务结果和未完成事项。",
            "Compact 后的回答是否仍符合刚才发生的真实工作。",
            "退出前的 Snapshot 是否记录了 compact 后的 conversation。",
        ),
    ),
    ManualStep(
        case_id="DPG-020",
        phase="resume",
        title="新进程恢复工作上下文",
        new_process=True,
        inputs=(RESUME_INPUT,),
        observe=(
            "Resume banner 与恢复过程是否让人明确知道发生了什么。",
            "Agent 是否能自然说明完成状态，而不是重新探索项目。",
            "恢复后的上下文是否准确，而非只保留模糊摘要。",
        ),
    ),
    ManualStep(
        case_id="DPG-021",
        phase="memory",
        title="自然回忆跨进程协作偏好",
        inputs=(MEMORY_INPUT,),
        observe=(
            "Agent 是否主动访问 durable Memory，而不是仅凭 conversation 猜测。",
            "回忆内容是否准确覆盖先规划、明确同意后执行、报告验证。",
            "Memory 与 resumed conversation 的职责是否能从体验中区分出来。",
        ),
    ),
)


@dataclass(frozen=True)
class PreparationResult:
    work_dir: Path
    snapshot_dir: Path
    memory_dir: Path


@dataclass(frozen=True)
class VerificationResult:
    returncode: int
    output: str


def _validate_run_id(run_id: str) -> None:
    if not run_id or run_id in {".", ".."} or Path(run_id).name != run_id:
        raise JourneyFailure(f"invalid run id: {run_id!r}")


def _validate_label(label: str) -> None:
    if not label or label in {".", ".."} or Path(label).name != label:
        raise JourneyFailure(f"invalid checkpoint label: {label!r}")


def _product_command(*, resume: bool) -> list[str]:
    command = ["uv", "run", "--project", str(REPO_ROOT), "oh"]
    if resume:
        command.append("--resume")
    command.extend(["--auto", "--sandbox"])
    return command


def compose_process_env(
    *,
    env_files: tuple[Path, ...],
    environ: dict[str, str],
) -> dict[str, str]:
    """Apply user/project dotenv layers while preserving shell precedence."""
    layered: dict[str, str] = {}
    for env_file in env_files:
        if not env_file.is_file():
            continue
        layered.update(
            {key: value for key, value in dotenv_values(env_file).items() if value is not None}
        )
    layered.update(environ)
    return layered


def _process_env() -> dict[str, str]:
    return compose_process_env(
        env_files=(Path.home() / ".openharness" / ".env", REPO_ROOT / ".env"),
        environ=dict(os.environ),
    )


def build_run_manifest(*, run_id: str) -> dict[str, Any]:
    """Record the suggestions shown to the human, without automatic oracles."""
    _validate_run_id(run_id)
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "mode": "manual",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture": str(WORK_DIR),
        "launch": {
            "cwd": str(WORK_DIR),
            "fresh": shlex.join(_product_command(resume=False)),
            "resume": shlex.join(_product_command(resume=True)),
        },
        "steps": [asdict(step) for step in JOURNEY_STEPS],
        "judgment": "human",
    }


def render_manual_runbook(*, run_id: str) -> str:
    """Render suggested natural interactions and human observation questions."""
    _validate_run_id(run_id)
    lines = [
        "# OpenHarness 完整工作流手动 Dogfood",
        "",
        f"Run ID：`{run_id}`",
        "",
        "这不是自动协议测试。下面是保持实验目标一致的自然输入建议；你可以按真实表达微调，",
        "最终体验和结果由你判断。Runner 只负责状态重置、启动、transcript 和 checkpoint。",
        "",
    ]
    for step in JOURNEY_STEPS:
        if step.new_process:
            lines.extend(
                [
                    "先在上一进程输入 `/exit`；外层 manual runner 会等待确认后启动 Resume。",
                    "",
                ]
            )
        lines.extend([f"## {step.case_id} · {step.title}", "", "建议输入：", ""])
        for value in step.inputs:
            lines.extend(["```text", value, "```", ""])
        lines.extend(["请观察：", ""])
        lines.extend(f"- {observation}" for observation in step.observe)
        lines.extend(
            [
                "",
                "如果使用第二个终端，可在此时留一个结构化 checkpoint：",
                "",
                "```bash",
                (
                    "uv run python -m dogfood.workflow_journey capture "
                    f"--run-id {run_id} --label {step.case_id.lower()}"
                ),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "完成 DPG-021 后输入 `/exit`。请把你的判断和体感写入本次 artifact 中的",
            "`notes.md`；Runner 不会替你标记 pass/fail。",
            "",
        ]
    )
    return "\n".join(lines)


def render_notes_template() -> str:
    lines = [
        "# Manual dogfood notes",
        "",
        "由运行者记录。Runner 不会根据这些内容自动判定通过或失败。",
        "",
    ]
    for step in JOURNEY_STEPS:
        lines.extend(
            [
                f"## {step.case_id} · {step.title}",
                "",
                "观察：",
                "",
                "判断：",
                "",
                "想改进的地方：",
                "",
            ]
        )
    return "\n".join(lines)


def _safe_remove_control_dir(path: Path, *, expected_parent: Path) -> None:
    resolved = path.resolve()
    parent = expected_parent.resolve()
    if resolved.parent != parent or resolved == parent:
        raise JourneyFailure(f"refusing to reset unexpected control-state path: {path}")
    if path.exists():
        shutil.rmtree(path)


def prepare_journey(*, source: Path, target: Path, runtime_root: Path) -> PreparationResult:
    """Reset the fixture plus its exact dogfood-only snapshot and memory dirs."""
    root = runtime_root.resolve()
    resolved_target = target.resolve()
    if resolved_target == root or not resolved_target.is_relative_to(root):
        raise JourneyFailure(f"target is outside dogfood runtime root: {target}")
    if not source.is_dir():
        raise JourneyFailure(f"journey fixture source is missing: {source}")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)

    snapshot_dir = get_snapshot_dir(target)
    memory_dir = get_project_memory_dir(target)
    home = Path.home()
    _safe_remove_control_dir(
        snapshot_dir,
        expected_parent=home / ".openharness" / "snapshots",
    )
    _safe_remove_control_dir(
        memory_dir,
        expected_parent=home / ".openharness" / "memory",
    )
    return PreparationResult(target, snapshot_dir, memory_dir)


async def _run_baseline_in_sandbox() -> VerificationResult:
    backend = SeatbeltBackend(cwd=WORK_DIR)
    session = await backend.open(workspace_runtime_profile())
    try:
        result = await session.execute(
            CommandOperation(command=VALIDATION_COMMAND_TEXT, cwd=WORK_DIR)
        )
    finally:
        await session.close()
    if isinstance(result, ProcessCompleted):
        return VerificationResult(result.exit_code, result.output)
    return VerificationResult(1, f"sandbox baseline failed: {result!r}\n")


def _run_baseline() -> VerificationResult:
    return asyncio.run(_run_baseline_in_sandbox())


def _require_baseline(result: VerificationResult) -> None:
    if result.returncode == 0 or "1 failed, 1 passed" not in result.output:
        raise JourneyFailure(
            "fixture baseline is not the expected '1 failed, 1 passed':\n" + result.output
        )


def _run_dir(run_id: str) -> Path:
    _validate_run_id(run_id)
    path = ARTIFACT_ROOT / run_id / "workflow-journey"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _effective_settings() -> Settings:
    return Settings(
        _env_file=(
            str(Path.home() / ".openharness" / ".env"),
            str(REPO_ROOT / ".env"),
        )
    )


def _memory_records() -> list[dict[str, Any]]:
    memory_dir = get_project_memory_dir(WORK_DIR)
    if not memory_dir.is_dir():
        return []
    store = FilesystemMemoryStore(project_dir=memory_dir)
    records: list[dict[str, Any]] = []
    for memory in sorted(store.discover().values(), key=lambda item: item.name):
        body = memory.body.rstrip()
        records.append(
            {
                "name": memory.name,
                "type": memory.type.value,
                "description": memory.description,
                "body": body,
                "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        )
    return records


def write_checkpoint(*, run_id: str, label: str) -> Path:
    """Capture inspectable state without making a model call or judgment."""
    _validate_label(label)
    artifact = collect_context_artifact(WORK_DIR, settings=_effective_settings())
    payload = {
        "schema": SCHEMA,
        "label": label,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "context": artifact,
        "fixture_hashes": hash_fixture(WORK_DIR),
        "memory_records": _memory_records(),
    }
    checkpoint_dir = _run_dir(run_id) / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    json_path = checkpoint_dir / f"{label}.json"
    text_path = checkpoint_dir / f"{label}.txt"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(render_text_report(artifact), encoding="utf-8")
    return json_path


def _write_contract_files(*, run_id: str) -> Path:
    run_dir = _run_dir(run_id)
    (run_dir / "manifest.json").write_text(
        json.dumps(build_run_manifest(run_id=run_id), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manual-runbook.md").write_text(
        render_manual_runbook(run_id=run_id),
        encoding="utf-8",
    )
    notes_path = run_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text(render_notes_template(), encoding="utf-8")
    return run_dir


@contextmanager
def _manual_process_context() -> Iterator[None]:
    previous_cwd = Path.cwd()
    previous_env = dict(os.environ)
    process_env = _process_env()
    os.chdir(WORK_DIR)
    os.environ.clear()
    os.environ.update(process_env)
    os.environ.update(
        {
            "NO_COLOR": "1",
            "PYTHONUNBUFFERED": "1",
            "UV_CACHE_DIR": str(REPO_ROOT / ".cache" / "uv"),
        }
    )
    try:
        yield
    finally:
        os.chdir(previous_cwd)
        os.environ.clear()
        os.environ.update(previous_env)


def _spawn_manual(command: list[str]) -> tuple[int, str]:
    output = bytearray()

    def record(master_fd: int) -> bytes:
        data = os.read(master_fd, 65536)
        output.extend(data)
        return data

    with _manual_process_context():
        status = pty.spawn(command, master_read=record)
    return os.waitstatus_to_exitcode(status), output.decode("utf-8", errors="replace")


def _write_record(
    *,
    run_id: str,
    baseline: VerificationResult,
    fresh_exit: int | None,
    resume_exit: int | None,
    status: str,
) -> None:
    (_run_dir(run_id) / "result.json").write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "run_id": run_id,
                "mode": "manual",
                "status": status,
                "human_judgment_required": True,
                "baseline": asdict(baseline),
                "fresh_process_exit": fresh_exit,
                "resume_process_exit": resume_exit,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_manual(*, run_id: str) -> None:
    """Run two user-driven REPL processes and record, but never score, them."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise JourneyFailure("manual mode requires an interactive terminal")
    prepare_journey(source=FIXTURE_SOURCE, target=WORK_DIR, runtime_root=RUNTIME_ROOT)
    run_dir = _write_contract_files(run_id=run_id)
    baseline = _run_baseline()
    _require_baseline(baseline)
    (run_dir / "baseline.txt").write_text(baseline.output, encoding="utf-8")
    write_checkpoint(run_id=run_id, label="00-prepared")
    print((run_dir / "manual-runbook.md").read_text(encoding="utf-8"))
    print("\n请亲自完成 DPG-016～019；完成 compact 后输入 /exit。\n")

    fresh_exit: int | None = None
    resume_exit: int | None = None
    fresh_transcript = ""
    resume_transcript = ""
    try:
        fresh_exit, fresh_transcript = _spawn_manual(_product_command(resume=False))
        (run_dir / "transcript-fresh.txt").write_text(fresh_transcript, encoding="utf-8")
        write_checkpoint(run_id=run_id, label="after-fresh")
        if fresh_exit != 0:
            raise JourneyFailure(f"fresh manual REPL exited {fresh_exit}")

        input("按 Enter 启动 Resume；随后亲自完成 DPG-020～021，再输入 /exit：")
        resume_exit, resume_transcript = _spawn_manual(_product_command(resume=True))
        (run_dir / "transcript-resume.txt").write_text(resume_transcript, encoding="utf-8")
        write_checkpoint(run_id=run_id, label="after-resume")
        if resume_exit != 0:
            raise JourneyFailure(f"resume manual REPL exited {resume_exit}")

        (run_dir / "transcript.txt").write_text(
            "# fresh\n" + fresh_transcript + "\n# resume\n" + resume_transcript,
            encoding="utf-8",
        )
        _write_record(
            run_id=run_id,
            baseline=baseline,
            fresh_exit=fresh_exit,
            resume_exit=resume_exit,
            status="recorded",
        )
        print(f"manual dogfood recorded; add your judgment to: {run_dir / 'notes.md'}")
    except BaseException:
        (run_dir / "transcript.txt").write_text(
            "# fresh\n" + fresh_transcript + "\n# resume\n" + resume_transcript,
            encoding="utf-8",
        )
        _write_record(
            run_id=run_id,
            baseline=baseline,
            fresh_exit=fresh_exit,
            resume_exit=resume_exit,
            status="interrupted",
        )
        raise


def prepare_command(*, run_id: str) -> None:
    prepare_journey(source=FIXTURE_SOURCE, target=WORK_DIR, runtime_root=RUNTIME_ROOT)
    run_dir = _write_contract_files(run_id=run_id)
    baseline = _run_baseline()
    _require_baseline(baseline)
    (run_dir / "baseline.txt").write_text(baseline.output, encoding="utf-8")
    write_checkpoint(run_id=run_id, label="00-prepared")
    print(f"prepared: {WORK_DIR}")
    print(f"runbook: {run_dir / 'manual-runbook.md'}")
    print(f"notes: {run_dir / 'notes.md'}")
    print(baseline.output.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("prepare", "manual", "capture"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--run-id",
            default=datetime.now().strftime("%Y%m%d-%H%M%S"),
        )
        if name == "capture":
            command.add_argument("--label", required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "prepare":
            prepare_command(run_id=args.run_id)
        elif args.command == "manual":
            run_manual(run_id=args.run_id)
        else:
            path = write_checkpoint(run_id=args.run_id, label=args.label)
            print(f"captured: {path}")
        return 0
    except (JourneyFailure, OSError, ValueError) as exc:
        print(f"workflow journey failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
