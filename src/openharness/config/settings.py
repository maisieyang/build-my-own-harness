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

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from openharness.mcp import McpServerConfig
from openharness.observability.logging import LogFormat, LogLevel
from openharness.permissions import PermissionMode


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
