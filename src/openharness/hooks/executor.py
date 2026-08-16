"""``execute_hook_chain`` — the algorithm core — P3-T4.4d.

Implements the chain resolve algorithm locked by P3-T4 Three-Axis B:

- **Modify accumulates**:hook_A modify → ctx updates → hook_B sees changes
  → may further modify. Each ``new_*`` field is **last-wins** when multiple
  hooks set it.
- **First-deny-wins**:as soon as any hook returns ``HookResult.deny(...)``,
  the chain stops and the deny propagates. Subsequent hooks don't run
  (user wanting "always-observe" puts that hook **before** the deny hook,
  per Three-Axis registration-order semantics).
- **None / allow** = pass-through. Both treated as "I have no opinion".

Per Three-Axis G (OnError semantics):

- Hook raises Python exception → fire OnError chain (one-level deep,
  prevents infinite recursion) → wrap as :class:`HookError` → re-raise.
- ``decision="deny"`` on PostToolUse / PostApiCall → **auto-convert** to
  ``modify(new_is_error=True, new_output=message)`` (Three-Axis F:soft
  post-deny lets content-moderation hooks mark a result as failure
  after-the-fact).
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from openharness.errors import HookError
from openharness.hooks.context import OnErrorContext, PreToolUseContext
from openharness.hooks.result import HookResult
from openharness.observability import get_logger

if TYPE_CHECKING:
    from openharness.hooks.context import Hook, HookContext
    from openharness.hooks.events import HookEvent
    from openharness.hooks.registry import HookRegistry


_POST_EVENTS: frozenset[HookEvent] = frozenset({"PostToolUse", "PostApiCall"})


logger = get_logger("hooks")


async def execute_hook_chain(
    registry: HookRegistry,
    event: HookEvent,
    ctx: HookContext,
    *,
    hook_subset: list[Hook] | None = None,
) -> HookResult | None:
    """Run all hooks registered for ``event``, returning the accumulated
    result(or ``None`` if all hooks passed through).

    Returns:
    - ``None``                — every hook passed through (allow/None)
    - ``HookResult.deny(...)`` — first deny hit; later hooks not run
    - ``HookResult.modify(...)`` — accumulated modifications

    Raises:
    - :class:`HookError`  — a hook itself raised an exception.
      Before raising, the executor fires OnError hooks one-level deep
      (avoiding infinite recursion).

    ``hook_subset`` (P11-T6 D29.7): when provided, only the supplied
    hooks run instead of the full ``registry.get(event)`` chain. Used
    by the engine's one-shot PTL recompile path to fire only PreApiCall
    hooks flagged with ``re_run_on_reactive_rebuild``. The subset is
    treated AS IF it were the full chain — OnError dispatch, deny /
    modify accumulation rules all apply identically.
    """
    hooks = hook_subset if hook_subset is not None else registry.get(event)
    if not hooks:
        return None  # zero overhead when no hooks registered

    current_ctx = ctx
    accumulated_new_input: dict[str, Any] | None = None
    accumulated_new_request: object | None = None
    accumulated_new_output: str | None = None
    accumulated_new_metadata: dict[str, Any] | None = None
    accumulated_new_is_error: bool | None = None
    any_modify = False

    for hook in hooks:
        hook_name = getattr(hook, "__name__", repr(hook))
        # P3-T5.5d: log every invocation at DEBUG. Default WARNING level
        # hides this — hook fan-out can be high (5+ hooks x 5 events), so
        # users opt into the noise via ``--log-level DEBUG``.
        # NB: structlog reserves ``event`` as the message name (first
        # positional);our HookEvent fact ships as ``hook_event``.
        logger.debug("hook_invoke", hook=hook_name, hook_event=event)
        try:
            result = await hook(current_ctx)
        except Exception as exc:
            # P3-T5.5d: hook crash is exceptional — log at WARNING so it
            # surfaces under default level. Only the exception class name
            # is logged; the full exception still propagates via
            # ``raise HookError(...) from exc`` for callers / tests.
            logger.warning(
                "hook_failed",
                hook=hook_name,
                hook_event=event,
                error=type(exc).__name__,
            )
            # Three-Axis G:fire OnError chain (one-level deep), then re-raise.
            if event != "OnError":
                await _fire_on_error_one_level(
                    registry,
                    exc,
                    where="hook",
                    tool_name=_tool_name_from_ctx(current_ctx),
                )
            raise HookError(f"hook {hook_name!r} crashed: {exc}") from exc

        if result is None:
            continue
        if result.decision == "allow":
            continue  # explicit allow == pass-through

        # Post-event soft-deny conversion (Three-Axis F).
        if result.decision == "deny" and event in _POST_EVENTS:
            result = HookResult(
                decision="modify",
                new_is_error=True,
                new_output=f"post-hook deny: {result.message or 'unspecified'}",
            )

        if result.decision == "deny":
            return result  # first-deny-wins, chain breaks

        # decision == "modify": accumulate and update ctx for next hook.
        any_modify = True
        if result.new_input is not None:
            accumulated_new_input = result.new_input
            if isinstance(current_ctx, PreToolUseContext):
                current_ctx = replace(current_ctx, tool_input=result.new_input)
        if result.new_request is not None:
            accumulated_new_request = result.new_request
            # Update ctx.request if this is a PreApiCall context.
            if hasattr(current_ctx, "request"):
                current_ctx = replace(current_ctx, request=result.new_request)  # type: ignore[arg-type]
        if result.new_output is not None:
            accumulated_new_output = result.new_output
        if result.new_metadata is not None:
            accumulated_new_metadata = result.new_metadata
        if result.new_is_error is not None:
            accumulated_new_is_error = result.new_is_error

    if not any_modify:
        return None

    return HookResult(
        decision="modify",
        new_input=accumulated_new_input,
        new_request=accumulated_new_request,
        new_output=accumulated_new_output,
        new_metadata=accumulated_new_metadata,
        new_is_error=accumulated_new_is_error,
    )


async def _fire_on_error_one_level(
    registry: HookRegistry,
    exception: Exception,
    where: Literal["api", "tool", "hook", "loop"],
    tool_name: str | None,
) -> None:
    """Fire OnError hooks **once**, suppressing any exception they raise.

    One-level deep prevents OnError-on-OnError-on-OnError infinite recursion.
    OnError hooks that themselves crash get their exception silently dropped
    (we already have the original error to surface).
    """
    on_error_hooks = registry.get("OnError")
    if not on_error_hooks:
        return
    on_error_ctx = OnErrorContext(
        exception=exception,
        where=where,
        tool_name=tool_name,
    )
    for hook in on_error_hooks:
        # OnError hook crashing must not mask the original error — suppress.
        with contextlib.suppress(Exception):
            await hook(on_error_ctx)


def _tool_name_from_ctx(ctx: HookContext) -> str | None:
    """Extract tool_name from a HookContext if present (Pre/PostToolUse)."""
    return getattr(ctx, "tool_name", None)
