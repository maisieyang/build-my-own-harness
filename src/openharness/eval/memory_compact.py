"""memory_compact eval consumer — 决策面 #1 / B2(D45,fail-open 最高风险)。

被测对象:**生产** `full_compact`(services/compact.py 的 L4 9-slot 摘要),
原样调用不复制。种植事实回收 oracle(D45.1):dataset 每 case = 一段待压缩
历史 + 埋入的 must_recall 事实,压缩后判事实在不在 summary。

Consumer pattern 同 skill_trigger:own Sample/Output/infer/loader,cassette
primitives 复用 substrate,零新 framework。infer 真调 full_compact(不像
tool_choice 只看吐出的意图)——因为 B2 测的是摘要**结果**,必须真压缩。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from openharness.eval.cassette import (
    CassetteKey,
    CassetteMissingError,
    CassetteMode,
    CassetteStore,
)
from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.services.compact import full_compact

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api.client import SupportsStreamingMessages
    from openharness.eval.protocol import Score

_SUMMARY_MARKER = "[Conversation history summarized below — older messages elided]"


@dataclass(frozen=True)
class MemoryCompactSample:
    """一段待压缩历史 + 种植事实。``must_recall``:压缩后必须留存的关键
    事实(子串);``must_not_recall``:必须被丢弃的噪声。"""

    case_id: str
    capability: str
    messages: list[ConversationMessage]
    must_recall: tuple[str, ...]
    must_not_recall: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class MemoryCompactOutput:
    """压缩产出。``summary_text`` = 提取出的 9-slot 摘要文本;
    ``did_apply`` = full_compact 是否真的压缩了(False = 没跑成)。"""

    summary_text: str
    did_apply: bool


def load_memory_compact_dataset(path: Path) -> list[MemoryCompactSample]:
    """Load ``evals/memory_compact/dataset.yaml`` into typed samples."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    samples: list[MemoryCompactSample] = []
    for entry in data["samples"]:
        messages = [ConversationMessage.model_validate(m) for m in entry["messages"]]
        samples.append(
            MemoryCompactSample(
                case_id=entry["case_id"],
                capability=entry["capability"],
                messages=messages,
                must_recall=tuple(entry["must_recall"]),
                must_not_recall=tuple(entry.get("must_not_recall", [])),
                notes=entry.get("notes", ""),
            )
        )
    return samples


def _extract_summary_from_messages(new_messages: list[ConversationMessage], did_apply: bool) -> str:
    """从 full_compact 返回的 messages 里捞出摘要文本。full_compact 把摘要
    拼成一个 boundary marker + summary 的 user message;取 marker 之后的
    那条(们)拼起来。did_apply=False 时返回空串。"""
    if not did_apply:
        return ""
    parts: list[str] = []
    for m in new_messages:
        for b in m.content:
            if isinstance(b, TextBlock):
                t = b.text
                if _SUMMARY_MARKER in t:
                    t = t.split(_SUMMARY_MARKER, 1)[1]
                parts.append(t)
    return "\n".join(parts)


async def infer_memory_compact(
    *,
    sample: MemoryCompactSample,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int = 8192,
) -> MemoryCompactOutput:
    """真调生产 full_compact,提取摘要文本。

    ``max_tokens=8192``:适配参照模型 qwen-max 的输出上限(F16——生产
    full_compact 默认 20_000 超 qwen-max 的 8192 硬顶,是潜在可移植性 bug,
    生产用 qwen3.7-max 上限够高未爆;短对话的 9-slot 摘要 8192 绰绰有余,
    非掩盖而是用合身预算)。
    """
    new_messages, did_apply = await full_compact(
        list(sample.messages),
        model=model,
        api_client=api_client,
        max_tokens=max_tokens,
    )
    summary = _extract_summary_from_messages(new_messages, did_apply)
    return MemoryCompactOutput(summary_text=summary, did_apply=did_apply)


# ---------------------------------------------------------------------------
# Cassette wrapper
# ---------------------------------------------------------------------------


def _summarize_sample(sample: MemoryCompactSample) -> str:
    return f"{sample.case_id}: recall={sample.must_recall}"


def _serialize_output(output: MemoryCompactOutput) -> dict[str, Any]:
    return {"summary_text": output.summary_text, "did_apply": output.did_apply}


def _deserialize_output(payload: dict[str, Any]) -> MemoryCompactOutput:
    return MemoryCompactOutput(summary_text=payload["summary_text"], did_apply=payload["did_apply"])


async def cassetted_infer_memory_compact(
    *,
    sample: MemoryCompactSample,
    api_client: SupportsStreamingMessages | None,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
    max_tokens: int = 8192,
) -> MemoryCompactOutput:
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
    output = await infer_memory_compact(
        sample=sample, api_client=api_client, model=model, max_tokens=max_tokens
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
class MemoryCompactCaseResult:
    sample: MemoryCompactSample
    output: MemoryCompactOutput
    scores: list[Score]


async def run_memory_compact_eval(
    dataset_path: Path,
    scorers: list[Any],
    api_client: SupportsStreamingMessages | None,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
) -> list[MemoryCompactCaseResult]:
    """End-to-end: dataset → per-sample compact (cassette-aware) → scorers."""
    samples = load_memory_compact_dataset(dataset_path)
    store = CassetteStore(cassette_root) if cassette_root is not None else None
    results: list[MemoryCompactCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_memory_compact(
            sample=sample,
            api_client=api_client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = [await scorer.score(sample, output) for scorer in scorers]
        results.append(MemoryCompactCaseResult(sample=sample, output=output, scores=scores))
    return results
