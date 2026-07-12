"""skill_trigger eval consumer — decision surface #5 / C1 (D41 P0 第二个).

Probes single-step LoadSkill triggering: given the **production** prompt
assembly (`build_system_prompt` + `## Available Skills` catalog section +
LoadSkill tool description) over a fixture catalog, does the model call
LoadSkill when a task matches a skill, pick the exact slug, and stay away
when nothing matches?

Consumer pattern per D35.6: own Sample/Output/infer/loader, cassette
primitives reused from the substrate, zero new framework. Single-shot by
design — one LLM round-trip, no tool execution (the TS4 self-correction
case plants the unknown-slug error history in the dataset, engine-format
``is_error`` result included, so the assertion lands on the post-error
step while the eval stays one call per case).

The catalog is a **fixture** (part of the dataset, not user content):
4 synthetic skills echoing the finance-skills dogfood shape that drove
#5's P3→P0 reshuffle (D41.7).
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
from openharness.skills.model import Skill
from openharness.tools import create_default_tool_registry
from openharness.tools.load_skill import LoadSkillTool

if TYPE_CHECKING:
    from openharness.api.client import SupportsStreamingMessages
    from openharness.eval.protocol import Score

# Same machine-neutral environment hygiene as tool_choice (no local paths).
_EVAL_ENV = EnvironmentInfo(
    os_name="Linux",
    os_version="6.1",
    shell="bash",
    cwd=Path("/workspace"),
    python_version="3.12",
)

_FIXTURE_SOURCE = Path("/eval-fixture")


@dataclass(frozen=True)
class SkillTriggerSample:
    """One skill-trigger case. ``expected_slug=None`` asserts restraint:
    no LoadSkill call (other tool calls are ALLOWED — the task may
    legitimately need Edit/Bash; only the skill decision is under test)."""

    case_id: str
    capability: str
    shape: str
    messages: list[ConversationMessage]
    expected_slug: str | None
    notes: str


@dataclass(frozen=True)
class SkillTriggerOutput:
    """What the model produced in its single decision turn."""

    tool_uses: tuple[ToolUseBlock, ...]
    text: str
    stop_reason: str | None


class _FixtureSkillStore:
    """In-memory SkillStore over the dataset's fixture catalog."""

    def __init__(self, skills: dict[str, Skill]) -> None:
        self._skills = skills

    def discover(self) -> dict[str, Skill]:
        return dict(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)


def build_fixture_store(catalog: list[Skill]) -> _FixtureSkillStore:
    """Catalog list → SkillStore the prompt/tool layer consumes."""
    return _FixtureSkillStore({s.name: s for s in catalog})


def load_skill_trigger_dataset(path: Path) -> tuple[list[SkillTriggerSample], list[Skill]]:
    """Load ``evals/skill_trigger/dataset.yaml`` → (samples, fixture catalog).

    Missing required keys raise ``KeyError`` naming the field — a bad case
    fails at load time, not mid-run.
    """
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    catalog = [
        Skill(
            name=entry["name"],
            description=entry["description"],
            body=entry.get("body", "(eval fixture body)"),
            version=None,
            source_path=_FIXTURE_SOURCE,
        )
        for entry in data["catalog"]
    ]
    samples: list[SkillTriggerSample] = []
    for entry in data["samples"]:
        messages = [ConversationMessage.model_validate(m) for m in entry["messages"]]
        samples.append(
            SkillTriggerSample(
                case_id=entry["case_id"],
                capability=entry["capability"],
                shape=entry["shape"],
                messages=messages,
                expected_slug=entry["expected_slug"],
                notes=entry["notes"],
            )
        )
    return samples, catalog


def _build_subject(catalog: list[Skill]) -> tuple[str, Any]:
    """Production prompt + registry with LoadSkill wired — mirrors the
    cli.py bootstrap (`registry.register(LoadSkillTool(skill_store))` +
    `build_system_prompt(..., skill_store=...)`)."""
    store = build_fixture_store(catalog)
    registry = create_default_tool_registry()
    registry.register(LoadSkillTool(store))
    system = build_system_prompt(registry.to_api_schema(), _EVAL_ENV, skill_store=store)
    return system, registry


async def infer_skill_trigger(
    *,
    sample: SkillTriggerSample,
    catalog: list[Skill],
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int = 1024,
) -> SkillTriggerOutput:
    """One LLM round-trip against the production prompt+registry subject."""
    system, registry = _build_subject(catalog)
    request = ApiMessageRequest(
        model=model,
        max_tokens=max_tokens,
        system=system,
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
        return SkillTriggerOutput(tool_uses=(), text="", stop_reason=None)

    tool_uses = tuple(
        block for block in completion.message.content if isinstance(block, ToolUseBlock)
    )
    text = "".join(
        block.text for block in completion.message.content if isinstance(block, TextBlock)
    )
    return SkillTriggerOutput(tool_uses=tool_uses, text=text, stop_reason=completion.stop_reason)


# ---------------------------------------------------------------------------
# Cassette wrapper — mirrors cassetted_infer_tool_choice
# ---------------------------------------------------------------------------


def _summarize_sample(sample: SkillTriggerSample) -> str:
    for message in sample.messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                return f"{sample.case_id}: {block.text}"
    return sample.case_id


def _serialize_output(output: SkillTriggerOutput) -> dict[str, Any]:
    return {
        "tool_uses": [{"id": tu.id, "name": tu.name, "input": tu.input} for tu in output.tool_uses],
        "text": output.text,
        "stop_reason": output.stop_reason,
    }


def _deserialize_output(payload: dict[str, Any]) -> SkillTriggerOutput:
    tool_uses = tuple(
        ToolUseBlock(id=tu["id"], name=tu["name"], input=tu["input"]) for tu in payload["tool_uses"]
    )
    return SkillTriggerOutput(
        tool_uses=tool_uses,
        text=payload["text"],
        stop_reason=payload["stop_reason"],
    )


async def cassetted_infer_skill_trigger(
    *,
    sample: SkillTriggerSample,
    catalog: list[Skill],
    api_client: SupportsStreamingMessages | None,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
    max_tokens: int = 1024,
) -> SkillTriggerOutput:
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
    output = await infer_skill_trigger(
        sample=sample, catalog=catalog, api_client=api_client, model=model, max_tokens=max_tokens
    )
    if cassette_mode == "record":
        if cassette_store is None:
            msg = "record mode requires a cassette_store"
            raise ValueError(msg)
        cassette_store.save(key, _summarize_sample(sample), _serialize_output(output))
    return output


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillTriggerCaseResult:
    """One case's complete result: input, model decision, all verdicts."""

    sample: SkillTriggerSample
    output: SkillTriggerOutput
    scores: list[Score]


async def run_skill_trigger_eval(
    dataset_path: Path,
    scorers: list[Any],
    api_client: SupportsStreamingMessages | None,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
) -> list[SkillTriggerCaseResult]:
    """End-to-end: dataset → per-sample infer (cassette-aware) → all scorers."""
    samples, catalog = load_skill_trigger_dataset(dataset_path)
    store = CassetteStore(cassette_root) if cassette_root is not None else None
    results: list[SkillTriggerCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_skill_trigger(
            sample=sample,
            catalog=catalog,
            api_client=api_client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = [await scorer.score(sample, output) for scorer in scorers]
        results.append(SkillTriggerCaseResult(sample=sample, output=output, scores=scores))
    return results
