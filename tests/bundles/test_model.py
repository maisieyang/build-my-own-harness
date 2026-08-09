"""Tests for the canonical three-layer bundle shape."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from openharness.bundles.model import Bundle, parse_bundle

if TYPE_CHECKING:
    from pathlib import Path


def _bundle(tmp_path: Path, *, name: str = "x", description: str = "d") -> Bundle:
    return Bundle(
        name=name,
        description=description,
        system_prompt=None,
        tools_whitelist=None,
        hook_names=(),
        source_path=tmp_path / "x.md",
    )


def test_bundle_is_frozen_and_validates_identity(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.name = "y"  # type: ignore[misc]
    with pytest.raises(ValueError, match="invalid bundle name"):
        _bundle(tmp_path, name="1bad")
    with pytest.raises(ValueError, match="description must be non-empty"):
        _bundle(tmp_path, description=" ")


def test_parse_full_canonical_bundle(tmp_path: Path) -> None:
    path = tmp_path / "review.md"
    path.write_text(
        "---\nname: review\ndescription: Review\nsystem_prompt: review carefully\n"
        "tools:\n  whitelist: [Read, Grep]\nhooks: [audit_log]\n---\n"
    )
    bundle = parse_bundle(path)
    assert bundle is not None
    assert bundle.system_prompt == "review carefully"
    assert bundle.tools_whitelist == ("Read", "Grep")
    assert bundle.hook_names == ("audit_log",)


def test_parse_bundle_with_empty_tools_block_keeps_full_catalog(tmp_path: Path) -> None:
    path = tmp_path / "default.md"
    path.write_text("---\nname: default\ndescription: Default catalog\ntools: {}\n---\n")

    bundle = parse_bundle(path)

    assert bundle is not None
    assert bundle.tools_whitelist is None


@pytest.mark.parametrize(
    "content",
    [
        "plain markdown",
        "---\nname: x\n---\n",
        "---\ndescription: d\n---\n",
        "---\nname: x\ndescription: d\nsystem_prompt: [invalid]\n---\n",
        "---\nname: x\ndescription: d\ntools: invalid\n---\n",
        "---\nname: x\ndescription: d\ntools:\n  whitelist: [Read, 1]\n---\n",
        "---\nname: x\ndescription: d\nhooks: invalid\n---\n",
        "---\nname: 'bad name'\ndescription: d\n---\n",
    ],
)
def test_malformed_bundle_is_skipped(tmp_path: Path, content: str) -> None:
    path = tmp_path / "x.md"
    path.write_text(content)
    assert parse_bundle(path) is None


def test_legacy_deny_paths_is_rejected_not_silently_ignored(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "x.md"
    path.write_text("---\nname: x\ndescription: d\ndeny_paths: [secrets/**]\n---\n")
    assert parse_bundle(path) is None
    captured = capsys.readouterr()
    assert "bundle_legacy_deny_paths" in caplog.text + captured.out + captured.err
