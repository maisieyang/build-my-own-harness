"""``apply_bundle_to_context`` — P5d-T3b.

Pure helper that takes a base set of QueryContext-building primitives +
a :class:`Bundle`, and returns the same primitives with the bundle's
overrides applied. Pure(no I/O), so unit tests don't need fixtures
beyond ad-hoc Settings / ToolRegistry / HookRegistry instances.

**Composition pattern**:Phase 5d is the first *cross-layer tenant* — this
helper composes the 4 layered abstractions Phase 5a-7b established:

- **Layer 1 — system_prompt**:bundle.system_prompt REPLACES the base
  prompt entirely(`None` ⇒ base preserved).Caller is responsible for
  the bundle's prompt being self-contained.
- **Layer 2 — tool catalog**:if bundle.tools_whitelist is set,wrap the
  base registry with :class:`WhitelistRegistry`. ``None`` ⇒ base
  unchanged(LLM sees all tools).
- **Layer 3a — deny_paths**:AUGMENT — bundle.deny_paths concatenated to
  Settings.deny_paths(never replaces).Safer default per decisions/17
  risk row.
- **Layer 3b — hook chain**:clone base HookRegistry + register bundle's
  named hooks via :func:`resolve_hook`. Bundle hooks fire AFTER user-
  registered hooks(registration order = execution order per P3 D8.J).

The function returns a :class:`BundleApplication` frozen dataclass
collecting all four results.

Returns are wired into the actual ``QueryContext`` by ``cli._run_ask``
(T4); this helper is intentionally agnostic to QueryContext itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openharness.bundles.hooks import resolve_hook
from openharness.bundles.registry import WhitelistRegistry
from openharness.hooks import HookRegistry

if TYPE_CHECKING:
    from openharness.bundles.hook_plugins import HookSpec
    from openharness.bundles.model import Bundle
    from openharness.config.settings import Settings
    from openharness.hooks.events import HookEvent
    from openharness.tools import ToolRegistry


# Hardcoded enumeration mirrors the HookEvent Literal closed taxonomy
# (decisions/08 D13.1). Keeps clone iteration explicit + avoids triggering
# defaultdict insertions on the base registry during get().
_ALL_HOOK_EVENTS: tuple[HookEvent, ...] = (
    "PreToolUse",
    "PostToolUse",
    "PreApiCall",
    "PostApiCall",
    "OnError",
)


@dataclass(frozen=True)
class BundleApplication:
    """Result of applying a bundle to a base set of QueryContext primitives.

    Caller plugs each field straight into the corresponding ``QueryContext``
    construction site (system_prompt → field; tool_registry → field;
    hook_registry → field; settings → passed to
    :class:`TierBasedPermissionChecker` constructor for the
    permission_checker field).
    """

    tool_registry: ToolRegistry
    hook_registry: HookRegistry
    settings: Settings
    system_prompt: str


def apply_bundle_to_context(
    *,
    bundle: Bundle,
    tool_registry: ToolRegistry,
    hook_registry: HookRegistry,
    settings: Settings,
    system_prompt: str,
    plugin_hook_catalog: dict[str, HookSpec] | None = None,
) -> BundleApplication:
    """Apply bundle's 4-layer overrides to a base set of primitives.

    Pure function — never mutates inputs. The base ``HookRegistry`` is
    cloned(not aliased)so subsequent registration on the returned
    chain doesn't bleed into the base.

    P5e-T2: ``plugin_hook_catalog`` (default ``None``) threads through
    to :func:`resolve_hook` so bundle ``hook_names`` can reference
    plugin-discovered hooks when ``--enable-plugin-hooks`` is on.
    Default ``None`` preserves Phase 5d behavior byte-identical.
    """
    new_prompt = bundle.system_prompt if bundle.system_prompt is not None else system_prompt

    new_registry: ToolRegistry = tool_registry
    if bundle.tools_whitelist is not None:
        new_registry = WhitelistRegistry(tool_registry, set(bundle.tools_whitelist))

    new_settings: Settings = settings
    if bundle.deny_paths:
        new_settings = settings.model_copy(
            update={"deny_paths": settings.deny_paths + bundle.deny_paths}
        )

    new_hooks = _clone_hook_registry(hook_registry)
    for name in bundle.hook_names:
        event, hook_fn = resolve_hook(name, plugin_catalog=plugin_hook_catalog)
        # P11-T6 (D29.7): preserve plugin-declared re-run flag so
        # PreApiCall hooks whose injected content must survive PTL
        # retry rebuild get re-fired by the engine. Built-in hooks
        # (BUILTIN_HOOKS) don't currently set this; only plugin
        # ``HookSpec`` instances expose the field.
        re_run = (
            plugin_hook_catalog is not None
            and name in plugin_hook_catalog
            and plugin_hook_catalog[name].re_run_on_reactive_rebuild
        )
        new_hooks.register(event, hook_fn, re_run_on_reactive_rebuild=re_run)

    return BundleApplication(
        tool_registry=new_registry,
        hook_registry=new_hooks,
        settings=new_settings,
        system_prompt=new_prompt,
    )


def _clone_hook_registry(base: HookRegistry) -> HookRegistry:
    """Return a fresh HookRegistry mirroring base's registrations.

    Re-registration preserves order (registration order = execution
    order per P3-T4 D8.J). Bundle hooks appended via the caller will
    fire AFTER these base hooks for the same event.

    P11-T6 (D29.7): preserves the ``re_run_on_reactive_rebuild`` flag
    on PreApiCall hooks across the clone — otherwise bundle resolution
    would silently drop the property mid-session.
    """
    cloned = HookRegistry()
    # ``_reactive_rerun_hook_ids`` is keyed by ``id(hook)``; since we
    # re-register the *same* callable objects (not copies), the IDs
    # transfer 1:1.
    reactive_rerun_ids = base._reactive_rerun_hook_ids
    for event in _ALL_HOOK_EVENTS:
        for hook in base.get(event):
            cloned.register(
                event,
                hook,
                re_run_on_reactive_rebuild=id(hook) in reactive_rerun_ids,
            )
    return cloned
