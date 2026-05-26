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

* **Sensible default for ``model``**: ``qwen-plus`` balances cost and
  capability for the Phase 1 test target. CLI ``--model`` and
  ``OPENHARNESS_MODEL`` both override it. See D5.3.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from openharness.mcp import McpServerConfig
from openharness.observability.logging import LogFormat, LogLevel
from openharness.permissions import PermissionMode

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


class ExtractionSettings(BaseModel):
    """Tunables for the Phase 11 memory-extraction secondary pass (D29.5).

    Env-var overrides: ``OPENHARNESS_EXTRACTION__MODEL=qwen-turbo``
    sets ``settings.extraction.model``. CLI ``--no-extract`` flips
    ``enabled=False``.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Enable the per-turn extraction secondary pass. When false, "
            "the engine never invokes extract_memories_from_turn; agent "
            "writes to the memory store via filesystem tools instead. "
            "Override: ``OPENHARNESS_EXTRACTION__ENABLED`` env / "
            "``--no-extract`` CLI flag."
        ),
    )
    max_records_per_turn: int = Field(
        default=3,
        ge=0,
        description=(
            "Cap on memories proposed per turn. The LLM may suggest "
            "more; only the first ``max_records_per_turn`` get written. "
            "0 disables (same as ``enabled=False`` but keeps the LLM "
            "call for observability)."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Model for the extraction LLM call. ``None`` (default) uses "
            "the same model as the main conversation. Set to a cheaper "
            "variant (e.g., ``qwen-turbo``) to save cost. Phase 11 sub-"
            "decision per boundary doc."
        ),
    )
    timeout_s: float = Field(
        default=30.0,
        gt=0.0,
        description=(
            "Timeout for the extraction LLM call. Extraction is best-"
            "effort — timeout returns ExtractionResult with error set; "
            "the turn still completes."
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
        default="qwen-plus",
        description="Default model name; overridden by CLI --model.",
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
    extraction: ExtractionSettings = Field(
        default_factory=ExtractionSettings,
        description=(
            "Nested extraction tunables (D29.5 + D29.8). Env vars: "
            "``OPENHARNESS_EXTRACTION__ENABLED`` / "
            "``OPENHARNESS_EXTRACTION__MODEL`` etc. See :class:`ExtractionSettings`."
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
