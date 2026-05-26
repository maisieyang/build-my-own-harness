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
from typing import TYPE_CHECKING

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
    mark_memory_used,
    select_relevant_memories,
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
    MemoryManifest,
    build_system_prompt,
    detect_environment,
    load_claude_md_prompt,
)
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
)
from openharness.skills.store import EmptySkillStore, FilesystemSkillStore, SkillStore
from openharness.tools import LoadSkillTool, create_default_tool_registry

if TYPE_CHECKING:
    from openharness.config.settings import MemorySettings

# Phase 1 default. Lifted into a CLI flag (``--max-tokens``) so users can
# tune for short prompts or longer essays without editing code; kept
# generous enough that "hi" demos do not get truncated mid-sentence.
DEFAULT_MAX_TOKENS = 1024


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


def _build_memory_manifest_for_query(
    *,
    memory_store: MemoryStore,
    memory_dir: Path,
    query: str,
    memory_settings: MemorySettings,
) -> MemoryManifest:
    """Compute the :class:`MemoryManifest` to inject for ``query`` — P10-T4.4f.

    Three steps:

    1. ``select_relevant_memories(query, ...)`` — score + filter the
       discovered memories against query tokens; keep top-N per
       ``memory_settings.max_files``.
    2. ``mark_memory_used(m)`` on each picked memory — atomic
       frontmatter rewrite that increments ``use_count`` and stamps
       ``last_used_at``. Closes the loop Phase 13's stale-memory GC
       needs.
    3. ``_load_memory_entrypoint(memory_dir, max_bytes=...)`` — read
       the optional ``MEMORY.md`` index file if it exists and fits
       under the byte cap.

    Returns a :class:`MemoryManifest` ready to flow through
    :func:`build_system_prompt`'s ``memory_manifest`` kwarg.
    Empty manifest (no relevant memories AND no MEMORY.md) is fine —
    the formatters return ``None`` per section and ``build_system_prompt``
    omits both.
    """
    memories = memory_store.discover().values()
    relevant = select_relevant_memories(
        query,
        memories,
        max_results=memory_settings.max_files,
    )
    for memory in relevant:
        # Writes to disk — atomic via tempfile + os.replace. Failure
        # logs ``memory_usage_update_failed`` and is non-blocking.
        mark_memory_used(memory)
    entrypoint = _load_memory_entrypoint(memory_dir, max_bytes=memory_settings.max_entrypoint_bytes)
    return MemoryManifest(
        entrypoint_content=entrypoint,
        relevant=tuple(relevant),
    )


def _load_memory_entrypoint(memory_dir: Path, *, max_bytes: int) -> str | None:
    """Read ``MEMORY.md`` from ``memory_dir`` if it exists + fits under cap.

    Phase 10 (D28.6) doesn't truncate the index — by convention MEMORY.md
    is a small TOC. Files exceeding ``max_bytes`` are skipped entirely
    (returns ``None`` so ``## Memory`` section is omitted) rather than
    truncated with a marker, because a half-cut index is less useful
    than no index at all (the LLM can't navigate it).

    Returns ``None`` on: file doesn't exist, oversize, permission
    denied, or decode error. No log on the "doesn't exist" common
    case — projects without MEMORY.md are the normal Phase 10 state.
    """
    entrypoint_path = memory_dir / "MEMORY.md"
    if not entrypoint_path.exists():
        return None
    try:
        content = entrypoint_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(content.encode("utf-8")) > max_bytes:
        return None
    return content


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
        # any whitelist filter. P10-T4.4f: when memory is enabled AND
        # the bundle doesn't override, also inject ``claude_md_content``
        # + a per-query ``MemoryManifest`` so the LLM sees project
        # instructions + relevant memories alongside tools / env.
        if bundle is not None and bundle.system_prompt is not None:
            system_prompt = bundle.system_prompt
        else:
            memory_manifest: MemoryManifest | None = None
            if memory_store is not None and memory_dir is not None:
                memory_manifest = _build_memory_manifest_for_query(
                    memory_store=memory_store,
                    memory_dir=memory_dir,
                    query=prompt,
                    memory_settings=settings.memory,
                )
            system_prompt = build_system_prompt(
                effective_registry.to_api_schema(),
                env,
                skill_store=skill_store,
                claude_md_content=claude_md_content,
                memory_manifest=memory_manifest,
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
        )

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

        # P10-T4.4f: memory subsystem assembled ONCE per ``oh chat``
        # session. Per-turn ``MemoryManifest`` rebuild happens inside
        # the loop with the current user_input as the relevance query.
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
        )

        typer.echo("oh chat — multi-turn REPL. /help for commands, /exit to quit.")

        history: list[ConversationMessage] = []

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
                    )
            bundle_resolved = True

            # P10-T4.4f: rebuild system_prompt with per-turn memory
            # manifest unless the bundle explicitly overrode the prompt
            # (bundle.system_prompt set → user opted out of harness-
            # composed sections, memory included). Runs every turn so
            # multi-turn conversations get fresh relevance scoring
            # against the current user_input.
            if memory_store is not None and memory_dir is not None and not bundle_overrides_prompt:
                memory_manifest = _build_memory_manifest_for_query(
                    memory_store=memory_store,
                    memory_dir=memory_dir,
                    query=user_input,
                    memory_settings=settings.memory,
                )
                system_prompt = build_system_prompt(
                    effective_registry.to_api_schema(),
                    env,
                    skill_store=skill_store,
                    claude_md_content=claude_md_content,
                    memory_manifest=memory_manifest,
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
        help="Maximum tokens to generate (default 1024).",
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
