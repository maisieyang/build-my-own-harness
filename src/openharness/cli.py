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

import typer
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
    UnknownBundleError,
    apply_bundle_to_context,
    discover_plugin_hooks,
)
from openharness.commands import (
    FilesystemCommandStore,
    UnknownCommandError,
    resolve_command_invocation,
)
from openharness.compaction import TruncateToolResultHook
from openharness.config import Settings
from openharness.engine import QueryContext, run_query
from openharness.errors import LoopError, OpenHarnessError
from openharness.execution import ExecutionEnvironment, SandboxExecution
from openharness.execution.host import _HOST_EXECUTION
from openharness.hooks import HookRegistry
from openharness.mcp import McpClientPool
from openharness.observability import configure_logging

# Typer reflects ``Literal[...]`` types at RUNTIME to build Click Choice
# constraints — moving these into ``TYPE_CHECKING`` would break ``--log-level
# TRACE``-rejection and the corresponding test.
from openharness.observability.logging import LogFormat, LogLevel  # noqa: TC001
from openharness.permissions import PermissionMode, TierBasedPermissionChecker
from openharness.prompts import build_system_prompt, detect_environment
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
)
from openharness.skills.store import EmptySkillStore, FilesystemSkillStore, SkillStore
from openharness.tools import LoadSkillTool, create_default_tool_registry

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
    """
    return Settings()


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


# --------------------------------------------------------------------------- #
# Core async entry point                                                      #
# --------------------------------------------------------------------------- #


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
    enable_plugin_hooks_override: bool | None = None,
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
    # P5e-T3: plugin hook discovery is opt-in. CLI flag overrides
    # Settings. When OFF, ``discover_plugin_hooks()`` is never called
    # and bundle ``hooks:`` resolves only against BUILTIN_HOOKS — even
    # if plugin packages are installed.
    enable_plugin_hooks = (
        enable_plugin_hooks_override
        if enable_plugin_hooks_override is not None
        else settings.enable_plugin_hooks
    )

    # P3-T5.5e:configure logging FIRST so any subsequent error path
    # (client build / system prompt build) is observable.
    configure_logging(level=log_level, format=log_format)

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
    invoked_command = None
    if not no_commands:
        command_store = FilesystemCommandStore(
            global_dir=Path.home() / ".openharness" / "commands",
            project_dir=Path.cwd() / ".openharness" / "commands",
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
    bundle = None
    if invoked_command is not None and invoked_command.mode is not None:
        bundle_store = FilesystemBundleStore(
            global_dir=Path.home() / ".openharness" / "bundles",
            project_dir=Path.cwd() / ".openharness" / "bundles",
        )
        bundle = bundle_store.get(invoked_command.mode)
        if bundle is None:
            available = sorted(bundle_store.discover().keys())
            raise UnknownBundleError(invoked_command.mode, available=available)

    # P5e-T3: Plugin hook discovery. Runs ONCE when the user opted in
    # via Settings or ``--enable-plugin-hooks``. Cheap (one
    # ``importlib.metadata.entry_points`` call); cached implicitly
    # because discovery happens before ``apply_bundle_to_context``.
    # Empty dict when flag off — bundle resolution falls back to
    # BUILTIN_HOOKS only.
    plugin_hook_catalog = discover_plugin_hooks() if enable_plugin_hooks else {}

    client = _build_client(settings)
    registry = create_default_tool_registry()

    # P5-T5: bootstrap MCP servers from Settings.mcp_servers (D15.2). Pool
    # lives for the lifetime of the query — adapters' McpClient references
    # must stay valid through run_query. Empty config (no MCP servers) is
    # a no-op pool.
    pool = McpClientPool(
        settings.mcp_servers,
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
        skill_store: SkillStore
        if no_skills:
            skill_store = EmptySkillStore()
        else:
            skill_store = FilesystemSkillStore(
                global_dir=Path.home() / ".openharness" / "skills",
                project_dir=env.cwd / ".openharness" / "skills",
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

        # System prompt is built AFTER MCP tools + LoadSkill register so
        # the LLM's tool catalog includes them. Skill catalog is injected
        # by ``build_system_prompt`` itself via the ``skill_store`` kwarg.
        # P5d-T4: bundle.system_prompt REPLACES base when set; otherwise
        # build against EFFECTIVE registry so the tool catalog reflects
        # any whitelist filter.
        if bundle is not None and bundle.system_prompt is not None:
            system_prompt = bundle.system_prompt
        else:
            system_prompt = build_system_prompt(
                effective_registry.to_api_schema(), env, skill_store=skill_store
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
                enable_plugin_hooks_override=enable_plugin_hooks,
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
