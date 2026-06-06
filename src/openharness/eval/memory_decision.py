"""Memory-decision eval — Stage 1-5 substrate consumer for decision
surface #4 (inline decision class side-effects) per
``decisions/35-eval-coverage-map.md`` §D35.5 P0.

This module exposes the parallel Sample / infer / runner shape the
memory_decision eval needs, alongside (not on top of) focus_state's
substrate in :mod:`openharness.eval.protocol` + :mod:`openharness.eval.runner`.
The two consumers diverge at the **probabilistic surface** they probe:

- ``focus_state``: secondary-LLM-pass producing structured JSON output
  (``FocusState`` dataclass). Scorers inspect parsed fields.
- ``memory_decision``: main-loop inline tool-call decisions. The LLM
  receives the Claude-Code-style ``## Memory`` system prompt section,
  Write + Edit + Read tools registered, and a user message. The output
  is the ``ApiMessageCompleteEvent`` carrying tool_use blocks; scorers
  observe whether Write was called, frontmatter validity, MEMORY.md
  index updates, and destructive-overwrite avoidance.

The substrate pieces that ARE shared (D35.6 "substrate reuse ≥ rebuild"):

- :class:`Score` from :mod:`openharness.eval.protocol` (truly generic)
- :class:`CassetteStore` from :mod:`openharness.eval.cassette` (storage
  layer is content-agnostic via keys)
- :mod:`openharness.eval.results` persistence + RunMetadata

What's parallel (consumer-specific, NOT generalized):

- :class:`MemoryDecisionSample` — schema tailored to inline decision
  scenarios; carries ``pre_populated_files`` + ``user_msg`` +
  expected behavior flags, NOT focus_state's ``capability_assertions``
- :class:`MemoryDecisionOutput` — wraps the LLM message + emitted
  tool_use blocks
- :func:`infer_memory_decision` — sets up the temp memory dir per
  sample fixture, builds the request, returns observed output
- :func:`load_memory_decision_dataset` + :func:`run_memory_decision_eval`
  — thin runner that mirrors :func:`openharness.eval.runner.run_eval`
  shape

This parallel design is deliberate. Per CLAUDE.md §"Don't add
abstractions beyond what task requires" — refactoring the focus_state
substrate into a fully generic
``Sample[InputT] + Scorer[InputT, OutputT] + run_eval[InputT, OutputT]``
shape would be a multi-phase generalization. Phase 16 ships the second
consumer, validates the substrate-reuse pattern works at the
component layer (Score, CassetteStore, results), and leaves the
typing generalization for a future cleanup phase when a third
consumer surfaces and the pattern repeats.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openharness.eval.cassette import (
    CassetteKey,
    CassetteMissingError,
    CassetteMode,
    CassetteStore,
)
from openharness.protocols.content import TextBlock, ToolResultBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.tools import ToolSpec

if TYPE_CHECKING:
    from openharness.api import OpenAICompatibleApiClient
    from openharness.eval.protocol import Score


# ---------------------------------------------------------------------------
# System prompt + tool schemas the eval drives the LLM with
# ---------------------------------------------------------------------------


# The runtime ``## Memory`` rules section the production CLI now
# injects via :func:`prompts.memory.format_memory_rules_section`
# (Phase 16 T1, D36.10). For the eval we point the LLM at a fixture
# directory under ``/tmp`` so successful runs don't pollute the
# user's real ``~/.openharness/memory/`` tree.

_MEMORY_DIR_PLACEHOLDER = Path("/tmp/oh_eval_memory_decision")


# Re-build the rules-section text via the production formatter so the
# eval system prompt stays aligned with the contract the harness ships
# (no drift between eval prompt and production prompt). The dir is
# interpolated at sample setup time below.


def _build_eval_system_prompt(memory_dir: Path, memory_index_content: str | None) -> str:
    """Mirror :func:`prompts.system._format_combined_memory_section` for
    a fully-isolated eval system prompt (no Tools / Environment /
    CLAUDE.md sections injected — only the memory rules + index).

    This deliberately produces a smaller prompt than production so
    scorers reason about a focused causal surface: the LLM sees ONLY
    the memory contract, then a user message. Whether it writes /
    indexes / skips is fully attributable to the contract + the
    sample's user message.
    """
    # Local import to avoid circular module load (system.py imports
    # from eval.* in a future phase via the printers refactor).
    from openharness.prompts.memory import format_memory_rules_section

    rules = format_memory_rules_section(memory_dir)
    body = (memory_index_content or "").strip()
    if body:
        index_block = f"### Memory Index\n\n```md\n{body}\n```"
    else:
        index_block = "### Memory Index\n\n*(MEMORY.md is empty — no memories yet)*"
    return f"{rules}\n\n{index_block}"


_WRITE_TOOL = ToolSpec(
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

_EDIT_TOOL = ToolSpec(
    name="Edit",
    description="Replace exact old_string with new_string in a file at an absolute path.",
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

_READ_TOOL = ToolSpec(
    name="Read",
    description="Read a file at an absolute path.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
        },
        "required": ["file_path"],
    },
)


# ---------------------------------------------------------------------------
# Frontmatter validator (same regex shape the spike used — locked here)
# ---------------------------------------------------------------------------


FRONTMATTER_RE = re.compile(
    r"^---\s*\nname:\s*(?P<name>.+?)\s*\n"
    r"description:\s*(?P<description>.+?)\s*\n"
    r"metadata:\s*\n\s*type:\s*(?P<type>\w+)\s*\n---",
    re.MULTILINE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Sample + Output schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryDecisionSample:
    """One eval case probing decision surface #4 (inline decision).

    ``shape`` ∈ {"cold-start", "warm-start", "trivial-skip"}. Drives
    scorer's verdict logic (cold-start accepts Write on MEMORY.md;
    warm-start treats Write on MEMORY.md as destructive overwrite risk).

    ``pre_populated_files`` — when non-empty, the runner writes these
    into the per-sample memory dir BEFORE the LLM is called. Used to
    construct warm-start scenarios with an existing MEMORY.md + a few
    seed ``.md`` entries.

    ``user_msg`` — the user message that triggers the inline decision.
    Conversation is a single user turn (LLM has the system prompt with
    memory rules + the seeded index, then this user message).

    ``expect_write`` — whether the scenario should result in a new
    memory being written. Trivial-skip cases set this False.

    ``expected_memory_type`` — when ``expect_write=True``, the
    canonical type the rubric should accept. The LLM-judge rubric
    accepts multiple defensible readings (e.g., a preference might
    legitimately be ``user`` OR ``feedback``), so this is the
    *baseline* expectation, not the only valid answer.
    """

    case_id: str
    capability: str  # e.g., "M1-judge-preference"
    shape: str  # "cold-start" | "warm-start" | "trivial-skip"
    user_msg: str
    expect_write: bool
    expected_memory_type: str | None
    pre_populated_files: dict[str, str] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class MemoryDecisionOutput:
    """LLM output for one memory_decision sample.

    Carries the parsed tool_use blocks the LLM emitted across ALL
    turns plus the final free text. Multi-turn means the LLM may
    Read existing memory files, see the results, and then decide to
    Write — scorers analyze the **aggregate** tool_uses sequence to
    classify the decision (PASS / PARTIAL-chain-incomplete /
    FAIL-drift / FAIL-overwrite).

    ``turn_count`` records how many LLM round-trips happened in the
    loop. Useful for debugging "ran out of turns" scenarios (capped
    at :data:`_MAX_TURNS` per :func:`infer_memory_decision`).

    ``memory_dir`` is the fixture directory the infer call used —
    scorers compare tool_use ``file_path`` values against it to
    distinguish memory-dir writes from outside-the-fixture stray paths
    (the latter is a tool-misuse signal).
    """

    tool_uses: tuple[ToolUseBlock, ...]
    text: str
    memory_dir: Path
    turn_count: int = 1


# ---------------------------------------------------------------------------
# Per-sample fixture setup / teardown
# ---------------------------------------------------------------------------


def _resolve_sample_dir(case_id: str) -> Path:
    """Per-case dir under the eval root — keeps cases isolated so a
    run can re-execute without cross-pollution.

    Lives under ``/tmp`` so the user's real ``~/.openharness/memory/``
    tree is untouched even on a misbehaving run.
    """
    return _MEMORY_DIR_PLACEHOLDER / case_id


def _setup_fixture(sample: MemoryDecisionSample) -> Path:
    """Create the per-sample memory dir and seed any pre-populated files.

    Idempotent across re-runs — the dir is removed if present and
    rebuilt to the requested state. Returns the resolved dir path.
    """
    target = _resolve_sample_dir(sample.case_id)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for filename, content in sample.pre_populated_files.items():
        (target / filename).write_text(content, encoding="utf-8")
    return target


def _read_memory_index_for_eval(memory_dir: Path) -> str | None:
    """Read MEMORY.md for the eval's system-prompt injection.

    Mirrors :func:`cli._load_memory_index_for_injection` but without
    the 200-line cap WARN — the eval explicitly constructs fixtures
    well under cap, so a truncation event would be a test bug, not
    a production signal. Returns ``None`` if MEMORY.md doesn't exist
    (cold-start case).
    """
    index = memory_dir / "MEMORY.md"
    if not index.exists():
        return None
    return index.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Infer call — drive the LLM with the eval system prompt + tools + user msg
# ---------------------------------------------------------------------------


# Hard cap on the eval's tool-execution loop. Real CC sessions can run
# 50+ turns; for the memory_decision eval the canonical flow is
# Read → Write → (optional) Edit, all done in ≤ 3 turns. Capping at 6
# leaves headroom for retries / clarification asks but prevents
# runaway loops on a misbehaving model.
_MAX_TURNS = 6

# Cap Read tool_result bodies to keep multi-turn prompts under
# control. Memory files are tiny by design (the 200-line MEMORY.md
# cap from D36.8 + frontmatter+short body for individual entries) so
# 4 KB is generous.
_READ_TOOL_RESULT_MAX_CHARS = 4096


def _execute_tool_call(tool_use: ToolUseBlock, memory_dir: Path) -> ToolResultBlock:
    """Run one tool_use against the fixture dir and return its tool_result.

    Write / Edit modify the fixture so subsequent Reads see the
    updated state — same shape the production engine takes, just
    scoped to ``/tmp/oh_eval_memory_decision/<case>``. Failures (file
    not found, edit anchor missing, etc.) produce ``is_error=True``
    tool_results so the LLM can recover the same way it would in
    production. Tool execution is deliberately minimal — the eval's
    purpose is to observe the LLM's *decision* shape, not to test the
    Write / Edit tool implementations (those have their own unit
    tests in ``tests/tools/``).
    """
    name = tool_use.name
    input_data = tool_use.input

    if name == "Read":
        file_path = input_data.get("file_path", "")
        path = Path(file_path)
        if not path.is_absolute() or not path.exists():
            return ToolResultBlock(
                type="tool_result",
                tool_use_id=tool_use.id,
                content=f"Error: file not found: {file_path}",
                is_error=True,
            )
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResultBlock(
                type="tool_result",
                tool_use_id=tool_use.id,
                content=f"Error reading {file_path}: {exc}",
                is_error=True,
            )
        if len(content) > _READ_TOOL_RESULT_MAX_CHARS:
            content = content[:_READ_TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"
        return ToolResultBlock(
            type="tool_result",
            tool_use_id=tool_use.id,
            content=content,
            is_error=False,
        )

    if name == "Write":
        file_path = input_data.get("file_path", "")
        content_to_write = input_data.get("content", "")
        path = Path(file_path)
        if not path.is_absolute() or (memory_dir not in path.parents and path != memory_dir):
            # Reject writes outside the per-sample fixture so a stray
            # tool call can't leak onto the host filesystem.
            return ToolResultBlock(
                type="tool_result",
                tool_use_id=tool_use.id,
                content=(
                    f"Error: refusing to write outside eval fixture dir; "
                    f"got {file_path}, expected under {memory_dir}"
                ),
                is_error=True,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content_to_write, encoding="utf-8")
        return ToolResultBlock(
            type="tool_result",
            tool_use_id=tool_use.id,
            content=f"File written: {file_path}",
            is_error=False,
        )

    if name == "Edit":
        file_path = input_data.get("file_path", "")
        old_string = input_data.get("old_string", "")
        new_string = input_data.get("new_string", "")
        path = Path(file_path)
        if not path.is_absolute() or (memory_dir not in path.parents and path != memory_dir):
            return ToolResultBlock(
                type="tool_result",
                tool_use_id=tool_use.id,
                content=(f"Error: refusing to edit outside eval fixture dir; got {file_path}"),
                is_error=True,
            )
        if not path.exists():
            return ToolResultBlock(
                type="tool_result",
                tool_use_id=tool_use.id,
                content=f"Error: cannot Edit non-existent file {file_path}",
                is_error=True,
            )
        existing = path.read_text(encoding="utf-8")
        if old_string not in existing:
            return ToolResultBlock(
                type="tool_result",
                tool_use_id=tool_use.id,
                content=(f"Error: old_string not found in {file_path} — Edit cannot proceed"),
                is_error=True,
            )
        new_content = existing.replace(old_string, new_string, 1)
        path.write_text(new_content, encoding="utf-8")
        return ToolResultBlock(
            type="tool_result",
            tool_use_id=tool_use.id,
            content=f"Edit applied to {file_path}",
            is_error=False,
        )

    return ToolResultBlock(
        type="tool_result",
        tool_use_id=tool_use.id,
        content=f"Error: unknown tool {name!r}",
        is_error=True,
    )


async def _stream_one_turn(
    request: ApiMessageRequest,
    api_client: OpenAICompatibleApiClient,
) -> tuple[list[ToolUseBlock], list[TextBlock], str | None]:
    """Run one LLM round-trip; return (tool_uses, text_blocks, stop_reason)."""
    completion_event = None
    async for event in api_client.stream_message(request):
        if type(event).__name__ == "ApiMessageCompleteEvent":
            completion_event = event
            break
    if completion_event is None:
        return [], [], None

    tool_uses: list[ToolUseBlock] = []
    text_blocks: list[TextBlock] = []
    for block in completion_event.message.content:
        if isinstance(block, ToolUseBlock):
            tool_uses.append(block)
        elif isinstance(block, TextBlock):
            text_blocks.append(block)
    return tool_uses, text_blocks, completion_event.stop_reason


async def infer_memory_decision(
    *,
    sample: MemoryDecisionSample,
    api_client: OpenAICompatibleApiClient,
    model: str,
    max_tokens: int = 2048,
) -> MemoryDecisionOutput:
    """Multi-turn LLM infer with real tool execution against the fixture.

    Loop:

    1. Set up the per-sample fixture dir + read seeded MEMORY.md.
    2. Build the eval system prompt (CC ``## Memory`` rules + index).
    3. Issue the initial user message (``sample.user_msg``).
    4. Stream the LLM response; collect tool_use + text blocks.
    5. If ``stop_reason == "tool_use"`` and we are below
       :data:`_MAX_TURNS`, execute each tool against the fixture
       (real Write/Edit/Read against ``/tmp/oh_eval_memory_decision/<case>``)
       and append the assistant message + tool_results, then loop.
       Otherwise, exit.

    The accumulated ``tool_uses`` across all turns is what scorers
    analyze. ``turn_count`` records how many round-trips fired.

    Tool execution is real but scoped to the fixture dir — Write
    outside it gets rejected with an error tool_result, same as
    production's path-confinement check. This is critical: it means
    the LLM can complete the natural ``Read → judge → Write → Edit``
    chain that the canonical CC memory pattern requires, instead of
    being cut off after the first tool_use as the single-turn
    spike was.
    """
    memory_dir = _setup_fixture(sample)
    memory_index_content = _read_memory_index_for_eval(memory_dir)
    system_prompt = _build_eval_system_prompt(memory_dir, memory_index_content)

    messages: list[ConversationMessage] = [
        ConversationMessage(
            role="user",
            content=[TextBlock(text=sample.user_msg)],
        ),
    ]
    all_tool_uses: list[ToolUseBlock] = []
    text_parts: list[str] = []
    turn_count = 0

    for _ in range(_MAX_TURNS):
        turn_count += 1
        request = ApiMessageRequest(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            tools=[_WRITE_TOOL, _EDIT_TOOL, _READ_TOOL],
            stream=True,
        )
        tool_uses, text_blocks, stop_reason = await _stream_one_turn(request, api_client)
        all_tool_uses.extend(tool_uses)
        text_parts.extend(b.text for b in text_blocks)

        if stop_reason != "tool_use" or not tool_uses:
            # Natural end or no actionable tool_use — exit the loop.
            break

        # Append the assistant message (mixed text + tool_use) and a
        # user message with the tool_results, then loop.
        assistant_blocks: list[Any] = [*text_blocks, *tool_uses]
        messages.append(ConversationMessage(role="assistant", content=assistant_blocks))
        tool_results = [_execute_tool_call(tu, memory_dir) for tu in tool_uses]
        messages.append(ConversationMessage(role="user", content=list(tool_results)))

    return MemoryDecisionOutput(
        tool_uses=tuple(all_tool_uses),
        text="".join(text_parts),
        memory_dir=memory_dir,
        turn_count=turn_count,
    )


# ---------------------------------------------------------------------------
# Cassette wrapper — replay-friendly + record support (mirrors
# ``cassetted_infer_focus_state`` in :mod:`openharness.eval.cassette`)
# ---------------------------------------------------------------------------


def _summarize_sample_for_cassette(sample: MemoryDecisionSample) -> str:
    """One-line digest stored on the cassette so a human eyeball can
    confirm replay-mode is loading the matching sample."""
    preview = sample.user_msg[:120].replace("\n", " ")
    return f"[{sample.shape}] {preview}"


def _serialize_output_for_cassette(output: MemoryDecisionOutput) -> dict[str, Any]:
    """Cassette payload — captures everything scorers downstream need.

    Note (multi-turn): we record the AGGREGATE tool_uses list across
    all turns and the FINAL text. Replay reconstructs the same
    aggregate output without re-running the LLM, so scorers see
    byte-identical input. The trajectory of per-turn LLM responses is
    NOT reconstructable from the cassette — that's intentional, the
    eval's contract is the aggregate decision sequence not the
    intermediate steps.
    """
    return {
        "tool_uses": [
            {"name": t.name, "id": t.id, "input": dict(t.input)} for t in output.tool_uses
        ],
        "text": output.text,
        "turn_count": output.turn_count,
        # memory_dir is fixture-derived (case_id) so not stored;
        # re-resolved on replay.
    }


def _deserialize_output(case_id: str, payload: dict[str, Any]) -> MemoryDecisionOutput:
    tool_uses = tuple(
        ToolUseBlock(
            type="tool_use",
            id=entry["id"],
            name=entry["name"],
            input=entry["input"],
        )
        for entry in payload["tool_uses"]
    )
    return MemoryDecisionOutput(
        tool_uses=tool_uses,
        text=payload.get("text", ""),
        memory_dir=_resolve_sample_dir(case_id),
        turn_count=payload.get("turn_count", 1),
    )


async def cassetted_infer_memory_decision(
    *,
    sample: MemoryDecisionSample,
    api_client: OpenAICompatibleApiClient,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
) -> MemoryDecisionOutput:
    """Wrap :func:`infer_memory_decision` with the Stage 4 cassette layer.

    Cassette key namespace: ``kind="memory_decision_infer"`` so the
    payloads sit in their own subdirectory under the cassette root
    (parallel to focus_state's ``kind="infer"``).
    """
    if cassette_store is None or cassette_mode == "live":
        return await infer_memory_decision(sample=sample, api_client=api_client, model=model)

    key = CassetteKey(case_id=sample.case_id, model=model, kind="memory_decision_infer")

    if cassette_mode == "replay":
        record = cassette_store.load(key)
        if record is None:
            raise CassetteMissingError(
                f"Cassette missing: case={sample.case_id} "
                f"kind=memory_decision_infer model={model}. "
                f"Run with OPENHARNESS_EVAL_MODE=record first."
            )
        # Side-effect: still set up the fixture dir so scorers that
        # might inspect on-disk state (none currently do, but the
        # contract is symmetrical with live mode) see a consistent
        # snapshot.
        _setup_fixture(sample)
        return _deserialize_output(sample.case_id, record["response"])

    # record
    output = await infer_memory_decision(sample=sample, api_client=api_client, model=model)
    cassette_store.save(
        key=key,
        request_summary=_summarize_sample_for_cassette(sample),
        response=_serialize_output_for_cassette(output),
    )
    return output


# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------


def load_memory_decision_dataset(path: Path) -> list[MemoryDecisionSample]:
    """Parse ``evals/memory_decision/dataset.yaml`` into samples.

    YAML schema (top level dict with ``samples`` key):

    .. code-block:: yaml

        samples:
          - case_id: M1-cold-preference
            capability: M-judge-preference
            shape: cold-start | warm-start | trivial-skip
            user_msg: "I prefer terse responses without trailing summaries"
            expect_write: true
            expected_memory_type: feedback | user | project | reference | null
            pre_populated_files:                # optional, warm-start only
              MEMORY.md: |
                # Memory index
                ...
              existing-pref.md: |
                ---
                ...
            notes: |
              ...
    """
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    samples: list[MemoryDecisionSample] = []
    for entry in data["samples"]:
        samples.append(
            MemoryDecisionSample(
                case_id=entry["case_id"],
                capability=entry["capability"],
                shape=entry["shape"],
                user_msg=entry["user_msg"],
                expect_write=bool(entry["expect_write"]),
                expected_memory_type=entry.get("expected_memory_type"),
                pre_populated_files=entry.get("pre_populated_files") or {},
                notes=entry.get("notes", ""),
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryDecisionCaseResult:
    """Mirrors :class:`openharness.eval.runner.CaseResult` for memory_decision."""

    sample: MemoryDecisionSample
    output: MemoryDecisionOutput
    scores: list[Score]


async def run_memory_decision_eval(
    dataset_path: Path,
    scorers: list[Any],  # MemoryDecisionScorer-like (duck-typed)
    client: OpenAICompatibleApiClient,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
) -> list[MemoryDecisionCaseResult]:
    """End-to-end: load dataset → for each sample run LLM + all scorers.

    Mirrors :func:`openharness.eval.runner.run_eval` shape but drives
    :func:`cassetted_infer_memory_decision` instead of focus_state's
    infer call. Scorers are duck-typed: each must have ``score(sample,
    output) -> Score`` and ``dim`` property.
    """
    samples = load_memory_decision_dataset(dataset_path)
    store: CassetteStore | None = (
        CassetteStore(cassette_root) if cassette_root is not None else None
    )
    results: list[MemoryDecisionCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_memory_decision(
            sample=sample,
            api_client=client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = []
        for scorer in scorers:
            score = await scorer.score(sample, output)
            scores.append(score)
        results.append(MemoryDecisionCaseResult(sample=sample, output=output, scores=scores))
    return results
