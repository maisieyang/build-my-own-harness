"""Memory write capability spike — Phase 16 prep.

Tests whether the currently-configured model (read from .env via Settings)
can do the Claude-Code-style memory write pattern as a single LLM call:

  user msg → model emits Write(.md frontmatter) + Edit(MEMORY.md) tool_use
  blocks inline, OR correctly skips when the input is not memory-worthy.

This is a **capability spike**, not the formal gating eval defined in
decisions/35-eval-coverage-map.md D35.5 P0. The formal eval needs:
  - Stage 1-5 substrate consumer (evals/memory_decision/)
  - Stage 3 LLM-judge scorer for #4 inline decision surface
  - Stage 4 cassette for reproducibility
  - Stage 5 results persistence

This spike answers ONE question fast: is the current model capable enough
that building the formal eval is worth investing in?

Run:
    uv run python scripts/spike_memory_capability.py
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from openai import AsyncOpenAI

from openharness.api import OpenAICompatibleApiClient
from openharness.config.settings import Settings
from openharness.protocols.content import (
    TextBlock,
    ToolUseBlock,
)
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.tools import ToolSpec

# ---------------------------------------------------------------------------
# Claude-Code-style memory system prompt (condensed for spike)
# ---------------------------------------------------------------------------

MEMORY_SYSTEM_PROMPT = """\
You have a persistent memory system at /tmp/spike_memory/.

You should save memories about the user, their feedback, project context,
and references to external systems. Memory types:

- **user**: Information about the user's role, preferences, knowledge.
- **feedback**: Corrections OR confirmed approaches the user gave you.
- **project**: Who is doing what / why / by when in the current work.
- **reference**: Pointers to external systems (Slack channels, Linear projects).

## DO save when

- User states a preference or working style
- User corrects your approach OR confirms an unusual approach worked
- User shares project state (deadlines, who's responsible, why a decision was made)
- User points to an external system as the source of truth

## DO NOT save

- Code patterns / project structure (derivable from current code)
- Git history / recent commits
- Trivial questions or ephemeral task state
- Anything obvious from reading the codebase

## How to save (two-step, in the SAME response)

Step 1: Write the memory to its own file at /tmp/spike_memory/<slug>.md with frontmatter:

```markdown
---
name: short-kebab-case-slug
description: one-line summary
metadata:
  type: user | feedback | project | reference
---

<body — for feedback/project, structure as: rule/fact, then **Why:** and **How to apply:** lines.>
```

Step 2: Add a pointer line to /tmp/spike_memory/MEMORY.md:

`- [Title](file.md) — one-line hook`

If the user input is NOT memory-worthy, respond normally and DO NOT call any tools.

You have Write and Edit tools available. Use them to persist memories. Do not explain
what you are about to do — just do it. Respond briefly to the user after.
"""


# ---------------------------------------------------------------------------
# Tool schemas (Claude-Code shape)
# ---------------------------------------------------------------------------

WRITE_TOOL = ToolSpec(
    name="Write",
    description="Write content to a file at an absolute path. Overwrites if exists.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path."},
            "content": {"type": "string", "description": "File content."},
        },
        "required": ["file_path", "content"],
    },
)

EDIT_TOOL = ToolSpec(
    name="Edit",
    description="Replace exact old_string with new_string in a file.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


SCENARIOS = [
    {
        "id": "S1-preference",
        "expect_write": True,
        "expect_type": "feedback",
        "user_msg": "I prefer terse responses without trailing summaries — just show me the diff.",
        "rationale": "Stable user preference — should be feedback memory.",
    },
    {
        "id": "S2-correction",
        "expect_write": True,
        "expect_type": "feedback",
        "user_msg": "Actually we use Tavily for web search, not SerpAPI. SerpAPI is too expensive at our scale.",
        "rationale": "User correction with reasoning — should be feedback memory.",
    },
    {
        "id": "S3-trivial",
        "expect_write": False,
        "expect_type": None,
        "user_msg": "What's the current time?",
        "rationale": "Trivial ephemeral question — should NOT write.",
    },
    {
        "id": "S4-project-state",
        "expect_write": True,
        "expect_type": "project",
        "user_msg": (
            "We're freezing all non-critical merges after Thursday — "
            "mobile team is cutting a release branch."
        ),
        "rationale": "Project state with deadline + reason — should be project memory.",
    },
    {
        "id": "S5-reference",
        "expect_write": True,
        "expect_type": "reference",
        "user_msg": (
            "Pipeline bugs go in the Linear project INGEST. "
            "Check it if you want context on these tickets."
        ),
        "rationale": "External system pointer — should be reference memory.",
    },
]


# ---------------------------------------------------------------------------
# Observation helpers
# ---------------------------------------------------------------------------


FRONTMATTER_RE = re.compile(
    r"^---\s*\nname:\s*(.+?)\s*\ndescription:\s*(.+?)\s*\nmetadata:\s*\n\s*type:\s*(\w+)\s*\n---",
    re.MULTILINE | re.DOTALL,
)


def _path_contains_memory_md(t: ToolUseBlock) -> bool:
    return "MEMORY.md" in t.input.get("file_path", "")


def _touched_target_md(t: ToolUseBlock) -> bool:
    """Is this tool call against the .md file (not MEMORY.md)?"""
    path = t.input.get("file_path", "")
    return path.endswith(".md") and "MEMORY.md" not in path


def observe(scenario: dict, tool_uses: list[ToolUseBlock], mode: str) -> dict:
    """Score one scenario against expectations.

    ``mode`` ∈ {"cold", "warm"}:
    - cold: /tmp/spike_memory/ empty; both Write+Write or Write+Edit acceptable
    - warm: MEMORY.md already exists with 3 entries; Edit on MEMORY.md is ideal;
      Write would overwrite (risky); no touch = index drift (canonical failure).
    """
    write_calls = [t for t in tool_uses if t.name == "Write"]
    edit_calls = [t for t in tool_uses if t.name == "Edit"]
    read_calls = [t for t in tool_uses if t.name == "Read"]
    other_calls = [t for t in tool_uses if t.name not in {"Write", "Edit", "Read"}]

    out: dict = {
        "scenario_id": scenario["id"],
        "mode": mode,
        "expect_write": scenario["expect_write"],
        "write_count": len(write_calls),
        "edit_count": len(edit_calls),
        "read_count": len(read_calls),
        "other_count": len(other_calls),
        "verdict": "?",
        "notes": [],
    }

    if not scenario["expect_write"]:
        if not write_calls and not edit_calls:
            out["verdict"] = "PASS — correctly skipped"
        else:
            out["verdict"] = (
                f"FAIL — wrote memory when input was trivial "
                f"(Writex{len(write_calls)}, Editx{len(edit_calls)})"
            )
        return out

    # expect_write = True
    # 1) Did model write a target .md (non-MEMORY) file?
    target_writes = [t for t in write_calls if _touched_target_md(t)]
    if not target_writes:
        out["verdict"] = "FAIL — model did not Write any memory .md file"
        return out

    # 2) Frontmatter validity
    content = target_writes[0].input.get("content", "")
    fm = FRONTMATTER_RE.search(content)
    if not fm:
        out["verdict"] = "FAIL — Write called but frontmatter malformed"
        out["notes"].append(f"content_preview={content[:200]!r}")
        return out

    out["frontmatter_name"] = fm.group(1)
    out["frontmatter_type"] = fm.group(3)
    type_match = fm.group(3) == scenario["expect_type"]

    # 3) MEMORY.md interaction analysis
    memory_writes = [t for t in write_calls if _path_contains_memory_md(t)]
    memory_edits = [t for t in edit_calls if _path_contains_memory_md(t)]
    memory_reads = [t for t in read_calls if _path_contains_memory_md(t)]

    has_memory_write = bool(memory_writes)
    has_memory_edit = bool(memory_edits)
    has_memory_read = bool(memory_reads)
    memory_touched = has_memory_write or has_memory_edit

    type_prefix = (
        "" if type_match else f"(type={fm.group(3)!r} vs expected {scenario['expect_type']!r}) "
    )

    if mode == "cold":
        # Cold-start: Write or Edit on MEMORY.md both acceptable (file doesn't exist).
        if memory_touched:
            out["verdict"] = f"{type_prefix}PASS — wrote .md + touched MEMORY.md (cold-start OK)"
        elif has_memory_read:
            out["verdict"] = (
                f"{type_prefix}PARTIAL — Read MEMORY.md but didn't follow through "
                "(chain incomplete, would need multi-turn)"
            )
        else:
            out["verdict"] = (
                f"{type_prefix}FAIL — wrote .md but no action on MEMORY.md (index drift)"
            )
        return out

    # mode == "warm"
    # Warm-start: MEMORY.md exists. Edit = ideal; Write = risky overwrite;
    # Read-only = chain incomplete; nothing = canonical drift.
    if has_memory_edit:
        out["verdict"] = f"{type_prefix}PASS — wrote .md + Edit MEMORY.md (warm-start ideal)"
    elif has_memory_write:
        # Check whether the Write content preserved existing entries
        new_content = memory_writes[0].input.get("content", "")
        existing_anchors = [
            "Code style 80 chars",
            "Test runner command",
            "Build dir Linear project",
        ]
        preserved = sum(1 for a in existing_anchors if a in new_content)
        if preserved >= 2:
            out["verdict"] = (
                f"{type_prefix}PARTIAL — Write MEMORY.md preserved {preserved}/3 existing entries "
                "(wasteful but non-destructive)"
            )
        else:
            out["verdict"] = (
                f"{type_prefix}FAIL — Write MEMORY.md OVERWROTE existing entries "
                f"({preserved}/3 preserved; canonical destructive drift)"
            )
            out["notes"].append(f"new_MEMORY.md_preview={new_content[:200]!r}")
    elif has_memory_read:
        out["verdict"] = (
            f"{type_prefix}PARTIAL — Read MEMORY.md but didn't Edit/Write "
            "(chain incomplete, would continue next turn)"
        )
    else:
        out["verdict"] = (
            f"{type_prefix}FAIL — wrote .md but no action on MEMORY.md (canonical index drift)"
        )
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


WARM_START_BASE = Path("/tmp/spike_memory")

WARM_START_FILES = {
    "MEMORY.md": (
        "# Memory index\n\n"
        "- [Code style 80 chars](code-style-line-width.md) — wrap code at 80 chars max\n"
        "- [Test runner command](test-runner-command.md) — tests run via `uv run pytest -q`\n"
        "- [Build dir Linear project](linear-build-tracking.md) — infra bugs in Linear project BUILD\n"
    ),
    "code-style-line-width.md": (
        "---\nname: code-style-line-width\n"
        "description: Wrap code at 80 characters\n"
        "metadata:\n  type: feedback\n---\n\n"
        "Wrap code at 80 characters max.\n"
    ),
    "test-runner-command.md": (
        "---\nname: test-runner-command\n"
        "description: Tests run via uv run pytest -q\n"
        "metadata:\n  type: project\n---\n\n"
        "Tests run via uv run pytest -q, not python -m pytest.\n"
    ),
    "linear-build-tracking.md": (
        "---\nname: linear-build-tracking\n"
        "description: Infra bugs in Linear project BUILD\n"
        "metadata:\n  type: reference\n---\n\n"
        "Infra bugs go in Linear project BUILD.\n"
    ),
}


def setup_state(mode: str) -> None:
    """Pre-populate (warm) or clear (cold) /tmp/spike_memory/ before a scenario."""
    if WARM_START_BASE.exists():
        shutil.rmtree(WARM_START_BASE)
    WARM_START_BASE.mkdir(parents=True, exist_ok=True)
    if mode == "warm":
        for fname, content in WARM_START_FILES.items():
            (WARM_START_BASE / fname).write_text(content)


async def run_scenario(
    client: OpenAICompatibleApiClient, model: str, scenario: dict, mode: str
) -> dict:
    """One LLM call per scenario. Observe tool_use blocks emitted."""
    request = ApiMessageRequest(
        model=model,
        max_tokens=2048,
        system=MEMORY_SYSTEM_PROMPT,
        messages=[
            ConversationMessage(
                role="user",
                content=[TextBlock(text=scenario["user_msg"])],
            ),
        ],
        tools=[WRITE_TOOL, EDIT_TOOL],
        stream=True,
    )

    tool_uses: list[ToolUseBlock] = []
    text_parts: list[str] = []
    completion_event = None

    async for event in client.stream_message(request):
        event_type = type(event).__name__
        if event_type == "ApiMessageCompleteEvent":
            completion_event = event
            break

    if completion_event is None:
        return {
            "scenario_id": scenario["id"],
            "mode": mode,
            "verdict": "ERROR — no message complete event",
            "write_count": 0,
            "edit_count": 0,
            "read_count": 0,
            "other_count": 0,
            "notes": [],
            "tool_uses_raw": [],
            "text": "",
        }

    for block in completion_event.message.content:
        if isinstance(block, ToolUseBlock):
            tool_uses.append(block)
        elif isinstance(block, TextBlock):
            text_parts.append(block.text)

    obs = observe(scenario, tool_uses, mode)
    obs["text"] = "".join(text_parts)[:200] if text_parts else ""
    obs["tool_uses_raw"] = [(t.name, dict(t.input)) for t in tool_uses]
    return obs


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    sdk = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
    client = OpenAICompatibleApiClient(sdk=sdk)
    model = settings.model

    print("=" * 72)
    print("# Memory capability spike — Phase 16 prep (cold + warm)")
    print(f"# model: {model}")
    print(f"# base_url: {settings.base_url}")
    print(f"# scenarios: {len(SCENARIOS)} x 2 modes (cold, warm)")
    print("=" * 72)
    print()

    all_results: list[dict] = []
    for mode in ("cold", "warm"):
        print(f"###### MODE: {mode.upper()}-START ######")
        if mode == "warm":
            print("  (pre-populated MEMORY.md + 3 existing .md files)")
        print()
        for scen in SCENARIOS:
            setup_state(mode)  # fresh per-scenario isolation
            print(f"--- {scen['id']} [{mode}] ---")
            print(f"  expect_write: {scen['expect_write']} (type={scen['expect_type']!r})")
            print(f"  rationale:    {scen['rationale']}")
            print(f"  user_msg:     {scen['user_msg']!r}")
            try:
                obs = await run_scenario(client, model, scen, mode)
            except Exception as exc:
                obs = {
                    "scenario_id": scen["id"],
                    "mode": mode,
                    "verdict": (f"ERROR — {type(exc).__name__}: {str(exc)[:160]}"),
                    "write_count": 0,
                    "edit_count": 0,
                    "read_count": 0,
                    "other_count": 0,
                    "notes": [],
                    "tool_uses_raw": [],
                    "text": "",
                }
            all_results.append(obs)
            print(
                f"  tool_uses:    "
                f"Writex{obs['write_count']} Editx{obs['edit_count']} "
                f"Readx{obs.get('read_count', 0)} otherx{obs['other_count']}"
            )
            for tname, tinput in obs.get("tool_uses_raw", []):
                file_path = tinput.get("file_path", "?")
                content_preview = (
                    tinput.get("content", "")[:80].replace("\n", "\\n")
                    if "content" in tinput
                    else ""
                )
                old_preview = (
                    tinput.get("old_string", "")[:40].replace("\n", "\\n")
                    if "old_string" in tinput
                    else ""
                )
                new_preview = (
                    tinput.get("new_string", "")[:40].replace("\n", "\\n")
                    if "new_string" in tinput
                    else ""
                )
                keys = sorted(tinput.keys())
                if content_preview:
                    print(f"    [{tname}] {file_path} content={content_preview!r}...")
                elif old_preview or new_preview:
                    print(f"    [{tname}] {file_path} old={old_preview!r} new={new_preview!r}")
                else:
                    print(f"    [{tname}] keys={keys} input={str(tinput)[:120]}")
            if obs.get("frontmatter_type"):
                print(
                    f"  frontmatter:  type={obs['frontmatter_type']!r} "
                    f"name={obs.get('frontmatter_name', '')!r}"
                )
            if obs.get("text"):
                print(f"  text:         {obs['text']!r}")
            print(f"  ► VERDICT:    {obs['verdict']}")
            if obs.get("notes"):
                for n in obs["notes"]:
                    print(f"     - {n}")
            print()

    # Per-mode summary
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)

    def _bucket(results: list[dict]) -> tuple[int, int, int, int]:
        p = sum(1 for r in results if r["verdict"].startswith("PASS"))
        pa = sum(1 for r in results if r["verdict"].startswith("PARTIAL"))
        f = sum(1 for r in results if r["verdict"].startswith("FAIL"))
        e = sum(1 for r in results if r["verdict"].startswith("ERROR"))
        return p, pa, f, e

    cold_results = [r for r in all_results if r["mode"] == "cold"]
    warm_results = [r for r in all_results if r["mode"] == "warm"]

    print()
    print(f"{'Scenario':<28} {'COLD':<55} {'WARM':<55}")
    print(f"{'-' * 28} {'-' * 55} {'-' * 55}")
    for scen in SCENARIOS:
        cold_v = next(
            (r["verdict"] for r in cold_results if r["scenario_id"] == scen["id"]),
            "?",
        )
        warm_v = next(
            (r["verdict"] for r in warm_results if r["scenario_id"] == scen["id"]),
            "?",
        )
        print(f"{scen['id']:<28} {cold_v[:55]:<55} {warm_v[:55]:<55}")
    print()

    cp, cpa, cf, ce = _bucket(cold_results)
    wp, wpa, wf, we = _bucket(warm_results)
    print(f"  COLD-START:  PASS {cp}/5  PARTIAL {cpa}/5  FAIL {cf}/5  ERROR {ce}/5")
    print(f"  WARM-START:  PASS {wp}/5  PARTIAL {wpa}/5  FAIL {wf}/5  ERROR {we}/5")
    print()

    drift_count = sum(
        1 for r in warm_results if "canonical" in r["verdict"] or "OVERWROTE" in r["verdict"]
    )

    print("Interpretation (per D35.5 P0 gating intent):")
    if wp >= 4 and drift_count == 0:
        print("  ✓ Warm-start clean — model can hold the two-step contract on the production path")
        print(
            "  → Recommendation: proceed with Phase 16 boundary doc + memory_decision/ formal eval"
        )
    elif drift_count == 0 and wp + wpa >= 4:
        print("  ⚠ No destructive drift, but chain incomplete in some warm cases")
        print(
            "  → Recommendation: examine PARTIAL cases; stronger system prompt"
            " may close the gap; formal eval still worthwhile"
        )
    elif drift_count > 0:
        print(f"  ✗ {drift_count}/5 warm cases show canonical index drift or destructive overwrite")
        print(
            "  → Recommendation: do NOT pivot to LLM-self-decides as drawn; "
            "either prescriptive prompt or drop MEMORY.md index entirely "
            "(glob-based discovery)"
        )
    else:
        print("  ⚠ Mixed signal — examine each warm verdict")


if __name__ == "__main__":
    asyncio.run(main())
