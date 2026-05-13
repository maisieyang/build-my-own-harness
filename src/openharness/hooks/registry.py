"""HookRegistry — register/lookup hooks by event — P3-T4.4c.

Mirrors :class:`openharness.tools.ToolRegistry` pattern:in-memory map,
insertion-ordered, caller-owned list copy on read. Per Three-Axis J:
**registration order = execution order** (FIFO), no priority.

Per Three-Axis E: registry is injected into ``QueryContext`` (4e);Phase 3
does NOT do plugin / file-based discovery (that's Phase 5). Users register
hooks programmatically before constructing the context.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openharness.hooks.context import Hook
    from openharness.hooks.events import HookEvent


class HookRegistry:
    """In-memory map ``HookEvent -> list[Hook]``.

    Empty registry == no hooks(zero overhead in dispatch).

    Usage:

        registry = HookRegistry()
        registry.register("PreToolUse", my_log_hook)
        registry.register("PreToolUse", my_cost_hook)
        # ... later, executor reads:
        hooks = registry.get("PreToolUse")  # [my_log_hook, my_cost_hook]
    """

    def __init__(self) -> None:
        # defaultdict so register on a new event doesn't require seeding;
        # the executor will get an empty list for unseen events via get().
        self._hooks: dict[HookEvent, list[Hook]] = defaultdict(list)

    def register(self, event: HookEvent, hook: Hook) -> None:
        """Append ``hook`` to the FIFO chain for ``event``.

        Multiple hooks for the same event run in registration order
        (Three-Axis J). To run a hook BEFORE another, register it first.
        """
        self._hooks[event].append(hook)

    def get(self, event: HookEvent) -> list[Hook]:
        """Return the registered hooks for ``event`` in registration order.

        Returns a **caller-owned list copy** — mutating the returned list
        does not affect the registry(prevents accidental in-place removes
        during chain execution).
        """
        return list(self._hooks[event])

    def is_empty(self) -> bool:
        """True iff no hooks are registered for any event.

        Dispatch can short-circuit when this is true(saves the cost of
        constructing HookContext for events with no listeners).
        """
        return all(not hooks for hooks in self._hooks.values())
