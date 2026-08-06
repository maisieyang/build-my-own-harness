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

from openharness.tools.base import ExecutionDomain, TrustedControlSurface

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

    # Hooks run in-process and may deny or rewrite model calls. They are not
    # model-callable data-plane tools and are deliberately trusted control.
    execution_domain = ExecutionDomain.TRUSTED_CONTROL
    trusted_control_surface = TrustedControlSurface.HOOKS

    def __init__(self) -> None:
        # defaultdict so register on a new event doesn't require seeding;
        # the executor will get an empty list for unseen events via get().
        self._hooks: dict[HookEvent, list[Hook]] = defaultdict(list)
        # P11-T6 (D29.7): parallel set of PreApiCall hooks flagged to
        # re-run after a reactive PTL rebuild. ``id()``-keyed so closures
        # / bound methods that don't hash identically by value still
        # match. Membership is checked from :func:`get_reactive_rerun`.
        self._reactive_rerun_hook_ids: set[int] = set()

    def register(
        self,
        event: HookEvent,
        hook: Hook,
        *,
        re_run_on_reactive_rebuild: bool = False,
    ) -> None:
        """Append ``hook`` to the FIFO chain for ``event``.

        Multiple hooks for the same event run in registration order
        (Three-Axis J). To run a hook BEFORE another, register it first.

        ``re_run_on_reactive_rebuild`` (P11-T6 D29.7): if True AND
        ``event == "PreApiCall"``, the hook is re-fired after the
        engine's reactive PTL retry rebuilds the request — so
        injected content (memory, dynamic system prompt segments)
        survives the rebuild. No-op for non-PreApiCall events; the
        engine's reactive loop only re-runs PreApiCall hooks.
        """
        self._hooks[event].append(hook)
        if re_run_on_reactive_rebuild and event == "PreApiCall":
            self._reactive_rerun_hook_ids.add(id(hook))

    def get(self, event: HookEvent) -> list[Hook]:
        """Return the registered hooks for ``event`` in registration order.

        Returns a **caller-owned list copy** — mutating the returned list
        does not affect the registry(prevents accidental in-place removes
        during chain execution).
        """
        return list(self._hooks[event])

    def get_reactive_rerun(self, event: HookEvent) -> list[Hook]:
        """Return the subset of ``event`` hooks flagged for reactive
        rebuild re-run (P11-T6 D29.7).

        Always returns ``[]`` when ``event != "PreApiCall"`` — the
        engine only invokes this for the PTL retry path. Preserves
        registration order from the underlying ``_hooks[event]`` list.
        """
        if event != "PreApiCall" or not self._reactive_rerun_hook_ids:
            return []
        return [hook for hook in self._hooks[event] if id(hook) in self._reactive_rerun_hook_ids]

    def is_empty(self) -> bool:
        """True iff no hooks are registered for any event.

        Dispatch can short-circuit when this is true(saves the cost of
        constructing HookContext for events with no listeners).
        """
        return all(not hooks for hooks in self._hooks.values())

    def registration_count(self) -> int:
        """Return the number of active in-process hook registrations."""
        return sum(len(hooks) for hooks in self._hooks.values())
