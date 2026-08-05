"""Runtime configuration loaded from ``OPENHARNESS_*`` environment variables.

The :class:`Settings` class is the harness's single source of truth for
provider credentials and model selection. It is intentionally narrow in
Phase 1 — three fields, all plumbed straight to the OpenAI-compatible
client — but it carries the design contract that the rest of the CLI
relies on:

* **Provider-neutral prefix**: env vars are namespaced under
  ``OPENHARNESS_`` regardless of which Provider the base URL points to.
  Switching from Qwen to a future Anthropic-compatible endpoint changes
  the *value* of ``OPENHARNESS_BASE_URL``, never the *name* of any var.
  See ``decisions/05-cli.md`` D5.1.

* **Fail-fast on missing config**: ``api_key`` and ``base_url`` are
  required (no default). A missing value raises
  :class:`pydantic.ValidationError` at construction time, not at the
  first LLM call. This keeps the failure surface co-located with
  protocol-layer validation — every "bad input" path raises the same
  exception family. See D5.2.

* **Sensible default for ``model``**: ``qwen3.7-max`` — the strongest
  Qwen flagship available on the default endpoint. CLI ``--model`` and
  ``OPENHARNESS_MODEL`` both override it. See D5.3.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from openharness.mcp import McpServerConfig
from openharness.observability.logging import LogFormat, LogLevel
from openharness.permissions import PermissionMode, PermissionRules

# ---------------------------------------------------------------------------
# Nested sub-models — P10-T4.4e (D28.10)
# ---------------------------------------------------------------------------


class MemorySettings(BaseModel):
    """Tunables for the memory subsystem's read path.

    Lives as a nested sub-model on :class:`Settings`. Env-var overrides
    use the double-underscore convention from ``env_nested_delimiter``:
    ``OPENHARNESS_MEMORY__MAX_FILES=10`` sets ``settings.memory.max_files``.

    All caps are tuned for the Phase 10 "expected memory count <100
    per project" assumption. A project crossing that scale should
    increase ``max_files`` (more bodies injected per turn) and possibly
    lower ``max_body_chars`` (cap individual body verbosity to keep
    the total token budget reasonable).
    """

    max_files: int = Field(
        default=5,
        ge=0,
        description=(
            "Top-N cap for ``select_relevant_memories`` — at most this "
            "many memory bodies injected into the ``## Relevant Memories`` "
            "section per turn. ``0`` disables the section entirely."
        ),
    )
    max_entrypoint_bytes: int = Field(
        default=8_000,
        ge=0,
        description=(
            "Byte cap for the MEMORY.md entrypoint injected into the "
            "``## Memory`` section. Larger files are not loaded at all "
            "(Phase 10 doesn't truncate the index — the file is meant "
            "to be a small TOC by convention)."
        ),
    )
    max_body_chars: int = Field(
        default=8_000,
        ge=0,
        description=(
            "Per-memory body truncation cap for the ``## Relevant "
            "Memories`` section. Bodies above this cap get a "
            "``\\n...[truncated]...\\n`` marker. Matches HKUDS upstream."
        ),
    )
    max_claude_md_chars: int = Field(
        default=12_000,
        ge=0,
        description=(
            "Per-CLAUDE.md-file char cap for the cascade load. Files "
            "above this size are truncated with a marker — caps the "
            "worst case where a user writes a 100k-char CLAUDE.md that "
            "would otherwise blow the prompt budget."
        ),
    )


class CompactSettings(BaseModel):
    """Tunables for the Phase 11 auto-compact pipeline (D29.3 + D29.8).

    Env-var overrides use double-underscore: ``OPENHARNESS_COMPACT__THRESHOLD_RATIO=0.7``
    sets ``settings.compact.threshold_ratio``.

    ``enabled=False`` disables L2-L4 entirely; Phase 4's L1 microcompact
    + reactive PTL retry still run (L1 lives in the PostToolUse hook,
    not this pipeline). The CLI ``--no-auto-compact`` flag flips this.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Enable the L2-L4 auto-compact pipeline. When false, the "
            "engine skips proactive compaction; Phase 4's reactive "
            "PTL retry remains as the last-resort safety net. "
            "Override: ``OPENHARNESS_COMPACT__ENABLED`` env / "
            "``--no-auto-compact`` CLI flag."
        ),
    )
    threshold_ratio: float = Field(
        default=0.83,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of the model's context window that triggers "
            "auto-compact. Default 0.83 matches HKUDS upstream. "
            "Lower values compact more aggressively (saves PTL risk, "
            "costs more L4 LLM calls). Override: "
            "``OPENHARNESS_COMPACT__THRESHOLD_RATIO`` / "
            "``--compact-threshold`` CLI flag."
        ),
    )
    full_compact_max_tokens: int = Field(
        default=20_000,
        ge=1,
        description=(
            "max_tokens for the L4 summarize LLM call. 20k matches "
            "HKUDS — the 9-slot summary fits comfortably."
        ),
    )
    full_compact_timeout_s: float = Field(
        default=25.0,
        gt=0.0,
        description=(
            "Timeout for the L4 summarize LLM call. Above this, the "
            "summarize() asyncio.wait_for raises TimeoutError and L4 "
            "returns un-compacted (engine reactive PTL still catches)."
        ),
    )


class SnapshotHistorySettings(BaseModel):
    """Tunables for the Phase 13 history/ rotation (D31.2).

    Eager rotation runs after every per-turn snapshot write; this
    nested model holds the count + age thresholds. Both arms
    enforced together — whichever hits first drops oldest entries
    from history/.

    Env-var overrides via double-underscore:

    - ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT=200`` raises the
      count cap from default 100
    - ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_AGE_DAYS=30`` shortens
      the age cap from default 90
    - Either value at 0 disables that arm specifically (count=0
      keeps no history; age=0 keeps no age limit). max_count=0 means
      "rotation never preserves history" — current.json still rotates
      but immediately gets gc'd.
    """

    max_count: int = Field(
        default=100,
        ge=0,
        description=(
            "Maximum number of entries to keep in history/. Oldest "
            "dropped first when over. 0 = keep no history entries "
            "(history/ stays empty). Matches git reflog default count."
        ),
    )
    max_age_days: int = Field(
        default=90,
        ge=0,
        description=(
            "Maximum age in days for entries in history/. Older "
            "dropped on next rotation. 0 = disable age-based GC "
            "(only ``max_count`` enforced). Matches git reflog "
            "default age."
        ),
    )


class SnapshotSettings(BaseModel):
    """Tunables for the Phase 12 session-snapshot writer (D30.2 / D30.8).

    Snapshot writes are per-turn and ~5ms (JSON serialize + atomic
    file replace). ``enabled`` is env-only (no CLI flag) because the
    write is invisible cost — the user-visible knob lives on the READ
    side (``--no-resume``).

    Env-var overrides via the existing ``__`` delimiter:

    - ``OPENHARNESS_SNAPSHOT__ENABLED=false`` opts out of writing
    - ``OPENHARNESS_SNAPSHOT__MAX_AGE_WARN_DAYS=30`` tunes the
      staleness warning threshold at resume time
    - ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT=200`` (Phase 13)
    - ``OPENHARNESS_SNAPSHOT__LLM_FOCUS_STATE=true`` (Phase 13)
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Write per-turn JSON snapshot to "
            "``~/.openharness/snapshots/<basename>-<sha1>/current.json``. "
            "When false, no snapshot is written and ``--resume`` has "
            "nothing to load (the user-side read opt-out is "
            "``--no-resume``, not this setting)."
        ),
    )
    max_age_warn_days: int = Field(
        default=7,
        ge=0,
        description=(
            "Resume warns when loading a snapshot older than this. "
            "0 disables the age warning (other staleness signals — "
            "git HEAD drift, cwd mismatch, version mismatch — still fire)."
        ),
    )
    history: SnapshotHistorySettings = Field(
        default_factory=SnapshotHistorySettings,
        description=(
            "Nested history/ rotation tunables (Phase 13 D31.2). "
            "Env vars: ``OPENHARNESS_SNAPSHOT__HISTORY__MAX_COUNT`` + "
            "``OPENHARNESS_SNAPSHOT__HISTORY__MAX_AGE_DAYS``. See "
            ":class:`SnapshotHistorySettings`."
        ),
    )
    llm_focus_state: bool = Field(
        default=False,
        description=(
            "Opt-in (Phase 13 D31.7): at turn end, fire a secondary "
            "LLM call asking for the current goal + next_step in JSON "
            "and store the result in ``tool_metadata.task_focus_state`` "
            "(replacing the Phase 12 ``None`` placeholder). Adds "
            "~1-2s per turn when enabled. Failure-isolated: any "
            "exception / timeout / parse failure falls back to None "
            "placeholder. CLI: ``--llm-focus-state`` / "
            "``--no-llm-focus-state`` on ask + chat."
        ),
    )
    focus_state_model: str | None = Field(
        default=None,
        description=(
            "Override the model used for the focus-state secondary "
            "LLM call (Phase 13 D31.7). ``None`` (default) uses the "
            "main conversation's model. Set to a cheaper variant "
            "(e.g. ``qwen-turbo``) to reduce per-turn cost when "
            "focus-state is enabled."
        ),
    )


class WebSettings(BaseModel):
    """Tunables for the Phase 14 web tools (D29.4).

    Web tools (:class:`WebSearch` + :class:`WebFetch`) are opt-in via
    ``--enable-web`` on ``oh ask`` / ``oh chat`` *or* via
    ``OPENHARNESS_WEB__ENABLED=true``. When both signals are off, the
    tools are not registered and the default system prompt picks up
    the anti-substitution paragraph (D29.6).

    Env-var overrides via the existing ``__`` delimiter:

    - ``OPENHARNESS_WEB__ENABLED=true``
    - ``OPENHARNESS_WEB__SEARCH_PROVIDER=tavily``
    - ``OPENHARNESS_WEB__API_KEY=tvly-...``
    - ``OPENHARNESS_WEB__FETCH_TIMEOUT_SECONDS=15.0``
    - ``OPENHARNESS_WEB__FETCH_MAX_BYTES=2000000``
    - ``OPENHARNESS_WEB__FETCH_DEFAULT_MAX_CHARS=8000``

    D29.4: a single ``api_key`` field (not provider-specific naming
    like ``tavily_api_key``) — the provider is itself a config field,
    so swapping providers later does not require renaming the env
    var. The user's mental model stays "I configure web access".
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Master enable for the web tools (:class:`WebSearch` + "
            ":class:`WebFetch`). Default ``True`` (Phase 14.5 "
            "dogfood revision of D29.3 — matches Claude Code / "
            "Cursor / industry harness defaults). When ``True`` AND "
            ":attr:`api_key` is set, tools register and the system "
            "prompt switches to positive guidance. When ``True`` but "
            "no key is set, tools silently skip registration and the "
            "system prompt falls back to the anti-substitution "
            "paragraph (graceful degrade — new users without a "
            "Tavily account see v0.2.0 behavior, not a crash). "
            "Explicit ``--no-enable-web`` still forces OFF."
        ),
    )
    search_provider: str = Field(
        default="tavily",
        description=(
            "Which :class:`WebSearchProvider` implementation to "
            "instantiate when :class:`WebSearch` is registered. v1 "
            "ships only ``tavily`` (D29.2). Future ``brave`` / etc. "
            "land as siblings without touching the tool layer."
        ),
    )
    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "API key for the configured ``search_provider``. Wrapped "
            "in :class:`SecretStr` so it does not leak via ``repr`` "
            "or log lines. ``None`` means the key was not set — the "
            "tool layer surfaces a clear ``WebSearch is not "
            "configured`` error to the LLM rather than crashing."
        ),
    )
    fetch_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description=(
            "Per-URL timeout for :class:`WebFetch` HTTP GETs (D29.5). "
            "10s covers most slow news sites without making the agent "
            "feel unresponsive."
        ),
    )
    fetch_max_bytes: int = Field(
        default=5_000_000,
        gt=0,
        description=(
            "Body-size cap for :class:`WebFetch` (D29.5). 5MB is "
            "well above typical articles (~1MB) and guards against "
            "pathological pages that would blow memory."
        ),
    )
    fetch_default_max_chars: int = Field(
        default=10_000,
        gt=0,
        description=(
            "Default ``max_chars`` when the LLM does not specify one "
            "on a :class:`WebFetch` call (D29.5). 10k chars of "
            "markdown is roughly 2-3k tokens — comfortable for one "
            "tool result in a multi-turn research workflow."
        ),
    )


class Settings(BaseSettings):
    """OpenHarness runtime configuration.

    Loaded from ``OPENHARNESS_*`` env vars (and optionally a ``.env``
    file via the ``_env_file`` init kwarg). The loader is case-insensitive
    on env var names and ignores any unprefixed variable, so a stray
    ``API_KEY`` in the user's shell cannot silently override
    ``OPENHARNESS_API_KEY``.
    """

    model_config = SettingsConfigDict(
        env_prefix="OPENHARNESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # P10-T4.4e: enables nested-model env-var overrides like
        # ``OPENHARNESS_MEMORY__MAX_FILES=10`` setting
        # ``settings.memory.max_files``. The ``__`` delimiter is the
        # pydantic-settings convention; ``_`` would collide with field
        # names that contain underscores (``max_files``).
        env_nested_delimiter="__",
    )

    api_key: str = Field(
        ...,
        description="API key for the OpenAI-compatible Provider (required).",
    )
    base_url: str = Field(
        ...,
        description="OpenAI-compatible base URL (required).",
    )
    model: str = Field(
        default="qwen3.7-max",
        description="Default model name; overridden by CLI --model.",
    )
    goal_judge_model: str | None = Field(
        default=None,
        description=(
            "Override the model used for the /goal session judge (D48.4). "
            "``None`` (default) uses the main conversation's model. Set to "
            "a cheaper variant to reduce per-turn evaluation cost — but "
            "note the judge's reliability drops with weak models; the "
            "default stays on the main model on purpose (provider-reality "
            "divergence from CC's small-fast-model default). Env: "
            "OPENHARNESS_GOAL_JUDGE_MODEL."
        ),
    )
    goal_max_auto_turns: int = Field(
        default=25,
        ge=1,
        description=(
            "Backstop cap on consecutive /goal auto-continued turns before "
            "the loop pauses for human input (D48.5). The recommended "
            "bound lives in the condition itself (e.g. 'or stop after 20 "
            "turns'); this is the fail-closed ceiling behind it. Env: "
            "OPENHARNESS_GOAL_MAX_AUTO_TURNS."
        ),
    )
    extra_body: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Provider-specific request-body fields (JSON object) merged "
            "into every chat.completions call via the SDK's extra_body — "
            "e.g. '{\"enable_thinking\": false}' to disable DashScope qwen "
            "thinking mode. Generic passthrough: the harness carries no "
            "per-provider branches."
        ),
    )
    permission_mode: PermissionMode = Field(
        default=PermissionMode.DEFAULT,
        description=(
            "Permission policy. DEFAULT runs the deny-list normally; AUTO is "
            "P3-reserved (skip interactive confirmation, currently identical "
            "to DEFAULT); DRY_RUN never executes tools, emits 'would call X' "
            "events instead. Overridden by --auto / --dry-run CLI flags."
        ),
    )
    # ``Annotated[..., NoDecode]`` tells pydantic-settings:do NOT try to
    # JSON-decode this env value;hand the raw string to our validator.
    # Without NoDecode, ``OPENHARNESS_DENY_PATHS='secrets/**'`` triggers a
    # JSON parse error before our ``_parse_deny_paths`` runs.
    deny_paths: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        description=(
            "User-configured Tier 2 deny patterns for the AuthZ subsystem "
            "(P3-T3.3b). Comma-separated in env var: "
            "OPENHARNESS_DENY_PATHS='secrets/**,*.env'. Matches via "
            "openharness.permissions.tier_based._glob_match (fnmatch + "
            "`dir/**` recursive suffix). Empty tuple = no user rules."
        ),
    )
    permissions: PermissionRules = Field(
        default_factory=PermissionRules,
        description=(
            "loop-runtime L2: declarative permission rules aligned with Claude "
            "Code (``permissions.allow/deny/ask``, precedence deny>ask>allow). "
            "Nested env: ``OPENHARNESS_PERMISSIONS__ALLOW`` etc. Coexists with "
            "``permission_mode`` (posture) + ``deny_paths`` (Tier 2 legacy); the "
            "Tier 1 sensitive-path red line sits above every rule and cannot be "
            "overridden by an allow rule. See :class:`PermissionRules`."
        ),
    )

    log_level: LogLevel = Field(
        default="WARNING",
        description=(
            "Minimum log level. ``WARNING`` keeps the terminal quiet on normal "
            "runs; ``INFO`` shows turn / tool dispatch trace; ``DEBUG`` adds "
            "hook_invoke (verbose). Overridden by --log-level CLI flag."
        ),
    )
    log_format: LogFormat = Field(
        default="console",
        description=(
            "Log renderer. ``console`` for human reading (auto-no-color in "
            "non-TTY); ``json`` for one-JSON-per-line on stderr — "
            "jq / OTel / LangSmith exporter friendly. Overridden by "
            "--log-format CLI flag."
        ),
    )

    # P4-T4 (D14.3): Compaction Layer 1 default behaviour.
    tool_result_cap: int = Field(
        default=10_000,
        ge=0,
        description=(
            "Per-tool-result token cap for Layer 1 compaction "
            "(``TruncateToolResultHook``). Outputs above this cap get "
            "head/tail truncated with a marker. Codex-recommended 10k "
            "default. ``0`` disables truncation (same as --no-auto-truncate)."
        ),
    )
    auto_truncate: bool = Field(
        default=True,
        description=(
            "When true (default), the CLI auto-registers "
            "``TruncateToolResultHook`` so oversized tool outputs are "
            "compacted before the LLM sees them. When false, raw outputs "
            "flow through — Layer 2 reactive (prompt-too-long retry in "
            "the engine) still guards against blow-up. Overridden by the "
            "``--no-auto-truncate`` CLI flag."
        ),
    )

    # P5-T1 (D15.2): MCP servers to spawn at bootstrap. ``NoDecode``
    # stops pydantic-settings from doing its own JSON parse — our
    # validator handles both JSON-blob strings and tuples-of-McpServerConfig
    # (programmatic construction).
    mcp_servers: Annotated[tuple[McpServerConfig, ...], NoDecode] = Field(
        default=(),
        description=(
            "External MCP servers to spawn and register at bootstrap "
            "(Phase 5 federated tool registry, D15.2). Env var: "
            'OPENHARNESS_MCP_SERVERS=\'[{"name":"fs","command":'
            '["npx","-y","@modelcontextprotocol/server-filesystem",'
            '"/tmp"]}]\'. Empty tuple = no MCP integration, harness behaves '
            "as Phase 4."
        ),
    )
    # P5-T1 (D15.6): per-server trust whitelist for ``is_read_only``.
    # Untrusted servers' ``readOnlyHint`` claims are ignored — their tools
    # are forced through AuthZ Tier 3 strict path.
    trusted_mcp_servers: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        description=(
            "Server names whose ``annotations.readOnlyHint`` we trust "
            "(D15.6). Comma-separated env var: "
            "OPENHARNESS_TRUSTED_MCP_SERVERS='github,filesystem'. "
            "Servers not in this set get ``is_read_only=False`` forced "
            "regardless of what they self-report, ensuring AuthZ Tier 3 "
            "evaluates them as if they could mutate state. Same shape as "
            "``deny_paths`` (P3-T3.3b)."
        ),
    )

    # P6-T1 (D16.5): sub-agent depth bound. Top-level ``oh ask`` runs at
    # ``agent_depth=0``; each ``SpawnAgent`` invocation bumps the
    # sub-agent's ``QueryContext.agent_depth`` by 1, and ``SpawnAgent.execute``
    # refuses when ``parent.agent_depth + 1 > parent.max_agent_depth``.
    # Default 3 covers realistic delegation patterns (supervisor → research
    # → leaf tool calls); deeper structures usually indicate prompt design
    # problems or fork-bomb shape — fail loudly. ``0`` disables spawning
    # entirely (any spawn at depth 0 needs depth 1 ≤ 0 which is false).
    max_agent_depth: int = Field(
        default=3,
        ge=0,
        description=(
            "Maximum sub-agent recursion depth (Phase 6 / D16.5). Top-level "
            "``oh ask`` runs at depth 0; default 3 supports supervisor → "
            "research → leaf chains. ``0`` disables ``SpawnAgent`` "
            "invocations entirely (depth check refuses any spawn). Env var: "
            "OPENHARNESS_MAX_AGENT_DEPTH."
        ),
    )

    # P7b-T2 (D18.x): Docker sandbox substrate for BashTool. All default
    # OFF — existing behavior unchanged. Enable via ``--sandbox`` CLI
    # flag or ``OPENHARNESS_SANDBOX=true`` env var. The 5 ``sandbox_*``
    # configuration fields tune the container shape.
    sandbox_enabled: bool = Field(
        default=False,
        description=(
            "Enable the Docker sandbox substrate for Bash. When true, "
            "Bash commands execute inside a per-query container with the "
            "cwd bind-mounted read-write and no network by default. When "
            "false (default), Bash runs on the host via HostExecution. "
            "Env var: OPENHARNESS_SANDBOX_ENABLED. Overridden by the "
            "``--sandbox`` / ``--no-sandbox`` CLI flag."
        ),
    )
    sandbox_image: str = Field(
        default="python:3.12-slim",
        description=(
            "Docker image for the sandbox container (D18.7). Stock "
            "``python:3.12-slim`` (~120MB) covers bash/sh/python3/coreutils "
            "for most coding workflows. Override via "
            "``OPENHARNESS_SANDBOX_IMAGE=ubuntu:latest`` (image must "
            "provide POSIX sh; pure-binary images don't work)."
        ),
    )
    sandbox_network: str = Field(
        default="none",
        pattern=r"^(none|bridge)$",
        description=(
            "Container network mode (D18.5). ``none`` (default) blocks "
            "all external network — strongest default, defeats "
            "exfiltration. ``bridge`` enables NAT'd internet (needed for "
            "npm install / git clone etc.). Other modes (``host`` /"
            " custom networks) intentionally not supported."
        ),
    )
    sandbox_memory: str = Field(
        default="1g",
        description=(
            "Container memory limit, Docker-style spec (D18.6). ``1g`` / "
            "``512m`` / ``1024b`` etc. Suffix is case-insensitive. Kernel "
            "OOM-kills processes that exceed this without crashing the "
            "harness."
        ),
    )
    sandbox_cpus: float = Field(
        default=1.0,
        gt=0.0,
        description=(
            "Container CPU quota expressed as CPU equivalents (D18.6). "
            "1.0 = one full CPU; 0.5 = half a CPU. Internally translated "
            "to Docker's CpuQuota (period 100ms)."
        ),
    )
    sandbox_pids: int = Field(
        default=256,
        gt=0,
        description=(
            "Container process count limit (D18.6). Bounds fork bombs. "
            "256 covers normal coding workflows including npm install / "
            "pytest fork; bump for parallel build systems if needed."
        ),
    )
    # P7c-T2 (D23.4): OCI runtime selection. ``runc`` (default) shares
    # the host kernel; ``runsc`` (gVisor) interposes syscalls in user
    # space for stronger isolation at ~3x syscall overhead. Other OCI
    # runtimes (kata, sysbox, etc.) work too — no client-side
    # allowlist per D23.2.
    sandbox_runtime: str = Field(
        default="runc",
        description=(
            "OCI runtime for the sandbox container. ``runc`` (default) "
            "shares the host kernel; ``runsc`` selects gVisor for "
            "user-space syscall isolation (requires gVisor installed "
            "on the host:https://gvisor.dev/docs/user_guide/install/). "
            "Other registered OCI runtimes pass through unchanged. "
            "Env var: OPENHARNESS_SANDBOX_RUNTIME. Overridden by the "
            "``--sandbox-runtime`` CLI flag."
        ),
    )
    # P5e-T3 (decisions/18 D20.3): plugin hook discovery is opt-in.
    # Default OFF so a transitive dependency shipping
    # ``openharness.hooks`` entry points cannot register a hook the
    # user didn't consent to. When ON, ``cli._run_ask`` calls
    # ``discover_plugin_hooks()`` once at bootstrap and threads the
    # catalog through to bundle hook resolution.
    enable_plugin_hooks: bool = Field(
        default=False,
        description=(
            "Enable discovery of third-party hooks declared via the "
            "``openharness.hooks`` Python entry-point group (P5e). "
            "When false (default), bundle ``hooks:`` frontmatter "
            "resolves only against framework built-ins (``audit_log`` "
            "/ ``deny_writes``). Env var: "
            "OPENHARNESS_ENABLE_PLUGIN_HOOKS. Overridden by the "
            "``--enable-plugin-hooks`` / ``--no-enable-plugin-hooks`` "
            "CLI flag."
        ),
    )

    # P9-T3 (decisions/24 D27.4): plugin discovery is opt-in. Same
    # safety pattern as ``enable_plugin_hooks`` — plugins ship
    # arbitrary Python code (hook modules), so default off + explicit
    # consent. When ON, ``cli._run_ask`` / ``_run_chat`` constructs a
    # PluginLoader, discovers ``~/.openharness/plugins/<name>/manifest.yaml``,
    # fans out the 5 component types via LayeredStore wrappers + hook
    # catalog merge + mcp_servers append.
    enable_plugins: bool = Field(
        default=False,
        description=(
            "Enable discovery + loading of plugins from "
            "``~/.openharness/plugins/<name>/manifest.yaml`` (P9 / "
            "decisions/24). When false (default), ``oh plugins list`` "
            "still works (read-only introspection) but plugin "
            "components are NOT registered into the running registry. "
            "Env var: OPENHARNESS_ENABLE_PLUGINS. Overridden by the "
            "``--enable-plugins`` / ``--no-enable-plugins`` CLI flag."
        ),
    )

    # P10-T4.4e (decisions/25 D28.10): memory subsystem opt-OUT.
    # **Deviation from plugins' opt-in pattern**: memory is read-only +
    # side-effect-free in Phase 10 (the only write is ``use_count++`` to
    # a private user-dir file). No code-execution risk. Default ON so
    # the harness "knows the project" out of the box; opting out is for
    # users who explicitly want a stateless harness.
    enable_memory: bool = Field(
        default=True,
        description=(
            "Enable the memory subsystem (Phase 10, D28.10). When true "
            "(default), the CLI assembles a :class:`FilesystemMemoryStore` "
            "at ``~/.openharness/memory/<basename>-<sha1(cwd)>/``, loads "
            "the CLAUDE.md cascade, and injects both into the system "
            "prompt per turn. When false, neither section appears — "
            "the prompt is byte-identical to the pre-Phase-10 layout. "
            "Env var: OPENHARNESS_ENABLE_MEMORY. Overridden by the "
            "``--enable-memory`` / ``--no-enable-memory`` CLI flag."
        ),
    )
    memory: MemorySettings = Field(
        default_factory=MemorySettings,
        description=(
            "Nested memory subsystem tunables (D28.10). Env vars use the "
            "double-underscore convention: "
            "``OPENHARNESS_MEMORY__MAX_FILES=10`` overrides "
            "``memory.max_files``. See :class:`MemorySettings` for the "
            "full set of per-field knobs."
        ),
    )

    # P11-T5 (decisions/26 D29.8): Phase 11 compact + extraction
    # nested settings. Use the same double-underscore env convention
    # as `memory`. Phase 4's `tool_result_cap` + `auto_truncate` are
    # L1 microcompact knobs (independent of these L2-L4 knobs).
    compact: CompactSettings = Field(
        default_factory=CompactSettings,
        description=(
            "Nested compact-pipeline tunables (D29.3 + D29.8). Env vars: "
            "``OPENHARNESS_COMPACT__THRESHOLD_RATIO`` / "
            "``OPENHARNESS_COMPACT__ENABLED`` etc. See :class:`CompactSettings`."
        ),
    )
    snapshot: SnapshotSettings = Field(
        default_factory=SnapshotSettings,
        description=(
            "Nested snapshot tunables (D30.2 + D30.8). Env vars: "
            "``OPENHARNESS_SNAPSHOT__ENABLED`` / "
            "``OPENHARNESS_SNAPSHOT__MAX_AGE_WARN_DAYS``. "
            "See :class:`SnapshotSettings`. The user-visible read opt-"
            "out is the ``--no-resume`` CLI flag, not this setting."
        ),
    )
    web: WebSettings = Field(
        default_factory=WebSettings,
        description=(
            "Nested web-tools tunables (Phase 14 D29.4). Env vars: "
            "``OPENHARNESS_WEB__ENABLED`` / "
            "``OPENHARNESS_WEB__SEARCH_PROVIDER`` / "
            "``OPENHARNESS_WEB__API_KEY`` etc. See :class:`WebSettings`. "
            "User-visible opt-in is also the ``--enable-web`` CLI flag, "
            "OR'd with this setting at startup."
        ),
    )

    @field_validator("deny_paths", mode="before")
    @classmethod
    def _parse_deny_paths(cls, value: Any) -> Any:
        """Parse comma-separated env strings into a tuple of patterns.

        - ``"a,b,c"`` -> ``("a", "b", "c")``
        - ``"a, b ,c"`` -> ``("a", "b", "c")`` (whitespace stripped)
        - ``"a,,b,"`` -> ``("a", "b")`` (empty segments dropped)
        - already-tuple input passes through (programmatic construction)
        """
        if isinstance(value, str):
            return tuple(p.strip() for p in value.split(",") if p.strip())
        return value

    @field_validator("trusted_mcp_servers", mode="before")
    @classmethod
    def _parse_trusted_mcp_servers(cls, value: Any) -> Any:
        """Same shape as :meth:`_parse_deny_paths` — comma-separated env →
        tuple of names. Mirrors P3-T3.3b precedent."""
        if isinstance(value, str):
            return tuple(p.strip() for p in value.split(",") if p.strip())
        return value

    @field_validator("mcp_servers", mode="before")
    @classmethod
    def _parse_mcp_servers(cls, value: Any) -> Any:
        """Parse env-var JSON blob OR pass through programmatic input.

        Env shape (JSON array of objects, each with ``name`` + ``command``
        + optional ``env``)::

            OPENHARNESS_MCP_SERVERS='[
              {"name": "fs", "command": ["npx", "@.../server-filesystem"]},
              {"name": "gh", "command": ["node", "gh.js"],
               "env": {"GITHUB_TOKEN": "ghp_..."}}
            ]'

        Programmatic shape:tuple of :class:`McpServerConfig` (passes
        through unchanged); also accepts list of dicts (for testing
        convenience).

        Returns a tuple of :class:`McpServerConfig` instances — pydantic
        then accepts the tuple as the field's value.
        """
        import json

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"OPENHARNESS_MCP_SERVERS not valid JSON: {exc}") from exc
        if isinstance(value, (list, tuple)):
            return tuple(
                item
                if isinstance(item, McpServerConfig)
                else McpServerConfig(
                    name=item["name"],
                    command=tuple(item["command"]),
                    env=dict(item.get("env", {})),
                )
                for item in value
            )
        return value
