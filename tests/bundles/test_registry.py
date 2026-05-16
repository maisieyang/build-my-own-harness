"""Tests for ``WhitelistRegistry`` — P5d-T3a.

Four surfaces:

1. **``get``** — whitelisted returns the tool;non-whitelisted raises
   ``KeyError``; unknown raises ``KeyError``.
2. **``list_tools``** — filtered subset, insertion order preserved.
3. **``to_api_schema``** — filtered subset (LLM's catalog view).
4. **Bootstrap warning** — whitelist entries not in base get a
   ``bundle_whitelist_unknown_tools`` log line.

The wrapper is also tested for subclass compatibility:an instance
``isinstance(wrap, ToolRegistry) is True`` so ``QueryContext.tool_registry:
ToolRegistry`` accepts it without engine changes.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest

from openharness.bundles.registry import WhitelistRegistry
from openharness.observability import configure_logging
from openharness.tools import Bash, Edit, Grep, Read, ToolRegistry, Write

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


@pytest.fixture
def base_registry() -> ToolRegistry:
    """Base registry with the 5 file/shell tools (no SpawnAgent, to keep
    test names short)."""
    r = ToolRegistry()
    r.register(Read())
    r.register(Write())
    r.register(Edit())
    r.register(Bash())
    r.register(Grep())
    return r


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


class TestGet:
    def test_returns_whitelisted_tool(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, {"Read", "Grep"})
        tool = wrap.get("Read")
        assert tool.name == "Read"

    def test_non_whitelisted_raises_keyerror(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, {"Read"})
        with pytest.raises(KeyError):
            wrap.get("Write")  # exists in base but not in whitelist

    def test_unknown_raises_keyerror(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, {"Read"})
        with pytest.raises(KeyError):
            wrap.get("Nonexistent")


# --------------------------------------------------------------------------- #
# list_tools                                                                  #
# --------------------------------------------------------------------------- #


class TestListTools:
    def test_filtered_subset(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, {"Read", "Grep"})
        names = [t.name for t in wrap.list_tools()]
        assert names == ["Read", "Grep"]

    def test_preserves_base_insertion_order(self, base_registry: ToolRegistry) -> None:
        # Whitelist in reverse-base order — output should still match
        # base's insertion order (Read→Write→Edit→Bash→Grep).
        wrap = WhitelistRegistry(base_registry, {"Grep", "Read", "Bash"})
        names = [t.name for t in wrap.list_tools()]
        assert names == ["Read", "Bash", "Grep"]

    def test_empty_whitelist_yields_empty_list(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, set())
        assert wrap.list_tools() == []


# --------------------------------------------------------------------------- #
# to_api_schema                                                               #
# --------------------------------------------------------------------------- #


class TestToApiSchema:
    def test_filtered_schema(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, {"Read", "Grep"})
        names = [s.name for s in wrap.to_api_schema()]
        assert names == ["Read", "Grep"]

    def test_empty_whitelist_yields_empty(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, set())
        assert wrap.to_api_schema() == []


# --------------------------------------------------------------------------- #
# unknown names warning                                                       #
# --------------------------------------------------------------------------- #


class TestUnknownNamesWarning:
    def test_unknown_names_emit_warning(
        self, base_registry: ToolRegistry, stream: io.StringIO
    ) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        WhitelistRegistry(base_registry, {"Read", "FakeTool", "OtherFake"})

        events = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        unknown_events = [e for e in events if e.get("event") == "bundle_whitelist_unknown_tools"]
        assert len(unknown_events) == 1
        # Sorted list of unknown names — deterministic for the log consumer.
        assert unknown_events[0]["names"] == ["FakeTool", "OtherFake"]

    def test_all_known_names_no_warning(
        self, base_registry: ToolRegistry, stream: io.StringIO
    ) -> None:
        configure_logging(level="INFO", format="json", stream=stream)
        WhitelistRegistry(base_registry, {"Read", "Grep"})

        events = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        unknown_events = [e for e in events if e.get("event") == "bundle_whitelist_unknown_tools"]
        assert unknown_events == []

    def test_unknown_dropped_from_effective_set(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, {"Read", "FakeTool"})
        names = [t.name for t in wrap.list_tools()]
        assert names == ["Read"]
        with pytest.raises(KeyError):
            wrap.get("FakeTool")


# --------------------------------------------------------------------------- #
# subclass compatibility (the whole reason for subclass)                      #
# --------------------------------------------------------------------------- #


class TestSubclassCompat:
    def test_is_instance_of_toolregistry(self, base_registry: ToolRegistry) -> None:
        # Engine type-checks tool_registry as ToolRegistry — wrapper must
        # be a true subclass so isinstance + mypy --strict accept it.
        wrap = WhitelistRegistry(base_registry, {"Read"})
        assert isinstance(wrap, ToolRegistry)

    def test_register_forbidden(self, base_registry: ToolRegistry) -> None:
        wrap = WhitelistRegistry(base_registry, {"Read"})
        with pytest.raises(RuntimeError, match="immutable"):
            wrap.register(Read())
