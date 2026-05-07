"""Command-line interface for OpenHarness (P1-T4).

This module is the user-facing seam of Phase 1: it parses the command
line, loads :class:`Settings` from env, builds an
:class:`OpenAICompatibleApiClient`, dispatches a single request, and
streams the response through :func:`render_stream`.

Design highlights (rationale in ``learnings/04-cli.md``; external
contracts in ``decisions/05-cli.md``):

* **Typer** powers argument parsing (``--model`` / ``-m`` overrides,
  ``--max-tokens`` for budgeting, single positional prompt).
* **Provider-neutral env vars** (``OPENHARNESS_API_KEY`` /
  ``_BASE_URL`` / ``_MODEL``) are read by ``Settings``; the CLI never
  reaches into ``os.environ`` directly.
* **Differentiated error UX**: each error type maps to a one-line hint
  pointing at the next user action. No Python tracebacks in the default
  mode -- ``--debug`` is deferred to Tier 1.
* **Append-only streaming**: :func:`render_stream` lives in
  ``_stream_render.py`` and is unit-tested separately.

The two seams that tests substitute:

* :func:`_load_settings` -- replace to inject deterministic config.
* :func:`_build_client` -- replace with a stub client that yields
  canned :class:`ApiStreamEvent`s, exercising the entire CLI path
  without touching the network.

Both seams are module-level so ``monkeypatch.setattr`` works without
touching Typer internals.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import typer
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
from openharness.config import Settings
from openharness.protocols import (
    ApiMessageRequest,
    ConversationMessage,
    TextBlock,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.stream_events import ApiStreamEvent


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
) -> None:
    """Build the request, run the stream, render it. Not exception-handling
    aware -- the synchronous Typer command wraps this and translates
    exceptions into user-facing exit codes."""
    settings = _load_settings()
    model = model_override or settings.model

    request = ApiMessageRequest(
        model=model,
        max_tokens=max_tokens,
        messages=[
            ConversationMessage(
                role="user",
                content=[TextBlock(text=prompt)],
            ),
        ],
    )

    client = _build_client(settings)
    events: AsyncIterator[ApiStreamEvent] = client.stream_message(request)
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
) -> None:
    """Stream a single LLM response to stdout."""
    try:
        asyncio.run(_run_ask(prompt, model_override=model, max_tokens=max_tokens))
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
    except OpenHarnessApiError as exc:
        # Catch-all for any future API error subclass we have not
        # specialized. Better to report something than to vanish.
        typer.echo(f"API error: {exc}", err=True)
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
