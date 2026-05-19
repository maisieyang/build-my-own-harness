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
    from pathlib import Path

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


# --------------------------------------------------------------------------- #
# P5f-T1: discover_filesystem_hook_plugins                                    #
# --------------------------------------------------------------------------- #


class _StubModule:
    """Bare object playing the role of a loaded Python module.
    Attributes are inspected via ``vars()`` / ``getattr()`` exactly
    like a real module."""

    def __init__(self, **attrs: object) -> None:
        for name, value in attrs.items():
            setattr(self, name, value)


def _stub_loader_factory(
    mapping: dict[str, _StubModule | None],
) -> Callable[[Path], object | None]:
    """Return a callable that maps Path → stub module via filename.

    ``mapping`` keys are file basenames (e.g. ``"my_plugin.py"``);
    values are the module to return (or None to simulate load failure).
    """

    def _loader(path: Path) -> object | None:
        return mapping.get(path.name)

    return _loader


class TestDiscoverFilesystemHookPlugins:
    """P5f-T1: filesystem hook plugin discovery — ``*.py`` files in
    global + project layers."""

    def test_empty_dirs_returns_empty(self) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        catalog = discover_filesystem_hook_plugins()
        assert catalog == {}

    def test_single_plugin_in_global(self, tmp_path: Path) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "slack.py").write_text("# stub\n")
        spec = HookSpec(event="PostToolUse", hook=_dummy_hook)
        loader = _stub_loader_factory({"slack.py": _StubModule(slack_notify=spec)})

        catalog = discover_filesystem_hook_plugins(global_dir=global_dir, module_loader=loader)
        assert catalog == {"slack_notify": spec}

    def test_multiple_specs_in_one_file(self, tmp_path: Path) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        d = tmp_path / "global"
        d.mkdir()
        (d / "hooks.py").write_text("# stub\n")
        a = HookSpec(event="PostToolUse", hook=_dummy_hook)
        b = HookSpec(event="PreToolUse", hook=_other_hook)
        loader = _stub_loader_factory({"hooks.py": _StubModule(audit=a, deny=b)})

        catalog = discover_filesystem_hook_plugins(global_dir=d, module_loader=loader)
        assert set(catalog.keys()) == {"audit", "deny"}

    def test_module_load_failure_skipped(self, tmp_path: Path, stream: io.StringIO) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        configure_logging(level="INFO", format="json", stream=stream)

        d = tmp_path / "global"
        d.mkdir()
        (d / "good.py").write_text("# stub\n")
        (d / "bad.py").write_text("# stub\n")
        good_spec = HookSpec(event="PostToolUse", hook=_dummy_hook)
        # bad.py returns None from loader → simulates import error
        loader = _stub_loader_factory({"good.py": _StubModule(g=good_spec), "bad.py": None})

        catalog = discover_filesystem_hook_plugins(global_dir=d, module_loader=loader)
        assert catalog == {"g": good_spec}

    def test_module_with_no_specs_silently_skipped(
        self, tmp_path: Path, stream: io.StringIO
    ) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        configure_logging(level="INFO", format="json", stream=stream)

        d = tmp_path / "global"
        d.mkdir()
        (d / "empty.py").write_text("# stub\n")
        loader = _stub_loader_factory(
            {"empty.py": _StubModule(unrelated_var=42, some_func=lambda: None)}
        )

        catalog = discover_filesystem_hook_plugins(global_dir=d, module_loader=loader)
        assert catalog == {}

    def test_builtin_collision_skipped(self, tmp_path: Path, stream: io.StringIO) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        configure_logging(level="INFO", format="json", stream=stream)

        d = tmp_path / "global"
        d.mkdir()
        (d / "rogue.py").write_text("# stub\n")
        # Plugin attempts to register as `audit_log` (a built-in).
        rogue = HookSpec(event="PreToolUse", hook=_dummy_hook)
        loader = _stub_loader_factory({"rogue.py": _StubModule(audit_log=rogue)})

        catalog = discover_filesystem_hook_plugins(global_dir=d, module_loader=loader)
        assert catalog == {}
        events = _events(stream)
        assert any(e.get("event") == "filesystem_hook_collides_with_builtin" for e in events)

    def test_same_layer_collision_first_wins(self, tmp_path: Path, stream: io.StringIO) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        configure_logging(level="INFO", format="json", stream=stream)

        d = tmp_path / "global"
        d.mkdir()
        # Two files in same layer both expose ``shared``.
        (d / "a.py").write_text("# stub\n")
        (d / "b.py").write_text("# stub\n")
        spec_a = HookSpec(event="PostToolUse", hook=_dummy_hook)
        spec_b = HookSpec(event="PreToolUse", hook=_other_hook)
        loader = _stub_loader_factory(
            {"a.py": _StubModule(shared=spec_a), "b.py": _StubModule(shared=spec_b)}
        )

        catalog = discover_filesystem_hook_plugins(global_dir=d, module_loader=loader)
        # Sorted glob: a.py comes first → wins.
        assert catalog["shared"] is spec_a
        events = _events(stream)
        assert any(e.get("event") == "filesystem_hook_collision" for e in events)

    def test_project_overrides_global(self, tmp_path: Path, stream: io.StringIO) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        configure_logging(level="INFO", format="json", stream=stream)

        global_dir = tmp_path / "global"
        global_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (global_dir / "h.py").write_text("# stub\n")
        (project_dir / "h.py").write_text("# stub\n")

        global_spec = HookSpec(event="PostToolUse", hook=_dummy_hook)
        project_spec = HookSpec(event="PreToolUse", hook=_other_hook)

        # Discriminate by IMMEDIATE parent dir (not full path string)
        # because pytest's tmp_path injects the test method name
        # into the path — e.g. ``test_project_overrides_global0``
        # contains "global" and would match both paths if we string-
        # searched the full path. ``path.parent.name`` is the
        # ``global``/``project`` dir we actually created.
        def loader(path: Path) -> object | None:
            if path.parent.name == "global":
                return _StubModule(shared=global_spec)
            return _StubModule(shared=project_spec)

        catalog = discover_filesystem_hook_plugins(
            global_dir=global_dir,
            project_dir=project_dir,
            module_loader=loader,
        )
        # Project wins.
        assert catalog["shared"] is project_spec
        events = _events(stream)
        assert any(e.get("event") == "filesystem_hook_override" for e in events)

    def test_non_py_files_ignored(self, tmp_path: Path) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        d = tmp_path / "global"
        d.mkdir()
        (d / "hook.py").write_text("# stub\n")
        (d / "README.md").write_text("docs\n")
        (d / "config.json").write_text("{}\n")

        spec = HookSpec(event="PostToolUse", hook=_dummy_hook)
        loader = _stub_loader_factory({"hook.py": _StubModule(h=spec)})

        catalog = discover_filesystem_hook_plugins(global_dir=d, module_loader=loader)
        assert set(catalog.keys()) == {"h"}

    def test_missing_dir_skipped(self, tmp_path: Path) -> None:
        # Non-existent dir → skip-not-fail, empty catalog.
        from openharness.bundles import discover_filesystem_hook_plugins

        catalog = discover_filesystem_hook_plugins(
            global_dir=tmp_path / "does_not_exist",
        )
        assert catalog == {}

    def test_default_loader_loads_real_file(self, tmp_path: Path) -> None:
        # End-to-end: write an actual .py file, default loader imports it.
        from openharness.bundles import discover_filesystem_hook_plugins

        d = tmp_path / "global"
        d.mkdir()
        (d / "real.py").write_text(
            "from openharness.bundles import hook_spec\n"
            "@hook_spec('PostToolUse')\n"
            "async def my_real_hook(ctx):\n"
            "    return None\n",
            encoding="utf-8",
        )

        catalog = discover_filesystem_hook_plugins(global_dir=d)
        assert "my_real_hook" in catalog
        assert catalog["my_real_hook"].event == "PostToolUse"

    def test_default_loader_syntax_error_skipped(self, tmp_path: Path, stream: io.StringIO) -> None:
        from openharness.bundles import discover_filesystem_hook_plugins

        configure_logging(level="INFO", format="json", stream=stream)

        d = tmp_path / "global"
        d.mkdir()
        (d / "broken.py").write_text("def syntax error :::\n", encoding="utf-8")

        catalog = discover_filesystem_hook_plugins(global_dir=d)
        assert catalog == {}
        events = _events(stream)
        assert any(e.get("event") == "filesystem_hook_load_failed" for e in events)
