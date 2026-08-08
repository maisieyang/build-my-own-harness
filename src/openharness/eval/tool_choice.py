"""tool_choice eval consumer — decision surface #2 (D41 P0).

Probes single-step tool selection + input construction quality of the
**production** subject: `create_default_tool_registry()` tool descriptions
assembled through `build_system_prompt`. Consumer pattern per D35.6: own
Sample/Output/infer/loader, cassette primitives reused from the substrate,
zero new framework.

Single-shot by design: exactly one LLM round-trip per case, no tool
execution. The TC4 self-correction case plants the error history in the
dataset (assistant unknown-tool call + engine-format ``tool not found``
result) so the assertion lands on the post-error step while the eval stays
one call per case.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openharness.eval.cassette import (
    CassetteKey,
    CassetteMissingError,
    CassetteMode,
    CassetteStore,
)
from openharness.prompts.system import (
    PLAN_MODE_PROMPT_SECTION,
    EnvironmentInfo,
    build_system_prompt,
)
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.repl import shape_plan_tool_registry
from openharness.tools import ToolRegistry, create_default_tool_registry

if TYPE_CHECKING:
    from openharness.api.client import SupportsStreamingMessages
    from openharness.eval.protocol import Score

# Synthetic, machine-neutral environment — the eval system prompt must not
# leak local paths (same hygiene bar as D40.3's no-local-paths assertion).
_EVAL_ENV = EnvironmentInfo(
    os_name="Linux",
    os_version="6.1",
    shell="bash",
    cwd=Path("/workspace"),
    python_version="3.12",
)


@dataclass(frozen=True)
class ToolChoiceSample:
    """One tool-choice case. ``expected_tool=None`` asserts zero tool calls
    (TC5 restraint); ``expected_input_contains`` maps input field → any-of
    substrings (case-insensitive)."""

    case_id: str
    capability: str
    shape: str
    messages: list[ConversationMessage]
    expected_tool: str | None
    expected_input_contains: dict[str, list[str]]
    forbidden_tools: tuple[str, ...]
    project_instructions: str | None
    tool_posture: str
    notes: str


@dataclass(frozen=True)
class ToolChoiceOutput:
    """What the model produced in its single decision turn."""

    tool_uses: tuple[ToolUseBlock, ...]
    text: str
    stop_reason: str | None


def load_tool_choice_dataset(path: Path) -> list[ToolChoiceSample]:
    """Load ``evals/tool_choice/dataset.yaml`` into typed samples.

    Missing required keys raise ``KeyError`` naming the field — a bad case
    fails at load time, not mid-run.
    """
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    samples: list[ToolChoiceSample] = []
    for entry in data["samples"]:
        messages = [ConversationMessage.model_validate(m) for m in entry["messages"]]
        tool_posture = entry.get("tool_posture", "default")
        if tool_posture not in {"default", "plan"}:
            raise ValueError(f"invalid tool_posture: {tool_posture!r}")
        samples.append(
            ToolChoiceSample(
                case_id=entry["case_id"],
                capability=entry["capability"],
                shape=entry["shape"],
                messages=messages,
                expected_tool=entry["expected_tool"],
                expected_input_contains=entry["expected_input_contains"],
                forbidden_tools=tuple(entry["forbidden_tools"]),
                project_instructions=entry.get("project_instructions"),
                tool_posture=tool_posture,
                notes=entry["notes"],
            )
        )
    return samples


def _build_eval_system_prompt(sample: ToolChoiceSample) -> str:
    registry = _registry_for_sample(sample)
    instruction_content = None
    if sample.project_instructions is not None:
        instruction_content = (
            "## Project Instructions\n\n"
            "### /workspace/AGENTS.md\n\n"
            f"```md\n{sample.project_instructions}\n```"
        )
    prompt = build_system_prompt(
        registry.to_api_schema(),
        _EVAL_ENV,
        project_instructions_content=instruction_content,
    )
    if sample.tool_posture == "plan":
        prompt = f"{prompt}\n\n{PLAN_MODE_PROMPT_SECTION}"
    return prompt


def _registry_for_sample(sample: ToolChoiceSample) -> ToolRegistry:
    registry = create_default_tool_registry()
    return shape_plan_tool_registry(registry) if sample.tool_posture == "plan" else registry


async def infer_tool_choice(
    *,
    sample: ToolChoiceSample,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int = 1024,
) -> ToolChoiceOutput:
    """One LLM round-trip against the production registry + system prompt.

    The sample's message history is passed through verbatim (planted-error
    cases included); the first complete event's content is the decision
    under evaluation.
    """
    registry = _registry_for_sample(sample)
    request = ApiMessageRequest(
        model=model,
        max_tokens=max_tokens,
        system=_build_eval_system_prompt(sample),
        messages=sample.messages,
        tools=registry.to_api_schema(),
        stream=True,
    )
    completion: ApiMessageCompleteEvent | None = None
    async for event in api_client.stream_message(request):
        if isinstance(event, ApiMessageCompleteEvent):
            completion = event
            break
    if completion is None:
        return ToolChoiceOutput(tool_uses=(), text="", stop_reason=None)

    tool_uses = tuple(
        block for block in completion.message.content if isinstance(block, ToolUseBlock)
    )
    text = "".join(
        block.text for block in completion.message.content if isinstance(block, TextBlock)
    )
    return ToolChoiceOutput(tool_uses=tool_uses, text=text, stop_reason=completion.stop_reason)


# ---------------------------------------------------------------------------
# Cassette wrapper — mirrors cassetted_infer_focus_state / memory_decision
# ---------------------------------------------------------------------------


def _summarize_sample(sample: ToolChoiceSample) -> str:
    for message in sample.messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                return f"{sample.case_id}: {block.text}"
    return sample.case_id


def _serialize_output(output: ToolChoiceOutput) -> dict[str, Any]:
    return {
        "tool_uses": [{"id": tu.id, "name": tu.name, "input": tu.input} for tu in output.tool_uses],
        "text": output.text,
        "stop_reason": output.stop_reason,
    }


def _deserialize_output(payload: dict[str, Any]) -> ToolChoiceOutput:
    tool_uses = tuple(
        ToolUseBlock(id=tu["id"], name=tu["name"], input=tu["input"]) for tu in payload["tool_uses"]
    )
    return ToolChoiceOutput(
        tool_uses=tool_uses,
        text=payload["text"],
        stop_reason=payload["stop_reason"],
    )


async def cassetted_infer_tool_choice(
    *,
    sample: ToolChoiceSample,
    api_client: SupportsStreamingMessages | None,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
    max_tokens: int = 1024,
) -> ToolChoiceOutput:
    """Record / replay / live wrapper around :func:`infer_tool_choice`.

    ``api_client`` may be ``None`` only in replay mode — replay never calls
    the LLM (D33.2: missing cassette raises, no silent live fallback).
    """
    key = CassetteKey(case_id=sample.case_id, model=model, kind="infer")

    if cassette_mode == "replay":
        if cassette_store is None:
            msg = "replay mode requires a cassette_store"
            raise ValueError(msg)
        record = cassette_store.load(key)
        if record is None:
            msg = f"no infer cassette for {key.relative_path} — run record mode first"
            raise CassetteMissingError(msg)
        payload: dict[str, Any] = record["response"]
        return _deserialize_output(payload)

    if api_client is None:
        msg = f"{cassette_mode} mode requires an api_client"
        raise ValueError(msg)
    output = await infer_tool_choice(
        sample=sample, api_client=api_client, model=model, max_tokens=max_tokens
    )
    if cassette_mode == "record":
        if cassette_store is None:
            msg = "record mode requires a cassette_store"
            raise ValueError(msg)
        cassette_store.save(key, _summarize_sample(sample), _serialize_output(output))
    return output


# ---------------------------------------------------------------------------
# Runner — load dataset, infer, score; returns structured results (no print)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolChoiceCaseResult:
    """One case's complete result: input, model decision, all verdicts."""

    sample: ToolChoiceSample
    output: ToolChoiceOutput
    scores: list[Score]


async def run_tool_choice_eval(
    dataset_path: Path,
    scorers: list[Any],
    api_client: SupportsStreamingMessages | None,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
) -> list[ToolChoiceCaseResult]:
    """End-to-end: dataset → per-sample infer (cassette-aware) → all scorers."""
    samples = load_tool_choice_dataset(dataset_path)
    store = CassetteStore(cassette_root) if cassette_root is not None else None
    results: list[ToolChoiceCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_tool_choice(
            sample=sample,
            api_client=api_client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = [await scorer.score(sample, output) for scorer in scorers]
        results.append(ToolChoiceCaseResult(sample=sample, output=output, scores=scores))
    return results
