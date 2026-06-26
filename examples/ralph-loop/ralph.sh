#!/usr/bin/env bash
# Ralph loop on a real test-case gate, using stock `claude -p`.
# Outer loop = bash (verify chair + iteration cap). Inner fix = claude -p.
set -uo pipefail
cd "$(dirname "$0")"

GOAL='修好 mathy.py 让 pytest 全绿。只改 mathy.py 的实现，绝不修改 test_mathy.py 的任何断言。'
MAX_ITER=${1:-5}

for i in $(seq 1 "$MAX_ITER"); do
  # ① 验证椅：确定性 gate，退出码是唯一判据（不经 tail/pipe，保住 exit code）
  if uvx pytest -q >/tmp/ralph_pytest.log 2>&1; then
    echo "✅ GREEN — converged after $((i-1)) fix iteration(s)"
    exit 0
  fi
  FEEDBACK=$(tail -n 25 /tmp/ralph_pytest.log)

  echo "── iter $i/$MAX_ITER : tests RED, handing to claude -p ──"
  # ② fresh-context：不带 --resume，每轮新 session，只重喂 goal+反馈
  # ③ 把关椅：allowlist + acceptEdits（无人值守不弹窗）
  # ④ 硬栏：--max-budget-usd 单轮预算上限
  claude -p "$GOAL

pytest 当前失败，尾部输出：
$FEEDBACK

只改 mathy.py，不要动测试。" \
    --allowedTools "Read,Edit,Write,Bash(uvx pytest:*)" \
    --permission-mode acceptEdits \
    --max-budget-usd 0.50 \
    --output-format json \
  | jq -r '.result // .[]?.result // "(no result field)"' 2>/dev/null || true
done

# 撞迭代上限：报告 + 非零退出，绝不无声继续
echo "❌ hit max-iter=$MAX_ITER without going GREEN"
exit 1
