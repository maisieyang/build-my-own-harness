"""Query loop public entrypoint — typed stub for P2-T1 sub-unit 1c.

The body lands in P2-T4. P2-T1 ships only the *signature* so that:

- ``cli.py`` (P2-T6) can already import the symbol it will call.
- ``QueryContext`` (1a) and ``messages.py`` (1b) have a load-bearing consumer
  exercising their public types — if the signature here doesn't compile, one
  of the helper modules has a public-API problem.

Per D7.4 (Three-Axis kickoff): the entrypoint takes
``list[ConversationMessage]`` — not a bare prompt string — so the caller (CLI
for one-shot prompts, future Memory layer for restored history) controls
history shape; ``run_query`` only consumes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.engine.context import QueryContext
    from openharness.protocols import ApiStreamEvent, ConversationMessage


async def run_query(
    initial_messages: list[ConversationMessage],  # noqa: ARG001  -- P2-T4 body uses
    context: QueryContext,  # noqa: ARG001  -- P2-T4 body uses
) -> AsyncIterator[ApiStreamEvent]:
    """Drive the agent loop, yielding stream events until ``end_turn`` or the
    ``max_turns`` cap is reached.

    Stub for P2-T1: raises :class:`NotImplementedError` on first iteration.
    Body lands in P2-T4 (run_query core loop).
    """
    raise NotImplementedError("run_query body lands in P2-T4")
    # The `yield` below is unreachable but its mere presence in the AST is what
    # makes Python treat this as an async-generator function (so callers iterate
    # rather than await). Without it, this would be a plain coroutine and the
    # signature would be a lie.
    yield  # type: ignore[unreachable]
