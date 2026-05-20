"""Filesystem plugin hook example — log turn count + warn at high turns.

Drop this file at ``~/.openharness/hooks/turn_counter.py`` (global)
or ``<cwd>/.openharness/hooks/turn_counter.py`` (project) and run
with ``--enable-plugin-hooks``:

    cp examples/hooks/turn_counter.py ~/.openharness/hooks/
    oh ask --enable-plugin-hooks "list 5 git commands"

Verify discovery picked it up:

    oh hooks list --enable-plugin-hooks
    oh hooks describe count_turns --enable-plugin-hooks

The hook fires before each LLM API call (``PreApiCall``). It logs
a turn count and warns when the user is past a soft budget. It
never denies — pure observability.

Per Phase 5e/5f the framework imports this file at bootstrap when
``--enable-plugin-hooks`` is set;``@hook_spec(event)`` turns the
decorated function into a ``HookSpec`` discoverable as a module
attribute. The attribute name (``count_turns``) becomes the
``hook_names:`` reference in bundle frontmatter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.bundles import hook_spec
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from openharness.hooks.context import HookContext
    from openharness.hooks.result import HookResult

_logger = get_logger("plugin.turn_counter")

# Soft budget — warn but don't deny. Phase 6+ retro §4 noted hook-
# level deny is the bundle's deny_writes pattern;turn budgets are
# already enforced by ``OPENHARNESS_MAX_AGENT_DEPTH`` + the engine's
# ``max_turns``.
_SOFT_TURN_WARN_THRESHOLD = 5


@hook_spec("PreApiCall")
async def count_turns(context: HookContext) -> HookResult | None:
    """Log the current turn number before each LLM call.

    Observability-only: returns ``None`` (passthrough) so the API
    call always proceeds. Warning fired at turn >= 5 helps surface
    accidental long loops in `oh ask` (a normal interactive
    conversation rarely exceeds 5 turns per single ``oh ask``).
    """
    # Narrow from the union via duck-typing on the ``turn`` field —
    # only PreApiCallContext / PostApiCallContext carry it. The
    # hook is registered ON PreApiCall so this branch always hits in
    # practice;defensive isinstance saves the rare type confusion.
    turn = getattr(context, "turn", None)
    if turn is None:
        return None  # not a PreApiCall context — passthrough

    if turn >= _SOFT_TURN_WARN_THRESHOLD:
        _logger.warning(
            "turn_counter_high",
            turn=turn,
            threshold=_SOFT_TURN_WARN_THRESHOLD,
        )
    else:
        _logger.info("turn_counter", turn=turn)
    return None  # passthrough — never denies
