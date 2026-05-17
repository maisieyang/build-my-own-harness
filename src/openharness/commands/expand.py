"""``expand_command`` — turn a raw prompt into a resolved user message — P5b-T2.

Per ``decisions/14`` C3:if a prompt starts with ``/``, it's a slash
command invocation. Look up the command by name, substitute ``{args}``
in its body, return the resolved string. Otherwise return the prompt
unchanged.

Algorithm:

::

    expand_command("/review last commit", store)
      → store.get("review") → Command(body="Please review:\\n\\n{args}\\n")
      → body.format(args="last commit")
      → "Please review:\\n\\nlast commit\\n"

    expand_command("just a regular prompt", store)
      → "just a regular prompt"   # no leading /

    expand_command("/unknown args", store)
      → raises UnknownCommandError("unknown", available=[...])

Three placement rules for args:

- Body contains ``{args}`` → substituted in place.
- Body has no ``{args}`` placeholder + non-empty args → appended on a
  new line at end of body (so args never silently vanish).
- Empty args + ``{args}`` placeholder → substituted with empty string.

P5d-T4: :func:`resolve_command_invocation` exposes the resolved
``Command`` alongside the substituted prompt so callers can inspect
``Command.mode`` for bundle loading. :func:`expand_command` is now a
thin wrapper that discards the Command — existing P5b callers stay
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.commands.errors import UnknownCommandError

if TYPE_CHECKING:
    from openharness.commands.model import Command
    from openharness.commands.store import CommandStore


_PLACEHOLDER = "{args}"


def resolve_command_invocation(prompt: str, store: CommandStore) -> tuple[str, Command | None]:
    """Resolve a possible slash command invocation.

    Returns ``(prompt_unchanged, None)`` if ``prompt`` lacks a leading
    ``/`` (pass-through path).
    Returns ``(substituted_body, command)`` for a successful invocation;
    the caller can inspect ``command.mode`` to decide whether to load
    a ModeBundle.
    Raises :class:`UnknownCommandError` when ``/`` is present but no
    command matches.
    """
    if not prompt.startswith("/"):
        return prompt, None

    name, args = _split_invocation(prompt)
    command = store.get(name)
    if command is None:
        available = sorted(store.discover().keys())
        raise UnknownCommandError(name, available=available)

    return _substitute(command.body, args), command


def expand_command(prompt: str, store: CommandStore) -> str:
    """Resolve ``prompt`` into the final user message (P5b compat shim).

    Wraps :func:`resolve_command_invocation` and discards the Command
    object — preserves the P5b call surface for callers that only need
    the substituted prompt. Phase 5d's CLI bootstrap uses
    :func:`resolve_command_invocation` directly to access ``Command.mode``.
    """
    expanded, _command = resolve_command_invocation(prompt, store)
    return expanded


def _split_invocation(prompt: str) -> tuple[str, str]:
    """Split ``"/cmd args..."`` into ``("cmd", "args...")``.

    Strips the leading ``/`` and splits on the first whitespace run.
    No args → empty string for args. Trailing whitespace on args is
    preserved verbatim(some templates care about exact spacing).
    """
    body = prompt[1:]  # strip leading "/"
    parts = body.split(None, 1)
    if not parts:
        return "", ""  # ``"/"`` alone — name="" — store.get("") returns None
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _substitute(body: str, args: str) -> str:
    """Apply ``{args}`` placeholder rules.

    Uses plain ``str.replace`` rather than ``str.format`` so other
    curly-brace content in the body(e.g., Python code examples
    ``{"key": "value"}``)is NOT interpreted as format specifiers.
    Same robustness as Claude Code's slash-command substitution.
    """
    if _PLACEHOLDER in body:
        return body.replace(_PLACEHOLDER, args)
    if args:
        # No placeholder + non-empty args → append on a new line so args
        # never silently vanish. Body's trailing whitespace preserved.
        separator = "" if body.endswith("\n") else "\n"
        return f"{body}{separator}{args}"
    return body
