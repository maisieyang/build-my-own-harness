"""Tests for ``apply_bundle_to_context`` — P5d-T3b.

Five categories:

1. **Empty bundle** (only name + description) → all 4 outputs equal
   inputs (modulo HookRegistry being cloned, not aliased).
2. **system_prompt-only bundle** → only Layer 1 changes.
3. **whitelist-only bundle** → only Layer 2 changes; wrapper is a
   ``WhitelistRegistry`` instance.
4. **deny_paths-only bundle** → Settings.deny_paths AUGMENTED.
5. **hook_names-only bundle** → bundle's hooks registered into cloned
   chain; base chain unmodified.
6. **Full 4-layer bundle** → all 4 outputs reflect bundle.
7. **Hook resolution**: unknown name raises UnknownBundleError.
8. **Clone semantics**: registering on the result doesn't bleed into
   the base.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openharness.bundles.apply import apply_bundle_to_context
from openharness.bundles.errors import UnknownBundleError
from openharness.bundles.hooks import audit_log, deny_writes
from openharness.bundles.model import Bundle
from openharness.bundles.registry import WhitelistRegistry
from openharness.config.settings import Settings
from openharness.hooks import HookRegistry
from openharness.tools import Read, ToolRegistry, Write


def _make_bundle(
    *,
    name: str = "test",
    description: str = "test bundle",
    system_prompt: str | None = None,
    tools_whitelist: tuple[str, ...] | None = None,
    deny_paths: tuple[str, ...] = (),
    hook_names: tuple[str, ...] = (),
) -> Bundle:
    return Bundle(
        name=name,
        description=description,
        system_prompt=system_prompt,
        tools_whitelist=tools_whitelist,
        deny_paths=deny_paths,
        hook_names=hook_names,
        source_path=Path(f"/fake/{name}.md"),
    )


def _base_inputs() -> tuple[ToolRegistry, HookRegistry, Settings, str]:
    """Construct the 4 base inputs every test wants."""
    tr = ToolRegistry()
    tr.register(Read())
    tr.register(Write())
    hr = HookRegistry()
    settings = Settings(deny_paths=("base/**",))
    prompt = "BASE PROMPT"
    return tr, hr, settings, prompt


# --------------------------------------------------------------------------- #
# Empty bundle                                                                #
# --------------------------------------------------------------------------- #


class TestEmptyBundle:
    def test_passthrough(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle()  # no overrides

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        assert result.tool_registry is tr  # base unchanged
        assert result.settings is settings
        assert result.system_prompt == prompt
        # hook_registry is cloned, so NOT identity-equal — but empty.
        assert result.hook_registry is not hr
        assert result.hook_registry.is_empty()


# --------------------------------------------------------------------------- #
# Layer 1 — system_prompt only                                                #
# --------------------------------------------------------------------------- #


class TestSystemPromptOnly:
    def test_replaces_prompt(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(system_prompt="BUNDLE PROMPT")

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        assert result.system_prompt == "BUNDLE PROMPT"
        # Other layers untouched.
        assert result.tool_registry is tr
        assert result.settings is settings


# --------------------------------------------------------------------------- #
# Layer 2 — whitelist                                                         #
# --------------------------------------------------------------------------- #


class TestWhitelist:
    def test_wraps_in_whitelist_registry(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(tools_whitelist=("Read",))

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        assert isinstance(result.tool_registry, WhitelistRegistry)
        names = [t.name for t in result.tool_registry.list_tools()]
        assert names == ["Read"]

    def test_none_whitelist_passes_base_through(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(tools_whitelist=None)

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        # Identity check: bundle didn't ask for filtering.
        assert result.tool_registry is tr


# --------------------------------------------------------------------------- #
# Layer 3a — deny_paths                                                       #
# --------------------------------------------------------------------------- #


class TestDenyPaths:
    def test_augments_settings_deny_paths(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(deny_paths=("secrets/**", "*.env"))

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        # Base "base/**" still present + bundle's two patterns added.
        assert result.settings.deny_paths == ("base/**", "secrets/**", "*.env")
        # Original settings instance unchanged (model_copy returns new).
        assert settings.deny_paths == ("base/**",)

    def test_empty_deny_paths_settings_unchanged(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(deny_paths=())

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        # Identity check: no model_copy when bundle has no extra paths.
        assert result.settings is settings


# --------------------------------------------------------------------------- #
# Layer 3b — hook_names                                                       #
# --------------------------------------------------------------------------- #


class TestHookNames:
    def test_registers_bundle_hooks(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(hook_names=("audit_log", "deny_writes"))

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        post = result.hook_registry.get("PostToolUse")
        pre = result.hook_registry.get("PreToolUse")
        assert audit_log in post
        assert deny_writes in pre

    def test_base_hooks_preserved_in_clone(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        # Register a user hook on the base.
        user_hook = AsyncMock()
        hr.register("PreToolUse", user_hook)
        bundle = _make_bundle(hook_names=("deny_writes",))

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        pre = result.hook_registry.get("PreToolUse")
        # User hook first, then bundle hook (registration order = execution
        # order per P3-T4 D8.J).
        assert pre == [user_hook, deny_writes]

    def test_clone_doesnt_bleed_into_base(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(hook_names=("audit_log",))

        apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        # Base HookRegistry didn't gain audit_log.
        assert hr.is_empty()

    def test_unknown_hook_raises(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(hook_names=("nonexistent_hook",))

        with pytest.raises(UnknownBundleError) as exc_info:
            apply_bundle_to_context(
                bundle=bundle,
                tool_registry=tr,
                hook_registry=hr,
                settings=settings,
                system_prompt=prompt,
            )
        assert exc_info.value.kind == "hook"
        assert exc_info.value.name == "nonexistent_hook"


# --------------------------------------------------------------------------- #
# Full 4-layer bundle                                                         #
# --------------------------------------------------------------------------- #


class TestFullBundle:
    def test_all_four_layers_applied(self) -> None:
        tr, hr, settings, prompt = _base_inputs()
        bundle = _make_bundle(
            system_prompt="REVIEW MODE",
            tools_whitelist=("Read",),
            deny_paths=("secrets/**",),
            hook_names=("audit_log", "deny_writes"),
        )

        result = apply_bundle_to_context(
            bundle=bundle,
            tool_registry=tr,
            hook_registry=hr,
            settings=settings,
            system_prompt=prompt,
        )
        # Layer 1
        assert result.system_prompt == "REVIEW MODE"
        # Layer 2
        assert isinstance(result.tool_registry, WhitelistRegistry)
        assert [t.name for t in result.tool_registry.list_tools()] == ["Read"]
        # Layer 3a
        assert result.settings.deny_paths == ("base/**", "secrets/**")
        # Layer 3b
        assert audit_log in result.hook_registry.get("PostToolUse")
        assert deny_writes in result.hook_registry.get("PreToolUse")
