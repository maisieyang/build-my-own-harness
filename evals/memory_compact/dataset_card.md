# memory_compact Eval Dataset Card

> D45 / 决策面 #1 B2 · 2026-07-23 · four-declaration header(D35.3 + D41.5)

## Declarations(四声明头)

**1. Capability claim**:测**生产** `full_compact`(services/compact.py 的 L4
9-slot LLM 摘要,`_L4_COMPACT_SYSTEM_PROMPT`)在决策面 **#1 / B2**(compact
摘要;D41 §一)上的**信息保真**质量:一段长对话被压缩后,埋入的关键事实
有没有存活、噪声有没有被丢。**fail-open 最高风险面**——摘要坏了会静默成为
模型对全部前史的记忆,没人会发现。

**不为之设计**:
- 摘要**写得好不好**(措辞质量,那要软 judge;本 eval 只测信息保真)
- 跨 model 强弱比较(D35.8 前置未满足)
- L1-L3 分级压缩的触发逻辑(那是确定性代码,归 TDD)

**2. Input spec**:每 case = 一段 ~28 条消息的合成对话(> preserve_recent=12,
才会触发 full_compact),关键事实埋在**中段**(会被压掉的 older 区)。N=6:
5 个 fact-recall(各埋 3 个事实,分别对应 9-slot 的 Primary Request /
Decisions / Errors&Fixes / User Request / Pending Tasks 槽)+ 1 个
noise-exclusion(既测事实保留、又测逐字填充语不该进摘要的双向探针)。
扩量走 D41.6 飞轮。

**3. Judgment spec**:全确定性 keyword,零 LLM-judge。**种植事实回收
(D45.1 核心手法)**:摘要没有唯一正确答案(措辞无穷),但"关键事实在不在"
可枚举可 `=` 判——把开放生成问题**重述**成封闭存在性检查:
- `fact_recall`(binary)— 每个 must_recall 事实(大小写不敏感子串)必须
  出现在 summary;任一缺失 → FAIL 并点名;did_apply=False → 必 FAIL
- `noise_exclusion`(binary)— must_not_recall 噪声不得出现;空列表 vacuous

**4. Reference policy**:参照模型 **qwen-max**(与其余 eval 一致,跨 eval
可比)。注意:**参照模型 qwen-max ≠ 生产/benchmark 用的 qwen3.7-max**——
eval 测的是"参照系上 harness 逻辑/prompt 没坏",非"今日部署模型好坏"
(D41.5)。qwen-max 上限更低更挑剔,反而更易逼出兼容 bug(见 F15/F16)。

## Pass bar(ratify 2026-07-23)

- **Gate:qwen-max 上 `cases all-dims-pass = 6/6`**(全稳定绿)。
- 依据(N=4 画像):6,6,6,6 /6——四轮零方差全绿。9-slot 摘要在 8192 token
  预算下把全部埋入事实保住、噪声未漏。bar 满格有画像支撑(同 tool_choice)。

## 建设中挖出的 harness 真 bug(eval 的价值实证)

B2 第一次真跑(qwen-max)连挖两个跨 provider 兼容 bug,层层剥笋:
- **F15**(已修,同批 commit):`summarize` 用 `tools=[]` 禁用工具,translation
  层 `is not None` 把空数组照发,DashScope 拒空数组。影响所有 secondary-pass
  + 生产 compaction。修:truthy 检查省略字段。
- **F16**(已在 eval 侧规避,生产潜在):`full_compact` 默认 `max_tokens=20_000`
  超 qwen-max 的 8192 硬顶 → 400。生产用 qwen3.7-max(上限够高)未爆,但
  任何低上限 provider 会挂。eval 用 8192(短摘要绰绰有余)规避;生产的
  "按模型夹取 max_tokens"是独立切片(需模型上限表),记 backlog。

**两个 bug 都是 qwen-max 挑出来的,qwen3.7-max 一个不报——用更严的参照
模型是意外的好处。** 这正是 eval 的价值:在挑剔环境替你跑一遍,把"恰好
没爆"的隐患提前暴露。

## Cassettes & results

- `cassettes/qwen-max/infer/` — 6 case 回放基线(record 6/6;回放一致已验证)
- `results/qwen-max-run{1..4}.txt` — N=4 画像原始输出
- 复跑:`uv run oh dev eval memory_compact --mode replay`
