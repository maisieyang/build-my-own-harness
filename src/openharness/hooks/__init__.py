"""Middleware (hook) system — P3-T4.

The dispatch-pipeline extensibility layer that turns OpenHarness from a
closed tool into a platform. Per ``decisions/08`` D13.1 + P3-T4 Three-Axis:

- 5 lifecycle events (``HookEvent``):PreToolUse / PostToolUse /
  PreApiCall / PostApiCall / OnError
- Single ``HookResult`` return type with three decisions
  (allow / deny / modify);per-event field semantics documented in
  ``hooks/result.py``
- Chain resolve:**modify accumulates, first-deny-wins**
  (Express-middleware semantics, 4d)
- Registration via ``HookRegistry`` injected through ``QueryContext`` (4c-4e)

Sub-units land incrementally:

- 4a (this commit):``HookEvent`` Literal + ``HookResult`` dataclass
- 4b:5 ``HookContext`` per-event dataclasses + ``Hook`` callable alias
- 4c:``HookRegistry`` class
- 4d:``execute_hook_chain`` (modify累积 + first-deny-wins + 异常→OnError)
- 4e:``QueryContext.hook_registry`` field + cli.py wiring
- 4f:integrate PreToolUse + PostToolUse into ``_dispatch_one``
- 4g:integrate PreApiCall + PostApiCall + OnError into ``run_query``
- 4h:end-to-end smoke (real hooks)

Public API:

    from openharness.hooks import HookEvent, HookResult
"""

from __future__ import annotations

from openharness.hooks.events import HookEvent
from openharness.hooks.result import HookResult

__all__ = [
    "HookEvent",
    "HookResult",
]
