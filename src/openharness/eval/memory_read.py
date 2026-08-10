"""memory_read eval consumer — 决策面 #4 / C4(D41),memory READ 自选决策。

被测对象:**生产** prompt 组装 `build_system_prompt(memory_dir=, memory_index_
content=)` —— 渲染 `## Memory`(写规则 + MEMORY.md 索引)—— 加默认 Read 工具。
Phase 16 后 harness 不再代排相关性(relevance.py 退役),模型自扫索引自选 Read。

镜像 skill_trigger(C1):own Sample/Output/infer/loader,cassette 复用 substrate,
单步一次 round-trip、不执行 Read。fixture(MEMORY.md 索引 + memory slug 清单)是
dataset 的一部分,非用户内容。测的是**读的决策与选择**,非读到的内容。
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
from openharness.eval.selection import select_cases
from openharness.prompts.system import EnvironmentInfo, build_system_prompt
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from openharness.api.client import SupportsStreamingMessages
    from openharness.eval.protocol import Score

# 与 skill_trigger 同款机器中立环境(无本地路径泄漏)。
_EVAL_ENV = EnvironmentInfo(
    os_name="Linux",
    os_version="6.1",
    shell="bash",
    cwd=Path("/workspace"),
    python_version="3.12",
)

_FIXTURE_MEMORY_DIR = Path("/eval-fixture/memory")


@dataclass(frozen=True)
class MemoryFixture:
    """dataset 的共享 fixture:MEMORY.md 索引文本 + memory 文件 slug 清单
    (供 scorer 区分'memory 读'与'任务读')。"""

    index_content: str
    memory_dir: Path
    memory_slugs: tuple[str, ...]


@dataclass(frozen=True)
class MemoryReadSample:
    """一个 memory-read case。``expected_read_slug=None`` 断言 restraint:不读
    任何 memory(读任务文件允许)。``all_memory_slugs`` 从 fixture 复制,让
    scorer 自足判定路径是否指向 memory。"""

    case_id: str
    capability: str
    shape: str
    messages: list[ConversationMessage]
    expected_read_slug: str | None
    all_memory_slugs: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class MemoryReadOutput:
    """模型单次决策turn的产出(同 skill_trigger)。"""

    tool_uses: tuple[ToolUseBlock, ...]
    text: str
    stop_reason: str | None


def load_memory_read_dataset(path: Path) -> tuple[list[MemoryReadSample], MemoryFixture]:
    """Load ``evals/memory_read/dataset.yaml`` → (samples, fixture)。"""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    fixture_raw = data["fixture"]
    slugs = tuple(fixture_raw["memory_slugs"])
    fixture = MemoryFixture(
        index_content=fixture_raw["index_content"],
        memory_dir=_FIXTURE_MEMORY_DIR,
        memory_slugs=slugs,
    )
    samples: list[MemoryReadSample] = []
    for entry in data["samples"]:
        messages = [ConversationMessage.model_validate(m) for m in entry["messages"]]
        samples.append(
            MemoryReadSample(
                case_id=entry["case_id"],
                capability=entry["capability"],
                shape=entry["shape"],
                messages=messages,
                expected_read_slug=entry["expected_read_slug"],
                all_memory_slugs=slugs,
                notes=entry["notes"],
            )
        )
    return samples, fixture


def _build_subject(fixture: MemoryFixture) -> tuple[str, Any]:
    """生产 prompt + 默认 registry(含 Read)——镜像 cli.py:build_system_prompt
    带 memory_dir + memory_index_content 注入 `## Memory`(规则 + 索引)。"""
    registry = create_default_tool_registry()
    system = build_system_prompt(
        registry.to_api_schema(),
        _EVAL_ENV,
        memory_dir=fixture.memory_dir,
        memory_index_content=fixture.index_content,
    )
    return system, registry


async def infer_memory_read(
    *,
    sample: MemoryReadSample,
    fixture: MemoryFixture,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int = 1024,
) -> MemoryReadOutput:
    """一次 round-trip against 生产 prompt+registry subject。"""
    system, registry = _build_subject(fixture)
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
        return MemoryReadOutput(tool_uses=(), text="", stop_reason=None)

    tool_uses = tuple(
        block for block in completion.message.content if isinstance(block, ToolUseBlock)
    )
    text = "".join(
        block.text for block in completion.message.content if isinstance(block, TextBlock)
    )
    return MemoryReadOutput(tool_uses=tool_uses, text=text, stop_reason=completion.stop_reason)


# ---------------------------------------------------------------------------
# Cassette wrapper
# ---------------------------------------------------------------------------


def _summarize_sample(sample: MemoryReadSample) -> str:
    for message in sample.messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                return f"{sample.case_id}: {block.text}"
    return sample.case_id


def _serialize_output(output: MemoryReadOutput) -> dict[str, Any]:
    return {
        "tool_uses": [{"id": tu.id, "name": tu.name, "input": tu.input} for tu in output.tool_uses],
        "text": output.text,
        "stop_reason": output.stop_reason,
    }


def _deserialize_output(payload: dict[str, Any]) -> MemoryReadOutput:
    tool_uses = tuple(
        ToolUseBlock(id=tu["id"], name=tu["name"], input=tu["input"]) for tu in payload["tool_uses"]
    )
    return MemoryReadOutput(
        tool_uses=tool_uses, text=payload["text"], stop_reason=payload["stop_reason"]
    )


async def cassetted_infer_memory_read(
    *,
    sample: MemoryReadSample,
    fixture: MemoryFixture,
    api_client: SupportsStreamingMessages | None,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
) -> MemoryReadOutput:
    """Record / replay / live wrapper (D33.2: replay 永不落回 live)."""
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
    output = await infer_memory_read(
        sample=sample, fixture=fixture, api_client=api_client, model=model
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
class MemoryReadCaseResult:
    sample: MemoryReadSample
    output: MemoryReadOutput
    scores: list[Score]


async def run_memory_read_eval(
    dataset_path: Path,
    scorers: list[Any],
    api_client: SupportsStreamingMessages | None,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
    case_id: str | None = None,
) -> list[MemoryReadCaseResult]:
    """End-to-end: dataset → per-sample read decision (cassette-aware) → scorers."""
    samples, fixture = load_memory_read_dataset(dataset_path)
    samples = select_cases(samples, case_id)
    store = CassetteStore(cassette_root) if cassette_root is not None else None
    results: list[MemoryReadCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_memory_read(
            sample=sample,
            fixture=fixture,
            api_client=api_client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = [await scorer.score(sample, output) for scorer in scorers]
        results.append(MemoryReadCaseResult(sample=sample, output=output, scores=scores))
    return results
