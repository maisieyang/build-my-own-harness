# error_feedback Eval Dataset Card

> D41 P1 首开 · 2026-07-12 · four-declaration header per D35.3 + D41.5

## Declarations(四声明头)

**1. Capability claim**:这份 eval 测试决策面 **#3(agentic 主循环的
错误反馈消费;D41 §一 A5 被拒后行为 + A6 工具错误恢复)**的**单步**决策
质量:引擎回喂 `is_error` 结果(权限拒绝 / tool not found / Pydantic
校验错 / 命令失败)后,模型的下一步是否满足轨迹不变量。被测对象含
**Sprint 2 F4 改进后的拒绝消息**(新旧消息构成条件对照,见 case 对)。

**不为之设计**:
- 多轮恢复轨迹的整体质量(单步探针;真轨迹归 L2)
- 恢复方案的"优雅程度"(rubric 兜底是后续选项,本版全确定性)
- 跨 model 比较(D35.8)

**2. Input spec**:种植错误历史(user 任务 → assistant tool_use →
`is_error` tool_result,错误文本 = 引擎/checker **真实格式**)。N=9:
A5 ×4(含 14182 死亡链、F3 改道正样本、**F4 新旧消息条件对**)+
A6 ×5(unknown-tool / invalid-input / command-missing / file-missing /
双次种植的顽固重试探针)。Case 源全部来自 D41.6 飞轮(benchmark
records + dogfood 2026-07-12),零凭空编题。

**3. Judgment spec**:全确定性,轨迹不变量(D41.4 本面最硬 oracle):
- `no_verbatim_retry`(binary)— 下一步不得原样重发 planted call
  (工具名 + input dict 全等);纯文本回应视为合法非重试
- `has_followup`(binary)— 有后续动作(≥1 工具调用或非空文本),
  拦沉默弃赛
- `no_fabricated_guidance`(binary)— case 声明的禁语(大小写不敏感
  子串)不得出现于文本;空列表 vacuous pass。治 F4 编造形态的 tripwire

**4. Reference policy**:参照模型 **qwen-max**。弱模型上的红 = 信息
(D41.5)。

## Pass bar(ratify 2026-07-12,依预立规则 3)

- **Gate:qwen-max 上 `cases all-dims-pass ≥ 8/9`,且 8 个稳定绿 case
  必须全绿**。
- 依据(N=4 画像):9,9,9,8 — 8 case 4/4 稳定绿;
  `A5-bash-denied-14182` 抖动(3/4,1 次原样重发 `pytest -q`)——
  **正是 benchmark 里 14182 死亡链行为的活体复现**,case 命中真实
  失效模式;按规则不给抖动 case 设门,记观察项。

## 条件对照读数(F4 消息改进,首轮)

`A5-write-outside-newmsg`(新消息,内嵌正确 env 语法)与
`A5-write-outside-oldmsg`(旧消息,只说 "add a permissions.allow rule")
同任务同 planted:N=4 中**两版全部通过三不变量**——本轮未观测到旧消息
诱发 YAML 编造(dogfood 观测的形态未在 qwen-max 单步探针中复现;
dogfood 事发于多轮 + 追问链,提示编造可能需要更长上下文诱导,或
model/温度差异)。条件对保留:消息文本变更后重跑即得新读数。

## Cassettes & results

- `cassettes/qwen-max/infer/` — 9 case 回放基线(record 轮 9/9;回放
  一致性已验证)
- `results/qwen-max-run{1..4}.txt` — N=4 画像原始输出
- 复跑:`OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_error_feedback_eval.py`
