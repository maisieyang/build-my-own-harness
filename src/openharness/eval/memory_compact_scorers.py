"""memory_compact scorers — 种植事实回收(D45.1),全确定性 keyword。

两维,never collapsed:

- ``fact_recall``    — 每个 must_recall 事实必须(大小写不敏感)出现在
  summary。任一缺失 → FAIL 并点名缺哪个(fail-open 的静默丢失就是这里
  被抓住)。did_apply=False → 必 FAIL(压缩没跑成 = 事实必然没保留)。
- ``noise_exclusion`` — must_not_recall(灌水/临时噪声)不得出现在
  summary。空列表 vacuous pass。

不测摘要写得好不好(那要软 judge);测该保留的事实有没有保留、该丢的
噪声有没有丢——把开放生成问题重述成封闭存在性检查。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.eval.protocol import Score

if TYPE_CHECKING:
    from openharness.eval.memory_compact import MemoryCompactOutput, MemoryCompactSample


class FactRecallScorer:
    """每个 must_recall 事实必须出现在 summary。"""

    @property
    def dim(self) -> str:
        return "fact_recall"

    async def score(self, sample: MemoryCompactSample, output: MemoryCompactOutput) -> Score:
        if not output.did_apply:
            return Score(
                dim=self.dim,
                value=0.0,
                reason="compaction did not apply — no summary, facts lost",
                case_id=sample.case_id,
            )
        hay = output.summary_text.lower()
        missing = [f for f in sample.must_recall if f.lower() not in hay]
        if missing:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"dropped fact(s): {', '.join(missing)}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason=f"all {len(sample.must_recall)} fact(s) retained",
            case_id=sample.case_id,
        )


class NoiseExclusionScorer:
    """must_not_recall 噪声不得出现在 summary(空列表 vacuous pass)。"""

    @property
    def dim(self) -> str:
        return "noise_exclusion"

    async def score(self, sample: MemoryCompactSample, output: MemoryCompactOutput) -> Score:
        if not sample.must_not_recall:
            return Score(
                dim=self.dim,
                value=1.0,
                reason="vacuous pass: no noise list",
                case_id=sample.case_id,
            )
        if not output.did_apply:
            # 没压缩成不算噪声泄漏问题 — 这维 vacuous,fact_recall 已判 FAIL
            return Score(
                dim=self.dim,
                value=1.0,
                reason="vacuous: compaction did not apply (fact_recall owns the fail)",
                case_id=sample.case_id,
            )
        hay = output.summary_text.lower()
        leaked = [n for n in sample.must_not_recall if n.lower() in hay]
        if leaked:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"noise leaked into summary: {', '.join(leaked)}",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason="no noise leaked",
            case_id=sample.case_id,
        )
