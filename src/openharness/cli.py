"""Command-line interface for OpenHarness.

P1-T4 shipped a single-call CLI that streamed one LLM response. P2-T6.6e
rewrites ``_run_ask`` to drive the full agent loop (``run_query``):

* Build :class:`QueryContext` from ``Settings`` + the default tool registry +
  the canonical permission profile + the assembled system prompt + the
  detected environment and independent review/execution postures.
* Hand off to :func:`run_query`; render the streamed events.

Design highlights (rationale in ``learnings/04-cli.md`` + ``learnings/10-cli-loop.md``;
external contracts in ``decisions/05-cli.md`` + ``decisions/06`` + ``decisions/07``):

* **Provider-neutral env vars** (``OPENHARNESS_API_KEY`` / ``_BASE_URL`` /
  ``_MODEL`` / ``_PERMISSION_PROFILE``) are read by ``Settings``; the CLI never
  reaches into ``os.environ`` directly.
* **Differentiated error UX**: each error type maps to a category prefix
  in stderr; exit code 1. ``LoopLimitExceeded`` (D6.1) is caught by the
  dedicated ``except LoopError`` arm (P3-T2.2d) — "Loop error:" prefix
  signals the category, the embedded message itself names ``--max-turns``.
  Anything that escapes named arms lands in the ``except OpenHarnessError``
  root catch-all (P3-T2.2b widened from ``OpenHarnessApiError``).
* **Orthogonal postures**: ``--auto`` selects the exact-request reviewer;
  ``--dry-run`` lists tool calls without executing. They may be combined.

The seams that tests substitute:

* :func:`_load_settings` -- replace to inject deterministic config.
* :func:`_build_client` -- replace with a stub client that yields
  canned :class:`ApiStreamEvent`s.
* :func:`run_query` (module-level reference) -- replace to capture the
  constructed :class:`QueryContext` for flag-propagation tests.

All seams are module-level so ``monkeypatch.setattr`` works without
touching Typer internals.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import typer

# TTY input goes through prompt_toolkit (repl-ux plan §2): its own line
# editor handles CJK/ASCII mixed-script width correctly, which retires
# the Phase 14.5 gnureadline workaround. Non-TTY input never had line
# editing, so no readline backend is needed anywhere anymore.
from openharness import repl as _repl

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.stream_events import ApiStreamEvent
from openai import AsyncOpenAI
from pydantic import ValidationError

from openharness import __version__
from openharness._stream_render import (
    PrintResult,
    build_result_obj,
    collect_print_result,
    render_history_transcript,
    render_stream,
    render_stream_json,
)
from openharness.api import (
    AuthenticationFailure,
    OpenAICompatibleApiClient,
    OpenHarnessApiError,
    QuotaExceededFailure,
    RateLimitFailure,
    RequestFailure,
)
from openharness.bundles import (
    FilesystemBundleStore,
    HookSpec,
    UnknownBundleError,
    apply_bundle_to_context,
    discover_filesystem_hook_plugins,
    discover_plugin_hooks,
)
from openharness.commands import (
    FilesystemCommandStore,
    UnknownCommandError,
    resolve_command_invocation,
)
from openharness.commands.model import Command  # noqa: TC001 — runtime use in LayeredStore generic
from openharness.compaction import TruncateToolResultHook
from openharness.config import Settings
from openharness.engine import QueryContext, extract_authorization_context, run_query
from openharness.engine.errors import LoopLimitExceeded
from openharness.engine.slash_skill import synthesize_skill_envelope
from openharness.errors import LoopError, OpenHarnessError
from openharness.execution import (
    BoundaryVerification,
    DockerCommandBackend,
    ExecutionEffect,
    ExecutionEnvironment,
    OneShotOverlaySession,
    SandboxBackend,
    SandboxSession,
    SandboxUnavailableError,
    SeatbeltBackend,
)
from openharness.execution.host import _HOST_EXECUTION
from openharness.hooks import HookRegistry
from openharness.mcp import McpClientPool
from openharness.memory import (
    FilesystemMemoryStore,
    MemoryStore,
    ensure_project_memory_dir,
)
from openharness.observability import configure_logging, new_run_id

# Typer reflects ``Literal[...]`` types at RUNTIME to build Click Choice
# constraints — moving these into ``TYPE_CHECKING`` would break ``--log-level
# TRACE``-rejection and the corresponding test.
from openharness.observability.logging import (
    LogFormat,
    LogLevel,
    get_logger,
)
from openharness.permissions import (
    ActionDenyPolicy,
    ConfiguredActionDenyPolicy,
    ExecutionPosture,
    PermissionRuntime,
    PlanActionDenyPolicy,
    ReviewerPosture,
    RuntimePermissionProfile,
)
from openharness.plugins import (
    LayeredStore,
    LoadedPluginCatalogs,
    PluginLoader,
)
from openharness.prompts import (
    PLAN_MODE_PROMPT_SECTION,
    build_system_prompt,
    detect_environment,
    load_project_instructions,
)
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
)
from openharness.services.goal_judge import GoalJudgeVerdict, judge_goal_completion
from openharness.services.permission_reviewer import LlmPermissionReviewer
from openharness.services.run_session import RunSession, open_run_session
from openharness.skills.store import EmptySkillStore, FilesystemSkillStore, SkillStore
from openharness.tools import LoadSkillTool, create_default_tool_registry, register_memory_tools
from openharness.tools.web_fetch import WebFetch
from openharness.tools.web_search import TavilySearchProvider, WebSearch

# Default per-call output cap. Phase 1 originally shipped 1024 (no tools),
# but with tool-use ship (Phase 2) and especially Agent / Write tool calls
# that emit file content as the ``arguments`` JSON, 1024 routinely truncates
# mid-string. Bumped to 8192 to align with Claude Code / modern harness
# defaults — covers most file-creating tool calls in one shot. Users with
# tight budgets opt down via ``--max-tokens``.
DEFAULT_MAX_TOKENS = 8192

# loop-runtime L1: headless print-mode output shapes. Typer reflects this
# ``Literal`` at runtime to build the ``--output-format`` Click Choice (same
# mechanism as ``LogLevel`` / ``LogFormat`` above). ``text`` is wired in T1;
# ``json`` / ``stream-json`` land in T3 / T4.
OutputFormat = Literal["text", "json", "stream-json"]
SandboxBackendName = Literal["seatbelt", "docker-command"]


app = typer.Typer(
    name="oh",
    help="OpenHarness — a production-grade Python harness for LLM agents.",
    # repl-ux plan §1: bare ``oh`` enters the chat REPL (see ``_root``)
    # instead of printing help — one word enters the session. Help
    # stays reachable via ``oh --help``.
    no_args_is_help=False,
    add_completion=False,
)

# The public shell surface is organized by audience and responsibility. Bare
# ``oh`` remains the only visible agent entry; everything else lives below one
# of these four concepts.
config_app = typer.Typer(
    name="config",
    help="Configure OpenHarness.",
    epilog="Shortcut:\n\n  oh config                 Show effective settings.",
)
inspect_app = typer.Typer(
    name="inspect",
    help="Inspect runtime capabilities.",
    no_args_is_help=True,
    epilog=(
        "Examples:\n\n"
        "  oh inspect tools list       List registered tools.\n\n"
        "  oh inspect hooks list       List framework and plugin hooks.\n\n"
        "  oh inspect plugins list     List installed plugins."
    ),
)
state_app = typer.Typer(
    name="state",
    help="Inspect and maintain project state.",
    no_args_is_help=True,
    epilog=(
        "Examples:\n\n"
        "  oh state memory list        List project memories.\n\n"
        "  oh state memory path        Print the memory directory.\n\n"
        "  oh state snapshots list     List conversation snapshots.\n\n"
        "  oh state snapshots gc       Maintain snapshot history."
    ),
)
dev_app = typer.Typer(
    name="dev",
    help="Run repository development workflows.",
    no_args_is_help=True,
    epilog=(
        "Examples:\n\n"
        "  oh dev eval --help          Show available capability evals.\n\n"
        "  oh dev bench swebench --help  Show SWE-bench workflows."
    ),
)
app.add_typer(config_app, name="config")
app.add_typer(inspect_app, name="inspect")
app.add_typer(state_app, name="state")
app.add_typer(dev_app, name="dev")

# Private command harness for exercising the non-interactive runtime from
# tests and benchmark adapters. It is deliberately not mounted on ``app`` and
# has no console-script entry point: ``oh`` has one agent-starting front door.
headless_app = typer.Typer(add_completion=False)


@headless_app.callback()
def _headless_root() -> None:
    """Private parser root; intentionally unreachable from ``oh``."""


# --------------------------------------------------------------------------- #
# Seams (overridable in tests)                                                #
# --------------------------------------------------------------------------- #


def _load_settings() -> Settings:
    """Construct :class:`Settings` from the environment.

    Wrapped (rather than calling ``Settings()`` inline) so tests can
    monkeypatch this single function instead of fiddling with env vars
    or pydantic-settings internals.

    P7-T2 (D25.2):loads ``~/.openharness/.env`` if present(user-global
    layer)in addition to ``./.env`` from cwd(project layer).Project
    layer wins because it's later in the env_file tuple.``oh config
    edit`` opens the user-global file.Both files may be absent — the
    `OPENHARNESS_*` env vars set directly in the shell always work.
    """
    user_env = Path.home() / ".openharness" / ".env"
    return Settings(_env_file=(str(user_env), ".env"))


def _load_resume_snapshot(
    cwd: Path,
    *,
    resume_id: str | None,
) -> dict[str, Any] | None:
    """P12-T5 (D30.4 + D30.5): load a snapshot for ``--resume``.

    Surfaces the staleness contract to the CLI layer:

    - ``SnapshotNotFound`` → return None (caller warns "no snapshot —
      starting fresh"; resume falls through to fresh-session path)
    - ``SnapshotCwdMismatch`` / ``SnapshotVersionMismatch`` /
      ``SnapshotMalformed`` → raise typer.Exit(1) with stderr
      explaining (silent corruption / breakage; user MUST know)
    - git HEAD drift → warn-logged inside ``load_snapshot``; no
      additional CLI surface (the WARNING already shows in stderr
      under the default log level)

    Returns the snapshot dict on success, None on
    ``SnapshotNotFound`` (caller handles "start fresh").
    """
    from openharness.services.snapshot import (
        SnapshotCwdMismatch,
        SnapshotMalformed,
        SnapshotNotFound,
        SnapshotVersionMismatch,
        load_snapshot,
    )

    try:
        return load_snapshot(cwd, snapshot_id=resume_id)
    except SnapshotNotFound:
        if resume_id is not None:
            typer.echo(
                f"No snapshot matching id={resume_id!r} for cwd={cwd}",
                err=True,
            )
            raise typer.Exit(code=1) from None
        typer.echo(
            f"No snapshot for cwd={cwd} — starting fresh.",
            err=True,
        )
        return None
    except SnapshotCwdMismatch as exc:
        typer.echo(f"Snapshot cwd mismatch: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except SnapshotVersionMismatch as exc:
        typer.echo(f"Snapshot version mismatch: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except SnapshotMalformed as exc:
        typer.echo(f"Snapshot malformed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _build_client(settings: Settings) -> OpenAICompatibleApiClient:
    """Wire an :class:`OpenAICompatibleApiClient` from settings.

    Tests substitute this with a factory returning a stub client whose
    ``stream_message`` yields canned events.
    """
    sdk = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        # OpenHarness owns retry classification, delay caps, and user-visible
        # retry events. A second hidden SDK retry loop would undermine all three.
        max_retries=0,
    )
    return OpenAICompatibleApiClient(sdk=sdk, extra_body=settings.extra_body)


def _maybe_register_web_tools(
    registry: Any,
    *,
    enable_web: bool,
    settings: Settings,
    explicit_flag: bool = False,
) -> bool:
    """Conditionally register :class:`WebSearch` + :class:`WebFetch`.

    Returns the **effective** web state — ``True`` if both tools got
    registered, ``False`` otherwise. The caller passes the same value
    to :func:`build_system_prompt` as ``web_enabled=`` so the prompt
    matches the actual tool catalog (positive guidance only when
    tools are really available).

    Phase 14.5 dogfood revision of D29.3 (see
    [[feedback-opt-in-default-calibration]]): web tools now default
    ON (mirrors Claude Code / Cursor / industry harness behavior).
    Two degradation paths cover users who don't have a Tavily key:

    - ``enable_web=False`` (explicit ``--no-enable-web`` or settings
      override): return False, no-op, no warning.
    - ``enable_web=True`` AND ``settings.web.api_key is None``:
      - if ``explicit_flag=True`` (user typed ``--enable-web``):
        ``typer.Exit(1)`` with remediation — user explicitly asked
        and deserves a clear answer.
      - if ``explicit_flag=False`` (default-ON path): print a brief
        one-line stderr hint and return False so the system prompt
        falls back to the anti-substitution paragraph. New users
        with no Tavily key see v0.2.0 behavior, not a crash.
    """
    if not enable_web:
        return False
    if settings.web.api_key is None:
        if explicit_flag:
            typer.echo(
                "--enable-web requires OPENHARNESS_WEB__API_KEY to be set. "
                "Sign up at https://tavily.com (free tier, 1000/month) and "
                "export the key, then re-run.",
                err=True,
            )
            raise typer.Exit(code=1)
        # Default-ON path with no key — silently degrade. No stderr
        # noise: every non-interactive invocation would emit it otherwise,
        # which is unacceptable UX for users who don't care about
        # web at all. The system prompt's anti-substitution paragraph
        # tells the LLM to suggest ``--enable-web`` (which then
        # surfaces the setup hint contextually, only when the user
        # actually asks for web-needing info).
        return False
    api_key_value = settings.web.api_key.get_secret_value()
    web_search_provider = TavilySearchProvider(
        api_key=api_key_value,
        timeout_seconds=settings.web.fetch_timeout_seconds,
    )
    registry.register(WebSearch(provider=web_search_provider))
    registry.register(
        WebFetch(
            timeout_seconds=settings.web.fetch_timeout_seconds,
            max_bytes=settings.web.fetch_max_bytes,
            user_agent=f"OpenHarness/{__version__} (+webfetch)",
        )
    )
    return True


def _load_plugin_catalogs(
    enable_plugins: bool,
    plugins_dir: Path | None = None,
) -> LoadedPluginCatalogs:
    """Discover + fan-out plugins under ``~/.openharness/plugins/`` (P9-T3).

    Returns empty :class:`LoadedPluginCatalogs` when ``enable_plugins``
    is False (default safety — plugins ship arbitrary Python via hook
    modules, must be explicitly opted in per decisions/24 D27.4).

    When True, instantiates a :class:`PluginLoader`, discovers manifest
    files, and fans out into namespaced component catalogs ready to be
    wrapped via :class:`LayeredStore` (commands/skills/bundles), merged
    into ``plugin_hook_catalog`` (hooks), or appended to the MCP server
    list (mcp_servers).

    Emits the ``plugins_loaded`` observability event for trace-stitching.
    """
    if not enable_plugins:
        return LoadedPluginCatalogs()
    target_dir = (
        plugins_dir if plugins_dir is not None else (Path.home() / ".openharness" / "plugins")
    )
    loader = PluginLoader(target_dir)
    manifests = loader.discover()
    catalogs = loader.fan_out(manifests)
    logger = get_logger("plugins")
    logger.info(
        "plugins_loaded",
        count=len(manifests),
        names=sorted(manifests.keys()),
        plugins_dir=str(target_dir),
    )
    return catalogs


# --------------------------------------------------------------------------- #
# Core async entry point                                                      #
# --------------------------------------------------------------------------- #


# P16-T1 (D36.8): MEMORY.md hard line cap for the system-prompt injection
# path. Cap is a token-budget invariant, not a tunable — see
# decisions/36-phase-16-memory-pivot-boundary.md.
#
# (Phase 10's ``_build_memory_manifest_for_query`` + ``_load_memory_entrypoint``
# byte-cap helpers were removed in Phase 16 T2 per D36.7 — harness-side
# relevance ranking is superseded by LLM-self-selects from the MEMORY.md
# index injected via :func:`_load_memory_index_for_injection`.)
_MEMORY_INDEX_MAX_LINES = 200


def _load_memory_index_for_injection(memory_dir: Path) -> str | None:
    """Read MEMORY.md for D36.11 system-prompt injection — P16-T1.

    Behavior per D36.8 / D36.11:

    - File doesn't exist → return ``None`` (caller injects placeholder).
      No log; absent MEMORY.md is the normal cold-start state.
    - OSError / decode error → WARN log ``memory_index_read_failed``,
      return ``None`` (caller injects placeholder so session start
      does not fail).
    - File exists + ≤ 200 lines → return full content.
    - File exists + > 200 lines → WARN log ``memory_index_truncated``,
      return first 200 lines.

    The Phase 10 ``MemoryManifest`` byte-cap flow it superseded was
    retired in P16-T2 (D36.7). D36.8 explicitly switched the cap from
    bytes to lines for this path.
    """
    log = get_logger("memory")
    entrypoint_path = memory_dir / "MEMORY.md"
    if not entrypoint_path.exists():
        return None
    try:
        content = entrypoint_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning(
            "memory_index_read_failed",
            path=str(entrypoint_path),
            error=str(exc),
        )
        return None
    lines = content.splitlines()
    if len(lines) > _MEMORY_INDEX_MAX_LINES:
        log.warning(
            "memory_index_truncated",
            path=str(entrypoint_path),
            total_lines=len(lines),
            kept_lines=_MEMORY_INDEX_MAX_LINES,
        )
        lines = lines[:_MEMORY_INDEX_MAX_LINES]
    return "\n".join(lines)


@dataclass(frozen=True)
class AskOutcome:
    """Unified result from a single ``_run_ask`` invocation.

    ``print_result`` is populated by the JSON branch so an isolated run can
    attach its worktree metadata after the run-scoped context closes."""

    stop_reason: str | None
    print_result: PrintResult | None = None


@dataclass(frozen=True)
class SandboxConfig:
    """loop-runtime Track B T0: resolved sandbox configuration -- extracted
    out of ``_run_ask`` so ``services/run_session.py`` can resolve it once
    per run instead of once per attempt."""

    enabled: bool
    backend: SandboxBackendName
    image: str
    memory: str
    cpus: float
    runtime: str


def _resolve_sandbox_config(
    settings: Settings,
    *,
    sandbox_override: bool | None,
    sandbox_backend_override: SandboxBackendName | None,
    sandbox_image_override: str | None,
    sandbox_memory_override: str | None,
    sandbox_cpus_override: float | None,
    sandbox_runtime_override: str | None,
) -> SandboxConfig:
    """Resolve implementation-only backend settings; never permission intent."""
    return SandboxConfig(
        enabled=sandbox_override if sandbox_override is not None else settings.sandbox_enabled,
        backend=sandbox_backend_override or settings.sandbox_backend,
        image=sandbox_image_override or settings.sandbox_image,
        memory=sandbox_memory_override or settings.sandbox_memory,
        cpus=sandbox_cpus_override if sandbox_cpus_override is not None else settings.sandbox_cpus,
        runtime=sandbox_runtime_override or settings.sandbox_runtime,
    )


def _append_project_instructions(
    base_prompt: str,
    project_instructions_content: str | None,
) -> str:
    if project_instructions_content is None:
        return base_prompt
    return f"{base_prompt}\n\n{project_instructions_content}"


def _resolve_permission_profile(
    settings: Settings,
) -> RuntimePermissionProfile:
    """Return the session's single canonical authorization intent."""
    return settings.permission_profile


async def _open_sandbox_session(
    *,
    stack: contextlib.AsyncExitStack,
    config: SandboxConfig,
    profile: RuntimePermissionProfile,
    cwd: Path,
    pids: int,
) -> tuple[RuntimePermissionProfile, SandboxSession]:
    """Compile intent, open a backend, and verify its reported facts."""
    backend: SandboxBackend
    if config.backend == "seatbelt":
        backend = SeatbeltBackend(cwd=cwd)
    else:
        backend = DockerCommandBackend(
            cwd=cwd,
            image=config.image,
            memory=config.memory,
            cpus=config.cpus,
            pids=pids,
            runtime=config.runtime,
        )

    support = backend.preflight(profile)
    if not support.supported:
        features = ", ".join(support.unsupported_features) or "unknown"
        raise SandboxUnavailableError(
            f"{support.backend} cannot enforce canonical profile before open: {features}"
        )
    session = await backend.open(profile)
    boundary = session.boundary
    if (
        boundary.verification is not BoundaryVerification.VERIFIED
        or boundary.profile_fingerprint != profile.fingerprint
    ):
        await session.close()
        raise SandboxUnavailableError(
            f"{boundary.backend} did not return a verified boundary for the active profile"
        )
    if config.backend == "seatbelt":
        required = (
            ExecutionEffect.COMMAND,
            ExecutionEffect.FILE_READ,
            ExecutionEffect.FILE_WRITE,
            ExecutionEffect.FILE_SEARCH,
        )
        missing = tuple(effect.value for effect in required if not boundary.covers(effect))
        if missing:
            await session.close()
            raise SandboxUnavailableError(
                "seatbelt boundary is missing required local data-plane effects: "
                + ", ".join(missing)
            )
    overlay_session = OneShotOverlaySession(backend=backend, profile=profile, base=session)
    stack.push_async_callback(overlay_session.close)
    return profile, overlay_session


async def _run_ask(
    prompt: str,
    *,
    model_override: str | None,
    max_tokens: int,
    reviewer_posture_override: ReviewerPosture | None,
    execution_posture_override: ExecutionPosture | None,
    log_level_override: LogLevel | None,
    log_format_override: LogFormat | None,
    tool_result_cap_override: int | None,
    auto_truncate_override: bool | None,
    no_skills: bool = False,
    no_commands: bool = False,
    sandbox_override: bool | None = None,
    sandbox_backend_override: SandboxBackendName | None = None,
    sandbox_image_override: str | None = None,
    sandbox_memory_override: str | None = None,
    sandbox_cpus_override: float | None = None,
    sandbox_runtime_override: str | None = None,
    enable_plugin_hooks_override: bool | None = None,
    enable_plugins_override: bool | None = None,
    enable_memory_override: bool | None = None,
    enable_web_override: bool | None = None,
    compact_threshold_override: float | None = None,
    no_auto_compact: bool = False,
    resume: bool = False,
    resume_id: str | None = None,
    llm_focus_state_override: bool | None = None,
    output_format: OutputFormat = "text",
    print_mode: bool = False,
    cwd_override: Path | None = None,
    suppress_echo: bool = False,
    max_turns: int = 20,
) -> AskOutcome:
    """Build the QueryContext, run the loop, render the events.

    Returns the terminal ``stop_reason`` of the final assistant turn (or
    ``None`` if the run produced no completion event) so the synchronous
    Typer command can map run-level outcome → exit code in print mode
    (loop-runtime L1 T2). ``end_turn`` is a clean finish; anything else
    (e.g. ``max_tokens``) means the run stopped without completing.

    Not exception-handling aware -- the synchronous Typer command wraps
    this and translates exceptions into user-facing exit codes.
    """
    settings = _load_settings()
    model = model_override or settings.model
    reviewer_posture = reviewer_posture_override or settings.reviewer_posture
    execution_posture = execution_posture_override or settings.execution_posture
    log_level = log_level_override or settings.log_level
    log_format = log_format_override or settings.log_format
    tool_result_cap = (
        tool_result_cap_override
        if tool_result_cap_override is not None
        else settings.tool_result_cap
    )
    auto_truncate = (
        auto_truncate_override if auto_truncate_override is not None else settings.auto_truncate
    )
    # P7b-T2 (Track B T0: extracted to _resolve_sandbox_config so
    # services/run_session.py can resolve it once per run instead of once
    # per attempt): sandbox configuration — CLI flag overrides Settings.
    sandbox_config = _resolve_sandbox_config(
        settings,
        sandbox_override=sandbox_override,
        sandbox_backend_override=sandbox_backend_override,
        sandbox_image_override=sandbox_image_override,
        sandbox_memory_override=sandbox_memory_override,
        sandbox_cpus_override=sandbox_cpus_override,
        sandbox_runtime_override=sandbox_runtime_override,
    )
    sandbox_enabled = sandbox_config.enabled
    # P5e-T3: plugin hook discovery is opt-in. CLI flag overrides
    # Settings. When OFF, ``discover_plugin_hooks()`` is never called
    # and bundle ``hooks:`` resolves only against BUILTIN_HOOKS — even
    # if plugin packages are installed.
    enable_plugin_hooks = (
        enable_plugin_hooks_override
        if enable_plugin_hooks_override is not None
        else settings.enable_plugin_hooks
    )
    # P9-T3 (decisions/24 D27.4): plugin discovery is opt-in. CLI flag
    # overrides Settings. When OFF, plugin components are not loaded
    # into the running registry — but ``oh inspect plugins list`` still
    # works for read-only introspection.
    enable_plugins = (
        enable_plugins_override if enable_plugins_override is not None else settings.enable_plugins
    )
    # Durable memory is independent from target-project instructions.
    enable_memory = (
        enable_memory_override if enable_memory_override is not None else settings.enable_memory
    )
    # P14-T4 + Phase 14.5 (revised D29.3): web-tools default ON,
    # graceful no-key degrade. ``settings.web.enabled`` now defaults
    # True (industry harness convention). When the user has no
    # OPENHARNESS_WEB__API_KEY, ``_maybe_register_web_tools`` skips
    # registration silently and returns False; the system prompt
    # falls back to the D29.6 anti-substitution paragraph (so the
    # LLM is told explicitly NOT to Grep local files). Explicit
    # ``--enable-web`` + no key still hard-fails — user asked.
    if enable_web_override is not None:
        explicit_web_flag = True
        enable_web = enable_web_override
    else:
        explicit_web_flag = False
        enable_web = settings.web.enabled
    # P11-T5 (refined P17-T2 D37.3): compact CLI flags fold into
    # nested Settings. ``--no-auto-compact`` flips
    # ``compact.enabled=False`` regardless of env.
    # ``--compact-threshold 0.5`` overrides
    # ``compact.threshold_ratio``. Other CompactSettings knobs use
    # env-only override (``OPENHARNESS_COMPACT__FULL_COMPACT_TIMEOUT_S``
    # etc.) — no CLI flag for every field to keep the surface area
    # small. The Phase 11 extraction settings + ``--no-extract`` flag
    # were retired in Phase 17 D37.3.
    compact_enabled = settings.compact.enabled and not no_auto_compact
    compact_threshold_ratio = (
        compact_threshold_override
        if compact_threshold_override is not None
        else settings.compact.threshold_ratio
    )

    # P3-T5.5e:configure logging FIRST so any subsequent error path
    # (client build / system prompt build) is observable.
    configure_logging(level=log_level, format=log_format)

    # P9-T3: Discover + fan out plugin catalogs BEFORE any of the 5
    # extension subsystems are constructed, so each store can be wrapped
    # with :class:`LayeredStore` immediately. Empty catalogs returned
    # when ``enable_plugins`` is False — LayeredStore over empty dict
    # is functionally identical to the base store.
    plugin_catalogs = _load_plugin_catalogs(enable_plugins)

    # P5b-T3: Slash command expansion. Runs BEFORE the rest of bootstrap
    # so the resolved prompt flows through normally — engine never sees
    # the original ``/cmd ...`` form. Convention-driven storage per
    # decisions/14 C2 (same shape as Skills L2).
    #
    # ``--no-commands`` is a hard bypass: command resolution is NOT
    # called, so the slash prefix flows verbatim to the LLM. Calling
    # ``resolve_command_invocation`` with an :class:`EmptyCommandStore`
    # would instead raise :class:`UnknownCommandError` on any ``/<x>``
    # prompt — wrong behavior for the escape hatch (which is meant for
    # prompts that legitimately start with ``/``).
    #
    # ``UnknownCommandError`` from the live path is intentionally NOT
    # caught here — it propagates to the private synchronous command's
    # except chain so the user-facing error UX (with available catalog)
    # renders before any LLM call is attempted.
    #
    # P5d-T4: ``resolve_command_invocation`` also surfaces the resolved
    # ``Command`` so we can read ``Command.mode`` for ModeBundle loading
    # (Phase 5d's first cross-layer composition tenant).
    #
    # P9-T3: wrap with :class:`LayeredStore` so plugin-namespaced
    # commands (e.g., ``/my-plugin__deploy``) resolve via the same path.
    invoked_command = None
    if not no_commands:
        base_command_store = FilesystemCommandStore(
            global_dir=Path.home() / ".openharness" / "commands",
            project_dir=(cwd_override or Path.cwd()) / ".openharness" / "commands",
        )
        command_store = LayeredStore(
            base=base_command_store,
            plugin_catalog=plugin_catalogs.commands,
        )
        prompt, invoked_command = resolve_command_invocation(prompt, command_store)

    # P5d-T4: ModeBundle load. If the resolved command has a ``mode:``
    # field, load the named bundle from the same global/project layered
    # store as commands + skills. Skip-not-fail does NOT apply here —
    # a slash command that names a nonexistent bundle is a hard error
    # (:class:`UnknownBundleError`) because the user explicitly asked
    # for the bundle's mode. The actual ``apply_bundle_to_context``
    # call lives below, AFTER the base ToolRegistry + HookRegistry are
    # assembled, so the bundle composes against the fully-built
    # primitives (including MCP adapters + LoadSkill).
    #
    # P9-T3: bundle_store is also LayeredStore-wrapped so plugin bundles
    # (``my-plugin__ops-mode``) resolve identically.
    bundle = None
    if invoked_command is not None and invoked_command.mode is not None:
        base_bundle_store = FilesystemBundleStore(
            global_dir=Path.home() / ".openharness" / "bundles",
            project_dir=(cwd_override or Path.cwd()) / ".openharness" / "bundles",
        )
        bundle_store = LayeredStore(
            base=base_bundle_store,
            plugin_catalog=plugin_catalogs.bundles,
        )
        bundle = bundle_store.get(invoked_command.mode)
        if bundle is None:
            available = sorted(bundle_store.discover().keys())
            raise UnknownBundleError(invoked_command.mode, available=available)

    # P5e-T3 + P5f-T2: Plugin hook discovery. The flag enables BOTH
    # discovery sources (per D22.2 — same trust boundary):
    #
    # 1. Entry-point plugins (Phase 5e): packages declaring
    #    ``[project.entry-points."openharness.hooks"]`` in their
    #    pyproject.toml.
    # 2. Filesystem hook plugins (Phase 5f): ``.py`` files dropped
    #    under ``~/.openharness/hooks/`` (global) +
    #    ``<cwd>/.openharness/hooks/`` (project).
    #
    # Merge order (D22.4): entry-point catalog first, filesystem
    # second, first-wins on collision. Entry-point plugins shadow
    # filesystem plugins on same name because packaged plugins are a
    # stronger statement of intent (someone authored a pyproject.toml).
    plugin_hook_catalog: dict[str, HookSpec] = {}
    if enable_plugin_hooks:
        plugin_hook_catalog.update(discover_plugin_hooks())
        fs_catalog = discover_filesystem_hook_plugins(
            global_dir=Path.home() / ".openharness" / "hooks",
            project_dir=(cwd_override or Path.cwd()) / ".openharness" / "hooks",
        )
        for fs_name, fs_spec in fs_catalog.items():
            if fs_name not in plugin_hook_catalog:
                plugin_hook_catalog[fs_name] = fs_spec
            # else: entry-point won; silent (no log noise — the
            # filesystem-discovered spec hasn't been "skipped", just
            # never registered because the entry-point version is
            # equivalent or preferred).
    # P9-T3: Plugin-shipped hooks (declared in manifest's ``hooks:``
    # field) merge into the same catalog under namespaced keys
    # (``my-plugin__audit``). Plugin hooks live in plugin_catalogs.hooks
    # only when enable_plugins is True (empty dict otherwise — no-op).
    for ns_name, hook_spec in plugin_catalogs.hooks.items():
        if ns_name not in plugin_hook_catalog:
            plugin_hook_catalog[ns_name] = hook_spec
        # else: collision between plugin-shipped hook and another
        # discovery source — first-wins, like the entry-point /
        # filesystem precedence above.

    client = _build_client(settings)
    registry = create_default_tool_registry()

    # P5-T5: bootstrap MCP servers from Settings.mcp_servers (D15.2). Pool
    # lives for the lifetime of the query — adapters' McpClient references
    # must stay valid through run_query. Empty config (no MCP servers) is
    # a no-op pool.
    #
    # P9-T3: append plugin-shipped MCP servers (already namespaced via
    # ``<plugin>__<server>`` by PluginLoader.fan_out). User's
    # ``trusted_mcp_servers`` whitelist still applies — to trust a
    # plugin server, add the namespaced name explicitly.
    combined_mcp_servers = settings.mcp_servers + plugin_catalogs.mcp_servers
    pool = McpClientPool(
        combined_mcp_servers,
        trusted_servers=settings.trusted_mcp_servers,
        sandbox_cwd=cwd_override or Path.cwd(),
    )
    # P7b-T2: AsyncExitStack lets us conditionally enter SandboxExecution
    # without duplicating the body. ``--sandbox`` enters; otherwise the
    # default HostExecution singleton is used (matches Phase 7a default).
    async with pool, contextlib.AsyncExitStack() as stack:
        for adapter in pool.adapters:
            registry.register(adapter)

        # P14-T4 + Phase 14.5: conditional registration of web tools.
        # Returns the **effective** web state — False when web was
        # requested but no API key is set (graceful degrade). The
        # value flows into ``build_system_prompt`` so the prompt
        # matches the actual catalog.
        effective_web = _maybe_register_web_tools(
            registry, enable_web=enable_web, settings=settings, explicit_flag=explicit_web_flag
        )

        # P5c-T3: Skills bootstrap. Convention-driven storage (no Settings
        # field per decisions/12 L2):global = ~/.openharness/skills/,
        # project = cwd/.openharness/skills/, project overrides global on
        # same name. ``--no-skills`` swaps in :class:`EmptySkillStore` for
        # testing / debug; default scans both layers.
        env = detect_environment()
        if cwd_override is not None:
            env = replace(env, cwd=cwd_override)
        base_skill_store: SkillStore
        if no_skills:
            base_skill_store = EmptySkillStore()
        else:
            base_skill_store = FilesystemSkillStore(
                global_dir=Path.home() / ".openharness" / "skills",
                project_dir=env.cwd / ".openharness" / "skills",
            )
        # P9-T3: LayeredStore-wrap so plugin-namespaced skills
        # (``my-plugin__react-testing``) flow through the same
        # ``LoadSkillTool`` lookup + ``build_system_prompt`` catalog
        # injection. Plugin catalog empty → LayeredStore is a
        # transparent passthrough.
        skill_store: SkillStore = LayeredStore(
            base=base_skill_store,
            plugin_catalog=plugin_catalogs.skills,
        )
        # Register LoadSkill iff at least one skill is discovered — keeps
        # the tool catalog clean when no skills are authored. Discovery
        # warms the store's cache,so subsequent ``build_system_prompt``
        # and per-call ``store.get`` are O(1) lookups.
        if skill_store.discover():
            registry.register(LoadSkillTool(skill_store))

        # Durable project memory is a typed trusted-control surface. The
        # runtime owns its private storage directory and index; model-visible
        # tools operate on semantic records rather than filesystem paths.
        memory_dir: Path | None = None
        memory_store: FilesystemMemoryStore | None = None
        if enable_memory:
            memory_dir = ensure_project_memory_dir(env.cwd)
            memory_store = FilesystemMemoryStore(project_dir=memory_dir)
            memory_store.discover()
            register_memory_tools(registry, memory_store)

        # P4-T4.4b:Layer 1 default registration. When ``auto_truncate`` is on
        # AND the cap is positive, the framework auto-registers
        # ``TruncateToolResultHook`` so users get sensible compaction without
        # touching code. Users disable via ``--no-auto-truncate`` or
        # ``OPENHARNESS_AUTO_TRUNCATE=false``;Layer 2 reactive guard remains
        # in either case.
        hook_registry = HookRegistry()
        if auto_truncate and tool_result_cap > 0:
            hook_registry.register(
                "PostToolUse",
                TruncateToolResultHook(cap_tokens=tool_result_cap, model=model),
            )

        # P5d-T4: bundle composition. Applied AFTER base registry +
        # base hook_registry are assembled so the bundle composes
        # against the fully-built primitives (MCP adapters + LoadSkill
        # in registry; auto-truncate hook in hook_registry). Layer 2
        # (whitelist) wraps registry; Layer 3 (hooks) registers into a clone of
        # hook_registry. Layer 1 (system_prompt) handled below — bundle
        # overrides ours, else we build against the EFFECTIVE registry
        # so the LLM's tool catalog reflects the whitelist.
        effective_registry = registry
        effective_hook_registry = hook_registry
        if bundle is not None:
            application = apply_bundle_to_context(
                bundle=bundle,
                tool_registry=registry,
                hook_registry=hook_registry,
                system_prompt="",  # placeholder; prompt logic lives below
                plugin_hook_catalog=plugin_hook_catalog,
            )
            effective_registry = application.tool_registry
            effective_hook_registry = application.hook_registry

        project_instructions_content = (
            load_project_instructions(
                env.cwd,
                max_chars_per_file=settings.max_project_instruction_chars,
            )
            if settings.enable_project_instructions
            else None
        )

        # System prompt is built AFTER MCP tools + LoadSkill register so
        # the LLM's tool catalog includes them. Skill catalog is injected
        # by ``build_system_prompt`` itself via the ``skill_store`` kwarg.
        # A bundle may replace the harness base prompt, but target-project
        # instructions remain a separate project-owned context layer.
        # Otherwise build against the effective registry and compose project
        # instructions with the optional durable-memory section.
        if bundle is not None and bundle.system_prompt is not None:
            system_prompt = _append_project_instructions(
                bundle.system_prompt,
                project_instructions_content,
            )
        else:
            memory_index_content: str | None = None
            if memory_store is not None and memory_dir is not None:
                # P16-T2 (D36.7 / D36.11): the only memory-side
                # computation production needs is reading MEMORY.md for
                # injection. Phase 10's relevance ranking + use_count
                # bookkeeping was retired alongside extraction (D36.9).
                memory_index_content = memory_store.render_index(max_entries=200)
            system_prompt = build_system_prompt(
                effective_registry.to_api_schema(),
                env,
                skill_store=skill_store,
                project_instructions_content=project_instructions_content,
                memory_dir=memory_dir,
                memory_index_content=memory_index_content,
                web_enabled=effective_web,
            )

        # A sandboxed query consumes the verified session contract.  All
        # local core tools prefer ``sandbox_session``; the legacy execution
        # environment remains host only as an inactive compatibility field.
        execution_env: ExecutionEnvironment = _HOST_EXECUTION
        active_profile = _resolve_permission_profile(settings)
        sandbox_session: SandboxSession | None = None
        if sandbox_enabled:
            active_profile, sandbox_session = await _open_sandbox_session(
                stack=stack,
                config=sandbox_config,
                profile=active_profile,
                cwd=env.cwd,
                pids=settings.sandbox_pids,
            )
        permission_reviewer = (
            LlmPermissionReviewer(
                api_client=client,
                model=settings.permission_reviewer_model or model,
            )
            if reviewer_posture is ReviewerPosture.AUTO and settings.permission_auto_review
            else None
        )
        permission_runtime = PermissionRuntime(
            profile=active_profile,
            boundary=sandbox_session.boundary if sandbox_session is not None else None,
            reviewer=permission_reviewer,
        )

        context = QueryContext(
            api_client=client,
            tool_registry=effective_registry,
            action_deny_policy=ConfiguredActionDenyPolicy(),
            hook_registry=effective_hook_registry,
            system_prompt=system_prompt,
            cwd=env.cwd,
            model=model,
            max_tokens=max_tokens,
            # --max-turns (D40 M1 小批 finding): the loop cap was hard-fixed
            # at the dataclass default while LoopLimitExceeded's message
            # already promised this knob.
            max_turns=max_turns,
            reviewer_posture=reviewer_posture,
            execution_posture=execution_posture,
            autonomous=(reviewer_posture is ReviewerPosture.AUTO or print_mode),
            authorization_context=(prompt,),
            skill_store=skill_store,
            # P6-T1 (D16.5): propagate the sub-agent recursion cap from
            # Settings into the top-level QueryContext. ``agent_depth=0``
            # is the dataclass default for top-level invocations; each
            # ``SpawnAgent.execute`` builds the sub-context via
            # ``dataclasses.replace(parent, agent_depth=parent.agent_depth + 1)``.
            max_agent_depth=settings.max_agent_depth,
            # P7b-T2: substrate the BashTool delegates to. Default
            # HostExecution singleton when ``--sandbox`` is off.
            execution_env=execution_env,
            sandbox_session=sandbox_session,
            runtime_permission_profile=active_profile,
            enforced_boundary=(sandbox_session.boundary if sandbox_session is not None else None),
            permission_runtime=permission_runtime,
            # Compact and project-memory wiring.
            compact_enabled=compact_enabled,
            compact_threshold_ratio=compact_threshold_ratio,
            compact_preserve_recent_messages=settings.compact.preserve_recent_messages,
            compact_full_max_tokens=settings.compact.full_compact_max_tokens,
            compact_full_timeout_s=settings.compact.full_compact_timeout_s,
            memory_store=memory_store,
            # P12-T3 (D30.8): per-turn snapshot writer. ``--no-resume`` is the user-side
            # READ opt-out; writing stays on by default so a snapshot
            # always exists when the user later decides to ``--resume``.
            snapshot_enabled=settings.snapshot.enabled,
            snapshot_max_age_warn_days=settings.snapshot.max_age_warn_days,
            snapshot_history_max_count=settings.snapshot.history.max_count,
            snapshot_history_max_age_days=settings.snapshot.history.max_age_days,
            llm_focus_state_enabled=(
                llm_focus_state_override
                if llm_focus_state_override is not None
                else settings.snapshot.llm_focus_state
            ),
            llm_focus_state_model=settings.snapshot.focus_state_model,
        )

        # P12-T5 (D30.4): --resume loads the cwd's snapshot and
        # rebuilds the context's agent-state (model / max_tokens /
        # system_prompt / messages). Runtime state
        # (registries / hooks / execution_env / memory_store) stays
        # what we just built above — D30.7's split.
        #
        # On snapshot-not-found, _load_resume_snapshot returns None
        # (no-arg --resume warns + starts fresh) or exits 1 (explicit
        # --resume-id mismatch). Other staleness errors exit 1.
        initial_messages: list[ConversationMessage]
        if resume:
            snapshot = _load_resume_snapshot(env.cwd, resume_id=resume_id)
            if snapshot is not None:
                context, prior_messages = QueryContext.from_snapshot(
                    snapshot,
                    api_client=client,
                    tool_registry=effective_registry,
                    action_deny_policy=context.action_deny_policy,
                    reviewer_posture=reviewer_posture,
                    execution_posture=execution_posture,
                    autonomous=context.autonomous,
                    cwd=env.cwd,
                    hook_registry=effective_hook_registry,
                    execution_env=execution_env,
                    sandbox_session=sandbox_session,
                    runtime_permission_profile=active_profile,
                    enforced_boundary=(
                        sandbox_session.boundary if sandbox_session is not None else None
                    ),
                    permission_runtime=permission_runtime,
                    authorization_context=(prompt,),
                    skill_store=skill_store,
                    memory_store=memory_store,
                    snapshot_enabled=context.snapshot_enabled,
                    snapshot_max_age_warn_days=context.snapshot_max_age_warn_days,
                    snapshot_history_max_count=context.snapshot_history_max_count,
                    snapshot_history_max_age_days=context.snapshot_history_max_age_days,
                    llm_focus_state_enabled=context.llm_focus_state_enabled,
                    llm_focus_state_model=context.llm_focus_state_model,
                    compact_enabled=compact_enabled,
                    compact_threshold_ratio=compact_threshold_ratio,
                    compact_preserve_recent_messages=(settings.compact.preserve_recent_messages),
                    compact_full_max_tokens=settings.compact.full_compact_max_tokens,
                    compact_full_timeout_s=settings.compact.full_compact_timeout_s,
                    max_turns=context.max_turns,
                    max_agent_depth=settings.max_agent_depth,
                )
                initial_messages = [
                    *prior_messages,
                    ConversationMessage(role="user", content=[TextBlock(text=prompt)]),
                ]
            else:
                initial_messages = [
                    ConversationMessage(role="user", content=[TextBlock(text=prompt)]),
                ]
        else:
            initial_messages = [
                ConversationMessage(role="user", content=[TextBlock(text=prompt)]),
            ]
        events = run_query(initial_messages, context)
        # loop-runtime L1: session_id is a freshly-minted v1 run_id (one per
        # -p run). It does NOT yet correlate with the engine's internal log
        # run_id (threading that needs engine changes — out of scope for L1).
        if output_format == "json":
            import json

            # T3: drain silently, aggregate, emit ONE result object on stdout.
            # Run-level exit code still maps from stop_reason (T2), so a
            # non-end_turn run prints the json AND exits non-zero.
            collected = await collect_print_result(events)
            if not suppress_echo:
                typer.echo(json.dumps(build_result_obj(collected, session_id=new_run_id())))
            return AskOutcome(
                stop_reason=collected.stop_reason,
                print_result=collected,
            )
        if output_format == "stream-json":
            # T4: one JSON object per event, terminated by a result object.
            stream_stop_reason = await render_stream_json(
                events,
                session_id=new_run_id(),
            )
            return AskOutcome(stop_reason=stream_stop_reason)
        final_event = await render_stream(events)
        return AskOutcome(stop_reason=final_event.stop_reason if final_event is not None else None)


def _build_run_json_field(session: RunSession | None) -> dict[str, Any] | None:
    """Shape of the optional isolated-run metadata in headless JSON output."""
    if session is None:
        return None
    return {
        "run_id": session.run_id,
        "worktree_path": str(session.worktree.path) if session.worktree is not None else None,
        "branch_name": session.worktree.branch if session.worktree is not None else None,
        "status": session.status,
    }


def _attach_run_json_field(result_obj: dict[str, Any], session: RunSession | None) -> None:
    """Fold isolated-run metadata into a headless JSON result."""
    run_info = _build_run_json_field(session)
    if run_info is not None:
        result_obj["run"] = run_info


async def _dispatch_ask(
    prompt: str,
    *,
    isolate: bool,
    output_format: OutputFormat,
    print_mode: bool,
    common_run_ask_kwargs: dict[str, Any],
) -> tuple[AskOutcome, RunSession | None]:
    """Run one headless request, optionally inside an isolated worktree."""
    if not isolate:
        outcome = await _run_ask(
            prompt,
            output_format=output_format,
            print_mode=print_mode,
            **common_run_ask_kwargs,
        )
        return outcome, None

    async with open_run_session(
        cwd=Path.cwd(),
        isolate=True,
    ) as session:
        assert session is not None
        session_kwargs = dict(common_run_ask_kwargs)
        session_kwargs["cwd_override"] = session.cwd_override
        outcome = await _run_ask(
            prompt,
            output_format=output_format,
            print_mode=print_mode,
            suppress_echo=True,
            **session_kwargs,
        )
        session.status = "completed" if outcome.stop_reason == "end_turn" else "failed"

    return outcome, session


# --------------------------------------------------------------------------- #
# Interactive REPL                                                            #
# --------------------------------------------------------------------------- #


def _split_slash_invocation(prompt: str) -> tuple[str, str]:
    """Split ``"/name args..."`` into ``("name", "args...")``.

    Mirrors ``commands.expand._split_invocation`` but lives here so the
    REPL resolver can peek at the name BEFORE deciding between
    CommandStore and SkillStore (D38.1). Keeping a tiny local copy
    avoids importing a private symbol from the commands package.
    """
    body = prompt[1:]  # strip leading "/"
    parts = body.split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _emit_skill_catalog(skill_store: SkillStore) -> None:
    """Render a compact human catalog; full metadata loads with the Skill."""
    catalog = skill_store.discover()
    if not catalog:
        typer.echo("(no skills installed)")
        return
    names = sorted(catalog.keys())
    width = max(len(n) for n in names)
    # Keep the directory scan-friendly even when a Claude Code-format Skill
    # uses a YAML block scalar for routing details. The complete description
    # remains in the Skill object and the model-facing catalog; only this
    # human menu is summarized.
    description_width = max(24, 100 - width - 4)
    for name in names:
        skill = catalog[name]
        description = " ".join(skill.description.split())
        if len(description) > description_width:
            description = f"{description[: description_width - 1].rstrip()}…"
        typer.echo(f"  {name.ljust(width)}  {description}")


def _emit_memory_catalog(memory_store: MemoryStore | None, memory_dir: Path | None) -> None:
    """Render ``/memory`` output — alphabetical 3-column
    ``<name> <type> <description>``, matching ``oh state memory list``
    text format exactly so users see the same data whether they query
    from outside or inside the REPL.

    First line is a ``(memory dir: <path>)`` header — surfaces the
    per-cwd hashed dir name (``get_project_memory_dir`` produces
    ``<basename>-<sha1(resolved_cwd)[:12]>``) so a wrong-cwd run shows
    immediately rather than rendering an unrelated project's memories
    without explanation. Skipped when the subsystem is off (no dir to
    show).

    Missing memory subsystem (``--no-enable-memory`` or
    ``settings.enable_memory=False``) → ``(memory subsystem disabled)``.
    Empty store → ``(no memories yet)`` (matches ``oh state memory list``
    empty branch). D37.4 column widths.
    """
    if memory_store is None:
        typer.echo("(memory subsystem disabled)")
        return
    if memory_dir is not None:
        typer.echo(f"(memory dir: {memory_dir})")
    memories = list(memory_store.discover().values())
    if not memories:
        typer.echo("(no memories yet)")
        return
    memories.sort(key=lambda m: m.name.lower())
    name_width = min(_LIST_NAME_MAX, max(len(m.name) for m in memories)) + 2
    type_width = max(len(m.type.value) for m in memories) + 2
    for m in memories:
        name_field = _truncate(m.name, _LIST_NAME_MAX)
        type_field = m.type.value if m.type is not None else "(unknown)"
        if m.description and m.description.strip():
            desc_field = _truncate(m.description, _LIST_DESCRIPTION_MAX)
        else:
            desc_field = "(no description)"
        typer.echo(f"{name_field:<{name_width}}{type_field:<{type_width}}{desc_field}")


_CHAT_HELP_TEXT = """\
OpenHarness — multi-turn REPL commands:
  /exit, /quit       leave the REPL
  /clear             reset conversation history (keeps tools + mode)
  /compact           force full LLM-based compaction of the conversation
                     (Phase 11 D29.6) — replaces history with a six-section
                     handoff regardless of token threshold
  /plan [prompt]     enter plan mode (D47) — edits/commands are clamped
                     to read-only exploration; an approval menu appears
                     after each completed reply; parked permissions pause
                     planning before approval
  /goal <condition>  set a session goal (D48) — an independent checker
                     evaluates the conversation after each reply and
                     auto-continues turns until the condition holds;
                     bare /goal shows status, /goal clear stops early
  /permissions       show configured permission intent, verified runtime
                     boundary facts, and registered tool execution domains
  /approve [id]      approve a postponed exact request and continue
  /deny [id]         deny a postponed exact request and continue
  /resume            consume an externally recorded, unconsumed decision
  /skills            list available skills (Phase 18 D38.4)
  /memory            list memories in this project's memory store
  /help              show this message

User-authored slash commands (Phase 5b) work too — type ``/name args``
to expand a command. Bundles (Phase 5d) resolve on the FIRST message
of the session and persist for the rest. Phase 18 (D38.1) extends the
resolver: ``/<name>`` falls through to SkillStore if no Command matches,
synthesizing a LoadSkill envelope so the skill body lands in
conversation history without an LLM round-trip.

Use Ctrl+D (EOF) to exit; Ctrl+C cancels the current input line."""


async def _run_chat(
    *,
    initial_prompt: str | None = None,
    model_override: str | None,
    max_tokens: int,
    reviewer_posture_override: ReviewerPosture | None,
    execution_posture_override: ExecutionPosture | None,
    log_level_override: LogLevel | None,
    log_format_override: LogFormat | None,
    tool_result_cap_override: int | None,
    auto_truncate_override: bool | None,
    no_skills: bool = False,
    no_commands: bool = False,
    sandbox_override: bool | None = None,
    sandbox_backend_override: SandboxBackendName | None = None,
    sandbox_image_override: str | None = None,
    sandbox_memory_override: str | None = None,
    sandbox_cpus_override: float | None = None,
    sandbox_runtime_override: str | None = None,
    enable_plugin_hooks_override: bool | None = None,
    enable_plugins_override: bool | None = None,
    enable_memory_override: bool | None = None,
    enable_web_override: bool | None = None,
    compact_threshold_override: float | None = None,
    no_auto_compact: bool = False,
    resume: bool = False,
    resume_id: str | None = None,
    llm_focus_state_override: bool | None = None,
    max_turns: int | None = None,
) -> None:
    """Multi-turn REPL driver — P6+-T2.

    Builds a QueryContext once, then loops on ``input(">>> ")``,
    accumulating conversation history across turns. Each turn runs the
    same ``run_query`` engine the non-interactive runtime uses; the new
    ``ConversationCompleteEvent`` exposes the post-turn message list
    which becomes the next turn's ``initial_messages``.
    """
    # Bootstrap is largely identical to ``_run_ask``. Factoring is a
    # Phase 9 polish candidate — for now, the duplication is contained
    # and tested through both commands' integration tests.
    from openharness.protocols.stream_events import (
        ApiMessageCompleteEvent,
        ConversationCompleteEvent,
        PermissionParkedEvent,
        ToolExecutionCompletedEvent,
    )

    settings = _load_settings()
    model = model_override or settings.model
    max_turns = max_turns if max_turns is not None else settings.max_turns
    reviewer_posture = reviewer_posture_override or settings.reviewer_posture
    execution_posture = execution_posture_override or settings.execution_posture
    log_level = log_level_override or settings.log_level
    log_format = log_format_override or settings.log_format
    tool_result_cap = (
        tool_result_cap_override
        if tool_result_cap_override is not None
        else settings.tool_result_cap
    )
    auto_truncate = (
        auto_truncate_override if auto_truncate_override is not None else settings.auto_truncate
    )
    sandbox_config = _resolve_sandbox_config(
        settings,
        sandbox_override=sandbox_override,
        sandbox_backend_override=sandbox_backend_override,
        sandbox_image_override=sandbox_image_override,
        sandbox_memory_override=sandbox_memory_override,
        sandbox_cpus_override=sandbox_cpus_override,
        sandbox_runtime_override=sandbox_runtime_override,
    )
    enable_plugin_hooks = (
        enable_plugin_hooks_override
        if enable_plugin_hooks_override is not None
        else settings.enable_plugin_hooks
    )
    # P9-T3 (decisions/24 D27.4): plugin discovery is opt-in. Same
    # shape as ``_run_ask``.
    enable_plugins = (
        enable_plugins_override if enable_plugins_override is not None else settings.enable_plugins
    )
    # Durable memory is independent from target-project instructions.
    enable_memory = (
        enable_memory_override if enable_memory_override is not None else settings.enable_memory
    )
    # P14-T4 + Phase 14.5 (revised D29.3): web-tools default ON.
    # Same shape as ``_run_ask``; see that function for the rationale.
    if enable_web_override is not None:
        explicit_web_flag = True
        enable_web = enable_web_override
    else:
        explicit_web_flag = False
        enable_web = settings.web.enabled
    # P11-T5: compact + extraction wiring — same shape as ``_run_ask``.
    compact_enabled = settings.compact.enabled and not no_auto_compact
    compact_threshold_ratio = (
        compact_threshold_override
        if compact_threshold_override is not None
        else settings.compact.threshold_ratio
    )

    configure_logging(level=log_level, format=log_format)

    # P9-T3: plugin catalogs discovered + faned out up front; passed
    # through to each store wrapper + hook catalog + MCP pool.
    plugin_catalogs = _load_plugin_catalogs(enable_plugins)

    plugin_hook_catalog: dict[str, HookSpec] = {}
    if enable_plugin_hooks:
        plugin_hook_catalog.update(discover_plugin_hooks())
        fs_catalog = discover_filesystem_hook_plugins(
            global_dir=Path.home() / ".openharness" / "hooks",
            project_dir=Path.cwd() / ".openharness" / "hooks",
        )
        for fs_name, fs_spec in fs_catalog.items():
            if fs_name not in plugin_hook_catalog:
                plugin_hook_catalog[fs_name] = fs_spec
    # P9-T3: merge plugin-shipped hooks (namespaced).
    for ns_name, hook_spec in plugin_catalogs.hooks.items():
        if ns_name not in plugin_hook_catalog:
            plugin_hook_catalog[ns_name] = hook_spec

    client = _build_client(settings)
    registry = create_default_tool_registry()
    # P9-T3: combine env-derived MCP servers with plugin-shipped (namespaced).
    combined_mcp_servers = settings.mcp_servers + plugin_catalogs.mcp_servers
    pool = McpClientPool(
        combined_mcp_servers,
        trusted_servers=settings.trusted_mcp_servers,
        sandbox_cwd=Path.cwd(),
    )

    async with pool, contextlib.AsyncExitStack() as stack:
        for adapter in pool.adapters:
            registry.register(adapter)

        # P14-T4 + Phase 14.5: conditional web-tools registration.
        # Returns effective web state — degrades gracefully when web
        # was requested but no API key is configured.
        effective_web = _maybe_register_web_tools(
            registry, enable_web=enable_web, settings=settings, explicit_flag=explicit_web_flag
        )

        env = detect_environment()
        base_skill_store: SkillStore
        if no_skills:
            base_skill_store = EmptySkillStore()
        else:
            base_skill_store = FilesystemSkillStore(
                global_dir=Path.home() / ".openharness" / "skills",
                project_dir=env.cwd / ".openharness" / "skills",
            )
        # P9-T3: LayeredStore wrap — empty plugin catalog → transparent.
        skill_store: SkillStore = LayeredStore(
            base=base_skill_store,
            plugin_catalog=plugin_catalogs.skills,
        )
        if skill_store.discover():
            registry.register(LoadSkillTool(skill_store))

        memory_dir: Path | None = None
        memory_store: FilesystemMemoryStore | None = None
        if enable_memory:
            memory_dir = ensure_project_memory_dir(env.cwd)
            memory_store = FilesystemMemoryStore(project_dir=memory_dir)
            memory_store.discover()
            register_memory_tools(registry, memory_store)

        hook_registry = HookRegistry()
        if auto_truncate and tool_result_cap > 0:
            hook_registry.register(
                "PostToolUse",
                TruncateToolResultHook(cap_tokens=tool_result_cap, model=model),
            )

        execution_env: ExecutionEnvironment = _HOST_EXECUTION
        active_profile = _resolve_permission_profile(settings)
        sandbox_session: SandboxSession | None = None
        if sandbox_config.enabled:
            active_profile, sandbox_session = await _open_sandbox_session(
                stack=stack,
                config=sandbox_config,
                profile=active_profile,
                cwd=env.cwd,
                pids=settings.sandbox_pids,
            )
        permission_reviewer = (
            LlmPermissionReviewer(
                api_client=client,
                model=settings.permission_reviewer_model or model,
            )
            if reviewer_posture is ReviewerPosture.AUTO and settings.permission_auto_review
            else None
        )
        permission_runtime = PermissionRuntime(
            profile=active_profile,
            boundary=sandbox_session.boundary if sandbox_session is not None else None,
            reviewer=permission_reviewer,
        )

        # ``command_store`` is reused per-turn for user-authored
        # slash commands (Phase 5b). P9-T3: LayeredStore wrap so
        # plugin-namespaced commands resolve via the same path.
        command_store: LayeredStore[Command] | None
        if no_commands:
            command_store = None
        else:
            base_command_store = FilesystemCommandStore(
                global_dir=Path.home() / ".openharness" / "commands",
                project_dir=Path.cwd() / ".openharness" / "commands",
            )
            command_store = LayeredStore(
                base=base_command_store,
                plugin_catalog=plugin_catalogs.commands,
            )

        project_instructions_content = (
            load_project_instructions(
                env.cwd,
                max_chars_per_file=settings.max_project_instruction_chars,
            )
            if settings.enable_project_instructions
            else None
        )

        # Bundle resolves on FIRST turn only (per D24.4). Tracked
        # outside the loop so subsequent turns reuse the same context.
        # P10-T4.4f: ``bundle_overrides_prompt`` separates "bundle set
        # ``system_prompt`` (skip memory injection)" from "bundle did
        # nothing (do memory injection)" — both cases have
        # ``bundle_resolved=True`` after first turn.
        bundle_resolved: bool = False
        bundle_overrides_prompt: bool = False
        effective_registry = registry
        effective_hook_registry = hook_registry
        # Initial system_prompt — fallback for the memory-disabled path
        # AND for the brief window before the loop's first iteration
        # rebuilds with memory. CLAUDE.md threads through; memory
        # manifest does NOT (no user query yet).
        system_prompt = build_system_prompt(
            registry.to_api_schema(),
            env,
            skill_store=skill_store,
            project_instructions_content=project_instructions_content,
            web_enabled=effective_web,
        )

        typer.echo("OpenHarness — multi-turn REPL. Type / for the command menu, /exit to quit.")

        # P12-T5 (D30.4): --resume loads the latest snapshot for cwd
        # as the starting history; banner prints message count + git_head
        # for situational awareness. The QueryContext rebuilt per-turn
        # in the loop below (with per-turn memory injection) consumes
        # this history naturally. If --resume but no snapshot → warn +
        # start with empty history.
        history: list[ConversationMessage] = []
        if resume:
            snapshot = _load_resume_snapshot(env.cwd, resume_id=resume_id)
            if snapshot is not None:
                extra = snapshot.get("extra", {})
                runtime_state = extra.get("permission_runtime") if isinstance(extra, dict) else None
                if runtime_state is not None:
                    try:
                        permission_runtime = PermissionRuntime.from_state(
                            profile=permission_runtime.profile,
                            boundary=permission_runtime.boundary,
                            state=runtime_state,
                            reviewer=permission_runtime.reviewer,
                            denial_limit=permission_runtime.denial_limit,
                        )
                    except ValueError as exc:
                        typer.echo(f"Cannot resume permission state: {exc}", err=True)
                        raise typer.Exit(code=1) from exc
                from openharness.protocols.messages import ConversationMessage as _CM

                history = [_CM.model_validate(m) for m in snapshot["messages"]]
                from datetime import datetime as _dt

                created_at = snapshot.get("created_at", "?")
                git_head = snapshot.get("git_head") or "(no git)"
                # Format created_at into a human-readable short form
                try:
                    pretty_when = _dt.fromisoformat(created_at.replace("Z", "+00:00")).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                except (ValueError, AttributeError):
                    pretty_when = created_at
                typer.echo(
                    f"(resumed: {len(history)} messages from {pretty_when}; git_head={git_head})"
                )

        # D47 plan-mode state machine — REPL-memory only (D47.7: not
        # persisted; a dead session falls back to the ground state).
        chat_mode = _repl.ChatMode.DEFAULT

        # D48 session goal — 续跑式条件循环. ``goal_auto_turns`` counts
        # consecutive auto-continued turns for status and for the optional
        # configured circuit breaker; any manual input resets it.
        # ``pending_is_goal_feedback`` marks a
        # queued continuation so it is NOT echoed as ``>>> `` user input
        # (D48.2: checker feedback must never impersonate the user).
        goal: _repl.GoalState | None = None
        goal_auto_turns = 0
        pending_is_goal_feedback = False
        from openharness.services.compact import (
            estimate_message_tokens as _goal_estimate_tokens,
        )
        from openharness.services.snapshot import (
            SnapshotError,
            append_messages_to_snapshot,
            clear_conversation_snapshot,
            update_permission_runtime_snapshot,
        )

        def _extinguish_goal(event: str, condition: str) -> None:
            """Append a terminal goal sentinel (``met``/``cleared``) and
            persist it immediately (F17, dogfood 2026-07-28).

            The engine writes this turn's snapshot *inside* ``run_query``;
            the judge and these sentinels run after it returns. Appending to
            ``history`` alone means quitting here leaves the last persisted
            sentinel at ``set`` — and ``--resume`` would revive a goal that
            is already done, then auto-kickoff on it. The ``set`` sentinel
            needs no such amendment: its kickoff turn persists it.
            """
            sentinel = _repl.build_goal_sentinel(event, condition)
            history.append(sentinel)
            if settings.snapshot.enabled:
                append_messages_to_snapshot(cwd=env.cwd, messages=[sentinel])

        # D48.7 — --resume restores an active goal from transcript sentinels
        # (counters/timer/token baseline reset, CC 同款).
        if history:
            restored_condition = _repl.find_active_goal(history)
            if restored_condition is not None:
                goal = _repl.GoalState(
                    condition=restored_condition,
                    set_at=time.time(),
                    tokens_at_start=_goal_estimate_tokens(history, model=model),
                )
                typer.echo(f"(goal restored: {restored_condition})")

        # repl-ux plan §2/§3: on a real terminal, input goes through a
        # prompt_toolkit session — ``/`` pops the completion menu
        # (built-ins + commands + skills, D38.1 order), input history
        # persists per project, and the bottom toolbar shows model +
        # context usage from the numbers the compaction subsystem
        # already tracks. Non-TTY (pipes, tests, CI) keeps the legacy
        # ``input(">>> ")`` path untouched.
        prompt_session = None
        if _repl.is_interactive():
            from openharness.services.compact import (
                estimate_message_tokens as _estimate_tokens,
            )
            from openharness.services.compact import (
                get_context_window as _get_window,
            )

            def _status_line() -> str:
                return _repl.format_status_bar(
                    model=model,
                    used_tokens=_estimate_tokens(history, model=model),
                    context_window=_get_window(model),
                    threshold_ratio=compact_threshold_ratio if compact_enabled else None,
                    # Same late-binding closure pattern as ``history`` above:
                    # reads the current mode each render, so the toolbar
                    # flips the instant /plan or the approval menu does.
                    mode=("plan" if chat_mode is _repl.ChatMode.PLAN else None),
                    goal_active=goal is not None,
                )

            prompt_session = _repl.create_prompt_session(
                commands=_repl.collect_slash_commands(command_store, skill_store),
                history_path=_repl.default_history_path(env.cwd),
                status_provider=_status_line,
            )

        # D43.1: a root-command positional prompt is seeded as the first
        # REPL turn — answered, then the session stays open (no revolving
        # door). D43.4: Ctrl+D requires a double-press (armed on first EOF,
        # reset by any successful read) so a slip doesn't drop the session.
        pending_input: str | None = initial_prompt
        permission_resume_continuation = None
        permission_menu_deferred = False
        eof_armed = False

        async def _permission_choice() -> _repl.PermissionMenuChoice | None:
            typer.echo(_repl.PERMISSION_MENU_TEXT)
            while True:
                try:
                    if prompt_session is not None:
                        raw_choice = await prompt_session.prompt_async("permission> ")
                    else:
                        raw_choice = await asyncio.to_thread(input, "permission> ")
                except (EOFError, KeyboardInterrupt):
                    typer.echo(
                        "\n(permission decision postponed; the exact request remains parked. "
                        "Use /approve or /deny when ready.)"
                    )
                    return None
                parsed = _repl.parse_permission_menu_choice(raw_choice)
                if parsed is not None:
                    return parsed
                typer.echo("(enter 1 or 2; blank input never approves)")

        while True:
            manual_input_received = False
            permission_continuation = permission_resume_continuation
            permission_resume_continuation = None
            if (
                permission_continuation is None
                and not permission_menu_deferred
                and permission_runtime.parked_request is not None
                and permission_runtime.parked_continuation is not None
            ):
                permission_choice = await _permission_choice()
                if permission_choice is None:
                    permission_menu_deferred = True
                else:
                    request_id = permission_runtime.parked_request.request_id
                    if permission_choice is _repl.PermissionMenuChoice.APPROVE:
                        permission_runtime.approve_parked(request_id)
                        decision_label = "approved"
                    else:
                        permission_runtime.deny_parked(request_id, reason="user denied")
                        decision_label = "denied"
                    transition = permission_runtime.resume_decided()
                    permission_continuation = transition.continuation
                    if settings.snapshot.enabled:
                        update_permission_runtime_snapshot(cwd=env.cwd, runtime=permission_runtime)
                    typer.echo(f"({decision_label} exact request {request_id[:12]}; continuing)")
            if permission_continuation is not None:
                controller = permission_continuation.controller
                chat_mode = (
                    _repl.ChatMode.PLAN if controller.mode == "plan" else _repl.ChatMode.DEFAULT
                )
                if controller.mode == "goal" and goal is None and controller.goal_condition:
                    goal = _repl.GoalState(
                        condition=controller.goal_condition,
                        set_at=time.time(),
                        tokens_at_start=_goal_estimate_tokens(history, model=model),
                    )
            try:
                if permission_continuation is not None:
                    user_input = ""
                elif pending_input is not None:
                    user_input = pending_input
                    pending_input = None
                    if pending_is_goal_feedback:
                        # D48.2 — checker feedback is queued input but NOT
                        # user speech: the "(goal not met — continuing)" line
                        # was already shown; echoing ``>>> `` here would
                        # impersonate the user.
                        pending_is_goal_feedback = False
                    else:
                        typer.echo(f">>> {user_input}")
                elif prompt_session is not None:
                    user_input = await prompt_session.prompt_async(">>> ")
                    manual_input_received = True
                else:
                    user_input = await asyncio.to_thread(input, ">>> ")
                    manual_input_received = True
            except EOFError:
                if eof_armed:
                    typer.echo("")  # newline after EOF
                    break
                eof_armed = True
                typer.echo("\n(press Ctrl+D again to exit)")
                continue
            except KeyboardInterrupt:
                typer.echo("\n(use /exit to quit)")
                continue
            eof_armed = False

            user_input = user_input.strip()
            if not user_input and permission_continuation is None:
                continue

            # Built-in REPL commands (D24.3).
            if user_input in ("/exit", "/quit"):
                break
            if user_input == "/clear":
                cleared_goal = goal.condition if goal is not None else None
                if cleared_goal is not None:
                    _extinguish_goal("cleared", cleared_goal)
                    goal = None
                    goal_auto_turns = 0
                history = []
                permission_runtime.clear_pending_state()
                try:
                    clear_conversation_snapshot(cwd=env.cwd, runtime=permission_runtime)
                except SnapshotError as exc:
                    typer.echo(
                        f"(/clear snapshot failed: {exc}; old conversation may still resume)",
                        err=True,
                    )
                if cleared_goal is None:
                    typer.echo("(conversation cleared)")
                else:
                    typer.echo(f"(conversation and active goal cleared: {cleared_goal})")
                continue
            if user_input == "/help":
                typer.echo(_CHAT_HELP_TEXT)
                continue
            if user_input == "/permissions":
                typer.echo(
                    _repl.format_permissions_status(
                        profile=active_profile,
                        external_policy=active_profile.external_tools,
                        boundary=(
                            sandbox_session.boundary if sandbox_session is not None else None
                        ),
                        tool_domains=effective_registry.execution_domain_report(),
                        external_surfaces=effective_registry.external_effect_report(),
                        mcp_server_postures={
                            config.name: (
                                f"sandbox={'required' if config.sandbox else 'trusted-host-only'}, "
                                f"environment={config.environment_posture.value}, "
                                f"trust={'trusted' if config.name in settings.trusted_mcp_servers else 'untrusted'}, "
                                f"startup={'failed' if config.name in pool.dead_servers else 'active'}"
                            )
                            for config in combined_mcp_servers
                        },
                        trusted_control_status={
                            "hooks": (
                                f"enabled; registered={effective_hook_registry.registration_count()}; "
                                "may deny/modify calls after explicit registration"
                            ),
                            "plugins": (
                                f"{'enabled' if enable_plugins else 'disabled'}; "
                                "loading is an explicit trust decision"
                            ),
                        },
                        parked_request=permission_runtime.parked_request,
                    )
                )
                continue
            if user_input == "/approve" or user_input.startswith("/approve "):
                if permission_runtime.parked_request is None:
                    typer.echo("(no parked permission request)")
                    continue
                supplied_id = user_input.removeprefix("/approve").strip()
                request_id = supplied_id or permission_runtime.parked_request.request_id
                try:
                    permission_runtime.approve_parked(request_id)
                    transition = permission_runtime.resume_decided()
                except ValueError as exc:
                    typer.echo(f"(permission approval failed: {exc})", err=True)
                    continue
                if settings.snapshot.enabled:
                    update_permission_runtime_snapshot(cwd=env.cwd, runtime=permission_runtime)
                permission_resume_continuation = transition.continuation
                permission_menu_deferred = False
                typer.echo(f"(approved exact request {request_id[:12]}; continuing)")
                continue
            if user_input == "/deny" or user_input.startswith("/deny "):
                if permission_runtime.parked_request is None:
                    typer.echo("(no parked permission request)")
                    continue
                supplied_id = user_input.removeprefix("/deny").strip()
                request_id = supplied_id or permission_runtime.parked_request.request_id
                try:
                    permission_runtime.deny_parked(request_id, reason="user denied")
                    transition = permission_runtime.resume_decided()
                except ValueError as exc:
                    typer.echo(f"(permission denial failed: {exc})", err=True)
                    continue
                if settings.snapshot.enabled:
                    update_permission_runtime_snapshot(cwd=env.cwd, runtime=permission_runtime)
                permission_resume_continuation = transition.continuation
                permission_menu_deferred = False
                typer.echo(f"(denied exact request {request_id[:12]}; continuing)")
                continue
            if user_input == "/resume":
                try:
                    transition = permission_runtime.resume_decided()
                except ValueError:
                    typer.echo("(no permission decision to resume)")
                    continue
                if settings.snapshot.enabled:
                    update_permission_runtime_snapshot(cwd=env.cwd, runtime=permission_runtime)
                permission_resume_continuation = transition.continuation
                continue
            # A parked permission is a session-level stop, not a tool-local
            # warning. Starting another model turn before the owner decides
            # would allow one more tool to run before the engine notices the
            # old request (dogfood: Plan Grep parked, then Default Read and
            # Goal Write still executed). Keep lifecycle/status commands above
            # available, and allow an existing goal to be inspected or
            # cleared, but do not enter a new workflow or Agent turn.
            if permission_runtime.parked_request is not None:
                goal_status_only = False
                if user_input == "/goal" or user_input.startswith("/goal "):
                    goal_status_only = _repl.parse_goal_command(user_input).action in {
                        "show",
                        "clear",
                    }
                if not goal_status_only:
                    request_id = permission_runtime.parked_request.request_id[:12]
                    typer.echo(
                        f"(permission request pending {request_id}; no new Agent turn "
                        "started. Use /approve or /deny.)"
                    )
                    continue
            # D47 — enter plan mode. The clamp itself is the permissions deny
            # preset overlaid at context build; this just flips the state.
            # Leaving plan mode is menu-only (harness-owned gate, D47.2) —
            # there is deliberately no /default escape command and no
            # model-callable exit tool.
            if user_input == "/plan" or user_input.startswith("/plan "):
                if chat_mode is _repl.ChatMode.PLAN:
                    typer.echo("(already in plan mode)")
                    continue
                chat_mode = _repl.ChatMode.PLAN
                typer.echo(
                    "(plan mode: edits and shell commands are blocked; an "
                    "approval menu appears after each completed reply)"
                )
                plan_prompt = user_input.removeprefix("/plan").strip()
                if not plan_prompt:
                    continue
                user_input = plan_prompt
            # D48 — session goal command surface (set / show / clear).
            if user_input == "/goal" or user_input.startswith("/goal "):
                goal_cmd = _repl.parse_goal_command(user_input)
                if goal_cmd.action == "show":
                    if goal is None:
                        typer.echo("(no goal set — /goal <condition> to set one)")
                    else:
                        elapsed = time.time() - goal.set_at
                        tokens_delta = max(
                            0,
                            _goal_estimate_tokens(history, model=model) - goal.tokens_at_start,
                        )
                        typer.echo(
                            f"(goal: {goal.condition}\n"
                            f"  checks: {goal.iterations} · auto-turns: {goal_auto_turns} · "
                            f"elapsed: {elapsed:.0f}s · ~tokens since set: {tokens_delta}\n"
                            f"  last checker reason: {goal.last_reason or '(not yet evaluated)'})"
                        )
                elif goal_cmd.action == "clear":
                    if goal is None:
                        typer.echo("(no goal to clear)")
                    else:
                        _extinguish_goal("cleared", goal.condition)
                        typer.echo(f"(goal cleared: {goal.condition})")
                        goal = None
                        goal_auto_turns = 0
                else:  # set
                    assert goal_cmd.condition is not None  # parse contract for "set"
                    goal = _repl.GoalState(
                        condition=goal_cmd.condition,
                        set_at=time.time(),
                        tokens_at_start=_goal_estimate_tokens(history, model=model),
                    )
                    goal_auto_turns = 0
                    history.append(_repl.build_goal_sentinel("set", goal_cmd.condition))
                    typer.echo(
                        f"(goal set: {goal_cmd.condition}\n"
                        "  an independent checker evaluates after each reply; "
                        "tip: include a bound in the condition, e.g. "
                        "'or stop after 20 turns')"
                    )
                    # D48.9 — set 即开工:kickoff turn 立即发起,不等下一条
                    # 用户输入(CC "immediately start working" 同款).走 goal
                    # 注入通道,不回显为 ``>>> `` 用户输入.
                    pending_input = _repl.build_goal_kickoff(goal_cmd.condition)
                    pending_is_goal_feedback = True
                continue
            # Phase 18 D38.4: list available skills. Dogfood entry point so
            # users can confirm a freshly dropped SKILL.md was discovered.
            if user_input == "/skills":
                _emit_skill_catalog(skill_store)
                continue
            # ``/memory`` mirrors ``oh state memory list`` text format so
            # users don't have to leave the chat to inspect the memory store
            # the LLM is reading from. Read-only; identical 3-column layout
            # (name / type / description) as ``oh state memory list``. First line
            # is the ``(memory dir: ...)`` header so wrong-cwd runs surface
            # immediately rather than rendering an unrelated project's data.
            if user_input == "/memory":
                _emit_memory_catalog(memory_store, memory_dir)
                continue
            # P11-T5 (D29.6): force full LLM-based compaction. Same
            # primitive as auto-compact L4, but invoked unconditionally
            # on the current history. Replaces ``history`` with a single
            # user-role message carrying the six-section handoff so the next
            # turn picks it up as compressed context. No-op on empty
            # history. Errors surface inline; history is unchanged on
            # failure.
            if user_input == "/compact":
                if not history:
                    typer.echo("(no conversation to compact)")
                    continue
                from openharness.services.compact import (
                    estimate_message_tokens,
                    full_compact,
                )

                before_tokens = estimate_message_tokens(history, model=model)
                # Sprint 2 F1 (dogfood 2026-07-12): the auto-compact pipeline
                # keeps a 12-message recent window — right for the proactive
                # path, but it silently blocked the EXPLICIT command on short
                # conversations and then lied ("nothing to summarize"). An
                # explicit /compact is user intent: shrink the preserved tail
                # to the last exchange (2) and, when it still doesn't apply,
                # report the real reason.
                _explicit_preserve = 2
                if len(history) <= _explicit_preserve:
                    typer.echo(
                        f"(/compact: nothing to compact; history has {len(history)} "
                        f"message(s), all within the preserved tail of {_explicit_preserve})"
                    )
                    continue
                try:
                    new_history, did_apply = await full_compact(
                        history,
                        model=model,
                        api_client=client,
                        max_tokens=settings.compact.full_compact_max_tokens,
                        timeout_seconds=settings.compact.full_compact_timeout_s,
                        preserve_recent=_explicit_preserve,
                        raise_on_failure=True,
                    )
                except Exception as exc:
                    typer.echo(f"(/compact failed: {exc})", err=True)
                    continue
                if not did_apply:
                    typer.echo(
                        "(/compact failed: summarizer did not apply a compacted history; "
                        "history unchanged)"
                    )
                    continue
                history = new_history
                after_tokens = estimate_message_tokens(history, model=model)
                typer.echo(f"(compacted: {before_tokens} → {after_tokens} tokens)")
                continue

            # Phase 5b / 18 slash dispatch — D38.1 fallback order:
            #   built-ins (handled above) → CommandStore → SkillStore →
            #   UnknownCommandError. CommandStore-first preserves Phase
            #   5b's user-priority semantics; SkillStore-second gives CC
            #   ``/<skill>`` zero-migration UX (D38.1 rationale).
            invoked_command = None
            slash_skill_invoked = permission_continuation is not None
            if user_input.startswith("/"):
                slash_name, slash_args = _split_slash_invocation(user_input)
                cmd = command_store.get(slash_name) if command_store is not None else None
                if cmd is not None:
                    # CommandStore hit → Phase 5b path (substitute body + carry mode).
                    assert command_store is not None  # cmd came from command_store.get
                    user_input, invoked_command = resolve_command_invocation(
                        user_input, command_store
                    )
                else:
                    # CommandStore miss → SkillStore fallback (D38.1 step 3).
                    skill = skill_store.get(slash_name)
                    if skill is not None:
                        envelope = synthesize_skill_envelope(skill, slash_args)
                        history.extend(envelope)
                        # D38.5 forcing function: synth envelope is a UI action,
                        # not an LLM action — must be visible in observability so
                        # hook authors understand why PreToolUse did not fire.
                        get_logger("slash_skill").info(
                            "slash_skill_invoked",
                            skill_name=skill.name,
                            args_length=len(slash_args),
                            synthetic=True,
                        )
                        slash_skill_invoked = True
                    else:
                        # D38.1 step 4 — Unknown. Suggest closest skill if any.
                        typer.echo(f"Unknown command: {slash_name}", err=True)
                        skill_names = sorted(skill_store.discover().keys())
                        closest = difflib.get_close_matches(
                            slash_name, skill_names, n=3, cutoff=0.5
                        )
                        if closest:
                            typer.echo(f"Did you mean a skill? Closest: {', '.join(closest)}")
                        continue  # don't exit — let user retry

            # Phase 5d bundle resolution (FIRST turn only, per D24.4).
            if (
                not bundle_resolved
                and invoked_command is not None
                and invoked_command.mode is not None
            ):
                base_bundle_store = FilesystemBundleStore(
                    global_dir=Path.home() / ".openharness" / "bundles",
                    project_dir=Path.cwd() / ".openharness" / "bundles",
                )
                bundle_store = LayeredStore(
                    base=base_bundle_store,
                    plugin_catalog=plugin_catalogs.bundles,
                )
                bundle = bundle_store.get(invoked_command.mode)
                if bundle is None:
                    available = sorted(bundle_store.discover().keys())
                    typer.echo(
                        f"Unknown bundle: {invoked_command.mode!r}; "
                        f"available: {', '.join(available) or '(none)'}",
                        err=True,
                    )
                    continue
                application = apply_bundle_to_context(
                    bundle=bundle,
                    tool_registry=registry,
                    hook_registry=hook_registry,
                    system_prompt="",
                    plugin_hook_catalog=plugin_hook_catalog,
                )
                effective_registry = application.tool_registry
                effective_hook_registry = application.hook_registry
                if bundle.system_prompt is not None:
                    system_prompt = _append_project_instructions(
                        bundle.system_prompt,
                        project_instructions_content,
                    )
                    bundle_overrides_prompt = True
                else:
                    system_prompt = build_system_prompt(
                        effective_registry.to_api_schema(),
                        env,
                        skill_store=skill_store,
                        project_instructions_content=project_instructions_content,
                        web_enabled=effective_web,
                    )
            bundle_resolved = True

            # Only conversational input re-arms the Goal turn budget. Lifecycle
            # commands such as /approve, /deny, and /resume are an asynchronous
            # handoff and must preserve the counter exactly.
            if manual_input_received:
                goal_auto_turns = 0

            # Plan mode shapes the model-visible capability catalog. Keep the
            # full registry only for dispatch-time forged-call denial.
            turn_registry = (
                _repl.shape_plan_tool_registry(effective_registry)
                if chat_mode is _repl.ChatMode.PLAN
                else effective_registry
            )

            # P10-T4.4f: rebuild system_prompt with per-turn memory
            # manifest unless the bundle explicitly overrode the prompt
            # (bundle.system_prompt set → user opted out of harness-
            # composed sections, memory included). Runs every turn so
            # multi-turn conversations get fresh relevance scoring
            # against the current user_input.
            if not bundle_overrides_prompt:
                # P16-T1/T2 (D36.7 / D36.11): per-turn MEMORY.md re-read
                # so the LLM sees the index updated by the previous
                # turn's memory writes. Phase 10's relevance ranking +
                # use_count bookkeeping was retired alongside extraction
                # (D36.9) — the LLM picks what to Read from the index.
                memory_index_content = (
                    memory_store.render_index(max_entries=200)
                    if memory_store is not None and memory_dir is not None
                    else None
                )
                system_prompt = build_system_prompt(
                    turn_registry.to_api_schema(),
                    env,
                    skill_store=skill_store,
                    project_instructions_content=project_instructions_content,
                    memory_dir=memory_dir,
                    memory_index_content=memory_index_content,
                    web_enabled=effective_web,
                )

            # D47 — plan is a capability view plus deny-only forged-call
            # guard. Approval only exits that view; it never grants execution.
            turn_action_policy: ActionDenyPolicy = ConfiguredActionDenyPolicy()
            if chat_mode is _repl.ChatMode.PLAN:
                turn_action_policy = PlanActionDenyPolicy(
                    registry=effective_registry,
                    base=turn_action_policy,
                )
            # Posture (D47.6 / D48.8), not contract — appended per turn,
            # never stored. Plan posture and goal posture can coexist only
            # nominally (judge won't fire in plan mode, D48.1), but the goal
            # section stays visible so planning happens goal-aware.
            turn_system_prompt = system_prompt
            if chat_mode is _repl.ChatMode.PLAN:
                turn_system_prompt = f"{turn_system_prompt}\n\n{PLAN_MODE_PROMPT_SECTION}"
            if goal is not None:
                turn_system_prompt = (
                    f"{turn_system_prompt}\n\n{_repl.goal_prompt_section(goal.condition)}"
                )

            authorization_messages = list(history)
            if not slash_skill_invoked:
                authorization_messages.append(
                    ConversationMessage(role="user", content=[TextBlock(text=user_input)])
                )
            context = QueryContext(
                api_client=client,
                tool_registry=turn_registry,
                dispatch_tool_registry=(
                    effective_registry if chat_mode is _repl.ChatMode.PLAN else None
                ),
                action_deny_policy=turn_action_policy,
                hook_registry=effective_hook_registry,
                system_prompt=turn_system_prompt,
                cwd=env.cwd,
                model=model,
                max_tokens=max_tokens,
                max_turns=max_turns,
                reviewer_posture=reviewer_posture,
                execution_posture=execution_posture,
                autonomous=(reviewer_posture is ReviewerPosture.AUTO or goal is not None),
                authorization_context=extract_authorization_context(authorization_messages),
                controller_mode=(
                    "plan"
                    if chat_mode is _repl.ChatMode.PLAN
                    else ("goal" if goal is not None else "default")
                ),
                controller_goal_condition=goal.condition if goal is not None else None,
                skill_store=skill_store,
                max_agent_depth=settings.max_agent_depth,
                execution_env=execution_env,
                sandbox_session=sandbox_session,
                runtime_permission_profile=active_profile,
                enforced_boundary=(
                    sandbox_session.boundary if sandbox_session is not None else None
                ),
                permission_runtime=permission_runtime,
                # Compact and project-memory wiring. Mirrors ``_run_ask``.
                # Rebuilt per turn so /compact-toggled flags (future)
                # take effect on the next turn.
                compact_enabled=compact_enabled,
                compact_threshold_ratio=compact_threshold_ratio,
                compact_preserve_recent_messages=settings.compact.preserve_recent_messages,
                compact_full_max_tokens=settings.compact.full_compact_max_tokens,
                compact_full_timeout_s=settings.compact.full_compact_timeout_s,
                memory_store=memory_store,
                # P12-T3 (D30.8): snapshot writer mirrored from non-interactive execution.
                snapshot_enabled=settings.snapshot.enabled,
                snapshot_max_age_warn_days=settings.snapshot.max_age_warn_days,
                snapshot_history_max_count=settings.snapshot.history.max_count,
                snapshot_history_max_age_days=settings.snapshot.history.max_age_days,
                llm_focus_state_enabled=(
                    llm_focus_state_override
                    if llm_focus_state_override is not None
                    else settings.snapshot.llm_focus_state
                ),
                llm_focus_state_model=settings.snapshot.focus_state_model,
            )

            # Phase 18 D38.2: slash-skill envelope already extended history
            # (assistant tool_use + user tool_result + optional user TextBlock(args));
            # appending another user TextBlock here would duplicate the trailing args.
            if not slash_skill_invoked:
                history.append(
                    ConversationMessage(role="user", content=[TextBlock(text=user_input)])
                )

            captured: list[ConversationMessage] | None = None
            permission_confirmation_required: str | None = None
            conversation_completed = False
            worker_stop_reason: str | None = None

            async def _capture(
                events_iter: AsyncIterator[ApiStreamEvent],
            ) -> AsyncIterator[ApiStreamEvent]:
                nonlocal captured, conversation_completed, permission_confirmation_required
                nonlocal worker_stop_reason
                async for ev in events_iter:
                    if isinstance(ev, ApiMessageCompleteEvent):
                        worker_stop_reason = ev.stop_reason
                    elif isinstance(ev, ConversationCompleteEvent):
                        captured = ev.messages
                        conversation_completed = True
                    elif isinstance(ev, PermissionParkedEvent):
                        captured = ev.messages
                        permission_confirmation_required = ev.reason
                    elif (
                        isinstance(ev, ToolExecutionCompletedEvent)
                        and ev.is_error
                        and ev.output.startswith("permission denied (requires confirmation):")
                    ):
                        permission_confirmation_required = ev.output
                    yield ev

            try:
                query_events = (
                    run_query(history, context)
                    if permission_continuation is None
                    else run_query(history, context, continuation=permission_continuation)
                )
                await render_stream(_capture(query_events))
            except LoopLimitExceeded as exc:
                if exc.messages is None:
                    typer.echo(f"Loop error: {exc}", err=True)
                    continue
                # A caller-selected cap is a circuit breaker, not semantic
                # assistant completion. Preserve work and return control; an
                # active Goal must never send this forced stop to its judge.
                history = list(exc.messages)
                subject = "goal" if goal is not None else "agent"
                typer.echo(
                    f"({subject} paused at explicit turn limit ({exc.max_turns}); "
                    "progress checkpointed — send a message to continue or restart "
                    "with a different --max-turns value)"
                )
                continue
            except LoopError as exc:
                typer.echo(f"Loop error: {exc}", err=True)
                # Don't break — let user issue /clear or retry.
                continue
            except QuotaExceededFailure as exc:
                typer.echo(
                    f"Quota exhausted (HTTP {exc.status_code}): {exc}\n"
                    "Switch to a Provider/model with available quota, or wait for its reset.",
                    err=True,
                )
                continue
            except OpenHarnessApiError as exc:
                typer.echo(f"API error: {exc}", err=True)
                # REPL must survive provider-side failures (auth blip,
                # rate-limit, truncated tool_call). User can /clear,
                # adjust flags (e.g. --max-tokens), or retry.
                continue

            if captured is not None:
                history = captured

            if permission_confirmation_required is not None:
                if goal is not None and chat_mode is _repl.ChatMode.DEFAULT:
                    typer.echo(
                        "\a(goal blocked on permission — automation paused before the "
                        "goal checker. blocker: "
                        f"{permission_confirmation_required})"
                    )
                elif chat_mode is _repl.ChatMode.PLAN:
                    typer.echo("(plan paused on permission; no plan is ready to approve)")
                continue

            if (
                goal is not None
                and chat_mode is _repl.ChatMode.DEFAULT
                and conversation_completed
                and worker_stop_reason != "end_turn"
            ):
                typer.echo(
                    "(goal paused after incomplete worker stop "
                    f"({worker_stop_reason or 'unknown'}); send a message to continue "
                    "or /goal clear)"
                )
                goal_auto_turns = 0
                continue

            # D47.2/D47.5 — approval menu after every completed assistant turn
            # while in plan mode. A permission-interrupted turn is not a plan
            # the user can approve; resolve it and /resume first. For ordinary
            # completed turns, option 2 covers "model is still exploring".
            # This is the harness-owned gate: the model has no exit tool, and
            # natural language cannot flip the mode — only a menu choice can.
            if chat_mode is _repl.ChatMode.PLAN and conversation_completed:
                typer.echo(_repl.PLAN_MENU_TEXT)
                choice: _repl.PlanMenuChoice
                while True:
                    try:
                        if prompt_session is not None:
                            raw_choice = await prompt_session.prompt_async("plan> ")
                        else:
                            raw_choice = await asyncio.to_thread(input, "plan> ")
                    except EOFError:
                        # Fail-closed: exhausted/non-interactive input can
                        # never approve execution — treat as discard. The
                        # REPL's own double-Ctrl+D exit then fires normally.
                        choice = _repl.PlanMenuChoice.DISCARD
                        typer.echo("")
                        break
                    except KeyboardInterrupt:
                        choice = _repl.PlanMenuChoice.KEEP_PLANNING
                        typer.echo("")
                        break
                    parsed = _repl.parse_plan_menu_choice(raw_choice)
                    if parsed is not None:
                        choice = parsed
                        break
                    typer.echo("(enter 1-3)")
                if choice is _repl.PlanMenuChoice.APPROVE:
                    # Approval only exits the plan clamp and returns to the
                    # default conversational ground. Do not auto-execute or
                    # synthesize a goal: the user needs this interruption
                    # point to turn the plan into an explicit "/goal <target
                    # + verification>" before starting D48's loop.
                    chat_mode = _repl.ChatMode.DEFAULT
                    history.append(_repl.build_plan_approval_sentinel())
                    typer.echo("(plan approved — back to default mode)")
                elif choice is _repl.PlanMenuChoice.DISCARD:
                    chat_mode = _repl.ChatMode.DEFAULT
                    typer.echo("(plan discarded — back to default mode)")
                # KEEP_PLANNING: stay clamped; the next input keeps planning.

            # D48 — goal judge after every DEFAULT-state turn. Trigger is
            # mutually exclusive with the plan menu (chat_mode gate) and with
            # a just-queued turn (pending_input gate: a goal kickoff or
            # continuation must run before the judge sees it). The controller
            # distinguishes incomplete work from a broken judge: only NOT_MET
            # drives another worker turn; ERROR pauses automation.
            if goal is not None and chat_mode is _repl.ChatMode.DEFAULT and pending_input is None:
                evidence = _repl.goal_evidence_messages(history, goal.condition)
                transcript = render_history_transcript(evidence)
                result = await judge_goal_completion(
                    goal.condition,
                    transcript,
                    evidence_messages=evidence,
                    api_client=client,
                    model=settings.goal_judge_model or model,
                    timeout_seconds=60.0,
                )
                goal.iterations += 1
                goal.last_reason = result.reason
                if result.verdict is GoalJudgeVerdict.ERROR:
                    typer.echo(
                        "\a(goal checker unavailable — automation paused; "
                        "send a message to retry or /goal clear. "
                        f"checker: {result.reason})"
                    )
                    goal_auto_turns = 0
                elif result.verdict is GoalJudgeVerdict.MET:
                    elapsed = time.time() - goal.set_at
                    tokens_delta = max(
                        0,
                        _goal_estimate_tokens(history, model=model) - goal.tokens_at_start,
                    )
                    checked_turn_label = "turn" if goal.iterations == 1 else "turns"
                    continuation_label = "continuation" if goal_auto_turns == 1 else "continuations"
                    _extinguish_goal("met", goal.condition)
                    typer.echo(
                        f"\a(goal met after {goal.iterations} checked {checked_turn_label} "
                        f"({goal_auto_turns} {continuation_label}), "
                        f"~{tokens_delta} tokens, {elapsed:.0f}s — {result.reason})"
                    )
                    goal = None
                    goal_auto_turns = 0
                elif (
                    settings.goal_max_auto_turns is not None
                    and goal_auto_turns >= settings.goal_max_auto_turns
                ):
                    typer.echo(
                        f"\a(goal not met after {goal_auto_turns} auto-turns — "
                        "paused; send a message to continue or /goal clear. "
                        f"checker: {result.reason})"
                    )
                    goal_auto_turns = 0
                else:
                    goal_auto_turns += 1
                    goal_progress = str(goal_auto_turns)
                    if settings.goal_max_auto_turns is not None:
                        goal_progress += f"/{settings.goal_max_auto_turns}"
                    typer.echo(f"(goal not met — continuing ({goal_progress}): {result.reason})")
                    pending_input = _repl.build_goal_continuation(goal.condition, result.reason)
                    pending_is_goal_feedback = True


# --------------------------------------------------------------------------- #
# Typer command surface                                                       #
# --------------------------------------------------------------------------- #


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"openharness {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    _version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model name override."),
    max_tokens: int = typer.Option(
        DEFAULT_MAX_TOKENS,
        "--max-tokens",
        min=1,
        hidden=True,
        help="Max tokens per turn.",
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        min=1,
        hidden=True,
        help="Optional Agent-loop turn cap; omitted means model-terminated.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Use the automated reviewer for exact permission requests.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List tool calls; don't execute."),
    log_level: LogLevel | None = typer.Option(None, "--log-level", hidden=True),
    log_format: LogFormat | None = typer.Option(None, "--log-format", hidden=True),
    tool_result_cap: int | None = typer.Option(None, "--tool-result-cap", hidden=True, min=0),
    no_auto_truncate: bool = typer.Option(False, "--no-auto-truncate", hidden=True),
    no_skills: bool = typer.Option(False, "--no-skills", hidden=True),
    no_commands: bool = typer.Option(False, "--no-commands", hidden=True),
    sandbox: bool | None = typer.Option(
        None,
        "--sandbox/--no-sandbox",
        help="Enable or disable the verified execution sandbox.",
    ),
    sandbox_backend: SandboxBackendName | None = typer.Option(
        None,
        "--sandbox-backend",
        help="Select the verified sandbox backend.",
    ),
    sandbox_image: str | None = typer.Option(None, "--sandbox-image", hidden=True),
    sandbox_memory: str | None = typer.Option(None, "--sandbox-memory", hidden=True),
    sandbox_cpus: float | None = typer.Option(None, "--sandbox-cpus", hidden=True),
    sandbox_runtime: str | None = typer.Option(None, "--sandbox-runtime", hidden=True),
    enable_plugin_hooks: bool | None = typer.Option(
        None,
        "--enable-plugin-hooks/--no-enable-plugin-hooks",
        hidden=True,
    ),
    enable_plugins: bool | None = typer.Option(
        None,
        "--enable-plugins/--no-enable-plugins",
        hidden=True,
    ),
    enable_memory: bool | None = typer.Option(
        None,
        "--enable-memory/--no-enable-memory",
        hidden=True,
    ),
    enable_web: bool | None = typer.Option(
        None,
        "--enable-web/--no-enable-web",
        hidden=True,
    ),
    compact_threshold: float | None = typer.Option(
        None,
        "--compact-threshold",
        hidden=True,
        min=0.0,
        max=1.0,
    ),
    no_auto_compact: bool = typer.Option(False, "--no-auto-compact", hidden=True),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume the latest snapshot for the current project.",
    ),
    resume_id: str | None = typer.Option(
        None,
        "--resume-id",
        help="Resume the snapshot whose git commit matches this prefix.",
    ),
    llm_focus_state: bool | None = typer.Option(
        None,
        "--llm-focus-state/--no-llm-focus-state",
        hidden=True,
    ),
) -> None:
    """OpenHarness CLI. The root command starts the interactive session."""
    if ctx.invoked_subcommand is None:
        chat(
            model=model,
            max_tokens=max_tokens,
            max_turns=max_turns,
            auto=auto,
            dry_run=dry_run,
            log_level=log_level,
            log_format=log_format,
            tool_result_cap=tool_result_cap,
            no_auto_truncate=no_auto_truncate,
            no_skills=no_skills,
            no_commands=no_commands,
            sandbox=sandbox,
            sandbox_backend=sandbox_backend,
            sandbox_image=sandbox_image,
            sandbox_memory=sandbox_memory,
            sandbox_cpus=sandbox_cpus,
            sandbox_runtime=sandbox_runtime,
            enable_plugin_hooks=enable_plugin_hooks,
            enable_plugins=enable_plugins,
            enable_memory=enable_memory,
            enable_web=enable_web,
            compact_threshold=compact_threshold,
            no_auto_compact=no_auto_compact,
            resume=resume,
            resume_id=resume_id,
            llm_focus_state=llm_focus_state,
        )


@headless_app.command("run", help="Internal non-interactive runtime adapter.")
def _run_headless_command(
    prompt: str = typer.Argument(
        ...,
        help="Prompt for internal non-interactive execution.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model name; overrides OPENHARNESS_MODEL and the qwen-plus default.",
    ),
    max_tokens: int = typer.Option(
        DEFAULT_MAX_TOKENS,
        "--max-tokens",
        min=1,
        help=f"Maximum tokens to generate per call (default {DEFAULT_MAX_TOKENS}).",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Use the automated reviewer for exact permission requests.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List tool calls the loop would make without executing them.",
    ),
    print_mode: bool = typer.Option(
        False,
        "-p",
        "--print",
        help=(
            "Run one prompt non-interactively and exit. Pair with "
            "--output-format for machine-readable output."
        ),
    ),
    output_format: OutputFormat = typer.Option(
        "text",
        "--output-format",
        help=(
            "Headless output shape [text|json|stream-json]. ``text`` "
            "(default) streams the response as-is. ``json`` / ``stream-json`` "
            "are machine-readable (land in L1 T3 / T4)."
        ),
    ),
    max_turns: int = typer.Option(
        20,
        "--max-turns",
        min=1,
        help=(
            "Agent-loop turn cap per attempt (default 20). This is the knob "
            "LoopLimitExceeded's remediation message points at — raise it "
            "for long multi-step tasks (surfaced by the SWE-bench adapter, "
            "D40 M1, where real fixes ran 8-19 turns and one died on the cap)."
        ),
    ),
    isolate: bool = typer.Option(
        False,
        "--isolate",
        help=(
            "Run the headless request in a fresh git worktree instead of "
            "mutating the live cwd. Requires -p/--print and "
            "--output-format json. Repo must be a git working tree with no "
            "uncommitted changes at start. The worktree is auto-removed if "
            "unchanged; kept (path/branch reported in the emitted json's "
            "'run' field) if it has any diff, regardless of success/failure."
        ),
    ),
    log_level: LogLevel | None = typer.Option(
        None,
        "--log-level",
        help=(
            "Minimum log level [DEBUG|INFO|WARNING|ERROR]. Overrides "
            "OPENHARNESS_LOG_LEVEL and the WARNING default. Logs go to stderr."
        ),
    ),
    log_format: LogFormat | None = typer.Option(
        None,
        "--log-format",
        help=(
            "Log renderer [console|json]. Overrides OPENHARNESS_LOG_FORMAT and "
            "the console default. ``json`` emits one JSON object per line — "
            "pipe stderr through jq / OTel / LangSmith exporter."
        ),
    ),
    tool_result_cap: int | None = typer.Option(
        None,
        "--tool-result-cap",
        hidden=True,
        min=0,
        help=(
            "Layer 1 per-tool-result token cap. Outputs above this cap are "
            "head/tail truncated with a marker. Overrides "
            "OPENHARNESS_TOOL_RESULT_CAP and the 10000 default. ``0`` disables."
        ),
    ),
    no_auto_truncate: bool = typer.Option(
        False,
        "--no-auto-truncate",
        hidden=True,
        help=(
            "Disable Layer 1 truncation hook registration. Raw tool outputs "
            "flow through unchanged. Whole-request compaction and one-shot "
            "Prompt Too Long semantic recompilation remain active."
        ),
    ),
    no_skills: bool = typer.Option(
        False,
        "--no-skills",
        help=(
            "Skip scanning ~/.openharness/skills/ + .openharness/skills/ at "
            "bootstrap. LoadSkill tool is not registered and the 'Available "
            "Skills' system-prompt section is omitted. Useful for testing "
            "or when the skill catalog interferes with a one-shot run."
        ),
    ),
    no_commands: bool = typer.Option(
        False,
        "--no-commands",
        help=(
            "Skip scanning ~/.openharness/commands/ + .openharness/commands/ "
            "at bootstrap. Slash-prefixed prompts pass through verbatim to "
            "the LLM as user message. Useful for testing or for prompts that "
            "legitimately start with ``/``."
        ),
    ),
    sandbox: bool | None = typer.Option(
        None,
        "--sandbox/--no-sandbox",
        help=(
            "Install the configured verified boundary for local tools. "
            "Without it, local/delegated execution fails closed."
        ),
    ),
    sandbox_backend: SandboxBackendName | None = typer.Option(
        None,
        "--sandbox-backend",
        help=(
            "Verified backend [seatbelt|docker-command]. Seatbelt covers "
            "commands and core file tools; docker-command covers commands only."
        ),
    ),
    sandbox_image: str | None = typer.Option(
        None,
        "--sandbox-image",
        hidden=True,
        help=(
            "Docker image for the sandbox (default: python:3.12-slim). "
            "Overrides OPENHARNESS_SANDBOX_IMAGE."
        ),
    ),
    sandbox_memory: str | None = typer.Option(
        None,
        "--sandbox-memory",
        hidden=True,
        help=(
            "Container memory limit, Docker-style spec (1g / 512m / etc.). "
            "Overrides OPENHARNESS_SANDBOX_MEMORY (default 1g)."
        ),
    ),
    sandbox_cpus: float | None = typer.Option(
        None,
        "--sandbox-cpus",
        hidden=True,
        help=(
            "Container CPU quota in CPU equivalents (1.0 = one full CPU; "
            "0.5 = half). Overrides OPENHARNESS_SANDBOX_CPUS (default 1.0)."
        ),
    ),
    sandbox_runtime: str | None = typer.Option(
        None,
        "--sandbox-runtime",
        hidden=True,
        help=(
            "OCI runtime for the sandbox container. ``runc`` (default) "
            "shares the host kernel; ``runsc`` selects gVisor for user-"
            "space syscall isolation (requires gVisor installed: "
            "https://gvisor.dev/docs/user_guide/install/). Other "
            "registered OCI runtimes pass through unchanged. Overrides "
            "OPENHARNESS_SANDBOX_RUNTIME."
        ),
    ),
    enable_plugin_hooks: bool | None = typer.Option(
        None,
        "--enable-plugin-hooks/--no-enable-plugin-hooks",
        hidden=True,
        help=(
            "Enable discovery of third-party hooks declared via the "
            "``openharness.hooks`` Python entry-point group (Phase 5e). "
            "Default OFF — even if plugin packages are installed, their "
            "hooks are not loaded unless this flag is set. Overrides "
            "OPENHARNESS_ENABLE_PLUGIN_HOOKS."
        ),
    ),
    enable_plugins: bool | None = typer.Option(
        None,
        "--enable-plugins/--no-enable-plugins",
        hidden=True,
        help=(
            "Enable discovery + loading of plugins from "
            "~/.openharness/plugins/<name>/manifest.yaml (Phase 9). "
            "Default OFF — plugin components are not registered into "
            "the running registry unless this flag is set. ``oh inspect plugins "
            "list`` still works without it (read-only introspection). "
            "Overrides OPENHARNESS_ENABLE_PLUGINS."
        ),
    ),
    enable_memory: bool | None = typer.Option(
        None,
        "--enable-memory/--no-enable-memory",
        hidden=True,
        help=(
            "Enable the memory subsystem (Phase 10, D28.10). When ON "
            "(default), per-project durable memory is injected into the "
            "system prompt. Project instructions are controlled "
            "independently. When OFF, durable memory is omitted — useful "
            "for a stateless harness or for isolating from a misbehaving "
            "memory file. Overrides "
            "OPENHARNESS_ENABLE_MEMORY."
        ),
    ),
    enable_web: bool | None = typer.Option(
        None,
        "--enable-web/--no-enable-web",
        hidden=True,
        help=(
            "Enable web tools — WebSearch + WebFetch (Phase 14 D29.3). "
            "Default OFF: tools are not registered and the system "
            "prompt picks up the anti-substitution paragraph "
            "(prevents the LLM from Grep'ing local files when asked "
            "for external info). When ON: tools register, the system "
            "prompt switches to positive guidance, and "
            "OPENHARNESS_WEB__API_KEY must be set (Tavily free tier "
            "at https://tavily.com). Overrides OPENHARNESS_WEB__ENABLED."
        ),
    ),
    compact_threshold: float | None = typer.Option(
        None,
        "--compact-threshold",
        hidden=True,
        min=0.0,
        max=1.0,
        help=(
            "Safety ratio for the full request input budget after output "
            "tokens are reserved. The estimate includes system instructions, "
            "Tool schemas, and Conversation. Overrides "
            "OPENHARNESS_COMPACT__THRESHOLD_RATIO (default 0.83)."
        ),
    ),
    no_auto_compact: bool = typer.Option(
        False,
        "--no-auto-compact",
        hidden=True,
        help=(
            "Disable semantic auto-compact and one-shot Prompt Too Long "
            "request recompilation. Useful for tests that need byte-stable "
            "request shape or when summarization cost is unwanted."
        ),
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help=(
            "Load the latest snapshot for the current cwd as the "
            "conversation's starting history; the prompt argument "
            "becomes the next user turn (Phase 12 D30.4). When the "
            "snapshot exists, ``--model`` / ``--max-tokens`` / etc. "
            "from the snapshot OVERRIDE CLI flags so the agent's "
            "reasoning chain stays consistent with what it saw. "
            "When no snapshot exists for cwd, warns + starts fresh."
        ),
    ),
    resume_id: str | None = typer.Option(
        None,
        "--resume-id",
        help=(
            "Resume the snapshot whose ``git_head`` starts with this "
            "prefix (Phase 12; Phase 13 will enable history/ rotation). "
            "Implies ``--resume``. Phase 12 has only one snapshot per "
            "cwd (``current.json``) so a non-matching prefix exits 1."
        ),
    ),
    llm_focus_state: bool | None = typer.Option(
        None,
        "--llm-focus-state/--no-llm-focus-state",
        hidden=True,
        help=(
            "Opt in to LLM-authored ``tool_metadata.task_focus_state`` "
            "(Phase 13 D31.7). Fires a secondary LLM call at turn "
            "end asking for the current goal + next_step in JSON, "
            "stores the result in the snapshot. Adds "
            "~1-2s per turn. Default OFF preserves Phase 12 zero-"
            "cost behavior. Override: "
            "OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE env var."
        ),
    ),
) -> None:
    """Stream a single LLM response (with tool dispatch) to stdout."""
    # loop-runtime L1: ``--output-format`` is meaningless without ``-p``.
    # text (T1) / json (T3) / stream-json (T4) are all wired.
    if output_format != "text" and not print_mode:
        typer.echo(
            "--output-format only applies in headless print mode (-p/--print).",
            err=True,
        )
        raise typer.Exit(code=2)

    # Worktree isolation is intentionally independent of completion policy.
    if isolate and not print_mode:
        typer.echo(
            "--isolate only applies in headless print mode (-p/--print).",
            err=True,
        )
        raise typer.Exit(code=2)
    if isolate and output_format != "json":
        typer.echo(
            "--isolate only supports --output-format json.",
            err=True,
        )
        raise typer.Exit(code=2)

    reviewer_posture_override = ReviewerPosture.AUTO if auto else None
    execution_posture_override = ExecutionPosture.DRY_RUN if dry_run else None

    # `--no-auto-truncate` is the only way to set ``auto_truncate=False`` via
    # CLI;no positive ``--auto-truncate`` flag (it's the default).
    auto_truncate_override: bool | None = False if no_auto_truncate else None

    common_run_ask_kwargs: dict[str, Any] = {
        "model_override": model,
        "max_tokens": max_tokens,
        "reviewer_posture_override": reviewer_posture_override,
        "execution_posture_override": execution_posture_override,
        "log_level_override": log_level,
        "log_format_override": log_format,
        "tool_result_cap_override": tool_result_cap,
        "auto_truncate_override": auto_truncate_override,
        "no_skills": no_skills,
        "no_commands": no_commands,
        "sandbox_override": sandbox,
        "sandbox_backend_override": sandbox_backend,
        "sandbox_image_override": sandbox_image,
        "sandbox_memory_override": sandbox_memory,
        "sandbox_cpus_override": sandbox_cpus,
        "sandbox_runtime_override": sandbox_runtime,
        "enable_plugin_hooks_override": enable_plugin_hooks,
        "enable_plugins_override": enable_plugins,
        "enable_memory_override": enable_memory,
        "enable_web_override": enable_web,
        "compact_threshold_override": compact_threshold,
        "no_auto_compact": no_auto_compact,
        # P12-T5: --resume / --resume-id. --resume-id implies
        # --resume so the user doesn't have to type both.
        "resume": resume or resume_id is not None,
        "resume_id": resume_id,
        "llm_focus_state_override": llm_focus_state,
        "max_turns": max_turns,
    }

    outcome: AskOutcome | None = None
    session: RunSession | None = None
    try:
        outcome, session = asyncio.run(
            _dispatch_ask(
                prompt,
                isolate=isolate,
                output_format=output_format,
                print_mode=print_mode,
                common_run_ask_kwargs=common_run_ask_kwargs,
            )
        )
    except ValidationError as exc:
        # Configuration error (Settings missing OPENHARNESS_API_KEY etc.):
        # name the missing fields without dumping a stack trace.
        typer.echo(f"Configuration error:\n{exc}", err=True)
        typer.echo(
            "\nHint: set OPENHARNESS_API_KEY and OPENHARNESS_BASE_URL (see README for setup).",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except AuthenticationFailure as exc:
        typer.echo(
            f"Authentication failed (HTTP {exc.status_code}): {exc}\n"
            "Hint: verify OPENHARNESS_API_KEY is correct and active.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except QuotaExceededFailure as exc:
        typer.echo(
            f"Quota exhausted (HTTP {exc.status_code}): {exc}\n"
            "Hint: switch to a Provider/model with available quota, or wait for its reset.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except RateLimitFailure as exc:
        typer.echo(
            f"Rate-limited after retries (HTTP {exc.status_code}): {exc}\n"
            "Hint: wait a moment and retry, or check your Provider quota.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except RequestFailure as exc:
        status = exc.status_code if exc.status_code is not None else "unknown"
        typer.echo(
            f"Request failed (HTTP {status}): {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except LoopError as exc:
        # P3-T2.2d: dedicated arm for loop-control-flow errors. The "Loop error:"
        # prefix differentiates this category from the generic
        # OpenHarnessError catch-all below; the embedded message
        # (e.g., LoopLimitExceeded already names --max-turns) carries the
        # remediation, so no separate Hint line is needed here.
        typer.echo(f"Loop error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except UnknownCommandError as exc:
        # P5b-T3: user invoked /<name> but no command with that name is
        # registered. UnknownCommandError inherits from OpenHarnessError
        # so we MUST catch it before the root catch-all below (or it
        # would fall into the generic "Error:" prefix).
        # The message already contains the available catalog (formatted
        # by UnknownCommandError.__init__) so no separate Hint line.
        typer.echo(f"Unknown command: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except UnknownBundleError as exc:
        # P5d-T4: slash command's ``mode:`` references a bundle that
        # isn't registered (or an unknown hook name inside a bundle).
        # Same UX shape as UnknownCommandError: prefix + embedded
        # catalog from the exception message + exit 1.
        typer.echo(f"Unknown {exc.kind}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except OpenHarnessError as exc:
        # Root catch-all for any OpenHarness error not handled above:
        # the rare OpenHarnessApiError-but-not-Auth/Rate/Request, plus
        # P3+ ToolError / PermissionError / HookError once they raise.
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # An isolated request is emitted after the worktree context closes so the
    # terminal session status can be attached to the JSON result.
    if session is not None:
        import json

        assert outcome is not None
        assert outcome.print_result is not None
        result_obj = build_result_obj(outcome.print_result, session_id=new_run_id())
        _attach_run_json_field(result_obj, session)
        typer.echo(json.dumps(result_obj))
        if outcome.stop_reason != "end_turn":
            typer.echo(
                f"run did not complete cleanly (stop_reason={outcome.stop_reason or 'none'})",
                err=True,
            )
            raise typer.Exit(code=1)
        return

    # loop-runtime L1 T2: run-level two-tier exit code for print mode. A clean
    # ``end_turn`` already exits 0 (no exception). A run that stopped without
    # completing -- e.g. ``max_tokens`` (output cap) -- raises no exception but
    # is not a clean finish; surface it as non-zero so outer loops can react.
    # Goal-level completion belongs to the interactive ``/goal`` controller.
    final_stop_reason = outcome.stop_reason if outcome is not None else None
    if print_mode and final_stop_reason != "end_turn":
        typer.echo(
            f"run did not complete cleanly (stop_reason={final_stop_reason or 'none'})",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command(
    hidden=True,
    help="Compatibility entry for the interactive session; use bare `oh`.",
)
def chat(
    model: str | None = typer.Option(None, "--model", "-m", help="Model name override."),
    max_tokens: int = typer.Option(
        DEFAULT_MAX_TOKENS, "--max-tokens", min=1, help="Max tokens per turn."
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        min=1,
        hidden=True,
        help="Optional Agent-loop turn cap; omitted means model-terminated.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Use the automated reviewer for exact permission requests.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List tool calls; don't execute."),
    log_level: LogLevel | None = typer.Option(None, "--log-level"),
    log_format: LogFormat | None = typer.Option(None, "--log-format"),
    tool_result_cap: int | None = typer.Option(None, "--tool-result-cap", hidden=True, min=0),
    no_auto_truncate: bool = typer.Option(False, "--no-auto-truncate"),
    no_skills: bool = typer.Option(False, "--no-skills"),
    no_commands: bool = typer.Option(False, "--no-commands"),
    sandbox: bool | None = typer.Option(None, "--sandbox/--no-sandbox"),
    sandbox_backend: SandboxBackendName | None = typer.Option(
        None,
        "--sandbox-backend",
        help="Verified backend [seatbelt|docker-command].",
    ),
    sandbox_image: str | None = typer.Option(None, "--sandbox-image"),
    sandbox_memory: str | None = typer.Option(None, "--sandbox-memory"),
    sandbox_cpus: float | None = typer.Option(None, "--sandbox-cpus"),
    sandbox_runtime: str | None = typer.Option(None, "--sandbox-runtime"),
    enable_plugin_hooks: bool | None = typer.Option(
        None,
        "--enable-plugin-hooks/--no-enable-plugin-hooks",
        hidden=True,
    ),
    enable_plugins: bool | None = typer.Option(
        None,
        "--enable-plugins/--no-enable-plugins",
        hidden=True,
        help="Enable plugin discovery + loading (Phase 9). Default OFF.",
    ),
    enable_memory: bool | None = typer.Option(
        None,
        "--enable-memory/--no-enable-memory",
        hidden=True,
        help=(
            "Enable the memory subsystem (Phase 10). Default ON. "
            "Per-turn relevance scoring + CLAUDE.md injection. "
            "Overrides OPENHARNESS_ENABLE_MEMORY."
        ),
    ),
    enable_web: bool | None = typer.Option(
        None,
        "--enable-web/--no-enable-web",
        hidden=True,
        help=(
            "Enable WebSearch + WebFetch tools (Phase 14). Default "
            "OFF; system prompt picks up anti-substitution paragraph "
            "to prevent Grep-on-local-files hallucination. ON "
            "requires OPENHARNESS_WEB__API_KEY (Tavily free tier). "
            "Overrides OPENHARNESS_WEB__ENABLED."
        ),
    ),
    compact_threshold: float | None = typer.Option(
        None,
        "--compact-threshold",
        hidden=True,
        min=0.0,
        max=1.0,
        help=(
            "Full-request input safety ratio after output reservation. "
            "Overrides OPENHARNESS_COMPACT__THRESHOLD_RATIO."
        ),
    ),
    no_auto_compact: bool = typer.Option(
        False,
        "--no-auto-compact",
        hidden=True,
        help="Disable semantic auto-compact and Prompt Too Long request recompilation.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help=(
            "Load the latest snapshot for the current cwd as starting "
            "history; prints banner with message count + git_head "
            "(Phase 12 D30.4). No snapshot for cwd → warns + starts fresh."
        ),
    ),
    resume_id: str | None = typer.Option(
        None,
        "--resume-id",
        help=(
            "Resume the snapshot whose ``git_head`` matches this prefix "
            "(Phase 12). Implies ``--resume``. Non-matching prefix exits 1."
        ),
    ),
    llm_focus_state: bool | None = typer.Option(
        None,
        "--llm-focus-state/--no-llm-focus-state",
        hidden=True,
        help="Opt in to LLM-authored task_focus_state (Phase 13 D31.7). Default OFF.",
    ),
) -> None:
    """Multi-turn REPL and the sole public agent-starting command."""
    reviewer_posture_override = ReviewerPosture.AUTO if auto else None
    execution_posture_override = ExecutionPosture.DRY_RUN if dry_run else None

    auto_truncate_override: bool | None = False if no_auto_truncate else None

    try:
        asyncio.run(
            _run_chat(
                initial_prompt=None,
                model_override=model,
                max_tokens=max_tokens,
                max_turns=max_turns,
                reviewer_posture_override=reviewer_posture_override,
                execution_posture_override=execution_posture_override,
                log_level_override=log_level,
                log_format_override=log_format,
                tool_result_cap_override=tool_result_cap,
                auto_truncate_override=auto_truncate_override,
                no_skills=no_skills,
                no_commands=no_commands,
                sandbox_override=sandbox,
                sandbox_backend_override=sandbox_backend,
                sandbox_image_override=sandbox_image,
                sandbox_memory_override=sandbox_memory,
                sandbox_cpus_override=sandbox_cpus,
                sandbox_runtime_override=sandbox_runtime,
                enable_plugin_hooks_override=enable_plugin_hooks,
                enable_plugins_override=enable_plugins,
                enable_memory_override=enable_memory,
                enable_web_override=enable_web,
                compact_threshold_override=compact_threshold,
                no_auto_compact=no_auto_compact,
                # P12-T5: --resume / --resume-id (latter implies former).
                resume=resume or resume_id is not None,
                resume_id=resume_id,
                llm_focus_state_override=llm_focus_state,
            )
        )
    except ValidationError as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc
    except QuotaExceededFailure as exc:
        typer.echo(f"Quota exhausted (HTTP {exc.status_code}): {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (AuthenticationFailure, RateLimitFailure, RequestFailure) as exc:
        typer.echo(f"API error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except SandboxUnavailableError as exc:
        typer.echo(f"Sandbox error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except OpenHarnessError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------- #
# Configuration and runtime inspection commands                               #
# --------------------------------------------------------------------------- #


_CONFIG_TEMPLATE = """\
# OpenHarness user-global configuration
#
# This file is loaded by ``oh`` and internal runtimes as a LOWER-
# precedence layer than a ``.env`` in the project's cwd, which in turn
# is lower precedence than env vars set in your shell.
#
# Uncomment + set the values you want. The two required fields are
# OPENHARNESS_API_KEY and OPENHARNESS_BASE_URL.

# OPENHARNESS_API_KEY="sk-..."
# OPENHARNESS_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
# OPENHARNESS_MODEL="qwen-plus"
# OPENHARNESS_MAX_TURNS=100  # optional; omitted means model-terminated
# OPENHARNESS_GOAL_MAX_AUTO_TURNS=100  # optional; omit for goal-driven completion

# Canonical permission intent (nested overrides preserve workspace defaults)
# OPENHARNESS_PERMISSION_PROFILE__NETWORK__ENABLED="true"
# OPENHARNESS_PERMISSION_PROFILE__NETWORK__ALLOW_DOMAINS='["pypi.org"]'
# OPENHARNESS_PERMISSION_PROFILE__EXTERNAL_TOOLS__WEB="allow"

# Observability
# OPENHARNESS_LOG_LEVEL="WARNING"
# OPENHARNESS_LOG_FORMAT="console"  # console / json

# Sandbox
# OPENHARNESS_SANDBOX_ENABLED="false"
# OPENHARNESS_SANDBOX_IMAGE="python:3.12-slim"
# OPENHARNESS_SANDBOX_RUNTIME="runc"  # runc / runsc (gVisor)

# Plugin hooks (opt-in)
# OPENHARNESS_ENABLE_PLUGIN_HOOKS="false"
"""


def _redact_secret(value: str | None) -> str:
    """Last-4-chars redaction for secrets in ``oh config show``."""
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


# ----- oh inspect tools ------------------------------------------------------

tools_app = typer.Typer(name="tools", help="Inspect registered tools.")
inspect_app.add_typer(tools_app, name="tools")


@tools_app.command("list", help="List the tools registered in the default registry.")
def tools_list(
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text (default) or json.",
    ),
) -> None:
    """List the 6 built-in tools (Read/Write/Edit/Bash/Grep/Agent).

    MCP adapters + LoadSkill register conditionally based on Settings
    + filesystem state, so this offline listing shows only the
    framework's default catalog. This command intentionally reports the
    static registry rather than running a model turn.
    """
    from openharness.tools import create_default_tool_registry

    registry = create_default_tool_registry()
    if format == "json":
        import json

        data = [
            {
                "name": tool.name,
                "description": tool.description,
                "is_read_only": tool.is_read_only,
                "trust_source": tool.trust_source,
            }
            for tool in registry.list_tools()
        ]
        typer.echo(json.dumps(data, indent=2))
        return
    for tool in registry.list_tools():
        ro = " [read-only]" if tool.is_read_only else ""
        typer.echo(f"{tool.name:12s}{ro:13s} {tool.description}")


@tools_app.command("show", help="Show a tool's full schema + metadata.")
def tools_show(
    name: str = typer.Argument(..., help="Tool name (case-sensitive)."),
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text (default) or json.",
    ),
) -> None:
    """Print name, description, is_read_only, trust_source, input_schema."""
    import json

    from openharness.tools import create_default_tool_registry

    registry = create_default_tool_registry()
    try:
        tool = registry.get(name)
    except KeyError:
        available = ", ".join(t.name for t in registry.list_tools())
        typer.echo(
            f"Unknown tool: {name!r}; available: {available}",
            err=True,
        )
        raise typer.Exit(code=1) from None

    schema = tool.input_model.model_json_schema()
    if format == "json":
        typer.echo(
            json.dumps(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "is_read_only": tool.is_read_only,
                    "trust_source": tool.trust_source,
                    "input_schema": schema,
                },
                indent=2,
            )
        )
        return

    typer.echo(f"name:         {tool.name}")
    typer.echo(f"description:  {tool.description}")
    typer.echo(f"is_read_only: {tool.is_read_only}")
    typer.echo(f"trust_source: {tool.trust_source}")
    typer.echo("input_schema:")
    for line in json.dumps(schema, indent=2).splitlines():
        typer.echo(f"  {line}")


# ----- oh config -------------------------------------------------------------

# Field names whose values are redacted on display.
_SECRET_FIELDS = frozenset({"api_key"})


@config_app.command("show", help="Print the effective Settings (env-resolved).")
def config_show(
    format: str = typer.Option("text", "--format", "-f"),
) -> None:
    """Print Settings as resolved from env vars + .env files.

    ``api_key`` is redacted (last 4 chars + ``***``). If required
    fields aren't set, prints a friendly hint instead of a stack
    trace.
    """
    import json

    try:
        settings = _load_settings()
    except ValidationError as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        typer.echo(
            "\nHint: set OPENHARNESS_API_KEY and OPENHARNESS_BASE_URL "
            "(via shell env, ~/.openharness/.env, or ./.env), then "
            "re-run ``oh config show``.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    data = settings.model_dump()
    for secret_field in _SECRET_FIELDS:
        if secret_field in data:
            data[secret_field] = _redact_secret(data[secret_field])

    if format == "json":
        # ``default=str`` covers tuple-of-pydantic-models like
        # mcp_servers; pydantic v2's model_dump already converts most.
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    width = max(len(k) for k in data) + 2
    for k, v in data.items():
        typer.echo(f"{k:<{width}}= {v!r}")


@config_app.callback(invoke_without_command=True)
def _config_root(ctx: typer.Context) -> None:
    """Show effective configuration when no explicit action is selected."""
    if ctx.invoked_subcommand is None:
        config_show(format="text")


@config_app.command("edit", help="Open ~/.openharness/.env in $EDITOR.")
def config_edit() -> None:
    """Open the user-global config file in ``$EDITOR``(or ``nano`` /
    ``vi``). Creates the file with a commented template if absent.

    The file is loaded by ``_load_settings()`` as a lower-precedence
    layer than ``./.env`` from cwd, which is lower than shell env vars.
    """
    import os
    import shutil
    import subprocess

    config_dir = Path.home() / ".openharness"
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    if not env_file.exists():
        env_file.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        typer.echo(f"Created {env_file} with template.")

    editor = os.environ.get("EDITOR") or shutil.which("nano") or shutil.which("vi")
    if editor is None:
        typer.echo(
            f"$EDITOR is unset and neither nano nor vi found. Edit {env_file} manually.",
            err=True,
        )
        raise typer.Exit(code=1)

    # ``check=False``: editor exits with non-zero on user abort (e.g.,
    # vi :q!); we don't want to surface that as an OpenHarness error.
    subprocess.run([editor, str(env_file)], check=False)


# ----- oh inspect hooks ------------------------------------------------------

hooks_app = typer.Typer(name="hooks", help="Inspect framework + plugin hooks.")
inspect_app.add_typer(hooks_app, name="hooks")


@hooks_app.command("list", help="List built-in hooks (and plugins with --enable-plugin-hooks).")
def hooks_list(
    format: str = typer.Option("text", "--format", "-f"),
    enable_plugin_hooks: bool = typer.Option(
        False,
        "--enable-plugin-hooks/--no-enable-plugin-hooks",
        hidden=True,
        help="Also discover entry-point + filesystem plugin hooks.",
    ),
) -> None:
    """List ``BUILTIN_HOOKS`` keys + event; with the flag, also list
    discovered plugins.

    Plugin discovery here is offline-safe — it never invokes the
    hook;just inspects ``HookSpec.event`` + the source label.
    """
    import json

    from openharness.bundles import BUILTIN_HOOKS

    rows: list[dict[str, str]] = [
        {"name": name, "event": event, "source": "builtin"}
        for name, (event, _hook) in BUILTIN_HOOKS.items()
    ]

    if enable_plugin_hooks:
        from openharness.bundles import (
            discover_filesystem_hook_plugins,
            discover_plugin_hooks,
        )

        for name, spec in discover_plugin_hooks().items():
            rows.append({"name": name, "event": spec.event, "source": "entry-point"})
        fs = discover_filesystem_hook_plugins(
            global_dir=Path.home() / ".openharness" / "hooks",
            project_dir=Path.cwd() / ".openharness" / "hooks",
        )
        for name, spec in fs.items():
            rows.append({"name": name, "event": spec.event, "source": "filesystem"})

    if format == "json":
        typer.echo(json.dumps(rows, indent=2))
        return

    name_width = max((len(r["name"]) for r in rows), default=4) + 2
    event_width = max((len(r["event"]) for r in rows), default=5) + 2
    for r in rows:
        typer.echo(f"{r['name']:<{name_width}}{r['event']:<{event_width}}({r['source']})")


@hooks_app.command("describe", help="Describe a hook (event + docstring).")
def hooks_describe(
    name: str = typer.Argument(..., help="Hook name."),
    enable_plugin_hooks: bool = typer.Option(
        False,
        "--enable-plugin-hooks/--no-enable-plugin-hooks",
        hidden=True,
        help="Look up plugin hooks too.",
    ),
) -> None:
    """Print event + docstring for ``name``.

    Built-in hooks always resolve; plugin hooks require the flag (so
    we don't import unknown ``.py`` files when the user didn't opt
    in).
    """
    import inspect

    from openharness.bundles import BUILTIN_HOOKS

    event: str
    if name in BUILTIN_HOOKS:
        builtin_event, builtin_hook = BUILTIN_HOOKS[name]
        event = builtin_event
        source = "builtin"
        doc = inspect.getdoc(builtin_hook) or "(no docstring)"
    elif enable_plugin_hooks:
        from openharness.bundles import (
            discover_filesystem_hook_plugins,
            discover_plugin_hooks,
        )

        plugin_catalog: dict[str, HookSpec] = dict(discover_plugin_hooks())
        for fs_name, fs_spec in discover_filesystem_hook_plugins(
            global_dir=Path.home() / ".openharness" / "hooks",
            project_dir=Path.cwd() / ".openharness" / "hooks",
        ).items():
            plugin_catalog.setdefault(fs_name, fs_spec)

        if name not in plugin_catalog:
            available = sorted(set(BUILTIN_HOOKS.keys()) | set(plugin_catalog.keys()))
            typer.echo(
                f"Unknown hook: {name!r}; available: {', '.join(available)}",
                err=True,
            )
            raise typer.Exit(code=1)
        spec = plugin_catalog[name]
        event = spec.event
        source = "plugin"
        doc = inspect.getdoc(spec.hook) or "(no docstring)"
    else:
        available = sorted(BUILTIN_HOOKS.keys())
        typer.echo(
            f"Unknown hook: {name!r}; available (built-in): "
            f"{', '.join(available)}. "
            f"Add --enable-plugin-hooks to include plugins.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"name:   {name}")
    typer.echo(f"event:  {event}")
    typer.echo(f"source: {source}")
    typer.echo("")
    typer.echo(doc)


# ----- oh state memory -------------------------------------------------------
# P10-T5: read-only inspection subcommands per D28.11.
# No add / edit / remove — write surface defers to Phase 11's extraction
# secondary pass + future CLI add command.

memory_app = typer.Typer(
    name="memory",
    help="Inspect the current project's memory store (read-only).",
)
state_app.add_typer(memory_app, name="memory")


# P17-T4 (D37.4): name + description truncation caps for the text
# renderer. Wide enough to be useful, narrow enough to keep one
# memory per line on a standard 120-col terminal.
_LIST_NAME_MAX = 40
_LIST_DESCRIPTION_MAX = 60


def _truncate(s: str, limit: int) -> str:
    """Truncate ``s`` to at most ``limit`` chars, ending with a single-char
    ellipsis when truncation occurred. Matches the visual contract the
    list renderer ships."""
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


@memory_app.command("list", help="List memories in this project's store.")
def memory_list(
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text (default) or json.",
    ),
) -> None:
    """List memories sorted alphabetically by name (case-insensitive).

    Text renderer displays three columns per D37.4: ``name`` (truncated
    to 40 chars), ``type``, ``description`` (truncated to 60 chars).
    Empty store → single ``(no memories yet)`` line. Malformed files
    don't appear (``parse_memory`` returns ``None``; the store skips
    them silently). To see warnings, re-run with ``--log-level INFO``.
    """
    import json

    from openharness.memory.paths import get_project_memory_dir
    from openharness.memory.store import FilesystemMemoryStore

    storage_dir = get_project_memory_dir(Path.cwd())
    store = FilesystemMemoryStore(project_dir=storage_dir)
    memories = list(store.discover().values())

    if not memories:
        if format == "json":
            typer.echo("[]")
            return
        typer.echo("(no memories yet)")
        return

    # D37.4: alphabetical by name, case-insensitive.
    memories.sort(key=lambda m: m.name.lower())

    if format == "json":
        data = [
            {
                "name": m.name,
                "id": m.id,
                "type": m.type.value,
                "scope": m.scope.value,
                "use_count": m.use_count,
                "last_used_at": (m.last_used_at.isoformat() if m.last_used_at else None),
                "updated_at": m.updated_at.isoformat(),
                "description": m.description,
                "source_path": str(m.source_path),
            }
            for m in memories
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    # Text format: name / type / description columns per D37.4.
    # Field fallbacks for defensive UX even though parse_memory rejects
    # missing description / type today — keeps the renderer robust if
    # a future schema relaxation lets these through.
    name_width = min(_LIST_NAME_MAX, max(len(m.name) for m in memories)) + 2
    type_width = max(len(m.type.value) for m in memories) + 2
    for m in memories:
        name_field = _truncate(m.name, _LIST_NAME_MAX)
        type_field = m.type.value if m.type is not None else "(unknown)"
        if m.description and m.description.strip():
            desc_field = _truncate(m.description, _LIST_DESCRIPTION_MAX)
        else:
            desc_field = "(no description)"
        typer.echo(f"{name_field:<{name_width}}{type_field:<{type_width}}{desc_field}")


@memory_app.command("show", help="Show a memory's full frontmatter + body.")
def memory_show(
    name_or_id: str = typer.Argument(..., help="Memory name OR id."),
) -> None:
    """Look up by ``name`` first, fall back to ``id``.

    Prints the raw markdown file content (frontmatter + body) so the
    user sees exactly what's on disk. Missing memory → exit 1 with
    available names listed (mirrors :class:`UnknownCommandError`).
    """
    from openharness.memory.paths import get_project_memory_dir
    from openharness.memory.store import FilesystemMemoryStore

    storage_dir = get_project_memory_dir(Path.cwd())
    store = FilesystemMemoryStore(project_dir=storage_dir)
    catalog = store.discover()

    # Lookup by name first
    memory = catalog.get(name_or_id)
    # Fallback: lookup by id
    if memory is None:
        for m in catalog.values():
            if m.id == name_or_id:
                memory = m
                break

    if memory is None:
        available = sorted(catalog.keys())
        available_str = ", ".join(available) if available else "(none — storage empty)"
        typer.echo(
            f"Unknown memory: {name_or_id!r}; available: {available_str}",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(memory.source_path.read_text(encoding="utf-8"))


@memory_app.command("path", help="Print the memory storage directory for cwd.")
def memory_path() -> None:
    """Resolve + print the per-project memory dir path. Exits 0 even
    when the directory doesn't exist yet — the path is computable per
    D28.1 (lazy mkdir on first write).
    """
    from openharness.memory.paths import get_project_memory_dir

    typer.echo(str(get_project_memory_dir(Path.cwd())))


# --------------------------------------------------------------------------- #
# Plugin inspection                                                           #
# --------------------------------------------------------------------------- #
#
# Read-only discovery — calls PluginLoader.discover_with_format() only;
# no fan_out so Python-hook modules don't get imported as a side effect
# of asking "what plugins do I have installed?". Same role as
# ``oh state memory list`` / ``oh state snapshots list``: fast, side-effect-free
# introspection entry point that confirms a freshly-dropped plugin dir
# was actually discovered.

plugins_app = typer.Typer(
    name="plugins",
    help="Inspect installed plugins (read-only).",
)
inspect_app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list", help="List installed plugins under ~/.openharness/plugins/.")
def plugins_list(
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text (default) or json.",
    ),
    log_level: LogLevel | None = typer.Option(
        None,
        "--log-level",
        help=(
            "Logging level for discovery side events. Default WARNING — "
            "use INFO to see plugin_discovered for each plugin, or DEBUG "
            "for full discovery noise."
        ),
    ),
) -> None:
    """List discovered plugins with format / version / component counts.

    Text format columns (alphabetical by plugin name):

        NAME                          FORMAT  VERSION  SKILLS  MCP_SERVERS

    Empty plugins root or no plugins discovered → ``(no plugins installed)``
    text or ``[]`` JSON. CC plugins always report ``MCP_SERVERS=0`` per
    D39.9 (``.mcp.json`` is silently ignored in M2 — HTTP MCP transport
    support is a future phase). Plugins whose manifest fails to parse
    do not appear in the output; re-run with ``--log-level INFO`` to see
    the discovery events (``plugin_discovered``, ``plugin_dual_manifest``).
    """
    import json

    # Configure structlog once so per-discovery INFO events stay out of
    # stdout in the default (WARNING) case but become visible behind
    # ``--log-level INFO``. Without this call structlog's default
    # PrintLoggerFactory writes every level to stdout regardless.
    configure_logging(level=log_level or "WARNING", format="console")

    plugins_dir = Path.home() / ".openharness" / "plugins"
    loader = PluginLoader(plugins_dir)
    results = loader.discover_with_format()

    if not results:
        if format == "json":
            typer.echo("[]")
            return
        typer.echo("(no plugins installed)")
        return

    # D39.7 alphabetical-by-name. tuple() unpacking + sort separately.
    ordered = sorted(results.items(), key=lambda kv: kv[0])

    if format == "json":
        data = [
            {
                "name": name,
                "format": fmt,
                "version": manifest.version,
                "skills_count": len(manifest.skills),
                "mcp_servers_count": len(manifest.mcp_servers),
                "source_path": str(manifest.source_path),
            }
            for name, (manifest, fmt) in ordered
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    # Text format: 5 columns. Widths sized to data + minimum header width.
    name_width = max(len(name) for name, _ in ordered)
    name_width = max(name_width, len("NAME")) + 2
    fmt_width = max(len("FORMAT"), 2) + 2
    version_width = max(len(manifest.version) for _, (manifest, _) in ordered)
    version_width = max(version_width, len("VERSION")) + 2
    skills_col_width = len("SKILLS") + 2
    typer.echo(
        f"{'NAME':<{name_width}}"
        f"{'FORMAT':<{fmt_width}}"
        f"{'VERSION':<{version_width}}"
        f"{'SKILLS':<{skills_col_width}}"
        "MCP_SERVERS"
    )
    for name, (manifest, fmt) in ordered:
        typer.echo(
            f"{name:<{name_width}}"
            f"{fmt:<{fmt_width}}"
            f"{manifest.version:<{version_width}}"
            f"{len(manifest.skills):<{skills_col_width}}"
            f"{len(manifest.mcp_servers)}"
        )


# --------------------------------------------------------------------------- #
# Snapshot inspection and maintenance                                         #
# --------------------------------------------------------------------------- #
#
# Mirrors the ``oh state memory list / show / path`` pattern:
# typer sub-app with 3 read-mostly subcommands for user-side
# introspection. ``list`` is discoverability, ``show`` is inspection,
# ``gc`` is force-cleanup outside the per-turn eager rotation path.

snapshot_app = typer.Typer(
    name="snapshot",
    help="Inspect and maintain conversation snapshots for the current project.",
)
state_app.add_typer(snapshot_app, name="snapshots")


def _snapshot_list_entries(cwd: Path) -> list[tuple[str, Path]]:
    """Return ``[(id, path), ...]`` for ``current.json`` + history/ entries.

    Sorted: ``current`` always first; history/ newest-first by mtime.
    Returns empty list when no snapshot dir exists.
    """
    from openharness.services.snapshot import get_snapshot_dir

    snapshot_dir = get_snapshot_dir(cwd)
    if not snapshot_dir.exists():
        return []

    entries: list[tuple[str, Path]] = []
    current_path = snapshot_dir / "current.json"
    if current_path.exists():
        entries.append(("current", current_path))

    history_dir = snapshot_dir / "history"
    if history_dir.exists():
        history_paths: list[tuple[float, Path]] = []
        for path in history_dir.iterdir():
            if path.suffix != ".json":
                continue  # pragma: no cover — defensive: non-.json file in history/
            try:
                history_paths.append((path.stat().st_mtime, path))
            except OSError:  # pragma: no cover — defensive: file vanished mid-scan
                continue
        history_paths.sort(key=lambda e: e[0], reverse=True)
        for _mtime, path in history_paths:
            # ID = filename without .json suffix
            entries.append((path.stem, path))

    return entries


def _format_age(created_iso: str) -> str:
    """Human-readable relative age from an ISO timestamp string."""
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    try:
        created = _dt.fromisoformat(created_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "?"
    now = _dt.now(_tz.utc)
    delta = now - created
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


@snapshot_app.command("list", help="List snapshots (current + history/) for cwd.")
def snapshot_list(
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text (default) or json.",
    ),
) -> None:
    """Tabular listing — ``current`` first, then history/ newest-first.

    Columns: ID / CREATED / MESSAGES / GIT_HEAD / AGE.
    Empty case prints "(no snapshots — storage at <path>)".
    """
    import json as _json

    from openharness.services.snapshot import get_snapshot_dir

    cwd = Path.cwd()
    entries = _snapshot_list_entries(cwd)

    if not entries:
        if format == "json":
            typer.echo("[]")
            return
        snapshot_dir = get_snapshot_dir(cwd)
        typer.echo(f"(no snapshots — storage at {snapshot_dir})")
        return

    # Parse each entry's JSON to extract display fields
    parsed: list[dict[str, Any]] = []
    for id_, path in entries:
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            continue
        parsed.append(
            {
                "id": id_,
                "created_at": data.get("created_at", "?"),
                "message_count": len(data.get("messages", [])),
                "git_head": data.get("git_head") or "(no git)",
                "path": str(path),
            }
        )

    if format == "json":
        typer.echo(_json.dumps(parsed, indent=2))
        return

    # Text format: aligned columns
    id_width = max(len(p["id"]) for p in parsed) + 2
    id_width = max(id_width, 12)  # min "ID" header width
    typer.echo(f"{'ID':<{id_width}}{'CREATED':<22}{'MESSAGES':<10}{'GIT_HEAD':<11}{'AGE':<10}")
    for p in parsed:
        created = p["created_at"]
        if len(created) > 19:
            # ISO datetime — render the date/time part only
            created = created[:19].replace("T", " ")
        typer.echo(
            f"{p['id']:<{id_width}}"
            f"{created:<22}"
            f"{p['message_count']:<10}"
            f"{p['git_head']:<11}"
            f"{_format_age(p['created_at']):<10}"
        )


def _resolve_snapshot_id(cwd: Path, snapshot_id: str) -> Path:
    """Look up a snapshot by ``current`` literal or git_head prefix.

    Raises ``typer.Exit(1)`` on not-found or ambiguous prefix
    (with stderr message listing matches).
    """
    entries = _snapshot_list_entries(cwd)

    if snapshot_id == "current":
        for id_, path in entries:
            if id_ == "current":
                return path
        typer.echo(f"No current snapshot for cwd={cwd}", err=True)
        raise typer.Exit(code=1)

    # Prefix match against history entry IDs (and current.json's git_head if requested)
    matches: list[tuple[str, Path]] = []
    for id_, path in entries:
        if id_ == "current":
            continue
        # ID format: <git_head>-<YYYYMMDDhhmmss> (or "<git_head>-<YYYYMMDDhhmmss>-<n>")
        # Match against the leading git_head portion
        git_head_part = id_.split("-")[0]
        if git_head_part.startswith(snapshot_id) or id_.startswith(snapshot_id):
            matches.append((id_, path))

    if not matches:
        typer.echo(
            f"No snapshot for cwd={cwd} matching id prefix {snapshot_id!r}",
            err=True,
        )
        raise typer.Exit(code=1)

    if len(matches) > 1:
        match_ids = ", ".join(id_ for id_, _ in matches)
        typer.echo(
            f"Ambiguous snapshot id {snapshot_id!r} — matches: {match_ids}",
            err=True,
        )
        raise typer.Exit(code=1)

    return matches[0][1]


@snapshot_app.command("show", help="Render a specific snapshot for inspection.")
def snapshot_show(
    snapshot_id: str = typer.Argument(
        ..., help="Snapshot ID: ``current`` literal or git_head prefix."
    ),
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text (default) or json (raw on-disk file).",
    ),
) -> None:
    """Print the snapshot's metadata + message one-liners.

    ``--format json`` prints the raw on-disk JSON for tooling /
    machine consumption. Text format uses Phase 11's message
    one-liner shape (80 char cap, newlines → spaces).
    """
    import json as _json

    cwd = Path.cwd()
    path = _resolve_snapshot_id(cwd, snapshot_id)

    raw = path.read_text(encoding="utf-8")
    if format == "json":
        typer.echo(raw)
        return

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        typer.echo(f"Snapshot file unparseable: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Render header
    typer.echo(f"id:            {snapshot_id}")
    typer.echo(f"path:          {path}")
    typer.echo(f"created_at:    {data.get('created_at', '?')}")
    typer.echo(f"git_head:      {data.get('git_head') or '(no git)'}")
    typer.echo(f"model:         {data.get('model', '?')}")
    typer.echo(f"message_count: {len(data.get('messages', []))}")
    system_prompt = data.get("system_prompt") or ""
    if len(system_prompt) > 240:
        system_prompt = system_prompt[:240] + "..."
    typer.echo(f"system_prompt: {system_prompt}")
    typer.echo("")
    typer.echo("messages:")
    for i, msg in enumerate(data.get("messages", []), start=1):
        role = msg.get("role", "?")
        content = msg.get("content") or []
        oneliner = ""
        if content:
            block = content[0]
            block_type = block.get("type", "?")
            if block_type == "text":
                oneliner = block.get("text", "")
            elif block_type == "tool_use":
                oneliner = f"[tool] {block.get('name', '?')}"
            elif block_type == "tool_result":
                oneliner = f"[result] {str(block.get('content', ''))[:60]}"
            else:
                oneliner = f"[{block_type}]"
        # Collapse newlines + cap at 80
        oneliner = oneliner.replace("\n", " ").replace("\r", " ")[:80]
        typer.echo(f"  {i:3d}. [{role}] {oneliner}")


@snapshot_app.command("gc", help="Force-rotate history/ entries exceeding count/age thresholds.")
def snapshot_gc(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report what WOULD be dropped without actually unlinking.",
    ),
) -> None:
    """Manual force-cleanup outside the per-turn eager rotation path.

    Reads ``settings.snapshot.history.*`` thresholds. ``--dry-run``
    lists what would be dropped without doing it; exit 0 either way.
    """
    from openharness.services.snapshot import (
        _gc_history,
        get_snapshot_dir,
    )

    settings = _load_settings()
    cwd = Path.cwd()
    snapshot_dir = get_snapshot_dir(cwd)
    history_dir = snapshot_dir / "history"

    if not history_dir.exists() or not any(history_dir.iterdir()):
        typer.echo("(no snapshots to gc)")
        return

    max_count = settings.snapshot.history.max_count
    max_age_days = settings.snapshot.history.max_age_days

    if dry_run:
        # Replicate _gc_history's dropping logic in a side-effect-free way
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        entries: list[tuple[float, Path]] = []
        for p in history_dir.iterdir():
            if p.suffix != ".json":
                continue  # pragma: no cover — defensive
            try:
                entries.append((p.stat().st_mtime, p))
            except OSError:  # pragma: no cover — defensive
                continue
        entries.sort(key=lambda e: e[0], reverse=True)

        would_drop: list[Path] = []
        # Count arm
        if max_count >= 0:
            tail = entries[max_count:] if max_count > 0 else entries[:]
            would_drop.extend(p for _m, p in tail)
            kept = entries[:max_count] if max_count > 0 else []
        else:
            kept = entries
        # Age arm
        if max_age_days > 0:
            cutoff = _dt.now(_tz.utc).timestamp() - max_age_days * 86400
            for mtime, p in kept:
                if mtime < cutoff:
                    would_drop.append(p)

        if not would_drop:
            typer.echo(f"(nothing to drop — {len(entries)} entries within thresholds)")
            return
        typer.echo(f"(dry-run) Would drop {len(would_drop)} snapshot(s):")
        for p in would_drop:
            typer.echo(f"  {p}")
        return

    dropped = _gc_history(history_dir, max_count=max_count, max_age_days=max_age_days)
    typer.echo(f"Dropped {len(dropped)} snapshot(s) from history/")


# --------------------------------------------------------------------------- #
# ``oh dev eval`` — capability evals                                          #
# --------------------------------------------------------------------------- #


eval_app = typer.Typer(
    name="eval",
    help="Run capability evals.",
)
dev_app.add_typer(eval_app, name="eval")

_SCRIPT_EVALS = (
    "error_feedback",
    "memory_compact",
    "memory_read",
    "permission_review",
    "skill_trigger",
    "tool_choice",
    "verify_judge",
)


def _repository_root() -> Path:
    """Return the checkout root that owns the contributor eval scripts."""
    return Path(__file__).resolve().parents[2]


def _require_eval_mode(mode: str | None) -> str:
    if mode is None:
        typer.echo(
            "Missing option '--mode'. Choose live, record, or replay explicitly; "
            "live and record call the configured model.",
            err=True,
        )
        raise typer.Exit(code=2)
    if mode not in ("live", "record", "replay"):
        typer.echo(
            f"Invalid --mode={mode!r}; expected one of: live / record / replay",
            err=True,
        )
        raise typer.Exit(code=2)
    return mode


def _run_manual_eval(
    name: str,
    *,
    mode: str | None,
    model: str | None,
    case_id: str | None,
) -> None:
    """Launch one repository eval only after an explicit mode selection."""
    selected_mode = _require_eval_mode(mode)
    root = _repository_root()
    script = root / "scripts" / f"spike_{name}_eval.py"
    if not script.is_file():
        typer.echo(f"Eval script not found: {script}", err=True)
        raise typer.Exit(code=1)

    env = dict(os.environ)
    env["OPENHARNESS_EVAL_MODE"] = selected_mode
    env.pop("OPENHARNESS_EVAL_CASE", None)
    if model is not None:
        env["OPENHARNESS_MODEL"] = model
    if case_id is not None:
        env["OPENHARNESS_EVAL_CASE"] = case_id

    completed = subprocess.run([sys.executable, str(script)], cwd=root, env=env, check=False)
    if completed.returncode:
        raise typer.Exit(code=completed.returncode)


def _register_script_eval(name: str) -> None:
    def command(
        mode: str | None = typer.Option(
            None,
            "--mode",
            "-m",
            help="Required: live, record, or replay.",
        ),
        model: str | None = typer.Option(
            None,
            "--model",
            help="Override OPENHARNESS_MODEL for this run.",
        ),
        case_id: str | None = typer.Option(
            None,
            "--case",
            help="Run exactly one dataset case by case_id.",
        ),
    ) -> None:
        _run_manual_eval(name, mode=mode, model=model, case_id=case_id)

    command.__name__ = f"eval_{name}"
    eval_app.command(name, help=f"Run the {name} eval manually.")(command)


for _script_eval in _SCRIPT_EVALS:
    _register_script_eval(_script_eval)


@eval_app.command(
    "focus_state",
    help=(
        "Run eval against services/focus_state.py — 8 capability-anchored cases, "
        "4 scorers (parse + keyword + substring + LLM-judge), version-stamped results."
    ),
)
def eval_focus_state(  # pragma: no cover — experimental eval surface (excluded from coverage gate)
    mode: str | None = typer.Option(
        None,
        "--mode",
        "-m",
        help=(
            "Cassette mode: 'live' (real LLM, no cassette save), "
            "'record' (real LLM + save cassette), "
            "'replay' (load cassette, no LLM call)."
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Override OPENHARNESS_MODEL for this run.",
    ),
    no_results: bool = typer.Option(
        False,
        "--no-results",
        help="Skip writing results JSONL (default: write to evals/focus_state/results/).",
    ),
    case_id: str | None = typer.Option(
        None,
        "--case",
        help="Run exactly one dataset case by case_id.",
    ),
) -> None:
    """Run capability-anchored eval against ``services/focus_state.py``.

    Mirrors ``scripts/spike_focus_state_eval.py`` (legacy debug entry) but
    as a proper CLI subcommand. Dataset, cassettes, and results all live
    under ``evals/focus_state/``. Cassette + version stamping per D33 + D34.

    Self-preference accepted (judge_model = main model, D32.5).
    """
    import asyncio
    from typing import cast

    from openharness.eval._printers import print_case, print_summary
    from openharness.eval.cassette import CassetteMode, CassetteStore
    from openharness.eval.protocol import Scorer  # noqa: TC001 — used as list[Scorer] annotation
    from openharness.eval.results import (
        RunMetadata,
        build_result_filename,
        compute_file_hash,
        compute_rubric_hashes,
        compute_text_hash,
        get_git_info,
        utc_iso_now,
        write_run_results,
    )
    from openharness.eval.rubrics import CAPABILITY_RUBRICS
    from openharness.eval.runner import run_eval
    from openharness.eval.scorers import (
        CapabilityAssertionsScorer,
        CapabilityLLMJudgeScorer,
        GoalKeywordMatchScorer,
        ParseOkScorer,
    )
    from openharness.services.focus_state import FOCUS_STATE_SYSTEM_PROMPT

    cassette_mode = cast("CassetteMode", _require_eval_mode(mode))

    settings = _load_settings()
    client = _build_client(settings)
    effective_model = model or settings.model

    project_root = Path.cwd()
    dataset_path = project_root / "evals" / "focus_state" / "dataset.yaml"
    cassette_root = project_root / "evals" / "focus_state" / "cassettes"
    results_root = project_root / "evals" / "focus_state" / "results"

    if not dataset_path.exists():
        typer.echo(
            f"Dataset not found at {dataset_path}. "
            "`oh dev eval focus_state` must be run from the project root containing evals/.",
            err=True,
        )
        raise typer.Exit(code=1) from None

    cassette_store = CassetteStore(cassette_root)

    scorers: list[Scorer] = [
        ParseOkScorer(),
        GoalKeywordMatchScorer(),
        CapabilityAssertionsScorer(),
        CapabilityLLMJudgeScorer(
            api_client=client,
            model=effective_model,
            cassette_store=cassette_store,
            cassette_mode=cassette_mode,
        ),
    ]

    typer.echo("# focus_state.py eval — `oh dev eval focus_state`")
    typer.echo(f"# model:         {effective_model}")
    typer.echo(f"# dataset:       {dataset_path.relative_to(Path.cwd())}")
    typer.echo(f"# cassettes:     {cassette_root.relative_to(Path.cwd())}")
    typer.echo(f"# cassette_mode: {cassette_mode}")
    if not no_results:
        typer.echo(f"# results dir:   {results_root.relative_to(Path.cwd())}")
    typer.echo(f"# scorers:       {[type(s).__name__ for s in scorers]}")

    async def _orchestrate() -> None:
        started_at = utc_iso_now()
        results = await run_eval(
            dataset_path,
            scorers,
            client,
            effective_model,
            cassette_root=cassette_root,
            cassette_mode=cassette_mode,
            case_id=case_id,
        )

        for result in results:
            print_case(result)
        print_summary(results)

        if no_results:
            return

        completed_at = utc_iso_now()
        git_commit, git_dirty = get_git_info(project_root)
        metadata = RunMetadata(
            started_at=started_at,
            completed_at=completed_at,
            model=effective_model,
            judge_model=effective_model,
            cassette_mode=cassette_mode,
            dataset_path=str(dataset_path.relative_to(project_root)),
            dataset_sha256=compute_file_hash(dataset_path),
            prompt_sha256=compute_text_hash(FOCUS_STATE_SYSTEM_PROMPT),
            prompt_text=FOCUS_STATE_SYSTEM_PROMPT,
            rubric_sha256s=compute_rubric_hashes(CAPABILITY_RUBRICS),
            rubric_texts=dict(CAPABILITY_RUBRICS),
            scorer_classes=[type(s).__name__ for s in scorers],
            n_cases=len(results),
            git_commit=git_commit,
            git_dirty=git_dirty,
        )
        result_path = results_root / build_result_filename(metadata)
        write_run_results(result_path, metadata, results)
        typer.echo(f"\n# results written: {result_path.relative_to(Path.cwd())}")

    asyncio.run(_orchestrate())


@eval_app.command(
    "memory_decision",
    help=("Run the memory write-decision eval — 6 cases, 5 scorers, version-stamped results."),
)
def eval_memory_decision(  # pragma: no cover — experimental eval surface (excluded from coverage gate)
    mode: str | None = typer.Option(
        None,
        "--mode",
        "-m",
        help=(
            "Cassette mode: 'live' (real LLM, no cassette save), "
            "'record' (real LLM + save cassette), "
            "'replay' (load cassette, no LLM call)."
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Override OPENHARNESS_MODEL for this run.",
    ),
    no_results: bool = typer.Option(
        False,
        "--no-results",
        help="Skip writing results JSONL (default: write to evals/memory_decision/results/).",
    ),
    case_id: str | None = typer.Option(
        None,
        "--case",
        help="Run exactly one dataset case by case_id.",
    ),
) -> None:
    """Run the typed durable-memory decision eval.

    The eval drives the production ``MemoryList`` / ``MemoryShow`` /
    ``MemoryUpsert`` / ``MemoryDelete`` tools against an isolated store.
    It checks whether the model persists only durable facts, supplies a
    valid typed payload, preserves existing records, and chooses a
    defensible memory category. The warm-start pass bar is 80%.
    """
    import asyncio
    from typing import cast

    from openharness.eval._memory_decision_printers import (
        print_case,
        print_summary,
    )
    from openharness.eval.cassette import CassetteMode, CassetteStore
    from openharness.eval.memory_decision import (
        _MEMORY_DIR_PLACEHOLDER,
        _build_eval_system_prompt,
        run_memory_decision_eval,
    )
    from openharness.eval.memory_decision_scorers import (
        JudgmentScorer,
        MemoryTypeLLMJudgeScorer,
        PayloadValidScorer,
        PersistenceIntegrityScorer,
    )
    from openharness.eval.results import (
        RunMetadata,
        build_result_filename,
        compute_file_hash,
        compute_rubric_hashes,
        compute_text_hash,
        get_git_info,
        utc_iso_now,
        write_memory_decision_results,
    )
    from openharness.eval.rubrics import CAPABILITY_RUBRICS

    cassette_mode = cast("CassetteMode", _require_eval_mode(mode))

    settings = _load_settings()
    client = _build_client(settings)
    effective_model = model or settings.model

    project_root = Path.cwd()
    dataset_path = project_root / "evals" / "memory_decision" / "dataset.yaml"
    cassette_root = project_root / "evals" / "memory_decision" / "cassettes"
    results_root = project_root / "evals" / "memory_decision" / "results"

    if not dataset_path.exists():
        typer.echo(
            f"Dataset not found at {dataset_path}. "
            "`oh dev eval memory_decision` must be run from the project root containing evals/.",
            err=True,
        )
        raise typer.Exit(code=1) from None

    cassette_store = CassetteStore(cassette_root)

    scorers = [
        JudgmentScorer(),
        PayloadValidScorer(),
        PersistenceIntegrityScorer(),
        MemoryTypeLLMJudgeScorer(
            api_client=client,
            model=effective_model,
            cassette_store=cassette_store,
            cassette_mode=cassette_mode,
        ),
    ]

    typer.echo("# memory_decision eval — `oh dev eval memory_decision`")
    typer.echo(f"# model:         {effective_model}")
    typer.echo(f"# dataset:       {dataset_path.relative_to(Path.cwd())}")
    typer.echo(f"# cassettes:     {cassette_root.relative_to(Path.cwd())}")
    typer.echo(f"# cassette_mode: {cassette_mode}")
    if not no_results:
        typer.echo(f"# results dir:   {results_root.relative_to(Path.cwd())}")
    typer.echo(f"# scorers:       {[type(s).__name__ for s in scorers]}")

    # Stamp the system prompt by reconstructing it for the dir
    # placeholder — actual per-case prompts interpolate sample dirs,
    # but the *template* is what we hash for D34.3 reproducibility.
    eval_prompt_template = _build_eval_system_prompt(
        _MEMORY_DIR_PLACEHOLDER, "<generated memory index interpolated per case>"
    )
    # Subset of CAPABILITY_RUBRICS that this eval actually uses, so
    # the rubric_hashes stamp reflects what was applied (not
    # focus_state's T4-T7 set).
    eval_rubrics = {k: v for k, v in CAPABILITY_RUBRICS.items() if k.startswith("M-")}

    async def _orchestrate() -> None:
        started_at = utc_iso_now()
        results = await run_memory_decision_eval(
            dataset_path,
            scorers,
            client,
            effective_model,
            cassette_root=cassette_root,
            cassette_mode=cassette_mode,
            case_id=case_id,
        )

        for r in results:
            print_case(r)
        print_summary(results)

        if no_results:
            return

        completed_at = utc_iso_now()
        git_commit, git_dirty = get_git_info(project_root)
        metadata = RunMetadata(
            started_at=started_at,
            completed_at=completed_at,
            model=effective_model,
            judge_model=effective_model,
            cassette_mode=cassette_mode,
            dataset_path=str(dataset_path.relative_to(project_root)),
            dataset_sha256=compute_file_hash(dataset_path),
            prompt_sha256=compute_text_hash(eval_prompt_template),
            prompt_text=eval_prompt_template,
            rubric_sha256s=compute_rubric_hashes(eval_rubrics),
            rubric_texts=eval_rubrics,
            scorer_classes=[type(s).__name__ for s in scorers],
            n_cases=len(results),
            git_commit=git_commit,
            git_dirty=git_dirty,
        )
        result_path = results_root / build_result_filename(metadata)
        write_memory_decision_results(result_path, metadata, results)
        typer.echo(f"\n# results written: {result_path.relative_to(Path.cwd())}")

    asyncio.run(_orchestrate())


# ----- oh dev bench (sub-app lives in swebench/cli.py) -----------------------

from openharness.swebench.cli import bench_app  # noqa: E402

dev_app.add_typer(bench_app, name="bench")


def main() -> None:
    """The sole public console-script entry point: ``oh``."""
    app()
