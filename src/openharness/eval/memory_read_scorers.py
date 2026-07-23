"""memory_read scorers — memory READ 自选决策(C4),tool-call 存在性硬 oracle。

镜像 skill_trigger 的两维拆分(决策 vs 选择),never collapsed:

- ``read_decision`` — must-read case(用户明确 recall/check/remember):模型必须
  Read 至少一个 memory 文件;restraint case(用户说 ignore / 无相关记忆):模型
  必须**不**读任何 memory 文件(读任务文件等非 memory 路径是允许的)。
- ``memory_selection`` — must-read case:读的必须是**对**的那个 memory(点名读错
  哪个);restraint case:无可选,vacuous pass。

"memory 读" vs "任务读"靠路径里是否命中已知 memory slug 区分(sample 携带 fixture
的 all_memory_slugs)。单步、不执行 Read——测的是**读的决策**,非读到的内容。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.eval.protocol import Score
from openharness.protocols.content import ToolUseBlock

if TYPE_CHECKING:
    from openharness.eval.memory_read import MemoryReadOutput, MemoryReadSample


def _memory_reads(sample: MemoryReadSample, output: MemoryReadOutput) -> list[str]:
    """返回 output 里命中 memory 的 Read 所读的 slug 列表(路径含某 slug)。"""
    hits: list[str] = []
    for block in output.tool_uses:
        if not isinstance(block, ToolUseBlock) or block.name != "Read":
            continue
        path = str(block.input.get("path", ""))
        for slug in sample.all_memory_slugs:
            if slug in path:
                hits.append(slug)
    return hits


class ReadDecisionScorer:
    """must-read → 必须读到某 memory;restraint → 必须不读任何 memory。"""

    @property
    def dim(self) -> str:
        return "read_decision"

    async def score(self, sample: MemoryReadSample, output: MemoryReadOutput) -> Score:
        reads = _memory_reads(sample, output)
        if sample.expected_read_slug is not None:
            # must-read:读到任意 memory 即决策正确(选对不对交给 selection 维)
            if reads:
                return Score(
                    dim=self.dim,
                    value=1.0,
                    reason=f"read memory as mandated (read: {', '.join(reads)})",
                    case_id=sample.case_id,
                )
            return Score(
                dim=self.dim,
                value=0.0,
                reason="MUST-access violated: user asked to recall but no memory was read",
                case_id=sample.case_id,
            )
        # restraint:不得读任何 memory(非 memory 读允许)
        if reads:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=f"restraint violated: read memory when should abstain ({', '.join(reads)})",
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=1.0,
            reason="abstained from memory read as expected",
            case_id=sample.case_id,
        )


class MemorySelectionScorer:
    """must-read case:读的必须是对的那个 memory。restraint:vacuous pass。"""

    @property
    def dim(self) -> str:
        return "memory_selection"

    async def score(self, sample: MemoryReadSample, output: MemoryReadOutput) -> Score:
        if sample.expected_read_slug is None:
            return Score(
                dim=self.dim,
                value=1.0,
                reason="restraint case — no selection to make (vacuous)",
                case_id=sample.case_id,
            )
        reads = _memory_reads(sample, output)
        if sample.expected_read_slug in reads:
            return Score(
                dim=self.dim,
                value=1.0,
                reason=f"selected the correct memory: {sample.expected_read_slug}",
                case_id=sample.case_id,
            )
        if reads:
            return Score(
                dim=self.dim,
                value=0.0,
                reason=(
                    f"wrong memory: expected {sample.expected_read_slug}, read {', '.join(reads)}"
                ),
                case_id=sample.case_id,
            )
        return Score(
            dim=self.dim,
            value=0.0,
            reason=f"read no memory, cannot select expected {sample.expected_read_slug}",
            case_id=sample.case_id,
        )
