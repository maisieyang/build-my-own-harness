# Learnings — Phase 11 (Summarization Substrate — Write Path + Compact + Extract)

> Phase 11 起止 / 2026-05-26(单日 session,Phase 10 闭合后立即起,中间
> 一次 context-window 折叠继续推进)
> 7 capabilities (P11-T1…T7) / **12 commits** / 7,018 行净增
> (2,370 src + 3,657 tests + 991 docs/decisions/plan)
> 1608 tests(Phase 10 close)→ **1789 tests** (+181) / ruff + mypy --strict clean / cov 95%
>
> 本文件回答的题:**Phase 10 把读路径拆出来,Phase 11 真的从这个分离里
> 获益了吗?——以及 ``summarize()`` 原语在 3 个 trigger 共用前的 ROI
> 兑现了多少?**

---

## 1. 数据点

| 维度 | Phase 9(plugins) | Phase 10(memory 读) | **Phase 11(memory 写 + compact + extract)** |
|---|---|---|---|
| Capability | 5 | 6 | **7** |
| 生产代码净增 | ~600 | ~3,400 | **~2,370** |
| 测试净增 | ~150 | ~205(1608 − 1403) | **+181**(1789 − 1608) |
| 新模块 / 包 | 1(`plugins/`) | 2(`memory/` + `prompts/` 重构) | **1**(`services/`,3 个原语) |
| 新 Settings 字段 | 1 | 2 | **2 nested**(`compact` + `extraction`,共 8 个 leaf 字段) |
| 新 CLI flag | 1 | 1 | **3**(`--no-auto-compact` / `--compact-threshold` / `--no-extract`) |
| 新 CLI 子命令 | 0 | 3 | **0**(只加 `/compact` REPL 内置命令) |
| 新 hooks 事件 | 0 | 0 | **0**(但新 `HookSpec.re_run_on_reactive_rebuild` 字段 + Engine 重跑分支) |
| **保护层 zero-diff** | ✓ 5 个 stores | ✓ 11 个目录 | ✓ **9/10 目录**(bundles/ 一处 additive kwarg 例外) |
| 时间 | 2.5 天 | 1 天 | **1 天**(单 session + 一次中间折叠) |

**关键观察**:Phase 11 是 Phase 8 + Phase 10 抽象的**第 5 次独立 consumer 压测**——
`services/summarize.py` 作为新原语,被 compact L4 + extract 两个 consumer
立即共用(总会有 N=1 → N=2 的"是不是抽象对了"的实证瞬间),而 ``markdown_store/``
依旧 zero diff(memory 写路径直接用 Phase 10 store 的 ``add_or_update``)。

---

## 2. 每个 task 的 takeaway

| Task | 一句话总结 |
|---|---|
| **P11-T1 — summarize() primitive**(07e4e81) | ~220 行单文件原语:`async def summarize(messages, system_prompt, model, api_client, tools_disabled=True, ...) -> str`,三层嵌套 retry(外 ``asyncio.wait_for`` timeout / 中 PTL drop-oldest 重试 max 3 / 内 streaming 重试 max 2),`SupportsStreamingMessages` Protocol 抽离让 stub 测试不依赖 `ApiClient` 实例。**踩坑**:caller 的 messages 列表必须 `list(messages)` 防御性拷贝——summarize 三层 retry 内部会 mutate,污染调用方导致下一 turn 行为不可预测。 |
| **P11-T2 — session_memory 5-slot**(ab5f497) | `get_session_memory_dir(cwd)` → `~/.openharness/session-memory/<basename>-<sha1[:12]>/`(跟 memory dir 同 hash 算法),`update_session_memory_file` 原子写 tempfile+os.replace,`_render_5_slot` 5 个 slot 的 markdown 模板,**12k 字符 cap** 多级 cascade(对话先 pop / artifact 再 pop / 硬截断收尾)。**踩坑**:`Path.home()` 又栽一次——这次 service 里写常量没问题,但函数体内 lazy 求值能让 conftest 隔离继续生效。 |
| **P11-T3 — compact L0-L4 escalation**(a8475ae + 740518a) | `auto_compact_if_needed` orchestrator:L0 token 估算 → L2 context_collapse(deterministic head 900 + tail 500) → L3 session_memory reuse(读 checkpoint,1h freshness) → L4 LLM 9-slot full compact。L4 prompt **字面拷贝 HKUDS 原版**(D29.3 sub-decision)。engine/query.py 集成在 PreApiCall 之前——hooks 看到的 messages 已经压缩过。**踩坑**:L4 测试一开始用了 30 条 8k 字符消息,被 L2(2400 字符阈值)先吃掉降到 threshold 以下,根本没机会到 L4;改成 50 条 2000 字符消息才稳定触发 L4。 |
| **P11-T4 — extract + team scope + secret scan**(ae3406b) | `extract_memories_from_turn(...) -> ExtractionResult`,EXTRACTION_SYSTEM_PROMPT 严格 schema(JSON only / max 3 records / read-only)。**signature-dedup 优先 name** —— 同 name 不同 body 共存,relevance 挑分高的。`MemoryScope.TEAM` 启用 + `check_team_memory_secrets` 6 个 regex(PEM/AWS/GitHub/Anthropic/OpenAI/generic),secret 命中静默 drop 并记 `memory_team_secret_blocked`。**踩坑**:extraction 默认开 + stub LLM 没法满 JSON schema → 每个用 stub 的测试都有 `memory_extract_failed` 警告噪声,conftest 加 `OPENHARNESS_EXTRACTION__ENABLED=false` 默认 off 解决。 |
| **P11-T5 — CompactSettings + ExtractionSettings + CLI + /compact**(a1c1747) | nested pydantic `CompactSettings` + `ExtractionSettings`,`env_nested_delimiter="__"` 让 `OPENHARNESS_COMPACT__THRESHOLD_RATIO=0.7` 工作。3 个 CLI flag 写在 ask + chat 镜像。`/compact` REPL 内置命令调 `full_compact()` 不走 threshold。**踩坑**:MCP filesystem 测试用 `pytest.mark.integration` 绕开 conftest 的 extraction-off,extraction 默认 on 起来后 stub 的 `last_request` 被 extract 的 `tools=[]` 调用覆盖 → 测试断言 `Read in tools` 失败。修法:测试自己 `monkeypatch.setenv("OPENHARNESS_EXTRACTION__ENABLED", "false")`。 |
| **P11-T6-6a — PreApiCall reactive re-run**(624467c) | 关 Phase 4 retro §6 的债。`HookSpec.re_run_on_reactive_rebuild: bool = False` 加字段;`HookRegistry.register(..., re_run_on_reactive_rebuild=False)` kwarg 把 ID 存进 `_reactive_rerun_hook_ids: set[int]`(`id(hook)` 而非 hash,因为 closures 不一定 hashable);`execute_hook_chain` 加 `hook_subset` kwarg 让 engine 只跑标记子集;engine PTL retry 重建 request 后调一遍 subset。default False 保 byte-identical。**踩坑**:deny 测试在 original chain 直接 deny 没到 rerun 代码;改成 "first call return None, second call deny"。 |
| **P11-T6-6b — stopwords + body_hits>=2 阈值**(6b48127) | 解 Phase 10 D28.7 sub-decision。22 词最小英文 stopword set,**仅减 query 不减 memory tokens**(否则短 memory body 失信号),surface 阈值收紧 `meta_hits >= 1 OR body_hits >= 2` —— Phase 10 T6 那条 "the" 共享单 body_hit 假阳就靠这个截住。Han 字符不在 set 里 → 中文 query 字节相等。**踩坑**:旧 P10 测试 `test_body_hit_alone_sufficient` 编码的是被 D29.9 推翻的契约,改成 `test_single_body_hit_no_longer_surfaces`。 |
| **P11-T7 — E2E + invariant + retro**(084aee6 + 0d0b5e9 + 本 commit) | 7a 跑通 extract → store → relevance → use_count 闭环(真 `FilesystemMemoryStore` + 真 `mark_memory_used`,只 stub LLM)。7b 50 条短消息 + L2 旁路 → L4 触发 → message count 50 → 14。7d 跨 10 个保护目录 git log 全 zero-diff 验证(bundles/ 一处 additive kwarg 例外)。**踩坑**:Phase 11 写完后 cov 掉到 93.26%(`fail_under=95` 卡红),靠 13 个 backfill 测试(reactive rerun deny/modify 语义 + extract 每个 invalid-record 分支 + session_memory 三阶 cascade)拉回 95%。 |

---

## 3. Framework-level 主题 — Phase 11 真正学到的

### 3.1 ⭐⭐⭐ Substrate-first compounds:`summarize()` 一个原语 + 两个 consumer 同 phase 落地

Phase 10 retro §3.1 给的预测:

> ⭐ Phase 11 预测:`summarize(messages, retention_policy) -> summary`
> 作为 secondary-pass 原语,3 个 trigger(extract / compact L4 / future
> write_memory)共用。如果这个抽象成立,**Phase 11 又是一次同样形态
> 的复利**。

**实际落地**:T1 ship `summarize()` 时只为 compact L4 用(N=1 consumer)。
T4 ship extract 时**直接调用 `summarize(messages=[...], system_prompt=EXTRACTION_SYSTEM_PROMPT, tools_disabled=True)`,
零修改 `summarize()` 内部**。两个 consumer 关心的差异(prompt /
tools/max_tokens / timeout)全部走 caller-provided kwargs;`summarize()`
自己**不识别 trigger**——没有 "if extract: ..." 分支。

这是 Phase 10 retro §3.1 列的"substrate 复利的真正成立条件"全部应验:

| 条件 | T1→T4 兑现 |
|---|---|
| 抽象边界跟语义边界对齐 | ✓ `summarize` 的语义就是"messages → string with retries",compact / extract 共用此语义 |
| domain-specific 字段显式分类 | ✓ prompt + tools_disabled 是 caller 自己拼,不进原语 |
| 不预测未来 consumer | ✓ T1 只为 compact 设计,T4 / `/compact` REPL / 测试 mock 都没逼着加新参数 |
| 接口最小可用 | ✓ 7 个参数,4 个有 default(`max_tokens / timeout_seconds / tools_disabled / messages`),实际 caller 只传 messages + system_prompt + model + api_client |

**对比 Phase 7c 的 evidence**:Phase 7c 是 `ExecutionEnvironment`
substrate 抽出来,12% LoC 复用 BashTool。Phase 11 比 Phase 7c 更强——
**两个 consumer 在同 phase 内落地,没有等到下一 phase 验证**,所以
信号更早、更便宜地确认了抽象。

⭐ Phase 12 预测:`session_memory` 的 5-slot 模板也会变成 substrate——
`oh ask --resume` 启动时读 checkpoint 重建 session,write_memory 写
checkpoint。两个新 consumer + 已有 compact L3 读 = 第 3 个 consumer。

### 3.2 ⭐⭐ 跨切关切验证(cross-cutting invariant verification)是 phase 收尾的"安全门"

Phase 11 收尾运行了 10 个保护目录的 `git log P11-range -- <dir>`,结果:

| 保护目录 | Phase 11 commits | 性质 |
|---|---|---|
| `src/openharness/markdown_store/` | **0** | ⭐ Phase 8 substrate 第 5 次零修改 |
| `src/openharness/skills/` | 0 | |
| `src/openharness/commands/` | 0 | |
| `src/openharness/plugins/` | 0 | |
| `src/openharness/mcp/` | 0 | |
| `src/openharness/permissions/` | 0 | |
| `src/openharness/prompts/` | 0 | ⭐ Phase 10 refactor 后稳态 |
| `src/openharness/protocols/` | 0 | |
| `src/openharness/tools/` | 0 | |
| `src/openharness/bundles/` | **1**(624467c additive kwarg) | T6-6a 给 HookSpec 加字段 + apply 加 plumbing,**public API 完全向后兼容** |

唯一动了的 `bundles/` 是 D29.7 设计阶段就预见到的 additive
extension(boundary doc 明确点名 `HookSpec` + `_clone_hook_registry`
需要 plumbing),不是"phase 收尾才发现还得动一处"的意外。

**判断 framework**:

| invariant check 抓到的真正问题类型 |
|---|
| 计划外的耦合泄漏(本来应该在 X 层做完的事,默默修了 Y 层) |
| 抽象边界假说被打破(本以为 Y 层"不需要知道 phase N 的事") |
| commit 拆分错误(Phase N+1 的代码被错塞进 Phase N 的 commit) |

Phase 11 这次 invariant check **没有抓到任何这类问题**——所有动到的
都是计划文档预告过的。这本身就是抽象边界还在的最强证据。

### 3.3 ⭐ 测试默认值与产品默认值的张力 —— extraction defaults ON 暴露的 testability tax

Phase 11 extraction 设计为**默认 ON**(D29.5 + D29.8)。直觉上没问题:
用户 0 配置就能享受 memory 自动积累。但实施后立刻撞到:

- 每个用 stub LLM 的测试在 turn 末多一次 LLM 调用
- stub 不可能满足 EXTRACTION_SYSTEM_PROMPT 的 JSON schema → `memory_extract_failed` 噪声
- 部分集成测试(MCP filesystem)的 `stub.last_request` 被 extract 调用覆盖,断言失败

**解法**:conftest 全局 `OPENHARNESS_EXTRACTION__ENABLED=false`(D29.5
保留产品默认 ON,测试默认 OFF)。但 `pytest.mark.integration` 测试绕开
conftest,这些得在 test fixture 里自己设 env。

**深一层的 lesson**:任何"加大默认副作用面积"的 feature,**都得提前
评估它对现有测试 substrate 的污染**。Phase 4 的 PostToolUse 自动截断
hook 当时是 opt-out 设计,所以无此问题;Phase 11 选 opt-out 是产品
判断对的,但测试侧的迁移成本(conftest + 8 个 integration 测试 fix)
应该列入 capability 的 acceptance,而不是收尾才发现。

**判断 framework**:

| 新 feature 默认 ON 的 testability 检查表 | 在 boundary doc 阶段就回答 |
|---|---|
| 现有测试用了什么 stub / mock? | 那个 stub 是否能满足新 feature 的新调用? |
| 现有断言面是什么形状? | 新 feature 的副作用是否打破"single LLM call"等假设? |
| conftest / pytest.mark.integration 互动? | 通用 isolation 是否够,还是部分测试得自己 setenv? |

**真正的代价**:Phase 11 多出来的"测试默认 OFF / 产品默认 ON"分裂,
是个**永久的 cognitive load**——每次写新测试都得问"我需不需要打开
extraction"。Phase 12 之前应该评估能否把 extract 设计成更"测试无感"的
形态(比如检测到 stub 客户端时自动跳过)。

### 3.4 ⭐⭐ 三层 retry 的设计:外层 timeout + 中层 PTL drop + 内层 stream retry

`summarize()` 三层嵌套 retry 是 Phase 11 唯一的"复杂度集中点":

```python
asyncio.wait_for(timeout=25.0):           # 外:绝对时间上限
    for ptl_attempt in range(3):           # 中:context 太大 drop 1/5 重试
        for stream_attempt in range(2):    # 内:streaming 中途断重连
            await client.stream_message(...)
```

设计取舍:

- **外层 timeout** = 用户感知上限(L4 不能让用户等 1 分钟)
- **中层 PTL retry** = 业务意义重试(messages 太大就丢一部分再试)
- **内层 stream retry** = 网络瞬态(socket close / read 中断)

三层独立 max,任何一层 exhausted 不影响其他层的剩余 budget。
**总最坏耗时** = `timeout`(因为外层管死)。**总最坏 LLM 调用次数** =
`3 PTL × 2 stream = 6`。

**踩坑**:测试一开始只测了"3 PTL 用尽 raise PTL",没测"内层 stream
失败重试再失败 raise OpenHarnessApiError"。后者作为 contract 同等重要——
单独加了 `test_streaming_retry_exhausts` 才覆盖。

**判断 framework**:

| 多层 retry 设计的 sanity check |
|---|
| 每层 retry 的失败语义是否真的不同?(不同 = 该层成立;相同 = 折叠) |
| 是否存在"内层成功但外层 timeout"的中间态?契约怎么处理? |
| 各层 max 加起来的最坏 LLM cost 是否还在预算内? |

### 3.5 mid-session context-window 折叠的实战 —— 凡能 checkpoint 的全部 checkpoint

Phase 11 跑到 T5 中途撞到 context 折叠,折叠回来后状态:

- 所有已 commit 的代码 / 文档 / 测试 ✓ checkpoint 在 git
- 未 commit 的 in-progress 改动 ✓ checkpoint 在 working tree
- 当前 task 进度 ✓ checkpoint 在 TaskCreate / TaskUpdate
- "我刚刚做到 T5 哪一步" ✓ 由 summary 恢复

但 **缺失** 的是**为什么 MCP 测试 fail** 的具体侦察脉络(`/private/tmp`
里的 debug print 输出 / stderr 内容)。折叠后重新走 debug print 路径
才定位到 extraction default-ON 是元凶。

**Lesson**:长 session 任何"我现在想到的诊断假设"在能直接转化成代码 /
测试断言时,**立刻转化**——不要靠对话记忆。折叠后那些"我刚刚验证过 X
不是问题"全部要重做。

**判断 framework**:

| 长 session 信息保存的优先级 |
|---|
| 已确认的事实 → commit message / 测试断言 |
| 进行中的工作 → working tree + Task list |
| 待验证的假设 → 直接写成测试断言或 TODO 注释 |
| 已排除的假设 → 不必保存,但写成 retro 让后人知道走过 |

---

## 4. 预测 vs 实际踩坑

### 4.1 plan 里 plan §risks 预测的 8 个风险

| 预测 | 实际命中? |
|---|---|
| summarize() PTL turtles-all-the-way-down | ❌ 三层 retry 设计正确,没有递归 |
| Stopwords 破现有 Phase 10 relevance 测试 | ⚠️ 部分中——1 个测试(body_hit_alone)需要倒置语义,其余 18 个 unaffected |
| 9-slot prompt 输出不解析 | ❌ 测试拉了 5 个 known good response,`_extract_summary` 正常工作;失败回退路径有但没触发 |
| extraction 默认 main 模型成本 | ⚠️ Phase 11 不评估,留给 Phase 12 retro 看真实使用量 |
| session_memory file race | ❌ os.replace 原子性 + read_text single shot 设计如预期,无 race report |
| PreApiCall rerun 破现有 hooks | ❌ default False 保兼容,所有现有 hooks 一次没 re-fire |
| TEAM scope 6 个 regex 误伤 | ❌ 6 个 regex 都是 prefix-anchored,无误伤(测试覆盖了 PEM/AWS/etc.) |
| engine extract 加 2-3s 延迟 | ✅ 命中——awaited(blocking)实施确实加延迟,但 retro 评估**保 await**:fire-and-forget 错误吞没风险更高,2s 延迟对 single-shot ask 可接受;Phase 12 评估是否值得切 |

**评估**:预测命中率 1/8 严重 + 2/8 部分 + 5/8 没中。**预测准确度比
Phase 10 (1/3 命中) 更低**,但所有"没中"都是因为**设计阶段就主动
mitigation 了**(三层 retry / default False / prefix-anchor),不是
漏掉。

### 4.2 没预测到但出现的踩坑

1. **extract default-ON + stub LLM 的测试噪声**(T4 完成后 conftest 修)
   —— §3.3 详述,影响所有用 stub 的现有测试。
2. **MCP integration 测试绕开 conftest 的 extraction-off**(T5 中)
   —— `pytest.mark.integration` 是设计上的 escape hatch,但意外让
   extraction default-ON 的副作用泄漏到这些测试。修法:test 自己
   setenv。**未来 framework lesson**:integration 测试也需要默认的
   "干净 env"基线,只是 vs 真 API key 那种"必须用真 env"的少数。
3. **compact L4 测试受 L2 干扰**(T7-7b)—— L2 把 long-text 消息
   collapse 到 head 900 + tail 500,导致总 token 数掉到 threshold
   以下,L4 不触发。修法:测试改用 50 条 2000 字符消息(每条都
   < L2 阈值 2400)。**未来 framework lesson**:测试 deeper layer 时
   要构造 "earlier layers 帮不上忙" 的 input shape。
4. **覆盖率从 95% 跌到 93%**(T7 收尾时发现)—— 新 src/ 多了 2370 行,
   error-handling 分支没自动覆盖;靠 13 个 backfill 测试拉回。
   **未来 framework lesson**:capability acceptance 应该写 "目标
   coverage delta",T1-T6 各 capability 完成时 measure,不等 T7。
5. **conftest debug print 在 fixture setup 阶段不显示**(T5 调试时)
   —— pytest 默认 capture fixture setup 的 stdout/stderr;`-s` 不
   always 解决,得 `--capture=no` 或 result.stderr 检索。
6. **`HookResult.modify` 不存在**(T7 backfill 时)—— 实际 API 是
   `modify_request / modify_input / modify_output`,粒度更细。文档
   错;修了 callsite。

---

## 5. Phase 12 预测

Phase 12 候选(plan 列的 "Phase 11 specifically NOT mitigated"):

- **`oh ask --resume`** —— 启动时读 session_memory checkpoint 重建
  上下文,作为 fresh ask 的"上半场"
- **session snapshot 格式** —— Phase 11 的 5-slot markdown checkpoint
  够人读,但 resume 需要的恢复信息更多(tool_metadata 完整 dict /
  hook state / permission_mode);可能引入 JSON snapshot 二级格式
- **auto-dream subprocess** —— 周期性后台 consolidation(合并语义
  相近的 memory / GC 一年没用的)
- **`oh memory add` 显式写 CLI** —— extraction 后这条 case weaker
  but 仍有"我想手工归档某个发现"的场景

### 5.1 预测会踩的坑

1. **resume 重建 ToolRegistry / HookRegistry 状态**:Phase 11 已经把
   `QueryContext` 加了 `memory_store / session_memory_path / extract_*`
   等 9 个新字段,resume 必须重新构造这些。**对策**:写一个
   `QueryContext.from_snapshot(snapshot)` factory,显式列出哪些字段
   来自 snapshot / 哪些来自重新 discover。
2. **snapshot vs git working tree drift**:resume 时如果代码已经改了
   (用户中途 edit 了 source),老 snapshot 里的"file X 在 line N"是
   stale 信息。**对策**:snapshot 存 git commit hash + 启动 resume 时
   warn 如果当前 HEAD 不同。
3. **session_memory 模板格式 v1 → v2 演进**:Phase 11 的 5-slot
   markdown 是手工模板,Phase 12 可能想加 fields(tool_metadata 白名单
   / token usage history)。**对策**:文件头加 `# Session Memory v1`
   sentinel,read 时分流 parser。

### 5.2 Phase 11 之后的"待 mark"sub-decisions

| 题 | 我的 lean | 等 phase 12 boundary doc 拍 |
|---|---|---|
| extraction 跑 fire-and-forget 还是 awaited | awaited(Phase 11 选)看真实使用一周后 retro | ✓ |
| session_memory 写入触发频率 | 每 user turn 末(Phase 11 design,但 T7 没实施 engine 端 writer)| ✓ |
| snapshot 格式 | JSON(同 HKUDS,不自创);5-slot markdown 留给 L3 read | ✓ |
| TEAM scope 何时显式 push 给 git | 不自动 push,留给用户手工 commit team/ 目录 | ✓ |

---

## 6. Phase 11 总结

- **7 capabilities 全 ship** / 12 commits / 单 session(中间一次 context
  折叠继续推进)
- **10 个保护目录 git log 验证:9 zero-diff + 1 additive-only**(bundles/
  的 D29.7 plumbing)⭐⭐⭐
- **`summarize()` 一个原语在同 phase 内被 2 个 consumer 共用**——
  Phase 10 retro §3.1 的预测兑现 ⭐⭐⭐
- **Phase 4 PreApiCall 重跑债 + Phase 10 stopwords sub-decision 双双关闭** ⭐⭐
- 1789 测试 / ruff + mypy --strict clean / **cov 95%**(从 93.26% 通过
  backfill 拉回)
- 1 个 testability tax(extraction default-ON 污染 stub 测试)成立,
  conftest + per-test setenv 缓解,**结构性更优解 Phase 12 评估**
- 0 个 boundary doc invariant 被破坏;1 处 plan 阶段未预见但 boundary
  doc 阶段决定的 additive bundles/ plumbing

**Phase 12 起步状态**:`services/`(summarize / compact / session_memory /
extract)+ `memory/`(read + write 全闭合)+ `compact L0-L4 + /compact`
就位,可以直接在 session-resume infrastructure 上开 snapshot + auto-dream。

Phase 10 把 "read 与 write 分两 phase" 的实验做完了。**Phase 11 验收
结果**:读 / 写分开**确实降低了 Phase 11 的设计认知负担**——T4 (write
extract) 不需要回头改 T1 (read substrate 已 ship 在 Phase 10);
write 路径完全 additive 在 read 路径之上。Phase 12 的 resume 也将受益
于同样的分层。

但 substrate 复利的真正成本——**N=1 → N=2 跨 phase 的 6 个月延迟**——
在 Phase 11 内部被 compress 成 "T1 → T4 跨 capability 的同 session
延迟",所以**复利证据更早也更可疑**:N=2 共用是验证 substrate 的
最低门槛,要等 Phase 12+ 看 N=3 (resume 加进来) 才能真正确认。
