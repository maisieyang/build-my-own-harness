"""Deterministic tests for the context-management dogfood inspector."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from dogfood.context_inspector import (
    FIXTURE_SOURCE,
    build_large_probe_text,
    collect_context_artifact,
    prepare_context_fixture,
    render_text_report,
)

from openharness.config import Settings
from openharness.services.snapshot import get_snapshot_dir

if TYPE_CHECKING:
    from pathlib import Path


def _write_markdown_entry(path: Path, *, name: str, description: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _settings() -> Settings:
    return Settings(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="qwen3.7-max",
        enable_plugins=False,
        enable_memory=True,
    )


def test_collect_context_artifact_reports_discovery_and_persisted_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cwd = tmp_path / "project"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("project rule\n", encoding="utf-8")
    _write_markdown_entry(
        cwd / ".openharness" / "skills" / "probe-skill.md",
        name="probe-skill",
        description="context probe",
        body="PROBE_SKILL_BODY",
    )
    _write_markdown_entry(
        cwd / ".openharness" / "commands" / "probe-command.md",
        name="probe-command",
        description="context command",
        body="Inspect {args}",
    )
    plugin = home / ".openharness" / "plugins" / "installed-only"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "installed-only", "version": "0.1.0", "description": "fixture"}),
        encoding="utf-8",
    )

    snapshot_path = get_snapshot_dir(cwd) / "current.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "version": 2,
                "schema": "openharness.snapshot.v2",
                "created_at": "2026-08-12T00:00:00+00:00",
                "git_head": None,
                "cwd": str(cwd.resolve()),
                "model": "qwen3.7-max",
                "permission_profile_fingerprint": "fixture",
                "system_prompt": (
                    "base\n\n## Tools\n\n"
                    "- **Read** -- read files\n"
                    "- **LoadSkill** -- load skill\n\n"
                    "## Available Skills (call LoadSkill to expand)\n\n"
                    "- **probe-skill** -- context probe\n\n"
                    "## Environment\n\n- cwd: fixture\n\n"
                    "## Session goal\n\nfinish the probe"
                ),
                "max_tokens": 8192,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "start"}]},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "Read",
                                "input": {"path": "probe.txt"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "content": "file not found",
                                "is_error": True,
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "[goal-status] set: finish the probe",
                            }
                        ],
                    },
                ],
                "tool_metadata": {"recent_files": ["probe.txt"], "verified_work": []},
                "extra": {"permission_runtime": {"parked_request": {"request_id": "p1"}}},
            }
        ),
        encoding="utf-8",
    )

    artifact = collect_context_artifact(cwd, settings=_settings())

    assert artifact["schema"] == "openharness.dogfood.context-artifact.v3"
    assert artifact["configuration"]["model"] == "qwen3.7-max"
    assert artifact["configuration"]["context_window"] == 262_144
    assert artifact["configuration"]["compact_threshold_tokens"] == 217_579
    assert artifact["discovery"]["project_instruction_files"] == [str(cwd / "AGENTS.md")]
    assert artifact["discovery"]["commands"] == ["probe-command"]
    assert artifact["discovery"]["skills"] == ["probe-skill"]
    assert artifact["discovery"]["installed_plugins"][0]["name"] == "installed-only"
    assert artifact["discovery"]["installed_plugins"][0]["loaded"] is False
    assert artifact["snapshot"]["message_count"] == 4
    assert artifact["snapshot"]["roles"] == {"assistant": 1, "user": 3}
    assert artifact["snapshot"]["blocks"] == {"text": 2, "tool_result": 1, "tool_use": 1}
    assert artifact["snapshot"]["tool_uses"] == {"Read": 1}
    assert artifact["snapshot"]["tool_result_count"] == 1
    assert artifact["snapshot"]["error_tool_result_count"] == 1
    assert artifact["snapshot"]["synthetic_skill_load_count"] == 0
    assert artifact["snapshot"]["context_markers"] == {
        "compact_boundary": 0,
        "full_summary": 0,
    }
    assert artifact["snapshot"]["tool_result_markers"] == {
        "collapsed_body": 0,
        "native_char_truncation": 0,
        "post_tool_token_truncation": 0,
    }
    assert artifact["snapshot"]["assistant_marker_mentions"] == {
        "collapsed_body": 0,
        "native_char_truncation": 0,
        "post_tool_token_truncation": 0,
    }
    assert artifact["snapshot"]["active_goal"] == "finish the probe"
    assert artifact["snapshot"]["permission_runtime"]["parked_request_present"] is True
    assert artifact["snapshot"]["stored_context"]["tools"] == ["Read", "LoadSkill"]
    assert artifact["snapshot"]["stored_context"]["skills"] == ["probe-skill"]
    assert artifact["snapshot"]["stored_context"]["headings"][-1] == "## Session goal"
    assert artifact["snapshot"]["estimated_message_tokens"] > 0

    report = render_text_report(artifact)
    assert "qwen3.7-max" in report
    assert "Read, LoadSkill" in report
    assert "finish the probe" in report
    assert "parked=yes" in report


def test_marker_counts_separate_tool_results_from_assistant_mentions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cwd = tmp_path / "project"
    cwd.mkdir()
    snapshot_path = get_snapshot_dir(cwd) / "current.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "version": 2,
                "schema": "openharness.snapshot.v2",
                "created_at": "2026-08-12T00:00:00+00:00",
                "git_head": None,
                "cwd": str(cwd.resolve()),
                "model": "qwen3.7-max",
                "permission_profile_fingerprint": "fixture",
                "system_prompt": "base",
                "max_tokens": 8192,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "bash-1",
                                "content": "HEAD ... [truncated 153694 chars] ... TAIL",
                                "is_error": False,
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "read-1",
                                "content": "HEAD ... [truncated 31431 tokens] ... TAIL",
                                "is_error": False,
                            }
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "我观察到 [truncated 153694 chars],也引用了 "
                                    "[truncated 31431 tokens]。"
                                ),
                            }
                        ],
                    },
                ],
                "tool_metadata": {},
                "extra": {},
            }
        ),
        encoding="utf-8",
    )

    artifact = collect_context_artifact(cwd, settings=_settings())

    assert artifact["snapshot"]["tool_result_markers"] == {
        "collapsed_body": 0,
        "native_char_truncation": 1,
        "post_tool_token_truncation": 1,
    }
    assert artifact["snapshot"]["assistant_marker_mentions"] == {
        "collapsed_body": 0,
        "native_char_truncation": 1,
        "post_tool_token_truncation": 1,
    }


def test_collect_context_artifact_handles_missing_runtime_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cwd = tmp_path / "fresh"
    cwd.mkdir()

    artifact = collect_context_artifact(cwd, settings=_settings())

    assert artifact["snapshot"]["exists"] is False
    assert artifact["memory"]["index_exists"] is False
    assert artifact["discovery"]["commands"] == []
    assert artifact["discovery"]["skills"] == []


def test_large_probe_and_prepare_are_deterministic(tmp_path: Path) -> None:
    payload = build_large_probe_text()
    assert payload.startswith("HEAD_ANCHOR=context-head-0812\n")
    assert "MIDDLE_ANCHOR=context-middle-0812" in payload
    assert payload.rstrip().endswith("TAIL_ANCHOR=context-tail-0812")
    assert len(payload) > 100_000

    source = tmp_path / "source"
    source.mkdir()
    (source / "context_facts.md").write_text("facts\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    target = runtime_root / "context-management"

    prepare_context_fixture(source=source, target=target, runtime_root=runtime_root)
    first = (target / "large_context.txt").read_text(encoding="utf-8")
    (target / "large_context.txt").write_text("mutated\n", encoding="utf-8")
    prepare_context_fixture(source=source, target=target, runtime_root=runtime_root)

    assert (target / "context_facts.md").read_text(encoding="utf-8") == "facts\n"
    assert (target / "large_context.txt").read_text(encoding="utf-8") == first == payload


def test_context_facts_use_runtime_cwd_instead_of_a_stale_fixture_path() -> None:
    facts = (FIXTURE_SOURCE / "context_facts.md").read_text(encoding="utf-8")

    assert ".dogfood/work/context-management/" not in facts
    assert "启动 REPL 时的当前工作目录" in facts
    assert "`pwd`" in facts
