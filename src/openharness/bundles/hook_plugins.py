"""Plugin hook discovery — P5e (entry points) + P5f (filesystem `*.py`).

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
    from pathlib import Path

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


def _default_module_loader(path: Path) -> object | None:
    """Default loader for filesystem hook plugins — P5f-T1.

    Imports ``path`` as a uniquely-named module to avoid namespace
    clashes between global/project versions of the same file name.
    Module name = ``openharness._user_hook_<sha8>_<stem>`` where
    sha8 is the first 8 hex chars of SHA-256 of the absolute path.

    Returns the loaded module object, or ``None`` if any error
    occurs during import (warning logged). Same skip-not-fail
    discipline as the entry-point loader.

    The module is NOT registered into ``sys.modules`` permanently —
    only stashed there during ``exec_module`` so the module's own
    ``from foo import bar`` statements can resolve circular refs,
    then popped before returning.
    """
    import hashlib
    import importlib.util
    import sys

    try:
        absolute = path.resolve()
    except OSError as exc:
        _logger.warning(
            "filesystem_hook_path_failed",
            path=str(path),
            error=str(exc),
        )
        return None

    sha8 = hashlib.sha256(str(absolute).encode()).hexdigest()[:8]
    module_name = f"openharness._user_hook_{sha8}_{path.stem}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, absolute)
    except (OSError, ValueError) as exc:
        _logger.warning(
            "filesystem_hook_spec_failed",
            path=str(path),
            error=str(exc),
        )
        return None
    if spec is None or spec.loader is None:
        _logger.warning(
            "filesystem_hook_spec_invalid",
            path=str(path),
        )
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # temporary, for circular-import
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        # Any error during module-level code execution: syntax error,
        # ImportError on a missing dependency, RuntimeError thrown from
        # module-level statement. Skip + warn.
        _logger.warning(
            "filesystem_hook_load_failed",
            path=str(path),
            error=str(exc),
        )
        sys.modules.pop(module_name, None)
        return None
    finally:
        # Even on success, remove from sys.modules so subsequent
        # imports of unrelated code aren't polluted with our temp
        # module name.
        sys.modules.pop(module_name, None)
    return module


def discover_filesystem_hook_plugins(
    *,
    global_dir: Path | None = None,
    project_dir: Path | None = None,
    module_loader: Callable[[Path], object | None] | None = None,
) -> dict[str, HookSpec]:
    """Discover plugin hooks dropped under ``~/.openharness/hooks/*.py``
    (global) + ``<cwd>/.openharness/hooks/*.py`` (project) — P5f-T1.

    Per decisions/20 D22.1 / D22.3:each ``.py`` file is imported and
    scanned for module-level :class:`HookSpec` attributes (the
    decorated names from ``@hook_spec``). The plugin's name == the
    attribute name on the imported module (file name is irrelevant —
    one file can export multiple hooks).

    Collision rules (D22.3, mirrors entry-point discovery):

    - Plugin name collides with :data:`BUILTIN_HOOKS` →
      ``filesystem_hook_collides_with_builtin`` warning, skipped.
    - Same-layer same-name collision (two files in the same directory
      both expose ``HookSpec`` with the same name) →
      ``filesystem_hook_collision`` warning, first-wins.
    - Project overrides global on same name →
      ``filesystem_hook_override`` info logged.

    Module load failure (syntax error, ImportError, etc.) →
    ``filesystem_hook_load_failed`` warning, skip.Module with no
    ``HookSpec`` attributes is silently skipped (empty hook file is
    benign).

    The ``module_loader`` test seam (D22.6) lets tests inject stub
    modules without filesystem I/O. Production defaults to
    :func:`_default_module_loader`.
    """
    if module_loader is None:
        module_loader = _default_module_loader

    catalog: dict[str, HookSpec] = {}
    if global_dir is not None:
        _scan_filesystem_dir(catalog, global_dir, layer="global", module_loader=module_loader)
    if project_dir is not None:
        _scan_filesystem_dir(catalog, project_dir, layer="project", module_loader=module_loader)
    return catalog


def _scan_filesystem_dir(
    catalog: dict[str, HookSpec],
    directory: Path,
    *,
    layer: str,
    module_loader: Callable[[Path], object | None],
) -> None:
    """Scan ``directory`` for ``*.py`` files, load each, merge specs
    into ``catalog`` honoring collision policy."""
    if not directory.exists() or not directory.is_dir():
        return
    for path in sorted(directory.glob("*.py")):
        module = module_loader(path)
        if module is None:
            continue  # warning already logged inside loader
        # Walk module attributes; pick up every HookSpec instance.
        # Sorted attribute names = deterministic ordering for tests
        # and reproducible discovery across machines.
        specs = [
            (attr, getattr(module, attr))
            for attr in sorted(vars(module).keys())
            if isinstance(getattr(module, attr, None), HookSpec)
        ]
        if not specs:
            # Empty hook file is benign — file imported cleanly but
            # exported no HookSpec. Skip silently.
            continue
        for name, spec in specs:
            if name in BUILTIN_HOOKS:
                _logger.warning(
                    "filesystem_hook_collides_with_builtin",
                    name=name,
                    path=str(path),
                )
                continue
            if name in catalog:
                if layer == "project":
                    _logger.info(
                        "filesystem_hook_override",
                        name=name,
                        new_path=str(path),
                    )
                    catalog[name] = spec
                else:
                    _logger.warning(
                        "filesystem_hook_collision",
                        name=name,
                        skipped_path=str(path),
                    )
                continue
            catalog[name] = spec
