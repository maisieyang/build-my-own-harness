"""Plugin hook discovery — P5e-T1.

Per ``decisions/18-phase-5e-boundary.md``: third-party Python packages
ship hooks via the ``openharness.hooks`` entry-point group.

Plugin author UX (decisions/18 D20.2):

::

    # my_pkg/hooks.py
    from openharness.bundles import hook_spec

    @hook_spec("PostToolUse")
    async def slack_notify(context):
        # send Slack notification on tool dispatch complete
        ...

    # pyproject.toml
    [project.entry-points."openharness.hooks"]
    slack_notify = "my_pkg.hooks:slack_notify"

End-user invocation:

::

    oh ask --enable-plugin-hooks "/review last commit"
    # where review.md → mode: code-review, and code-review bundle has
    # hooks: [audit_log, slack_notify]

Discovery is **opt-in** via :attr:`Settings.enable_plugin_hooks` (D20.3).
When the flag is OFF, this module's :func:`discover_plugin_hooks` is
not called and bundle ``hook_names`` resolves only against
:data:`openharness.bundles.hooks.BUILTIN_HOOKS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from openharness.bundles.hooks import BUILTIN_HOOKS
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from openharness.hooks import Hook
    from openharness.hooks.events import HookEvent


class _EntryPointLike(Protocol):
    """Duck-typed shape of :class:`importlib.metadata.EntryPoint`.

    Production code receives real ``EntryPoint`` instances;tests
    inject ``_StubEntryPoint``. Both satisfy this Protocol structurally,
    so the discovery loop's attribute access(``.name`` / ``.value`` /
    ``.load()``)is type-safe under mypy --strict.
    """

    name: str
    value: str

    def load(self) -> object: ...


_logger = get_logger("bundles")

_DEFAULT_GROUP = "openharness.hooks"


@dataclass(frozen=True)
class HookSpec:
    """A discovered plugin hook entry — ``event`` + ``hook`` callable.

    Plugin authors construct via :func:`hook_spec` decorator;framework
    consumers (``discover_plugin_hooks``) read the fields. Frozen so
    a HookSpec can't be tampered with post-discovery.
    """

    event: HookEvent
    hook: Hook


def hook_spec(event: HookEvent) -> Callable[[Hook], HookSpec]:
    """Decorator wrapping a hook function in a :class:`HookSpec`.

    Plugin authors apply at the function definition site::

        @hook_spec("PostToolUse")
        async def my_audit(context):
            ...

    The decorated name becomes the entry-point attribute target;
    discovery loads the wrapped :class:`HookSpec` and registers it.
    """

    def decorator(fn: Hook) -> HookSpec:
        return HookSpec(event=event, hook=fn)

    return decorator


def discover_plugin_hooks(
    *,
    group: str = _DEFAULT_GROUP,
    entry_point_source: Callable[..., Iterable[_EntryPointLike]] | None = None,
) -> dict[str, HookSpec]:
    """Discover plugin hooks declared via the ``openharness.hooks``
    entry-point group.

    Returns ``{name: HookSpec}`` for every valid plugin entry. Per
    decisions/18 D20.4 collision policy:

    - Plugin name colliding with :data:`BUILTIN_HOOKS` → skipped +
      warning (framework hooks shadow plugins).
    - Plugin-plugin collision → first-wins (entry-point iteration
      order) + warning on subsequent.
    - Plugin load error / wrong type → skipped + warning. Bootstrap
      never raises — one bad plugin must not prevent others from
      loading (same skip-not-fail discipline as parse_command /
      parse_skill / parse_bundle).

    The ``entry_point_source`` keyword-only seam (D20.6) lets tests
    inject stub entry points without installing real packages. In
    production, the default :func:`importlib.metadata.entry_points`
    is used.

    A failure of ``entry_point_source`` itself (e.g. corrupted
    package metadata) is caught and returns ``{}`` + warning rather
    than propagating.
    """
    try:
        if entry_point_source is None:
            from importlib.metadata import entry_points as _entry_points

            # stdlib EntryPoint satisfies _EntryPointLike structurally
            # (matching name / value / load()) but mypy doesn't infer
            # the Protocol match automatically — cast keeps the
            # production codepath strictly typed without weakening the
            # test seam's signature.
            eps = cast("Iterable[_EntryPointLike]", _entry_points(group=group))
        else:
            eps = entry_point_source(group=group)
    except Exception as exc:
        _logger.warning(
            "plugin_hook_discovery_failed",
            group=group,
            error=str(exc),
        )
        return {}

    catalog: dict[str, HookSpec] = {}
    for ep in eps:
        if ep.name in BUILTIN_HOOKS:
            _logger.warning(
                "plugin_hook_collides_with_builtin",
                name=ep.name,
                value=ep.value,
            )
            continue
        if ep.name in catalog:
            _logger.warning(
                "plugin_hook_collision",
                name=ep.name,
                existing_value=ep.value,
            )
            continue
        try:
            loaded = ep.load()
        except Exception as exc:
            # ImportError, AttributeError, or anything else thrown by
            # the plugin's module-level code. Skip + warn.
            _logger.warning(
                "plugin_hook_load_failed",
                name=ep.name,
                value=ep.value,
                error=str(exc),
            )
            continue
        if not isinstance(loaded, HookSpec):
            _logger.warning(
                "plugin_hook_invalid_spec",
                name=ep.name,
                value=ep.value,
                got=type(loaded).__name__,
            )
            continue
        catalog[ep.name] = loaded

    return catalog
