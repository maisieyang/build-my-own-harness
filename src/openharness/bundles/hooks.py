"""``BUILTIN_HOOKS`` registry — P5d-T2.

Per ``decisions/17`` D19.4:Phase 5d ships TWO framework-provided named
hooks that bundle frontmatter can reference by string:

- ``audit_log`` — ``PostToolUse``;emits structured audit log event
  for each tool dispatch in the bundle's mode(useful for compliance
  trace via ``jq`` filtering on ``event=audit_tool_complete``)
- ``deny_writes`` — ``PreToolUse``;denies any tool whose
  ``is_read_only`` is False(belt-and-braces "read-only mode" so the
  bundle's ``tools.whitelist`` isn't the only safety net)

User-supplied custom hooks via plugin discovery defer to Phase 5e
when a real third-party-extension demand surfaces.

Bundle's ``hooks: [name1, name2]`` field passes through
:func:`resolve_hook` which raises :class:`UnknownBundleError`(reused
error type, ``kind="hook"``)on unknown name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.bundles.errors import UnknownBundleError
from openharness.hooks import (
    HookEvent,
    HookResult,
    PostToolUseContext,
    PreToolUseContext,
)
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from openharness.hooks import Hook
    from openharness.hooks.context import HookContext

_logger = get_logger("bundles")


async def audit_log(context: HookContext) -> HookResult | None:
    """``PostToolUse`` hook that emits a structured audit log event.

    Mirrors the existing ``tool_complete`` log shape but tagged
    ``event=audit_tool_complete`` so ``jq``-filtered trace consumers can
    distinguish bundle-driven audit records from the framework's default
    dispatch logging.

    Returns ``None`` — passthrough; doesn't modify ToolResult.
    """
    # Defensive: hook is registered on PostToolUse but Python type system
    # is flexible. Narrow to PostToolUseContext for the audit fields.
    if not isinstance(context, PostToolUseContext):
        return None  # not the event we registered for; passthrough
    _logger.info(
        "audit_tool_complete",
        tool_name=context.tool_name,
        tool_use_id=context.tool_use_id,
        is_error=context.result.is_error,
        output_len=len(context.result.output),
    )
    return None


async def deny_writes(
    context: HookContext,
) -> HookResult | None:
    """``PreToolUse`` hook that denies any tool with ``is_read_only=False``.

    Belt-and-braces "read-only mode":even if the bundle's
    ``tools.whitelist`` accidentally permits a mutating tool, this hook
    catches it at dispatch time.

    Looks up ``is_read_only`` via ``context.exec_context.parent_query.
    tool_registry.get(tool_name).is_read_only`` — the registry the engine
    used to find the tool already has the attribute.

    Returns ``HookResult.deny(message)`` for mutating tools,``None``
    (passthrough)for read-only ones.
    """
    if not isinstance(context, PreToolUseContext):
        return None
    parent = context.exec_context.parent_query
    if parent is None:
        # Defensive — engine populates parent_query (P6-T2). If absent,
        # we can't look up the tool; passthrough rather than crash.
        return None
    try:
        tool = parent.tool_registry.get(context.tool_name)
    except KeyError:
        # Tool not in registry → engine will surface its own "tool not
        # found" error; our deny doesn't need to fire.
        return None
    if tool.is_read_only:
        return None  # read-only tool — allow
    return HookResult.deny(f"deny_writes: tool {context.tool_name!r} is not read-only")


# Module-level catalog. Each entry is (event, hook_callable). Bundle
# frontmatter references these by name (the dict key).
BUILTIN_HOOKS: dict[str, tuple[HookEvent, Hook]] = {
    "audit_log": ("PostToolUse", audit_log),
    "deny_writes": ("PreToolUse", deny_writes),
}


def resolve_hook(name: str) -> tuple[HookEvent, Hook]:
    """Look up a built-in hook by name.

    Raises :class:`UnknownBundleError`(with ``kind="hook"``)on miss —
    same error type as nonexistent bundles so the CLI's error chain
    handles both via one ``except UnknownBundleError`` arm.
    """
    if name not in BUILTIN_HOOKS:
        raise UnknownBundleError(name, available=list(BUILTIN_HOOKS.keys()), kind="hook")
    return BUILTIN_HOOKS[name]
