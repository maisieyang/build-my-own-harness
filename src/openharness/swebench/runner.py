"""Headless invocation of the shipped ``oh`` CLI for one instance (D40.2).

The benchmark exercises the exact artifact users run — ``oh ask -p
--output-format json`` as a subprocess — never an in-process import that
would bypass the CLI layer, the headless permission posture, or the
loop-runtime wiring. The subprocess seam (``Invoker``) and the patch seam
(``Extractor``) are injectable so unit tests need neither a real model nor
real git.

Env posture per D40.5 + D40.6: write tools allowed by rule, Bash only
under ``--sandbox``, memory/snapshot forced off so neither the diff nor
the context is contaminated by harness state.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openharness.swebench.prompt import build_prompt
from openharness.swebench.workspace import WorkspaceError, extract_patch

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from openharness.swebench.model import SWEBenchInstance


@dataclass(frozen=True)
class RunConfig:
    """Knobs for one benchmark run; every instance in a batch shares one."""

    model: str | None = None
    sandbox: bool = False
    timeout_s: float = 1800.0
    oh_command: tuple[str, ...] = ("oh",)


@dataclass(frozen=True)
class Invocation:
    """Everything the subprocess seam needs — argv form, no shell."""

    argv: list[str]
    cwd: Path
    env: dict[str, str]
    timeout_s: float


@dataclass(frozen=True)
class InvocationResult:
    """``exit_code=None`` means the invocation hit the timeout."""

    exit_code: int | None
    stdout: str
    stderr: str


Invoker = Callable[[Invocation], InvocationResult]
Extractor = Callable[["Path"], str]


@dataclass(frozen=True)
class InstanceRunResult:
    """Per-instance outcome — the raw material of the failure taxonomy
    (D40.8): ``status`` is the adapter's own differentiated verdict,
    the envelope fields are the harness's self-report, ``model_patch``
    is what actually gets judged.
    """

    instance_id: str
    status: (
        str  # completed | timeout | invalid-envelope | patch-extraction-failed | workspace-failed
    )
    model_patch: str
    stop_reason: str | None = None
    exit_code: int | None = None
    num_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_s: float = 0.0
    detail: str | None = None


def build_argv(prompt: str, config: RunConfig) -> list[str]:
    """Assemble the headless ``oh ask`` argv (D40.2 / D40.7 single-shot)."""
    argv = [
        *config.oh_command,
        "ask",
        prompt,
        "-p",
        "--output-format",
        "json",
        "--no-skills",
        "--no-commands",
    ]
    if config.model is not None:
        argv += ["--model", config.model]
    if config.sandbox:
        argv.append("--sandbox")
    return argv


def build_env(base_env: Mapping[str, str], *, sandbox: bool) -> dict[str, str]:
    """Headless permission posture (D40.6) + harness-state isolation (D40.5).

    Read-only tools pass the headless fail-closed baseline on their own;
    the allow rules open exactly the write surface. ``Bash(*)`` only when
    the command substrate is the Docker sandbox.
    """
    env = dict(base_env)
    allow = "Edit(**),Write(**)"
    if sandbox:
        allow += ",Bash(*)"
    env["OPENHARNESS_PERMISSIONS__ALLOW"] = allow
    env["OPENHARNESS_ENABLE_MEMORY"] = "false"
    env["OPENHARNESS_SNAPSHOT__ENABLED"] = "false"
    return env


def default_invoker(invocation: Invocation) -> InvocationResult:
    """Real subprocess invoker — exercised end-to-end by the T7 smoke."""
    try:
        proc = subprocess.run(
            invocation.argv,
            cwd=invocation.cwd,
            env=invocation.env,
            capture_output=True,
            text=True,
            timeout=invocation.timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return InvocationResult(exit_code=None, stdout=stdout, stderr=stderr)
    return InvocationResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _parse_envelope(stdout: str) -> dict[str, Any] | None:
    """Find the terminal ``result`` envelope: last stdout line that parses
    as a JSON object with ``type == "result"``. Anything else → None."""
    for line in reversed(stdout.strip().splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def _tail(text: str, limit: int = 500) -> str:
    return text[-limit:] if len(text) > limit else text


def run_instance(
    instance: SWEBenchInstance,
    workspace: Path,
    config: RunConfig,
    *,
    invoke: Invoker = default_invoker,
    extract: Extractor = extract_patch,
    base_env: Mapping[str, str] | None = None,
) -> InstanceRunResult:
    """Run one instance headless in ``workspace`` and collect the verdict.

    Never raises for a per-instance failure — every failure mode maps to a
    differentiated ``status`` so a batch keeps going (D40.8) and the
    taxonomy keeps its raw material. A timed-out run still extracts the
    partial patch: best-effort work is a legitimate submission.
    """
    prompt = build_prompt(instance)
    env = build_env(os.environ if base_env is None else base_env, sandbox=config.sandbox)
    invocation = Invocation(
        argv=build_argv(prompt, config),
        cwd=workspace,
        env=env,
        timeout_s=config.timeout_s,
    )

    start = time.monotonic()
    result = invoke(invocation)
    duration_s = round(time.monotonic() - start, 2)

    try:
        patch = extract(workspace)
    except WorkspaceError as exc:
        return InstanceRunResult(
            instance_id=instance.instance_id,
            status="patch-extraction-failed",
            model_patch="",
            exit_code=result.exit_code,
            duration_s=duration_s,
            detail=str(exc),
        )

    if result.exit_code is None:
        return InstanceRunResult(
            instance_id=instance.instance_id,
            status="timeout",
            model_patch=patch,
            duration_s=duration_s,
            detail=_tail(result.stderr),
        )

    envelope = _parse_envelope(result.stdout)
    if envelope is None:
        return InstanceRunResult(
            instance_id=instance.instance_id,
            status="invalid-envelope",
            model_patch=patch,
            exit_code=result.exit_code,
            duration_s=duration_s,
            detail=f"stdout: {_tail(result.stdout)} | stderr: {_tail(result.stderr)}",
        )

    usage = envelope.get("usage")
    usage_dict: dict[str, Any] = usage if isinstance(usage, dict) else {}
    stop_reason = envelope.get("stop_reason")
    num_turns = envelope.get("num_turns")
    return InstanceRunResult(
        instance_id=instance.instance_id,
        status="completed",
        model_patch=patch,
        stop_reason=stop_reason if isinstance(stop_reason, str) else None,
        exit_code=result.exit_code,
        num_turns=num_turns if isinstance(num_turns, int) else None,
        input_tokens=(
            usage_dict.get("input_tokens")
            if isinstance(usage_dict.get("input_tokens"), int)
            else None
        ),
        output_tokens=(
            usage_dict.get("output_tokens")
            if isinstance(usage_dict.get("output_tokens"), int)
            else None
        ),
        duration_s=duration_s,
        detail=None,
    )
