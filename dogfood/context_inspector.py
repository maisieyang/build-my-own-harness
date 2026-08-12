"""只读收集 OpenHarness context-management dogfood 证据。

这个模块不启动 Agent、不调用模型,也不加载 plugin Python hooks。它读取当前配置、
filesystem catalogs、session-memory checkpoint 和 conversation snapshot,把多次手动
操作之间的上下文变化写成可比较的 JSON 与文本 artifact。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openharness.commands.store import FilesystemCommandStore
from openharness.config import Settings
from openharness.memory.paths import get_project_memory_dir
from openharness.plugins import PluginLoader
from openharness.prompts.project_instructions import discover_project_instruction_files
from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.repl import find_active_goal
from openharness.services.compact import (
    estimate_message_tokens,
    get_context_window,
    threshold_tokens,
)
from openharness.services.session_memory import get_session_memory_dir
from openharness.services.snapshot import SnapshotError, get_snapshot_dir, load_snapshot
from openharness.skills.store import FilesystemSkillStore
from openharness.tools.bash import MAX_OUTPUT_CHARS as BASH_OUTPUT_CHARS
from openharness.tools.grep import DEFAULT_LINE_CAP as GREP_DEFAULT_LINE_CAP
from openharness.tools.grep import HARD_LINE_CAP as GREP_HARD_LINE_CAP
from openharness.tools.read import MAX_READ_BYTES

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / ".dogfood"
FIXTURE_SOURCE = REPO_ROOT / "dogfood" / "fixtures" / "context-management"
WORK_DIR = RUNTIME_ROOT / "work" / "context-management"
ARTIFACT_ROOT = RUNTIME_ROOT / "artifacts"

_SCHEMA = "openharness.dogfood.context-artifact.v2"
_CATALOG_ITEM = re.compile(r"^- \*\*(.+?)\*\*\s+--", re.MULTILINE)
_TOOL_RESULT_MARKERS = {
    "collapsed_body": re.compile(r"\[collapsed [^\]]+\]"),
    "native_char_truncation": re.compile(r"\[truncated \d+ chars\]"),
    "post_tool_token_truncation": re.compile(r"\[truncated \d+ tokens\]"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.startswith("#")]


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    remainder = text[start + len(marker) :]
    next_heading = remainder.find("\n## ")
    if next_heading >= 0:
        remainder = remainder[:next_heading]
    return remainder


def _catalog_names(text: str, heading: str) -> list[str]:
    return _CATALOG_ITEM.findall(_section(text, heading))


def _file_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "line_count": len(text.splitlines()),
        "headings": _headings(text),
    }


def _installed_plugins(home: Path, *, load_enabled: bool) -> list[dict[str, Any]]:
    """Discover manifests without fan-out/importing plugin hook modules."""
    loader = PluginLoader(home / ".openharness" / "plugins")
    manifests = loader.discover()
    return [
        {
            "name": name,
            "version": manifest.version,
            "description": manifest.description,
            "source_path": str(manifest.source_path),
            "skills_count": len(manifest.skills),
            "commands_count": len(manifest.commands),
            "bundles_count": len(manifest.bundles),
            "hooks_count": len(manifest.hooks),
            "mcp_servers_count": len(manifest.mcp_servers),
            # This records the setting, not proof that fan-out succeeded. The
            # stored snapshot catalog below is the authoritative runtime evidence.
            "loaded": load_enabled,
        }
        for name, manifest in sorted(manifests.items())
    ]


def _permission_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    extra = snapshot.get("extra")
    state = extra.get("permission_runtime") if isinstance(extra, dict) else None
    if not isinstance(state, dict):
        return {"present": False, "parked_request_present": False}
    return {
        "present": True,
        "parked_request_present": state.get("parked_request") is not None,
    }


def _snapshot_summary(cwd: Path, *, model: str) -> dict[str, Any]:
    snapshot_path = get_snapshot_dir(cwd) / "current.json"
    if not snapshot_path.is_file():
        return {"path": str(snapshot_path), "exists": False}
    try:
        snapshot = load_snapshot(cwd)
        messages = [ConversationMessage.model_validate(item) for item in snapshot["messages"]]
    except (SnapshotError, KeyError, TypeError, ValueError) as exc:
        return {
            "path": str(snapshot_path),
            "exists": True,
            "load_error": f"{type(exc).__name__}: {exc}",
        }

    role_counts: Counter[str] = Counter()
    block_counts: Counter[str] = Counter()
    tool_uses: Counter[str] = Counter()
    tool_result_count = 0
    error_tool_result_count = 0
    synthetic_skill_load_count = 0
    context_marker_patterns = {
        "session_checkpoint": "Session memory checkpoint from earlier in this conversation:",
        "compact_boundary": "[Conversation history summarized below",
        "full_summary": "Summary of prior conversation:",
    }
    context_markers: Counter[str] = Counter()
    tool_result_markers: Counter[str] = Counter()
    assistant_marker_mentions: Counter[str] = Counter()
    for message in messages:
        role_counts[message.role] += 1
        for block in message.content:
            block_counts[block.type] += 1
            if isinstance(block, ToolUseBlock):
                tool_uses[block.name] += 1
                if block.name == "LoadSkill" and block.id.startswith("synth_"):
                    synthetic_skill_load_count += 1
            elif isinstance(block, ToolResultBlock):
                tool_result_count += 1
                if block.is_error:
                    error_tool_result_count += 1
                for name, pattern in _TOOL_RESULT_MARKERS.items():
                    if pattern.search(block.content):
                        tool_result_markers[name] += 1
            if isinstance(block, TextBlock):
                for name, marker in context_marker_patterns.items():
                    if marker in block.text:
                        context_markers[name] += 1
                if message.role == "assistant":
                    for name, pattern in _TOOL_RESULT_MARKERS.items():
                        if pattern.search(block.text):
                            assistant_marker_mentions[name] += 1

    system_prompt = snapshot.get("system_prompt")
    if not isinstance(system_prompt, str):
        system_prompt = ""
    estimated_tokens = estimate_message_tokens(messages, model=model)
    window = get_context_window(model)
    return {
        "path": str(snapshot_path),
        "exists": True,
        "created_at": snapshot.get("created_at"),
        "git_head": snapshot.get("git_head"),
        "snapshot_model": snapshot.get("model"),
        "message_count": len(messages),
        "roles": dict(sorted(role_counts.items())),
        "blocks": dict(sorted(block_counts.items())),
        "tool_uses": dict(sorted(tool_uses.items())),
        "tool_result_count": tool_result_count,
        "error_tool_result_count": error_tool_result_count,
        "synthetic_skill_load_count": synthetic_skill_load_count,
        "context_markers": {
            name: context_markers[name] for name in sorted(context_marker_patterns)
        },
        "tool_result_markers": {
            name: tool_result_markers[name] for name in sorted(_TOOL_RESULT_MARKERS)
        },
        "assistant_marker_mentions": {
            name: assistant_marker_mentions[name] for name in sorted(_TOOL_RESULT_MARKERS)
        },
        "estimated_message_tokens": estimated_tokens,
        "estimated_message_context_ratio": round(estimated_tokens / window, 6),
        "active_goal": find_active_goal(messages),
        "permission_runtime": _permission_summary(snapshot),
        "tool_metadata": snapshot.get("tool_metadata", {}),
        "stored_context": {
            "system_prompt_chars": len(system_prompt),
            "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            "headings": _headings(system_prompt),
            "tools": _catalog_names(system_prompt, "Tools"),
            "skills": _catalog_names(
                system_prompt,
                "Available Skills (call LoadSkill to expand)",
            ),
        },
    }


def collect_context_artifact(cwd: Path, *, settings: Settings) -> dict[str, Any]:
    """Collect a secret-free, side-effect-free snapshot of context state."""
    cwd = cwd.resolve()
    home = Path.home()
    model = settings.model
    skill_store = FilesystemSkillStore(
        global_dir=home / ".openharness" / "skills",
        project_dir=cwd / ".openharness" / "skills",
    )
    command_store = FilesystemCommandStore(
        global_dir=home / ".openharness" / "commands",
        project_dir=cwd / ".openharness" / "commands",
    )
    memory_dir = get_project_memory_dir(cwd)
    memory_index = memory_dir / "MEMORY.md"
    session_memory = get_session_memory_dir(cwd) / "checkpoint.md"
    context_window = get_context_window(model)
    compact_threshold = threshold_tokens(
        model,
        threshold_ratio=settings.compact.threshold_ratio,
    )

    artifact: dict[str, Any] = {
        "schema": _SCHEMA,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(cwd),
        "configuration": {
            "model": model,
            "context_window": context_window,
            "compact_enabled": settings.compact.enabled,
            "compact_threshold_ratio": settings.compact.threshold_ratio,
            "compact_threshold_tokens": compact_threshold,
            "full_compact_max_tokens": settings.compact.full_compact_max_tokens,
            "full_compact_timeout_s": settings.compact.full_compact_timeout_s,
            "auto_truncate": settings.auto_truncate,
            "tool_result_cap_tokens": settings.tool_result_cap,
            "snapshot_enabled": settings.snapshot.enabled,
            "memory_enabled": settings.enable_memory,
            "project_instructions_enabled": settings.enable_project_instructions,
            "plugins_enabled": settings.enable_plugins,
            "plugin_hooks_enabled": settings.enable_plugin_hooks,
            "web_enabled": settings.web.enabled,
            "web_key_configured": settings.web.api_key is not None,
            "mcp_servers": [server.name for server in settings.mcp_servers],
            "max_agent_depth": settings.max_agent_depth,
            "tool_native_limits": {
                "Read.max_bytes": MAX_READ_BYTES,
                "Bash.max_output_chars": BASH_OUTPUT_CHARS,
                "Grep.default_line_cap": GREP_DEFAULT_LINE_CAP,
                "Grep.hard_line_cap": GREP_HARD_LINE_CAP,
            },
        },
        "discovery": {
            "project_instruction_files": [
                str(path) for path in discover_project_instruction_files(cwd)
            ],
            "commands": sorted(command_store.discover()),
            "skills": sorted(skill_store.discover()),
            "installed_plugins": _installed_plugins(
                home,
                load_enabled=settings.enable_plugins,
            ),
        },
        "memory": {
            "directory": str(memory_dir),
            "index_exists": memory_index.is_file(),
            "index": _file_summary(memory_index),
        },
        "session_memory": _file_summary(session_memory),
        "snapshot": _snapshot_summary(cwd, model=model),
        "measurement_boundary": {
            "estimated_message_tokens_includes": [
                "conversation text",
                "tool_use name and input",
                "tool_result content",
                "images as a fixed estimate",
            ],
            "estimated_message_tokens_excludes": [
                "system prompt",
                "structured tool schemas",
                "provider framing overhead",
                "reserved output tokens",
            ],
            "note": (
                "The REPL toolbar and auto-compact threshold use the message-only estimate; "
                "stored_context reports system prompt size separately."
            ),
        },
    }
    return artifact


def render_text_report(artifact: dict[str, Any]) -> str:
    """Render the small human-readable companion to the JSON artifact."""
    cfg = artifact["configuration"]
    discovery = artifact["discovery"]
    snapshot = artifact["snapshot"]
    lines = [
        "OpenHarness context artifact",
        f"cwd: {artifact['cwd']}",
        (
            f"model: {cfg['model']} · window={cfg['context_window']} · "
            f"auto-compact={cfg['compact_threshold_tokens']} "
            f"({cfg['compact_threshold_ratio']:.0%})"
        ),
        (
            f"tool-result: cap={cfg['tool_result_cap_tokens']} tokens · "
            f"auto-truncate={cfg['auto_truncate']}"
        ),
        f"instructions: {len(discovery['project_instruction_files'])}",
        f"commands: {', '.join(discovery['commands']) or '(none)'}",
        f"skills: {', '.join(discovery['skills']) or '(none)'}",
        (
            "plugins: "
            + (
                ", ".join(
                    f"{item['name']}({'loaded' if item['loaded'] else 'installed-only'})"
                    for item in discovery["installed_plugins"]
                )
                or "(none)"
            )
        ),
        (f"session-memory: {'present' if artifact['session_memory']['exists'] else 'missing'}"),
    ]
    if not snapshot.get("exists"):
        lines.append("snapshot: missing")
        return "\n".join(lines) + "\n"
    if snapshot.get("load_error"):
        lines.append(f"snapshot: ERROR {snapshot['load_error']}")
        return "\n".join(lines) + "\n"

    stored = snapshot["stored_context"]
    permission = snapshot["permission_runtime"]
    lines.extend(
        [
            (
                f"snapshot: {snapshot['message_count']} messages · "
                f"~{snapshot['estimated_message_tokens']} message tokens"
            ),
            f"stored tools: {', '.join(stored['tools']) or '(none)'}",
            f"stored skills: {', '.join(stored['skills']) or '(none)'}",
            f"active goal: {snapshot['active_goal'] or '(none)'}",
            (
                "permission-runtime: "
                f"present={'yes' if permission['present'] else 'no'} · "
                f"parked={'yes' if permission['parked_request_present'] else 'no'}"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def build_large_probe_text() -> str:
    """Return a deterministic >100k context body with three stable anchors."""
    head = ["HEAD_ANCHOR=context-head-0812"]
    before_middle = [
        f"PREFIX_FILLER_{index:04d}=" + ("alpha-beta-gamma-" * 4) for index in range(900)
    ]
    middle = ["MIDDLE_ANCHOR=context-middle-0812"]
    after_middle = [
        f"SUFFIX_FILLER_{index:04d}=" + ("delta-epsilon-zeta-" * 4) for index in range(900)
    ]
    tail = ["TAIL_ANCHOR=context-tail-0812"]
    return "\n".join([*head, *before_middle, *middle, *after_middle, *tail]) + "\n"


def prepare_context_fixture(*, source: Path, target: Path, runtime_root: Path) -> None:
    """Reset the disposable fixture and generate its deliberately large file."""
    root = runtime_root.resolve()
    resolved_target = target.resolve()
    if resolved_target == root or not resolved_target.is_relative_to(root):
        raise ValueError(f"refusing to prepare target outside dogfood runtime root: {target}")
    if not source.is_dir():
        raise FileNotFoundError(f"context fixture source is missing: {source}")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    (target / "large_context.txt").write_text(build_large_probe_text(), encoding="utf-8")


def _load_effective_settings() -> Settings:
    user_env = Path.home() / ".openharness" / ".env"
    return Settings(_env_file=(str(user_env), ".env"))


def _write_capture(*, cwd: Path, run_id: str, label: str) -> tuple[Path, Path]:
    artifact = collect_context_artifact(cwd, settings=_load_effective_settings())
    output_dir = ARTIFACT_ROOT / run_id / "context"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{label}.json"
    text_path = output_dir / f"{label}.txt"
    json_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = render_text_report(artifact)
    text_path.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"artifacts: {json_path} · {text_path}")
    return json_path, text_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="reset the context probe fixture")
    prepare.add_argument("--target", type=Path, default=WORK_DIR)
    capture = subparsers.add_parser("capture", help="capture context evidence without a model call")
    capture.add_argument("--cwd", type=Path, default=REPO_ROOT)
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--label", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "prepare":
            prepare_context_fixture(
                source=FIXTURE_SOURCE,
                target=args.target,
                runtime_root=RUNTIME_ROOT,
            )
            print(f"prepared: {args.target}")
            print(f"large probe bytes: {(args.target / 'large_context.txt').stat().st_size}")
            return 0
        _write_capture(cwd=args.cwd, run_id=args.run_id, label=args.label)
        return 0
    except (OSError, ValueError) as exc:
        print(f"context inspector failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
