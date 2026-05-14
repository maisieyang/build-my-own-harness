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
