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
from openharness.config import Settings
from openharness.engine import QueryContext, run_query
from openharness.errors import LoopError, OpenHarnessError
from openharness.permissions import DenyListChecker, PermissionMode
from openharness.prompts import build_system_prompt, detect_environment
from openharness.protocols import (
    ConversationMessage,
    TextBlock,
)
from openharness.tools import create_default_tool_registry

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

    client = _build_client(settings)
    registry = create_default_tool_registry()
    env = detect_environment()
    system_prompt = build_system_prompt(registry.to_api_schema(), env)

    context = QueryContext(
        api_client=client,
        tool_registry=registry,
        permission_checker=DenyListChecker(),
        system_prompt=system_prompt,
        cwd=env.cwd,
        model=model,
        max_tokens=max_tokens,
        permission_mode=permission_mode,
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

    try:
        asyncio.run(
            _run_ask(
                prompt,
                model_override=model,
                max_tokens=max_tokens,
                permission_mode_override=permission_mode_override,
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
