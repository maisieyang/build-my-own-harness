"""error_feedback eval consumer — decision surface #3, A5+A6 (D41 P1 首开).

Probes the single next step after the engine feeds back an ``is_error``
tool result: a permission denial (A5) or a tool error (A6). Planted
histories use the engine's REAL error formats (``_dispatch_one``'s
"permission denied: ...", "tool not found: ...", "invalid input for ...")
and, for A5, the production checker's denial texts — including an
old-format/new-format pair so the Sprint 2 F4 message improvement is
measured as a controlled condition (条件戳纪律).

Case sources per D41.6 flywheel: astropy-14182 death chain (benchmark),
F3 fizzbuzz reroute + F4 config fabrication (dogfood 2026-07-12).

Consumer pattern per D35.6 — mirrors skill_trigger; single LLM round-trip,
no tool execution.
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
from openharness.prompts.system import EnvironmentInfo, build_system_prompt
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from openharness.api.client import SupportsStreamingMessages
    from openharness.eval.protocol import Score

_EVAL_ENV = EnvironmentInfo(
    os_name="Linux",
    os_version="6.1",
    shell="bash",
    cwd=Path("/workspace"),
    python_version="3.12",
)


@dataclass(frozen=True)
class PlantedCall:
    """The failed call planted in history — the verbatim-retry referent."""

    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ErrorFeedbackSample:
    """One planted-error case. ``forbidden_text``: substrings that must NOT
    appear in the model's text (case-insensitive) — fabricated-guidance
    tripwires authored per case."""

    case_id: str
    capability: str
    shape: str
    messages: list[ConversationMessage]
    planted: PlantedCall
    forbidden_text: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class ErrorFeedbackOutput:
    """The model's single post-error decision turn."""

    tool_uses: tuple[ToolUseBlock, ...]
    text: str
    stop_reason: str | None


def load_error_feedback_dataset(path: Path) -> list[ErrorFeedbackSample]:
    """Load ``evals/error_feedback/dataset.yaml`` into typed samples."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    samples: list[ErrorFeedbackSample] = []
    for entry in data["samples"]:
        messages = [ConversationMessage.model_validate(m) for m in entry["messages"]]
        planted = PlantedCall(
            name=entry["planted"]["name"],
            input=dict(entry["planted"]["input"]),
        )
        samples.append(
            ErrorFeedbackSample(
                case_id=entry["case_id"],
                capability=entry["capability"],
                shape=entry["shape"],
                messages=messages,
                planted=planted,
                forbidden_text=tuple(entry["forbidden_text"]),
                notes=entry["notes"],
            )
        )
    return samples


async def infer_error_feedback(
    *,
    sample: ErrorFeedbackSample,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int = 1024,
) -> ErrorFeedbackOutput:
    """One round-trip against the production registry + prompt; the planted
    history goes through verbatim."""
    registry = create_default_tool_registry()
    request = ApiMessageRequest(
        model=model,
        max_tokens=max_tokens,
        system=build_system_prompt(registry.to_api_schema(), _EVAL_ENV),
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
        return ErrorFeedbackOutput(tool_uses=(), text="", stop_reason=None)
    tool_uses = tuple(
        block for block in completion.message.content if isinstance(block, ToolUseBlock)
    )
    text = "".join(
        block.text for block in completion.message.content if isinstance(block, TextBlock)
    )
    return ErrorFeedbackOutput(tool_uses=tool_uses, text=text, stop_reason=completion.stop_reason)


def _summarize_sample(sample: ErrorFeedbackSample) -> str:
    for message in sample.messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                return f"{sample.case_id}: {block.text}"
    return sample.case_id


def _serialize_output(output: ErrorFeedbackOutput) -> dict[str, Any]:
    return {
        "tool_uses": [{"id": tu.id, "name": tu.name, "input": tu.input} for tu in output.tool_uses],
        "text": output.text,
        "stop_reason": output.stop_reason,
    }


def _deserialize_output(payload: dict[str, Any]) -> ErrorFeedbackOutput:
    tool_uses = tuple(
        ToolUseBlock(id=tu["id"], name=tu["name"], input=tu["input"]) for tu in payload["tool_uses"]
    )
    return ErrorFeedbackOutput(
        tool_uses=tool_uses, text=payload["text"], stop_reason=payload["stop_reason"]
    )


async def cassetted_infer_error_feedback(
    *,
    sample: ErrorFeedbackSample,
    api_client: SupportsStreamingMessages | None,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
    max_tokens: int = 1024,
) -> ErrorFeedbackOutput:
    """Record / replay / live wrapper (D33.2: replay never falls back live)."""
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
    output = await infer_error_feedback(
        sample=sample, api_client=api_client, model=model, max_tokens=max_tokens
    )
    if cassette_mode == "record":
        if cassette_store is None:
            msg = "record mode requires a cassette_store"
            raise ValueError(msg)
        cassette_store.save(key, _summarize_sample(sample), _serialize_output(output))
    return output


@dataclass(frozen=True)
class ErrorFeedbackCaseResult:
    """One case's complete result: input, model decision, all verdicts."""

    sample: ErrorFeedbackSample
    output: ErrorFeedbackOutput
    scores: list[Score]


async def run_error_feedback_eval(
    dataset_path: Path,
    scorers: list[Any],
    api_client: SupportsStreamingMessages | None,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
) -> list[ErrorFeedbackCaseResult]:
    """End-to-end: dataset → per-sample infer (cassette-aware) → all scorers."""
    samples = load_error_feedback_dataset(dataset_path)
    store = CassetteStore(cassette_root) if cassette_root is not None else None
    results: list[ErrorFeedbackCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_error_feedback(
            sample=sample,
            api_client=api_client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = [await scorer.score(sample, output) for scorer in scorers]
        results.append(ErrorFeedbackCaseResult(sample=sample, output=output, scores=scores))
    return results
