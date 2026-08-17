"""Model-behavior eval for the typed durable-memory decision surface."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from openharness.eval.cassette import (
    CassetteKey,
    CassetteMissingError,
    CassetteMode,
    CassetteStore,
)
from openharness.eval.selection import select_cases
from openharness.memory import FilesystemMemoryStore
from openharness.protocols.content import ContentBlock, TextBlock, ToolResultBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.tools import register_memory_tools
from openharness.tools.base import ToolExecutionContext, ToolRegistry

if TYPE_CHECKING:
    from openharness.api import OpenAICompatibleApiClient
    from openharness.eval.protocol import Score


_MEMORY_DIR_PLACEHOLDER = Path("/tmp/oh_eval_memory_decision")
_MAX_TURNS = 6


def _build_eval_system_prompt(memory_dir: Path, memory_index_content: str | None) -> str:
    """Build the same typed-memory contract used by production."""
    from openharness.prompts.memory import format_memory_rules_section

    rules = format_memory_rules_section(memory_dir)
    body = (memory_index_content or "").strip()
    if body:
        index_block = f"### Memory Index\n\n```md\n{body}\n```"
    else:
        index_block = "### Memory Index\n\n*(MEMORY.md is empty — no memories yet)*"
    return f"{rules}\n\n{index_block}"


@dataclass(frozen=True)
class MemoryDecisionSample:
    case_id: str
    capability: str
    shape: str
    user_msg: str
    expect_write: bool
    expected_memory_type: str | None
    pre_populated_files: dict[str, str] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class MemoryDecisionOutput:
    tool_uses: tuple[ToolUseBlock, ...]
    text: str
    memory_dir: Path
    turn_count: int = 1
    persisted_names: tuple[str, ...] = ()
    persisted_record_hashes: tuple[tuple[str, str], ...] = ()


def _resolve_sample_dir(case_id: str) -> Path:
    return _MEMORY_DIR_PLACEHOLDER / case_id


def _setup_fixture(sample: MemoryDecisionSample) -> Path:
    target = _resolve_sample_dir(sample.case_id)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for filename, content in sample.pre_populated_files.items():
        (target / filename).write_text(content, encoding="utf-8")
    return target


def _memory_registry(store: FilesystemMemoryStore) -> ToolRegistry:
    registry = ToolRegistry()
    register_memory_tools(registry, store)
    return registry


def _persisted_record_hashes(memory_dir: Path) -> tuple[tuple[str, str], ...]:
    """Fingerprint durable records; the generated index is rebuildable state."""
    return tuple(
        (path.name, sha256(path.read_bytes()).hexdigest())
        for path in sorted(memory_dir.glob("*.md"))
        if path.name != "MEMORY.md"
    )


async def _execute_tool_call(
    tool_use: ToolUseBlock,
    registry: ToolRegistry,
    memory_dir: Path,
) -> ToolResultBlock:
    """Execute the real typed tool against the isolated fixture store."""
    try:
        tool = registry.get(tool_use.name)
    except KeyError:
        return ToolResultBlock(
            type="tool_result",
            tool_use_id=tool_use.id,
            content=f"Error: unknown tool {tool_use.name!r}",
            is_error=True,
        )
    try:
        args = tool.input_model.model_validate(tool_use.input)
    except ValidationError as exc:
        return ToolResultBlock(
            type="tool_result",
            tool_use_id=tool_use.id,
            content=f"Error: invalid {tool_use.name} input: {exc}",
            is_error=True,
        )
    result = await tool.execute(args, ToolExecutionContext(cwd=memory_dir))
    return ToolResultBlock(
        type="tool_result",
        tool_use_id=tool_use.id,
        content=result.output,
        is_error=result.is_error,
    )


async def _stream_one_turn(
    request: ApiMessageRequest,
    api_client: OpenAICompatibleApiClient,
) -> tuple[list[ToolUseBlock], list[TextBlock], str | None]:
    completion: ApiMessageCompleteEvent | None = None
    async for event in api_client.stream_message(request):
        if isinstance(event, ApiMessageCompleteEvent):
            completion = event
            break
    if completion is None:
        return [], [], None
    tool_uses = [block for block in completion.message.content if isinstance(block, ToolUseBlock)]
    text_blocks = [block for block in completion.message.content if isinstance(block, TextBlock)]
    return tool_uses, text_blocks, completion.stop_reason


async def infer_memory_decision(
    *,
    sample: MemoryDecisionSample,
    api_client: OpenAICompatibleApiClient,
    model: str,
    max_tokens: int = 2048,
) -> MemoryDecisionOutput:
    """Drive the model and execute real typed Memory tools in a temp store."""
    memory_dir = _setup_fixture(sample)
    store = FilesystemMemoryStore(project_dir=memory_dir)
    registry = _memory_registry(store)
    system_prompt = _build_eval_system_prompt(
        memory_dir,
        store.render_index(max_entries=200),
    )
    messages: list[ConversationMessage] = [
        ConversationMessage(role="user", content=[TextBlock(text=sample.user_msg)])
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
            tools=registry.to_api_schema(),
            stream=True,
        )
        tool_uses, text_blocks, stop_reason = await _stream_one_turn(request, api_client)
        all_tool_uses.extend(tool_uses)
        text_parts.extend(block.text for block in text_blocks)
        if stop_reason != "tool_use" or not tool_uses:
            break
        messages.append(ConversationMessage(role="assistant", content=[*text_blocks, *tool_uses]))
        tool_results: list[ContentBlock] = [
            await _execute_tool_call(tool_use, registry, memory_dir) for tool_use in tool_uses
        ]
        messages.append(ConversationMessage(role="user", content=tool_results))

    return MemoryDecisionOutput(
        tool_uses=tuple(all_tool_uses),
        text="".join(text_parts),
        memory_dir=memory_dir,
        turn_count=turn_count,
        persisted_names=tuple(sorted(store.discover())),
        persisted_record_hashes=_persisted_record_hashes(memory_dir),
    )


def _summarize_sample_for_cassette(sample: MemoryDecisionSample) -> str:
    preview = sample.user_msg[:120].replace("\n", " ")
    return f"[{sample.shape}] {preview}"


def _serialize_output_for_cassette(output: MemoryDecisionOutput) -> dict[str, Any]:
    return {
        "tool_uses": [
            {"name": tool.name, "id": tool.id, "input": dict(tool.input)}
            for tool in output.tool_uses
        ],
        "text": output.text,
        "turn_count": output.turn_count,
        "persisted_names": list(output.persisted_names),
        "persisted_record_hashes": [list(item) for item in output.persisted_record_hashes],
    }


def _deserialize_output(case_id: str, payload: dict[str, Any]) -> MemoryDecisionOutput:
    return MemoryDecisionOutput(
        tool_uses=tuple(
            ToolUseBlock(
                type="tool_use",
                id=entry["id"],
                name=entry["name"],
                input=entry["input"],
            )
            for entry in payload["tool_uses"]
        ),
        text=payload.get("text", ""),
        memory_dir=_resolve_sample_dir(case_id),
        turn_count=payload.get("turn_count", 1),
        persisted_names=tuple(payload.get("persisted_names", ())),
        persisted_record_hashes=tuple(
            (str(item[0]), str(item[1])) for item in payload.get("persisted_record_hashes", ())
        ),
    )


async def _replay_persisted_state(
    sample: MemoryDecisionSample,
    output: MemoryDecisionOutput,
) -> MemoryDecisionOutput:
    """Execute recorded typed operations against a fresh deterministic fixture."""
    memory_dir = _setup_fixture(sample)
    store = FilesystemMemoryStore(project_dir=memory_dir)
    registry = _memory_registry(store)
    for tool_use in output.tool_uses:
        await _execute_tool_call(tool_use, registry, memory_dir)
    return replace(
        output,
        memory_dir=memory_dir,
        persisted_names=tuple(sorted(store.discover())),
        persisted_record_hashes=_persisted_record_hashes(memory_dir),
    )


async def cassetted_infer_memory_decision(
    *,
    sample: MemoryDecisionSample,
    api_client: OpenAICompatibleApiClient,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
) -> MemoryDecisionOutput:
    if cassette_store is None or cassette_mode == "live":
        return await infer_memory_decision(sample=sample, api_client=api_client, model=model)

    key = CassetteKey(case_id=sample.case_id, model=model, kind="memory_decision_infer")
    if cassette_mode == "replay":
        record = cassette_store.load(key)
        if record is None:
            raise CassetteMissingError(
                f"Cassette missing: case={sample.case_id} "
                f"kind=memory_decision_infer model={model}. "
                "Run with --mode record first."
            )
        output = _deserialize_output(sample.case_id, record["response"])
        return await _replay_persisted_state(sample, output)

    output = await infer_memory_decision(sample=sample, api_client=api_client, model=model)
    cassette_store.save(
        key=key,
        request_summary=_summarize_sample_for_cassette(sample),
        response=_serialize_output_for_cassette(output),
    )
    return output


def load_memory_decision_dataset(path: Path) -> list[MemoryDecisionSample]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
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
        for entry in data["samples"]
    ]


@dataclass(frozen=True)
class MemoryDecisionCaseResult:
    sample: MemoryDecisionSample
    output: MemoryDecisionOutput
    scores: list[Score]


async def run_memory_decision_eval(
    dataset_path: Path,
    scorers: list[Any],
    client: OpenAICompatibleApiClient,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
    case_id: str | None = None,
) -> list[MemoryDecisionCaseResult]:
    samples = select_cases(load_memory_decision_dataset(dataset_path), case_id)
    cassette_store = CassetteStore(cassette_root) if cassette_root is not None else None
    results: list[MemoryDecisionCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_memory_decision(
            sample=sample,
            api_client=client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=cassette_store,
        )
        scores = [await scorer.score(sample, output) for scorer in scorers]
        results.append(MemoryDecisionCaseResult(sample=sample, output=output, scores=scores))
    return results
