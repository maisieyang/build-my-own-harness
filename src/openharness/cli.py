"""Command-line interface for OpenHarness.

P1-T4 shipped a single-call CLI that streamed one LLM response. P2-T6.6e
rewrites ``_run_ask`` to drive the full agent loop (``run_query``):

* Build :class:`QueryContext` from ``Settings`` + the default tool registry +
  :class:`DenyListChecker` + the assembled system prompt + the detected
  environment + permission_mode (from ``--auto`` / ``--dry-run`` flags).
* Hand off to :func:`run_query`; render the streamed events.

Design highlights (rationale in ``learnings/04-cli.md`` + ``learnings/10-cli-loop.md``;
external contracts in ``decisions/05-cli.md`` + ``decisions/06`` + ``decisions/07``):

* **Provider-neutral env vars** (``OPENHARNESS_API_KEY`` / ``_BASE_URL`` /
  ``_MODEL`` / ``_PERMISSION_MODE``) are read by ``Settings``; the CLI never
  reaches into ``os.environ`` directly.
* **Differentiated error UX**: each error type maps to a category prefix
  in stderr; exit code 1. ``LoopLimitExceeded`` (D6.1) is caught by the
  dedicated ``except LoopError`` arm (P3-T2.2d) — "Loop error:" prefix
  signals the category, the embedded message itself names ``--max-turns``.
  Anything that escapes named arms lands in the ``except OpenHarnessError``
  root catch-all (P3-T2.2b widened from ``OpenHarnessApiError``).
* **Permission flags** ``--auto`` / ``--dry-run`` are mutually exclusive
  (D12.8). ``--dry-run`` lists every tool call without executing
  (D12.5). ``--auto`` is parsed but Phase 2 has no interactive
  confirmation flow yet.

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Side-effect import: when a readline backend is imported, Python's
# built-in ``input()`` switches to it, enabling backspace, arrow-key
# cursor motion, history (Up/Down), and Ctrl+R search inside the
# ``oh chat`` REPL prompt. Without it, raw-TTY ``input()`` echoes
# characters but ignores erase / arrow keys.
#
# **Order matters on macOS.** The stdlib ``readline`` is backed by
# ``libedit``, which has a known bug computing cursor positions when
# an input line mixes CJK (wide) and ASCII (narrow) characters —
# backspace lands at the wrong byte offset and the user cannot
# delete a mixed-script prompt. ``gnureadline`` (declared as a
# macOS-only dep in pyproject.toml) is a binding around the real
# GNU readline that fixes this. We import it FIRST so it claims the
# ``readline`` slot before stdlib's libedit-backed module gets a
# chance to. Linux already ships GNU readline natively; on Windows
# both imports fail and ``contextlib.suppress`` degrades silently.
with contextlib.suppress(ImportError):
    import gnureadline  # type: ignore[import-not-found]  # noqa: F401
with contextlib.suppress(ImportError):
    import readline  # noqa: F401

import typer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.stream_events import ApiStreamEvent
from openai import AsyncOpenAI
from pydantic import ValidationError

from openharness import __version__
from openharness._stream_render import render_stream
from openharness.api import (
    AuthenticationFailure,
    OpenAICompatibleApiClient,
    OpenHarnessApiError,
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
from openharness.engine import QueryContext, run_query
from openharness.errors import LoopError, OpenHarnessError
from openharness.execution import ExecutionEnvironment, SandboxExecution
from openharness.execution.host import _HOST_EXECUTION
from openharness.hooks import HookRegistry
from openharness.mcp import McpClientPool
from openharness.memory import (
    FilesystemMemoryStore,
    MemoryStore,
    get_project_memory_dir,
)
from openharness.observability import configure_logging

# Typer reflects ``Literal[...]`` types at RUNTIME to build Click Choice
# constraints — moving these into ``TYPE_CHECKING`` would break ``--log-level
# TRACE``-rejection and the corresponding test.
from openharness.observability.logging import (
    LogFormat,
    LogLevel,
    get_logger,
)
from openharness.permissions import PermissionMode, TierBasedPermissionChecker
from openharness.plugins import (
    LayeredStore,
    LoadedPluginCatalogs,
    PluginLoader,
)
from openharness.prompts import (
    build_system_prompt,
    detect_environment,
    load_claude_md_prompt,
)
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
)
from openharness.services.session_memory import get_session_memory_dir
from openharness.skills.store import EmptySkillStore, FilesystemSkillStore, SkillStore
from openharness.tools import LoadSkillTool, create_default_tool_registry
from openharness.tools.web_fetch import WebFetch
from openharness.tools.web_search import TavilySearchProvider, WebSearch

# Default per-call output cap. Phase 1 originally shipped 1024 (no tools),
# but with tool-use ship (Phase 2) and especially Agent / Write tool calls
# that emit file content as the ``arguments`` JSON, 1024 routinely truncates
# mid-string. Bumped to 8192 to align with Claude Code / modern harness
# defaults — covers most file-creating tool calls in one shot. Users with
# tight budgets opt down via ``--max-tokens``.
DEFAULT_MAX_TOKENS = 8192


app = typer.Typer(
    name="oh",
    help="OpenHarness — a production-grade Python harness for LLM agents.",
    no_args_is_help=True,
    add_completion=False,
)


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
    )
    return OpenAICompatibleApiClient(sdk=sdk)


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
        # noise: every ``oh ask`` invocation would emit it otherwise,
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


async def _run_ask(
    prompt: str,
    *,
    model_override: str | None,
    max_tokens: int,
    permission_mode_override: PermissionMode | None,
    log_level_override: LogLevel | None,
    log_format_override: LogFormat | None,
    tool_result_cap_override: int | None,
    auto_truncate_override: bool | None,
    no_skills: bool = False,
    no_commands: bool = False,
    sandbox_override: bool | None = None,
    sandbox_image_override: str | None = None,
    sandbox_network_override: str | None = None,
    sandbox_memory_override: str | None = None,
    sandbox_cpus_override: float | None = None,
    sandbox_runtime_override: str | None = None,
    enable_plugin_hooks_override: bool | None = None,
    enable_plugins_override: bool | None = None,
    enable_memory_override: bool | None = None,
    enable_web_override: bool | None = None,
    compact_threshold_override: float | None = None,
    no_auto_compact: bool = False,
    no_extract: bool = False,
    resume: bool = False,
    resume_id: str | None = None,
    llm_focus_state_override: bool | None = None,
) -> None:
    """Build the QueryContext, run the loop, render the events.

    Not exception-handling aware -- the synchronous Typer command wraps
    this and translates exceptions into user-facing exit codes.
    """
    settings = _load_settings()
    model = model_override or settings.model
    permission_mode = (
        permission_mode_override
        if permission_mode_override is not None
        else settings.permission_mode
    )
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
    # P7b-T2: sandbox configuration — CLI flag overrides Settings.
    sandbox_enabled = sandbox_override if sandbox_override is not None else settings.sandbox_enabled
    sandbox_image = sandbox_image_override or settings.sandbox_image
    sandbox_network = sandbox_network_override or settings.sandbox_network
    sandbox_memory = sandbox_memory_override or settings.sandbox_memory
    sandbox_cpus = (
        sandbox_cpus_override if sandbox_cpus_override is not None else settings.sandbox_cpus
    )
    sandbox_runtime = sandbox_runtime_override or settings.sandbox_runtime
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
    # into the running registry — but ``oh plugins list`` (T4) still
    # works for read-only introspection.
    enable_plugins = (
        enable_plugins_override if enable_plugins_override is not None else settings.enable_plugins
    )
    # P10-T4.4f (decisions/25 D28.10): memory subsystem opt-OUT. CLI
    # flag overrides Settings. When OFF, no FilesystemMemoryStore is
    # constructed, no CLAUDE.md cascade is loaded, system prompt is
    # byte-identical to pre-Phase-10 layout. Default ON because memory
    # is read-only + side-effect-free (only side effect is use_count++
    # in a private user-dir file).
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
    # P11-T5: compact + extraction CLI flags fold into nested Settings.
    # ``--no-auto-compact`` flips ``compact.enabled=False`` regardless
    # of env. ``--compact-threshold 0.5`` overrides
    # ``compact.threshold_ratio``. ``--no-extract`` flips
    # ``extraction.enabled=False``. Other CompactSettings /
    # ExtractionSettings knobs use env-only override
    # (``OPENHARNESS_COMPACT__FULL_COMPACT_TIMEOUT_S`` etc.) — no CLI
    # flag for every field to keep the surface area small.
    compact_enabled = settings.compact.enabled and not no_auto_compact
    compact_threshold_ratio = (
        compact_threshold_override
        if compact_threshold_override is not None
        else settings.compact.threshold_ratio
    )
    extract_enabled = settings.extraction.enabled and not no_extract

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
    # caught here — it propagates to the synchronous ``ask`` command's
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
            project_dir=Path.cwd() / ".openharness" / "commands",
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
            project_dir=Path.cwd() / ".openharness" / "bundles",
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
            project_dir=Path.cwd() / ".openharness" / "hooks",
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
        # (whitelist) wraps registry; Layer 3 (deny_paths) augments
        # settings; Layer 3 (hooks) registers into a clone of
        # hook_registry. Layer 1 (system_prompt) handled below — bundle
        # overrides ours, else we build against the EFFECTIVE registry
        # so the LLM's tool catalog reflects the whitelist.
        effective_registry = registry
        effective_hook_registry = hook_registry
        effective_settings = settings
        if bundle is not None:
            application = apply_bundle_to_context(
                bundle=bundle,
                tool_registry=registry,
                hook_registry=hook_registry,
                settings=settings,
                system_prompt="",  # placeholder; prompt logic lives below
                plugin_hook_catalog=plugin_hook_catalog,
            )
            effective_registry = application.tool_registry
            effective_hook_registry = application.hook_registry
            effective_settings = application.settings

        # P10-T4.4f: memory subsystem assembly. When enabled, construct
        # FilesystemMemoryStore + load CLAUDE.md cascade ONCE per
        # ``oh ask`` invocation. The per-query memory manifest (relevance
        # scoring + use_count tick) is computed below at prompt-build
        # time so the user's actual ``prompt`` arg drives selection.
        memory_dir: Path | None = None
        memory_store: MemoryStore | None = None
        claude_md_content: str | None = None
        if enable_memory:
            memory_dir = get_project_memory_dir(env.cwd)
            memory_store = FilesystemMemoryStore(project_dir=memory_dir)
            memory_store.discover()  # warm cache for relevance pass
            claude_md_content = load_claude_md_prompt(
                env.cwd,
                max_chars_per_file=settings.memory.max_claude_md_chars,
            )

        # System prompt is built AFTER MCP tools + LoadSkill register so
        # the LLM's tool catalog includes them. Skill catalog is injected
        # by ``build_system_prompt`` itself via the ``skill_store`` kwarg.
        # P5d-T4: bundle.system_prompt REPLACES base when set; otherwise
        # build against EFFECTIVE registry so the tool catalog reflects
        # any whitelist filter. P10-T4.4f (refined P16-T1/T2 D36.10/D36.11):
        # when memory is enabled AND the bundle doesn't override, inject
        # ``claude_md_content`` + the CC-style ``## Memory`` section
        # (rules block + MEMORY.md index) so the LLM sees project
        # instructions + memory entrypoint alongside tools / env.
        if bundle is not None and bundle.system_prompt is not None:
            system_prompt = bundle.system_prompt
        else:
            memory_index_content: str | None = None
            if memory_store is not None and memory_dir is not None:
                # P16-T2 (D36.7 / D36.11): the only memory-side
                # computation production needs is reading MEMORY.md for
                # injection. Phase 10's relevance ranking + use_count
                # bookkeeping was retired alongside extraction (D36.9).
                memory_index_content = _load_memory_index_for_injection(memory_dir)
            system_prompt = build_system_prompt(
                effective_registry.to_api_schema(),
                env,
                skill_store=skill_store,
                claude_md_content=claude_md_content,
                memory_dir=memory_dir,
                memory_index_content=memory_index_content,
                web_enabled=effective_web,
            )

        # P7b-T2 (D18.2): conditionally enter SandboxExecution.
        # ``--no-sandbox``(default)→ HostExecution singleton.
        # ``--sandbox`` → enter the substrate context here; BashTool
        # routes through it via ``QueryContext.execution_env``.
        execution_env: ExecutionEnvironment
        if sandbox_enabled:
            execution_env = await stack.enter_async_context(
                SandboxExecution(
                    cwd=env.cwd,
                    image=sandbox_image,
                    network=sandbox_network,
                    memory=sandbox_memory,
                    cpus=sandbox_cpus,
                    pids=settings.sandbox_pids,
                    runtime=sandbox_runtime,
                )
            )
        else:
            execution_env = _HOST_EXECUTION

        context = QueryContext(
            api_client=client,
            tool_registry=effective_registry,
            # P3-T3.3e:replaced Phase 2 DenyListChecker (Bash-only) with the
            # full three-Tier checker (hardcoded paths + user globs + mode-based +
            # carry-over Bash deny-list). P5d-T4: TierBasedPermissionChecker
            # reads ``settings.deny_paths`` which already includes the bundle's
            # extra patterns (via ``effective_settings``).
            permission_checker=TierBasedPermissionChecker(effective_registry, effective_settings),
            hook_registry=effective_hook_registry,
            system_prompt=system_prompt,
            cwd=env.cwd,
            model=model,
            max_tokens=max_tokens,
            permission_mode=permission_mode,
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
            # P11-T5: compact + extraction wiring. memory_store is the
            # write-path entry for Phase 11 extraction (uses Phase 10's
            # FilesystemMemoryStore). session_memory_path points at the
            # 5-slot checkpoint compact L3 reads (None when memory
            # subsystem disabled — engine still runs L2 + L4).
            compact_enabled=compact_enabled,
            compact_threshold_ratio=compact_threshold_ratio,
            compact_full_max_tokens=settings.compact.full_compact_max_tokens,
            compact_full_timeout_s=settings.compact.full_compact_timeout_s,
            session_memory_path=(
                get_session_memory_dir(env.cwd) / "checkpoint.md" if enable_memory else None
            ),
            memory_store=memory_store,
            extract_enabled=extract_enabled,
            extract_max_records=settings.extraction.max_records_per_turn,
            extract_timeout_s=settings.extraction.timeout_s,
            # P12-T3 (D30.8): per-turn snapshot writer. Engine fires
            # ``write_session_snapshot`` at user-turn end alongside
            # the session_memory writer (single tool_metadata producer
            # feeds both per D30.6). ``--no-resume`` is the user-side
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
        # permission_mode / system_prompt / messages). Runtime state
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
                    permission_checker=context.permission_checker,
                    cwd=env.cwd,
                    hook_registry=effective_hook_registry,
                    execution_env=execution_env,
                    skill_store=skill_store,
                    memory_store=memory_store,
                    session_memory_path=context.session_memory_path,
                    snapshot_enabled=context.snapshot_enabled,
                    snapshot_max_age_warn_days=context.snapshot_max_age_warn_days,
                    snapshot_history_max_count=context.snapshot_history_max_count,
                    snapshot_history_max_age_days=context.snapshot_history_max_age_days,
                    llm_focus_state_enabled=context.llm_focus_state_enabled,
                    llm_focus_state_model=context.llm_focus_state_model,
                    compact_enabled=compact_enabled,
                    compact_threshold_ratio=compact_threshold_ratio,
                    compact_full_max_tokens=settings.compact.full_compact_max_tokens,
                    compact_full_timeout_s=settings.compact.full_compact_timeout_s,
                    extract_enabled=extract_enabled,
                    extract_max_records=settings.extraction.max_records_per_turn,
                    extract_timeout_s=settings.extraction.timeout_s,
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
        await render_stream(events)


# --------------------------------------------------------------------------- #
# P6+: oh chat REPL                                                           #
# --------------------------------------------------------------------------- #


_CHAT_HELP_TEXT = """\
oh chat — multi-turn REPL commands:
  /exit, /quit       leave the REPL
  /clear             reset conversation history (keeps tools + mode)
  /compact           force full LLM-based compaction of the conversation
                     (Phase 11 D29.6) — replaces history with a 9-slot
                     summary regardless of token threshold
  /help              show this message

User-authored slash commands (Phase 5b) work too — type ``/name args``
to expand a command. Bundles (Phase 5d) resolve on the FIRST message
of the session and persist for the rest.

Use Ctrl+D (EOF) to exit; Ctrl+C cancels the current input line."""


async def _run_chat(
    *,
    model_override: str | None,
    max_tokens: int,
    permission_mode_override: PermissionMode | None,
    log_level_override: LogLevel | None,
    log_format_override: LogFormat | None,
    tool_result_cap_override: int | None,
    auto_truncate_override: bool | None,
    no_skills: bool = False,
    no_commands: bool = False,
    sandbox_override: bool | None = None,
    sandbox_image_override: str | None = None,
    sandbox_network_override: str | None = None,
    sandbox_memory_override: str | None = None,
    sandbox_cpus_override: float | None = None,
    sandbox_runtime_override: str | None = None,
    enable_plugin_hooks_override: bool | None = None,
    enable_plugins_override: bool | None = None,
    enable_memory_override: bool | None = None,
    enable_web_override: bool | None = None,
    compact_threshold_override: float | None = None,
    no_auto_compact: bool = False,
    no_extract: bool = False,
    resume: bool = False,
    resume_id: str | None = None,
    llm_focus_state_override: bool | None = None,
) -> None:
    """Multi-turn REPL driver — P6+-T2.

    Builds a QueryContext once, then loops on ``input(">>> ")``,
    accumulating conversation history across turns. Each turn runs the
    same ``run_query`` engine ``oh ask`` uses; the new
    ``ConversationCompleteEvent`` exposes the post-turn message list
    which becomes the next turn's ``initial_messages``.
    """
    # Bootstrap is largely identical to ``_run_ask``. Factoring is a
    # Phase 9 polish candidate — for now, the duplication is contained
    # and tested through both commands' integration tests.
    from openharness.protocols.stream_events import ConversationCompleteEvent

    settings = _load_settings()
    model = model_override or settings.model
    permission_mode = (
        permission_mode_override
        if permission_mode_override is not None
        else settings.permission_mode
    )
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
    sandbox_enabled = sandbox_override if sandbox_override is not None else settings.sandbox_enabled
    sandbox_image = sandbox_image_override or settings.sandbox_image
    sandbox_network = sandbox_network_override or settings.sandbox_network
    sandbox_memory = sandbox_memory_override or settings.sandbox_memory
    sandbox_cpus = (
        sandbox_cpus_override if sandbox_cpus_override is not None else settings.sandbox_cpus
    )
    sandbox_runtime = sandbox_runtime_override or settings.sandbox_runtime
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
    # P10-T4.4f: memory subsystem opt-OUT. Same shape as ``_run_ask``.
    # When enabled, memory_manifest is rebuilt **per turn** with the
    # current user_input as the relevance query — so multi-turn
    # conversations get fresh memory selection each turn instead of
    # only at session start.
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
    extract_enabled = settings.extraction.enabled and not no_extract

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

        hook_registry = HookRegistry()
        if auto_truncate and tool_result_cap > 0:
            hook_registry.register(
                "PostToolUse",
                TruncateToolResultHook(cap_tokens=tool_result_cap, model=model),
            )

        execution_env: ExecutionEnvironment
        if sandbox_enabled:
            execution_env = await stack.enter_async_context(
                SandboxExecution(
                    cwd=env.cwd,
                    image=sandbox_image,
                    network=sandbox_network,
                    memory=sandbox_memory,
                    cpus=sandbox_cpus,
                    pids=settings.sandbox_pids,
                    runtime=sandbox_runtime,
                )
            )
        else:
            execution_env = _HOST_EXECUTION

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

        # P10-T4.4f (refined P16-T1/T2 D36.11): memory subsystem
        # assembled ONCE per ``oh chat`` session. Per-turn MEMORY.md
        # re-read happens inside the loop so the LLM sees the index
        # updated by the previous turn's writes.
        memory_dir: Path | None = None
        memory_store: MemoryStore | None = None
        claude_md_content: str | None = None
        if enable_memory:
            memory_dir = get_project_memory_dir(env.cwd)
            memory_store = FilesystemMemoryStore(project_dir=memory_dir)
            memory_store.discover()  # warm cache for per-turn relevance pass
            claude_md_content = load_claude_md_prompt(
                env.cwd,
                max_chars_per_file=settings.memory.max_claude_md_chars,
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
        effective_settings = settings
        # Initial system_prompt — fallback for the memory-disabled path
        # AND for the brief window before the loop's first iteration
        # rebuilds with memory. CLAUDE.md threads through; memory
        # manifest does NOT (no user query yet).
        system_prompt = build_system_prompt(
            registry.to_api_schema(),
            env,
            skill_store=skill_store,
            claude_md_content=claude_md_content,
            web_enabled=effective_web,
        )

        typer.echo("oh chat — multi-turn REPL. /help for commands, /exit to quit.")

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

        while True:
            try:
                user_input = await asyncio.to_thread(input, ">>> ")
            except EOFError:
                typer.echo("")  # newline after EOF
                break
            except KeyboardInterrupt:
                typer.echo("\n(use /exit to quit)")
                continue

            user_input = user_input.strip()
            if not user_input:
                continue

            # Built-in REPL commands (D24.3).
            if user_input in ("/exit", "/quit"):
                break
            if user_input == "/clear":
                history = []
                typer.echo("(conversation cleared)")
                continue
            if user_input == "/help":
                typer.echo(_CHAT_HELP_TEXT)
                continue
            # P11-T5 (D29.6): force full LLM-based compaction. Same
            # primitive as auto-compact L4, but invoked unconditionally
            # on the current history. Replaces ``history`` with a single
            # user-role message carrying the 9-slot summary so the next
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
                try:
                    new_history, did_apply = await full_compact(
                        history,
                        model=model,
                        api_client=client,
                        max_tokens=settings.compact.full_compact_max_tokens,
                        timeout_seconds=settings.compact.full_compact_timeout_s,
                    )
                except Exception as exc:
                    typer.echo(f"(/compact failed: {exc})", err=True)
                    continue
                if not did_apply:
                    typer.echo("(/compact: nothing to summarize)")
                    continue
                history = new_history
                after_tokens = estimate_message_tokens(history, model=model)
                typer.echo(f"(compacted: {before_tokens} → {after_tokens} tokens)")
                continue

            # Phase 5b slash command expansion (for non-built-in).
            invoked_command = None
            if user_input.startswith("/") and command_store is not None:
                try:
                    user_input, invoked_command = resolve_command_invocation(
                        user_input, command_store
                    )
                except UnknownCommandError as exc:
                    typer.echo(f"Unknown command: {exc}", err=True)
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
                    settings=settings,
                    system_prompt="",
                    plugin_hook_catalog=plugin_hook_catalog,
                )
                effective_registry = application.tool_registry
                effective_hook_registry = application.hook_registry
                effective_settings = application.settings
                if bundle.system_prompt is not None:
                    system_prompt = bundle.system_prompt
                    bundle_overrides_prompt = True
                else:
                    system_prompt = build_system_prompt(
                        effective_registry.to_api_schema(),
                        env,
                        skill_store=skill_store,
                        claude_md_content=claude_md_content,
                        web_enabled=effective_web,
                    )
            bundle_resolved = True

            # P10-T4.4f: rebuild system_prompt with per-turn memory
            # manifest unless the bundle explicitly overrode the prompt
            # (bundle.system_prompt set → user opted out of harness-
            # composed sections, memory included). Runs every turn so
            # multi-turn conversations get fresh relevance scoring
            # against the current user_input.
            if memory_store is not None and memory_dir is not None and not bundle_overrides_prompt:
                # P16-T1/T2 (D36.7 / D36.11): per-turn MEMORY.md re-read
                # so the LLM sees the index updated by the previous
                # turn's memory writes. Phase 10's relevance ranking +
                # use_count bookkeeping was retired alongside extraction
                # (D36.9) — the LLM picks what to Read from the index.
                memory_index_content = _load_memory_index_for_injection(memory_dir)
                system_prompt = build_system_prompt(
                    effective_registry.to_api_schema(),
                    env,
                    skill_store=skill_store,
                    claude_md_content=claude_md_content,
                    memory_dir=memory_dir,
                    memory_index_content=memory_index_content,
                    web_enabled=effective_web,
                )

            context = QueryContext(
                api_client=client,
                tool_registry=effective_registry,
                permission_checker=TierBasedPermissionChecker(
                    effective_registry, effective_settings
                ),
                hook_registry=effective_hook_registry,
                system_prompt=system_prompt,
                cwd=env.cwd,
                model=model,
                max_tokens=max_tokens,
                permission_mode=permission_mode,
                skill_store=skill_store,
                max_agent_depth=settings.max_agent_depth,
                execution_env=execution_env,
                # P11-T5: compact + extraction. Mirrors ``_run_ask``.
                # Rebuilt per turn so /compact-toggled flags (future)
                # take effect on the next turn.
                compact_enabled=compact_enabled,
                compact_threshold_ratio=compact_threshold_ratio,
                compact_full_max_tokens=settings.compact.full_compact_max_tokens,
                compact_full_timeout_s=settings.compact.full_compact_timeout_s,
                session_memory_path=(
                    get_session_memory_dir(env.cwd) / "checkpoint.md" if enable_memory else None
                ),
                memory_store=memory_store,
                extract_enabled=extract_enabled,
                extract_max_records=settings.extraction.max_records_per_turn,
                extract_timeout_s=settings.extraction.timeout_s,
                # P12-T3 (D30.8): snapshot writer mirrored from ask.
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

            history.append(ConversationMessage(role="user", content=[TextBlock(text=user_input)]))

            captured: list[ConversationMessage] | None = None

            async def _capture(
                events_iter: AsyncIterator[ApiStreamEvent],
            ) -> AsyncIterator[ApiStreamEvent]:
                nonlocal captured
                async for ev in events_iter:
                    if isinstance(ev, ConversationCompleteEvent):
                        captured = ev.messages
                    yield ev

            try:
                await render_stream(_capture(run_query(history, context)))
            except LoopError as exc:
                typer.echo(f"Loop error: {exc}", err=True)
                # Don't break — let user issue /clear or retry.
                continue
            except OpenHarnessApiError as exc:
                typer.echo(f"API error: {exc}", err=True)
                # REPL must survive provider-side failures (auth blip,
                # rate-limit, truncated tool_call). User can /clear,
                # adjust flags (e.g. --max-tokens), or retry.
                continue

            if captured is not None:
                history = captured


# --------------------------------------------------------------------------- #
# Typer command surface                                                       #
# --------------------------------------------------------------------------- #


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"openharness {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    _version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """OpenHarness CLI."""


@app.command(help="Send a single prompt to the configured LLM and stream the response.")
def ask(
    prompt: str = typer.Argument(
        ...,
        help='User prompt. Quote multi-word prompts: oh ask "explain X".',
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
        help="Skip interactive confirmation prompts (Phase 3 reserved; no-op in Phase 2).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List tool calls the loop would make without executing them.",
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
        help=(
            "Disable Layer 1 truncation hook registration. Raw tool outputs "
            "flow through unchanged;Layer 2 reactive (prompt-too-long retry) "
            "remains active."
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
            "Enable the Docker sandbox substrate for Bash (Phase 7b). When "
            "on, Bash commands execute inside a per-query container with "
            "cwd bind-mounted read-write and network=none by default. "
            "Default OFF (host execution); overrides OPENHARNESS_SANDBOX."
        ),
    ),
    sandbox_image: str | None = typer.Option(
        None,
        "--sandbox-image",
        help=(
            "Docker image for the sandbox (default: python:3.12-slim). "
            "Overrides OPENHARNESS_SANDBOX_IMAGE."
        ),
    ),
    sandbox_network: str | None = typer.Option(
        None,
        "--sandbox-network",
        help=(
            "Container network mode [none|bridge]. ``none`` (default) "
            "blocks external network — strongest default. ``bridge`` "
            "enables NAT'd internet. Overrides OPENHARNESS_SANDBOX_NETWORK."
        ),
    ),
    sandbox_memory: str | None = typer.Option(
        None,
        "--sandbox-memory",
        help=(
            "Container memory limit, Docker-style spec (1g / 512m / etc.). "
            "Overrides OPENHARNESS_SANDBOX_MEMORY (default 1g)."
        ),
    ),
    sandbox_cpus: float | None = typer.Option(
        None,
        "--sandbox-cpus",
        help=(
            "Container CPU quota in CPU equivalents (1.0 = one full CPU; "
            "0.5 = half). Overrides OPENHARNESS_SANDBOX_CPUS (default 1.0)."
        ),
    ),
    sandbox_runtime: str | None = typer.Option(
        None,
        "--sandbox-runtime",
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
        help=(
            "Enable discovery + loading of plugins from "
            "~/.openharness/plugins/<name>/manifest.yaml (Phase 9). "
            "Default OFF — plugin components are not registered into "
            "the running registry unless this flag is set. ``oh plugins "
            "list`` still works without it (read-only introspection). "
            "Overrides OPENHARNESS_ENABLE_PLUGINS."
        ),
    ),
    enable_memory: bool | None = typer.Option(
        None,
        "--enable-memory/--no-enable-memory",
        help=(
            "Enable the memory subsystem (Phase 10, D28.10). When ON "
            "(default), CLAUDE.md cascade + per-project durable memory "
            "are injected into the system prompt. When OFF, neither "
            "section appears — useful for a stateless harness or for "
            "isolating from a misbehaving memory file. Overrides "
            "OPENHARNESS_ENABLE_MEMORY."
        ),
    ),
    enable_web: bool | None = typer.Option(
        None,
        "--enable-web/--no-enable-web",
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
        min=0.0,
        max=1.0,
        help=(
            "Auto-compact threshold as a fraction of the model's context "
            "window (Phase 11 D29.3). Above this ratio, auto-compact "
            "escalates L2→L3→L4. Overrides "
            "OPENHARNESS_COMPACT__THRESHOLD_RATIO (default 0.83)."
        ),
    ),
    no_auto_compact: bool = typer.Option(
        False,
        "--no-auto-compact",
        help=(
            "Disable proactive auto-compact (Phase 11). The engine's "
            "reactive prompt-too-long retry remains active as the last-"
            "resort safety net. Useful for tests that need byte-stable "
            "request shape or when L4 LLM cost is unwanted."
        ),
    ),
    no_extract: bool = typer.Option(
        False,
        "--no-extract",
        help=(
            "Disable the per-turn memory-extraction secondary pass "
            "(Phase 11 D29.5). The main conversation is unaffected. "
            "Overrides OPENHARNESS_EXTRACTION__ENABLED."
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
        help=(
            "Opt in to LLM-authored ``tool_metadata.task_focus_state`` "
            "(Phase 13 D31.7). Fires a secondary LLM call at turn "
            "end asking for the current goal + next_step in JSON, "
            "stores the result in snapshot + session_memory. Adds "
            "~1-2s per turn. Default OFF preserves Phase 12 zero-"
            "cost behavior. Override: "
            "OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE env var."
        ),
    ),
) -> None:
    """Stream a single LLM response (with tool dispatch) to stdout."""
    if auto and dry_run:
        typer.echo(
            "error: --auto and --dry-run are mutually exclusive",
            err=True,
        )
        raise typer.Exit(code=2)

    permission_mode_override: PermissionMode | None = None
    if dry_run:
        permission_mode_override = PermissionMode.DRY_RUN
    elif auto:
        permission_mode_override = PermissionMode.AUTO

    # `--no-auto-truncate` is the only way to set ``auto_truncate=False`` via
    # CLI;no positive ``--auto-truncate`` flag (it's the default).
    auto_truncate_override: bool | None = False if no_auto_truncate else None

    try:
        asyncio.run(
            _run_ask(
                prompt,
                model_override=model,
                max_tokens=max_tokens,
                permission_mode_override=permission_mode_override,
                log_level_override=log_level,
                log_format_override=log_format,
                tool_result_cap_override=tool_result_cap,
                auto_truncate_override=auto_truncate_override,
                no_skills=no_skills,
                no_commands=no_commands,
                sandbox_override=sandbox,
                sandbox_image_override=sandbox_image,
                sandbox_network_override=sandbox_network,
                sandbox_memory_override=sandbox_memory,
                sandbox_cpus_override=sandbox_cpus,
                sandbox_runtime_override=sandbox_runtime,
                enable_plugin_hooks_override=enable_plugin_hooks,
                enable_plugins_override=enable_plugins,
                enable_memory_override=enable_memory,
                enable_web_override=enable_web,
                compact_threshold_override=compact_threshold,
                no_auto_compact=no_auto_compact,
                no_extract=no_extract,
                # P12-T5: --resume / --resume-id. --resume-id implies
                # --resume so the user doesn't have to type both.
                resume=resume or resume_id is not None,
                resume_id=resume_id,
                llm_focus_state_override=llm_focus_state,
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


@app.command(help="Open an interactive multi-turn REPL (Phase 6+).")
def chat(
    model: str | None = typer.Option(None, "--model", "-m", help="Model name override."),
    max_tokens: int = typer.Option(
        DEFAULT_MAX_TOKENS, "--max-tokens", min=1, help="Max tokens per turn."
    ),
    auto: bool = typer.Option(False, "--auto", help="Skip confirmations."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List tool calls; don't execute."),
    log_level: LogLevel | None = typer.Option(None, "--log-level"),
    log_format: LogFormat | None = typer.Option(None, "--log-format"),
    tool_result_cap: int | None = typer.Option(None, "--tool-result-cap", min=0),
    no_auto_truncate: bool = typer.Option(False, "--no-auto-truncate"),
    no_skills: bool = typer.Option(False, "--no-skills"),
    no_commands: bool = typer.Option(False, "--no-commands"),
    sandbox: bool | None = typer.Option(None, "--sandbox/--no-sandbox"),
    sandbox_image: str | None = typer.Option(None, "--sandbox-image"),
    sandbox_network: str | None = typer.Option(None, "--sandbox-network"),
    sandbox_memory: str | None = typer.Option(None, "--sandbox-memory"),
    sandbox_cpus: float | None = typer.Option(None, "--sandbox-cpus"),
    sandbox_runtime: str | None = typer.Option(None, "--sandbox-runtime"),
    enable_plugin_hooks: bool | None = typer.Option(
        None, "--enable-plugin-hooks/--no-enable-plugin-hooks"
    ),
    enable_plugins: bool | None = typer.Option(
        None,
        "--enable-plugins/--no-enable-plugins",
        help="Enable plugin discovery + loading (Phase 9). Default OFF.",
    ),
    enable_memory: bool | None = typer.Option(
        None,
        "--enable-memory/--no-enable-memory",
        help=(
            "Enable the memory subsystem (Phase 10). Default ON. "
            "Per-turn relevance scoring + CLAUDE.md injection. "
            "Overrides OPENHARNESS_ENABLE_MEMORY."
        ),
    ),
    enable_web: bool | None = typer.Option(
        None,
        "--enable-web/--no-enable-web",
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
        min=0.0,
        max=1.0,
        help="Auto-compact threshold (Phase 11). Overrides OPENHARNESS_COMPACT__THRESHOLD_RATIO.",
    ),
    no_auto_compact: bool = typer.Option(
        False,
        "--no-auto-compact",
        help="Disable proactive auto-compact (Phase 11). Reactive PTL retry still active.",
    ),
    no_extract: bool = typer.Option(
        False,
        "--no-extract",
        help="Disable per-turn memory extraction (Phase 11 D29.5).",
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
        help="Opt in to LLM-authored task_focus_state (Phase 13 D31.7). Default OFF.",
    ),
) -> None:
    """Multi-turn REPL — same flag surface as ``ask`` minus the prompt arg."""
    if auto and dry_run:
        typer.echo("error: --auto and --dry-run are mutually exclusive", err=True)
        raise typer.Exit(code=2)

    permission_mode_override: PermissionMode | None = None
    if dry_run:
        permission_mode_override = PermissionMode.DRY_RUN
    elif auto:
        permission_mode_override = PermissionMode.AUTO

    auto_truncate_override: bool | None = False if no_auto_truncate else None

    try:
        asyncio.run(
            _run_chat(
                model_override=model,
                max_tokens=max_tokens,
                permission_mode_override=permission_mode_override,
                log_level_override=log_level,
                log_format_override=log_format,
                tool_result_cap_override=tool_result_cap,
                auto_truncate_override=auto_truncate_override,
                no_skills=no_skills,
                no_commands=no_commands,
                sandbox_override=sandbox,
                sandbox_image_override=sandbox_image,
                sandbox_network_override=sandbox_network,
                sandbox_memory_override=sandbox_memory,
                sandbox_cpus_override=sandbox_cpus,
                sandbox_runtime_override=sandbox_runtime,
                enable_plugin_hooks_override=enable_plugin_hooks,
                enable_plugins_override=enable_plugins,
                enable_memory_override=enable_memory,
                enable_web_override=enable_web,
                compact_threshold_override=compact_threshold,
                no_auto_compact=no_auto_compact,
                no_extract=no_extract,
                # P12-T5: --resume / --resume-id (latter implies former).
                resume=resume or resume_id is not None,
                resume_id=resume_id,
                llm_focus_state_override=llm_focus_state,
            )
        )
    except ValidationError as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (AuthenticationFailure, RateLimitFailure, RequestFailure) as exc:
        typer.echo(f"API error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except OpenHarnessError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------- #
# P7-T2: introspection subcommands (oh tools / oh config / oh hooks)         #
# --------------------------------------------------------------------------- #


_CONFIG_TEMPLATE = """\
# OpenHarness user-global configuration
#
# This file is loaded by ``oh ask`` / ``oh chat`` etc. as a LOWER-
# precedence layer than a ``.env`` in the project's cwd, which in turn
# is lower precedence than env vars set in your shell.
#
# Uncomment + set the values you want. The two required fields are
# OPENHARNESS_API_KEY and OPENHARNESS_BASE_URL.

# OPENHARNESS_API_KEY="sk-..."
# OPENHARNESS_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
# OPENHARNESS_MODEL="qwen-plus"

# Permissions
# OPENHARNESS_DENY_PATHS="secrets/**,*.env"
# OPENHARNESS_PERMISSION_MODE="default"  # default / auto / dry_run

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


# ----- oh tools --------------------------------------------------------------

tools_app = typer.Typer(name="tools", help="Inspect registered tools.")
app.add_typer(tools_app, name="tools")


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
    framework's default catalog. Run ``oh ask --dry-run "..."`` to see
    the effective registry for a real invocation.
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

config_app = typer.Typer(name="config", help="Inspect or edit OpenHarness configuration.")
app.add_typer(config_app, name="config")


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


# ----- oh hooks --------------------------------------------------------------

hooks_app = typer.Typer(name="hooks", help="Inspect framework + plugin hooks.")
app.add_typer(hooks_app, name="hooks")


@hooks_app.command("list", help="List built-in hooks (and plugins with --enable-plugin-hooks).")
def hooks_list(
    format: str = typer.Option("text", "--format", "-f"),
    enable_plugin_hooks: bool = typer.Option(
        False,
        "--enable-plugin-hooks/--no-enable-plugin-hooks",
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


# ----- oh memory -------------------------------------------------------------
# P10-T5: read-only inspection subcommands per D28.11.
# No add / edit / remove — write surface defers to Phase 11's extraction
# secondary pass + future CLI add command.

memory_app = typer.Typer(
    name="memory",
    help="Inspect project memory store (read-only — no add/edit in Phase 10).",
)
app.add_typer(memory_app, name="memory")


@memory_app.command("list", help="List memories in this project's store.")
def memory_list(
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text (default) or json.",
    ),
) -> None:
    """List memories sorted by ``(-use_count, name)`` — most-used first.

    Empty store → single ``(no memories — storage at <path>)`` line so
    the user sees WHERE to drop a hand-written memory file. Malformed
    files don't appear (``parse_memory`` returns ``None`` + warning
    log;the store skips them silently). To see warnings,
    re-run with ``--log-level INFO``.
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
        typer.echo(f"(no memories — storage at {storage_dir})")
        return

    memories.sort(key=lambda m: (-m.use_count, m.name))

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

    # Text format: aligned columns. Description truncated to 60 chars so
    # long descriptions don't blow out the line width.
    name_width = max(len(m.name) for m in memories) + 2
    type_width = max(len(m.type.value) for m in memories) + 2
    use_width = max(len(str(m.use_count)) for m in memories) + 2
    use_width = max(use_width, 5)  # min "use" header width
    for m in memories:
        desc = m.description if len(m.description) <= 60 else m.description[:57] + "..."
        last_used = m.last_used_at.isoformat() if m.last_used_at else "(never)"
        typer.echo(
            f"{m.name:<{name_width}}"
            f"{m.type.value:<{type_width}}"
            f"{m.use_count:<{use_width}}"
            f"{last_used:<32}"
            f"{desc}"
        )


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
# P13-T2 (D31.6): oh snapshot list / show / gc                                #
# --------------------------------------------------------------------------- #
#
# Mirrors the ``oh memory list / show / path`` pattern (Phase 10 T5):
# typer sub-app with 3 read-mostly subcommands for user-side
# introspection. ``list`` is discoverability, ``show`` is inspection,
# ``gc`` is force-cleanup outside the per-turn eager rotation path.

snapshot_app = typer.Typer(
    name="snapshot",
    help="Inspect + manage Phase 12 snapshots for the current cwd.",
)
app.add_typer(snapshot_app, name="snapshot")


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
# `oh eval` — Stage 1-5 capability-anchored prompt eval                        #
# --------------------------------------------------------------------------- #


eval_app = typer.Typer(
    name="eval",
    help="Run capability-anchored prompt eval (Stages 1-5 substrate).",
)
app.add_typer(eval_app, name="eval")


@eval_app.command(
    "focus_state",
    help=(
        "Run eval against services/focus_state.py — 8 capability-anchored cases, "
        "4 scorers (parse + keyword + substring + LLM-judge), version-stamped results."
    ),
)
def eval_focus_state(
    mode: str = typer.Option(
        "live",
        "--mode",
        "-m",
        help=(
            "Cassette mode (D33.2): "
            "'live' (real LLM, no cassette save), "
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

    if mode not in ("live", "record", "replay"):
        typer.echo(
            f"Invalid --mode={mode!r}; expected one of: live / record / replay",
            err=True,
        )
        raise typer.Exit(code=1) from None
    cassette_mode = cast("CassetteMode", mode)

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
            "`oh eval focus_state` must be run from the project root containing evals/.",
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

    typer.echo("# focus_state.py eval — `oh eval focus_state` (Stages 1-5 substrate)")
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


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #


def main() -> None:
    """Console-script entry point (``oh`` / ``openharness``).

    Typer's ``app()`` raises :class:`SystemExit` on completion, so this
    function does not return a value. ``__main__.py`` and the script
    wrapper both rely on that exit-code propagation.
    """
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
