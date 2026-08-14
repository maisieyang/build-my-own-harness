# memory_compact Eval Dataset Card

> D45 / 决策面 #1 B2 · 2026-08-12 · four-declaration header(D35.3 + D41.5)

## Declarations(四声明头)

**1. Capability claim**:测**生产** `full_compact`(services/compact.py 的
9-slot 语义摘要,`_L4_COMPACT_SYSTEM_PROMPT`)在决策面 **#1 / B2**(compact
摘要;D41 §一)上的**信息保真**质量:一段长对话被压缩后,埋入的关键事实
有没有存活、噪声有没有被丢。**fail-open 最高风险面**——摘要坏了会静默成为
模型对全部前史的记忆,没人会发现。

**不为之设计**:
- 摘要**写得好不好**(措辞质量,那要软 judge;本 eval 只测信息保真)
- 跨 model 强弱比较(D35.8 前置未满足)
- Tool Result 入口限流，以及摘要前只清理 old successful Read/Grep results 的触发与
  保真边界(这些是确定性代码,归 TDD)

当前生产链路为：Tool Result 入口预算 → Conversation 阈值 → summarizer 私有输入可选
省略较早且成功的 `Read`/`Grep` 结果（marker 必须确实更省 token）→ 9-slot Summary +
原始 recent tail → Prompt Too Long reactive fallback。保留的 ToolUse 只能重新查询当前
来源，不能重建精确历史输出。清理不是独立 Compact 终态；Summary 失败必须返回原始
Conversation。

**2. Input spec**:每 case = 一段 ~28 条消息的合成对话(> preserve_recent=12,
才会触发 full_compact),关键事实埋在**中段**(会被压掉的 older 区)。当前 N=10：

- 历史 ratified 3 cases：MC1、MC3、MC6，reference model 为 `qwen-max`；
- 修复后重新 ratified 3 cases：MC2、MC4、MC5。它们原本各有一个 must-recall 事实落在
  preserved tail，移入真正被摘要的 older 区后由 `qwen3.7-max` 重新 live/record；
- dogfood ratified 3 cases：MC7～MC9，分别验证最新状态覆盖旧状态、Slash Skill
  provenance、错误时间顺序，reference model 为 `qwen3.7-max`。
- 架构收敛 ratified 1 case：MC10 验证旧成功 `Read` Result 被清理后，后续已验证结论、
  用户约束与当前状态仍进入 Summary，reference model 为 `qwen3.7-max`。

当前 10 条均为 `status: ratified`。确定性 dataset 测试要求每个 `must_recall` 都真实出现
在 `messages[:-12]`，防止 preserved tail 假阳性复发。未来新增 case 仍应先标记为
`candidate`，完成 live → record → replay 后再晋级。

**3. Judgment spec**:全确定性 keyword,零 LLM-judge。**种植事实回收
(D45.1 核心手法)**:摘要没有唯一正确答案(措辞无穷),但"关键事实在不在"
可枚举可 `=` 判——把开放生成问题**重述**成封闭存在性检查:
- `fact_recall`(binary)— 每个 must_recall 事实(大小写不敏感子串)必须
  出现在 summary;任一缺失 → FAIL 并点名;did_apply=False → 必 FAIL
- `noise_exclusion`(binary)— must_not_recall 噪声不得出现;空列表 vacuous

**4. Reference policy**:所有新 live/record 都使用项目 `.env` 当前配置的
`OPENHARNESS_MODEL`；CLI 的 `--model` 只作为本次显式覆盖。脚本不提供历史模型的隐式
fallback。2026-07-23 的历史 cassette 身份仍是 `qwen-max`；其中 MC1、MC3、MC6
继续作为可信 replay baseline。MC2、MC4、MC5 与 MC7～MC9 的当前可信 cassette 均为
2026-08-12 使用 `qwen3.7-max` 录制。稳定 gate 按 model cohort 回放，不篡改历史模型身份。

## Pass bar

- **可信 ratified baseline**：10/10；历史 `qwen-max` cohort 3/3，当前
  `qwen3.7-max` cohort 7/7。
- **promotion bar**：candidate 必须在 `.env` 当前模型上定向 live 通过，再逐条 record
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
MC9 因而把“填充回复 15”续写成“填充消息 16”，而不是执行摘要。修复为：保留 9-slot
schema，同时加入 fidelity contract；并在待摘要历史末尾追加专用 user summarization
request，明确禁止续写对话序列、要求逐字核对 `KEY=VALUE` marker 与 synthetic envelope
provenance。MC8 曾在首次 record 时暴露一次不稳定遗漏，prompt 再收紧后重新 live/record
通过；失败 cassette 已被同 case/model 的通过记录覆盖。最终 6 条新 cohort replay 6/6。

2026-08-14 移除全局长 Block collapse，改为仅在 Summarizer 私有 older 输入中省略
successful `Read`/`Grep` Result，且 marker 必须确实更省 token。MC10 按当前 `.env`
的 `qwen3.7-max` 重新完成 live → record → replay 1/1，验证省略后仍保留后续结论、
用户约束与当前状态，并排除已省略的 Raw Read body。

## Cassettes & results

- `cassettes/qwen-max/infer/` — MC1～MC6 历史记录；当前 replay gate 只采信
  MC1、MC3、MC6
- `cassettes/qwen3.7-max/infer/` — MC2、MC4、MC5、MC7、MC8、MC9、MC10 当前可信记录
- `results/qwen-max-run{1..4}.txt` — 历史 N=4 画像原始输出
- 稳定基线 replay gate：
  `uv run pytest tests/eval/test_replay_gates.py -k memory_compact -q`
- 手动定向运行（会读取 `.env` 的 `OPENHARNESS_MODEL`）：

```bash
uv run oh dev eval memory_compact --mode live --case MC7-current-state-supersedes-stale
uv run oh dev eval memory_compact --mode live --case MC8-slash-skill-provenance
uv run oh dev eval memory_compact --mode live --case MC9-latest-error-ordering
uv run oh dev eval memory_compact --mode live --case MC2-decision-facts
uv run oh dev eval memory_compact --mode live --case MC4-user-request-facts
uv run oh dev eval memory_compact --mode live --case MC5-pending-tasks-facts
uv run oh dev eval memory_compact --mode live --case MC10-refetchable-result-cleanup
```

新增或修改 case 时，先用 `live` 运行；全部符合预期后改为 `record`，最后用 `replay`
确认 cassette 与 scorer 契约。2026-08-12 上述六条已完成完整链路并晋级 ratified。
2026-08-14 的 MC10 也已完成同一链路并晋级 ratified。
