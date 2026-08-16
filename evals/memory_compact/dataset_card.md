# memory_compact Eval Dataset Card

> D45 / 决策面 #1 B2 · 2026-08-12 · four-declaration header(D35.3 + D41.5)

## Declarations(四声明头)

**1. Capability claim**:测**生产** `full_compact`(services/compact.py 的
六段 Handoff 语义摘要,`_L4_COMPACT_SYSTEM_PROMPT`)在决策面 **#1 / B2**(compact
摘要;D41 §一)上的**信息保真**质量:一段长对话被压缩后,埋入的关键事实
有没有存活、噪声有没有被丢。**fail-open 最高风险面**——摘要坏了会静默成为
模型对全部前史的记忆,没人会发现。

**不为之设计**:
- 摘要**写得好不好**(措辞质量,那要软 judge;本 eval 只测信息保真)
- 跨 model 强弱比较(D35.8 前置未满足)
- Tool Result 入口限流，以及工具无关的旧 Result 清理、Message/Tool 双 recent 边界与
  阈值重估(这些是确定性代码,归 TDD)

当前生产链路为：Tool Result 入口预算 → 完整请求预算（instructions、Tool schemas、
Conversation、输出预留与安全余量）→ 在完整 Conversation 上并行
计算 recent messages（默认 12，可配置）与最近 3 次已完成 Tool 交互 → 保护集合外的
ToolResult 正文变为 `[cleared]`、ToolUse 名称和参数保留 → 重估 → 仍超阈值才执行六段 Handoff
Summary + 原始 recent tail。Provider 返回 Prompt Too Long 时只允许一次预算驱动的语义
重编译：选择协议完整且可放入预算的最大 recent 后缀并总结更早历史；第二次仍失败就显式
抛错，不盲删消息。Tool 清理可以成为独立 Compact 终态；Summary 看不到已清理的精确历史
输出。完整请求预算和 PTL 控制流是确定性行为，归 TDD；本 Eval 继续只验证 Summary 软契约。

**2. Input spec**:每 case = 一段 ~28 条消息的合成对话(> preserve_recent=12,
才会触发 full_compact),关键事实埋在**中段**(会被压掉的 older 区)。当前 N=10，覆盖
配置事实、架构决策、错误修复、用户约束、待办、噪声排除、最新状态、Skill provenance、
错误顺序和旧 ToolResult 清理后的连续性。

2026-08-16 基础 Prompt 从九段 Conversation Summary 改为六段 Handoff State。由于全部 case
都会经过这份生产 Prompt，先前 cassette 不再证明当前模型行为，N=10 全部退回
`status: candidate`。确定性 dataset 测试继续要求每个 `must_recall` 都真实出现在
`messages[:-12]`，防止 preserved tail 假阳性。全部完成 live → record → replay 后才能
重新晋级。

**3. Judgment spec**:全确定性 keyword,零 LLM-judge。**种植事实回收
(D45.1 核心手法)**:摘要没有唯一正确答案(措辞无穷),但"关键事实在不在"
可枚举可 `=` 判——把开放生成问题**重述**成封闭存在性检查:
- `fact_recall`(binary)— 每个 must_recall 事实(大小写不敏感子串)必须
  出现在 summary;任一缺失 → FAIL 并点名;did_apply=False → 必 FAIL
- `noise_exclusion`(binary)— must_not_recall 噪声不得出现;空列表 vacuous

**4. Reference policy**:所有新 live/record 都使用项目 `.env` 当前配置的
`OPENHARNESS_MODEL`；CLI 的 `--model` 只作为本次显式覆盖。脚本不提供历史模型的隐式
fallback。已有 `qwen-max` 与 `qwen3.7-max` cassette 只保留为历史证据；六段 Handoff
Prompt 必须在当前 `.env` 模型上重新完成整组 ratification，不能混用旧 cohort 证明新行为。

## Pass bar

- **可信 ratified baseline**：暂时为空；N=10 全部等待六段 Handoff Prompt 重新验证。
- **promotion bar**：10/10 candidate 必须在 `.env` 当前模型上 live 全维通过，再 record
  并 replay；不能先写 cassette 再反向降低 scorer 或断言。

## 建设中挖出的真 bug

B2 第一次真跑(qwen-max)连挖两个跨 provider 兼容 bug,层层剥笋:
- **F15**(已修,同批 commit):`summarize` 用 `tools=[]` 禁用工具,translation
  层 `is not None` 把空数组照发,DashScope 拒空数组。影响所有 secondary-pass
  + 生产 compaction。修:truthy 检查省略字段。
- **F16**(已在 eval 侧规避,生产潜在):`full_compact` 默认 `max_tokens=20_000`
  超 qwen-max 的 8192 硬顶 → 400。生产用 qwen3.7-max(上限够高)未爆,但
  任何低上限 provider 会挂。eval 用 8192(短摘要绰绰有余)规避;生产的
  "按模型夹取 max_tokens"是独立切片(需模型上限表),记 backlog。

这两个 bug 都由历史 `qwen-max` ratification 暴露。保留这段记录是为了说明旧 cassette
的来源，不用于决定今天 live eval 应读取哪个模型。

2026-08-12 又发现一个 eval 自身的假阳性：`_extract_summary_from_messages` 把模型生成的
summary 与 12 条 preserved recent messages 全部拼进 `summary_text`。MC2 的“蓝绿”、
MC4 的“UTF-8/BOM”、MC5 的“v2”都位于 recent tail，因此旧 scorer 即使摘要丢事实也会
通过。修复后 evaluator 只提取 `Summary of prior conversation:` 对应的生成摘要；三条
fixture 的事实也已移入 older 区；MC2、MC4、MC5 随后按当前 reference policy 重新
live/record/replay 并晋级 ratified。

2026-08-12 的 candidate ratification 又发现两个生产缺陷。第一，原 L4 prompt 没有明确
要求保留 Tool/Skill provenance、opaque marker、错误时间顺序和最新状态，MC8/MC9 live
会静默丢事实。第二，`full_compact` 直接以历史最后一条 assistant message 结束请求；
MC9 因而把“填充回复 15”续写成“填充消息 16”，而不是执行摘要。修复为：保留结构化
schema，同时加入 fidelity contract；并在待摘要历史末尾追加专用 user summarization
request，明确禁止续写对话序列、要求逐字核对 `KEY=VALUE` marker 与 synthetic envelope
provenance。MC8 曾在首次 record 时暴露一次不稳定遗漏，prompt 再收紧后重新 live/record
通过；失败 cassette 已被同 case/model 的通过记录覆盖。最终 6 条新 cohort replay 6/6。

2026-08-14 移除全局长 Block collapse，改为仅在 Summarizer 私有 older 输入中省略
successful `Read`/`Grep` Result；当时的 MC10 完成了 live → record → replay 1/1。
2026-08-16 生产链路进一步改为工具无关的旧 Result 清理，并把 recent messages 与最近
3 次 Tool 交互定义为并行保护边界；清理后重新估算，足够小时跳过 Summary。因此旧 MC10
cassette 不再证明当前行为，case 已改写并退回 candidate，等待手动重新 ratify。

同日基础 Prompt 收敛为 Current Objective、Current State、Verified Evidence、Decisions and
Constraints、Active Artifacts、Remaining Work 六段 Handoff，并移除 `<analysis>`、All User
Messages、Optional Next Step、uppercase marker 和 synthetic envelope 等基础 Prompt 特例。
该变化影响全部 Summary case，因此原 9 条 ratified case 也一并退回 candidate。

## Cassettes & results

- `cassettes/qwen-max/infer/` — 旧九段 Prompt 的历史记录，不进入当前稳定 gate
- `cassettes/qwen3.7-max/infer/` — 旧九段 Prompt/旧 MC10 契约的历史记录，等待覆盖录制
- `results/qwen-max-run{1..4}.txt` — 历史 N=4 画像原始输出
- 手动整组运行（会读取 `.env` 的 `OPENHARNESS_MODEL`）：

```bash
uv run oh dev eval memory_compact --mode live
uv run oh dev eval memory_compact --mode record
uv run oh dev eval memory_compact --mode replay
```

必须先确认整组 live 10/10，再执行 record；任何 live FAIL 都应停止，不覆盖历史 cassette。
record 与 replay 同样达到 10/10 后，才能把 N=10 晋级 ratified 并恢复稳定 replay gate。
