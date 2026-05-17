"""Tests for ``HookSpec`` + ``hook_spec`` + ``discover_plugin_hooks`` — P5e-T1.

Three surfaces:

1. **HookSpec dataclass** — frozen + happy construction.
2. **``hook_spec`` decorator** — wraps function in ``HookSpec``;
   preserves the original callable identity in the ``hook`` field.
3. **``discover_plugin_hooks``** — happy multi-plugin discovery +
   collision policy (built-in shadow, plugin-plugin first-wins) +
   skip-not-fail on load/type errors + outer ``entry_point_source``
   failure.

The ``entry_point_source`` test seam (D20.6) is the load-bearing
abstraction here:tests inject stub EntryPoints without ever needing
to ``pip install`` a real plugin package.
"""

from __future__ import annotations

import dataclasses
import io
import json
import logging
from typing import TYPE_CHECKING, Any

import pytest

from openharness.bundles.hook_plugins import (
    HookSpec,
    discover_plugin_hooks,
    hook_spec,
)
from openharness.observability import configure_logging

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from openharness.hooks.result import HookResult


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


# Test helpers — minimal hook callables that satisfy the Hook contract.
async def _dummy_hook(context: object) -> HookResult | None:
    del context
    return None


async def _other_hook(context: object) -> HookResult | None:
    del context
    return None


class _StubEntryPoint:
    """Minimal stand-in for :class:`importlib.metadata.EntryPoint`.

    The discovery code only touches ``name`` / ``value`` / ``load()``,
    so we don't need to inherit from the real class. Duck-typed.
    """

    def __init__(self, name: str, value: str, loaded: object | Exception) -> None:
        self.name = name
        self.value = value
        self._loaded = loaded

    def load(self) -> object:
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def _make_source(
    entry_points: list[_StubEntryPoint],
) -> Callable[..., list[_StubEntryPoint]]:
    """Return a callable that mimics ``importlib.metadata.entry_points``."""

    def _source(*, group: str) -> list[_StubEntryPoint]:
        del group
        return entry_points

    return _source


# --------------------------------------------------------------------------- #
# HookSpec dataclass                                                          #
# --------------------------------------------------------------------------- #


class TestHookSpec:
    def test_construction(self) -> None:
        spec = HookSpec(event="PostToolUse", hook=_dummy_hook)
        assert spec.event == "PostToolUse"
        assert spec.hook is _dummy_hook

    def test_is_frozen(self) -> None:
        spec = HookSpec(event="PreToolUse", hook=_dummy_hook)
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.event = "PostToolUse"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# hook_spec decorator                                                         #
# --------------------------------------------------------------------------- #


class TestHookSpecDecorator:
    def test_wraps_function(self) -> None:
        @hook_spec("PreToolUse")
        async def my_hook(context: object) -> HookResult | None:
            del context
            return None

        assert isinstance(my_hook, HookSpec)
        assert my_hook.event == "PreToolUse"
        # The decorator preserves the original callable identity via
        # the .hook field. ``my_hook`` IS the HookSpec, not the
        # original function — but ``my_hook.hook`` is the function.
        assert callable(my_hook.hook)

    def test_multiple_event_types(self) -> None:
        @hook_spec("PostToolUse")
        async def post_h(context: object) -> HookResult | None:
            del context
            return None

        @hook_spec("OnError")
        async def err_h(context: object) -> HookResult | None:
            del context
            return None

        assert post_h.event == "PostToolUse"
        assert err_h.event == "OnError"


# --------------------------------------------------------------------------- #
# discover_plugin_hooks — happy path                                          #
# --------------------------------------------------------------------------- #


class TestDiscoverHappy:
    def test_empty_source_returns_empty(self) -> None:
        catalog = discover_plugin_hooks(entry_point_source=_make_source([]))
        assert catalog == {}

    def test_two_valid_plugins(self) -> None:
        spec_a = HookSpec(event="PostToolUse", hook=_dummy_hook)
        spec_b = HookSpec(event="PreToolUse", hook=_other_hook)
        source = _make_source(
            [
                _StubEntryPoint("plugin_a", "pkg.mod:spec_a", spec_a),
                _StubEntryPoint("plugin_b", "pkg.mod:spec_b", spec_b),
            ]
        )

        catalog = discover_plugin_hooks(entry_point_source=source)
        assert set(catalog.keys()) == {"plugin_a", "plugin_b"}
        assert catalog["plugin_a"] is spec_a
        assert catalog["plugin_b"] is spec_b


# --------------------------------------------------------------------------- #
# discover_plugin_hooks — skip-not-fail                                       #
# --------------------------------------------------------------------------- #


class TestDiscoverSkipNotFail:
    def test_load_raises_skipped(self, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        spec_ok = HookSpec(event="PostToolUse", hook=_dummy_hook)
        source = _make_source(
            [
                _StubEntryPoint(
                    "broken_plugin",
                    "broken_pkg.mod:nonexistent",
                    ImportError("module not found"),
                ),
                _StubEntryPoint("good_plugin", "good_pkg.mod:spec", spec_ok),
            ]
        )

        catalog = discover_plugin_hooks(entry_point_source=source)
        # Bad plugin skipped; good plugin still loaded.
        assert set(catalog.keys()) == {"good_plugin"}
        # Warning emitted for the bad one.
        events = _events(stream)
        load_failed = [e for e in events if e.get("event") == "plugin_hook_load_failed"]
        assert len(load_failed) == 1
        assert load_failed[0]["name"] == "broken_plugin"

    def test_wrong_type_skipped(self, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        # Entry point loads but value isn't a HookSpec.
        source = _make_source([_StubEntryPoint("not_a_spec", "pkg.mod:value", "just a string")])

        catalog = discover_plugin_hooks(entry_point_source=source)
        assert catalog == {}
        events = _events(stream)
        invalid = [e for e in events if e.get("event") == "plugin_hook_invalid_spec"]
        assert len(invalid) == 1
        assert invalid[0]["got"] == "str"

    def test_builtin_collision_skipped(self, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        # Plugin tries to register under the name `audit_log` (built-in).
        rogue = HookSpec(event="PostToolUse", hook=_dummy_hook)
        source = _make_source([_StubEntryPoint("audit_log", "rogue_pkg.mod:spec", rogue)])

        catalog = discover_plugin_hooks(entry_point_source=source)
        # audit_log is built-in — plugin shadowed.
        assert catalog == {}
        events = _events(stream)
        collisions = [e for e in events if e.get("event") == "plugin_hook_collides_with_builtin"]
        assert len(collisions) == 1
        assert collisions[0]["name"] == "audit_log"

    def test_plugin_collision_first_wins(self, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        first_spec = HookSpec(event="PostToolUse", hook=_dummy_hook)
        second_spec = HookSpec(event="PreToolUse", hook=_other_hook)
        source = _make_source(
            [
                _StubEntryPoint("dup_name", "pkg_a:spec", first_spec),
                _StubEntryPoint("dup_name", "pkg_b:spec", second_spec),
            ]
        )

        catalog = discover_plugin_hooks(entry_point_source=source)
        # First-wins.
        assert catalog["dup_name"] is first_spec
        events = _events(stream)
        collisions = [e for e in events if e.get("event") == "plugin_hook_collision"]
        assert len(collisions) == 1
        assert collisions[0]["name"] == "dup_name"

    def test_outer_source_failure_returns_empty(self, stream: io.StringIO) -> None:
        configure_logging(level="INFO", format="json", stream=stream)

        def _failing_source(*, group: str) -> list[Any]:
            del group
            raise RuntimeError("metadata corrupted")

        catalog = discover_plugin_hooks(entry_point_source=_failing_source)
        assert catalog == {}
        events = _events(stream)
        failed = [e for e in events if e.get("event") == "plugin_hook_discovery_failed"]
        assert len(failed) == 1


# --------------------------------------------------------------------------- #
# Default group / production codepath sanity                                  #
# --------------------------------------------------------------------------- #


class TestDefaultGroup:
    def test_default_group_returns_iterable(self) -> None:
        # Production code path: no entry_point_source override. Uses
        # importlib.metadata.entry_points which always returns
        # iterable (possibly empty). No installed packages in this
        # test env declare the group, so result is empty — but the
        # call shouldn't raise.
        catalog = discover_plugin_hooks()
        assert isinstance(catalog, dict)


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
