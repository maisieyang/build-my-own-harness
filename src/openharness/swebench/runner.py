"""Private headless runtime invocation for one SWE-bench instance.

The adapter calls the harness's internal non-interactive function in an
isolated child process. It does not depend on, or add branches to, the public
``oh`` command surface. The process boundary preserves per-instance cwd/env
isolation and wall-clock timeout behavior. ``Invoker`` and ``Extractor`` stay
injectable so unit tests need neither a real model nor real git.

Env posture per D40.5 + D40.6: write tools allowed by rule, Bash only
under ``--sandbox``, memory/snapshot forced off so neither the diff nor
the context is contaminated by harness state.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from openharness.swebench.prompt import build_prompt
from openharness.swebench.workspace import WorkspaceError, extract_patch

if TYPE_CHECKING:
    from collections.abc import Mapping
    from multiprocessing.connection import Connection
    from pathlib import Path

    from openharness.swebench.model import SWEBenchInstance


@dataclass(frozen=True)
class RunConfig:
    """Knobs for one benchmark run; every instance in a batch shares one."""

    model: str | None = None
    sandbox: bool = False
    timeout_s: float = 1800.0
    # 小批 finding: real fixes ran 8-19 turns against oh's 20 default —
    # astropy-14182 died on the cap. 40 gives headroom without letting a
    # stuck run burn tokens forever (the wall-clock timeout still bounds it).
    max_turns: int = 40


@dataclass(frozen=True)
class Invocation:
    """Typed inputs passed to the private headless runtime process."""

    prompt: str
    cwd: Path
    env: dict[str, str]
    timeout_s: float
    model: str | None
    sandbox: bool
    max_turns: int


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


def _invoke_headless_child(invocation: Invocation, connection: Connection) -> None:
    """Child-process target that calls the internal runtime function directly."""
    import contextlib
    import io

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = 0
    try:
        os.chdir(invocation.cwd)
        os.environ.clear()
        os.environ.update(invocation.env)

        # Lazy import avoids a cli -> swebench.cli -> runner import cycle.
        from click.exceptions import Exit

        from openharness.cli import _run_headless_command

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                _run_headless_command(
                    prompt=invocation.prompt,
                    model=invocation.model,
                    max_tokens=8192,
                    auto=False,
                    dry_run=False,
                    print_mode=True,
                    output_format="json",
                    max_turns=invocation.max_turns,
                    isolate=False,
                    log_level=None,
                    log_format=None,
                    tool_result_cap=None,
                    no_auto_truncate=False,
                    no_skills=True,
                    no_commands=True,
                    sandbox=invocation.sandbox,
                    sandbox_backend=None,
                    sandbox_image=None,
                    sandbox_memory=None,
                    sandbox_cpus=None,
                    sandbox_runtime=None,
                    enable_plugin_hooks=None,
                    enable_plugins=None,
                    enable_memory=False,
                    enable_web=False,
                    compact_threshold=None,
                    no_auto_compact=False,
                    resume=False,
                    resume_id=None,
                    llm_focus_state=False,
                )
            except Exit as exc:
                exit_code = exc.exit_code
    except BaseException as exc:  # child must always report a result
        exit_code = 1
        stderr.write(f"Internal headless runtime failed: {exc}\n")
    finally:
        connection.send(
            InvocationResult(
                exit_code=exit_code,
                stdout=stdout.getvalue(),
                stderr=stderr.getvalue(),
            )
        )
        connection.close()


def default_invoker(invocation: Invocation) -> InvocationResult:
    """Run the internal headless API in a timeout-bounded child process."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_invoke_headless_child, args=(invocation, send))
    process.start()
    send.close()
    process.join(invocation.timeout_s)
    if process.is_alive():
        process.terminate()
        process.join()
        receive.close()
        return InvocationResult(exit_code=None, stdout="", stderr="timed out")
    if receive.poll():
        result = cast("InvocationResult", receive.recv())
        receive.close()
        return result
    receive.close()
    return InvocationResult(
        exit_code=process.exitcode if process.exitcode not in (None, 0) else 1,
        stdout="",
        stderr="non-interactive runtime exited without a result",
    )


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
    prompt = build_prompt(instance, executable=config.sandbox)
    env = build_env(os.environ if base_env is None else base_env, sandbox=config.sandbox)
    invocation = Invocation(
        prompt=prompt,
        cwd=workspace,
        env=env,
        timeout_s=config.timeout_s,
        model=config.model,
        sandbox=config.sandbox,
        max_turns=config.max_turns,
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
        # "Request failed (HTTP ..." is the headless runtime's rendering of every
        # API-layer death (transport disconnects, 4xx/5xx). Environment
        # noise must not be lumped with parse problems in the taxonomy.
        api_failed = "Request failed (HTTP" in result.stderr
        return InstanceRunResult(
            instance_id=instance.instance_id,
            status="api-failed" if api_failed else "invalid-envelope",
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
