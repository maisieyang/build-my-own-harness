# Learnings — Phase 4 (Compaction / Context Management)

> Phase 4 起止 / 2026-05-14 – 2026-05-15 / 用时 ~1.5 天
> 5 capabilities (T1-T5) / 12 sub-units / 6 commits / 709 tests / 97% coverage
>
> 本文件回答:**做完 Phase 4,关于 "把 stateless replay 协议变成有 budget
> 的状态管理" 这件事,学到了什么 framework-level 的东西。**

---

## 1. 数据点

| 维度 | Phase 3 | Phase 4 |
|---|---|---|
| Capability(task) | 6 | **5** (T1-T5) |
| Sub-units | 30+ | **12** |
| Decision records | 2 | **1** (D10 boundary, D14.1-D14.5) |
| 总测试数 | 628 | **709** (+81) |
| 总覆盖率 | 96.90% | **97.00%** |
| 总 commits | 32 | **6** |
| Phase 4 加的 module | — | `compaction/` 4 files |
| Phase 4 触碰的既有 module | — | `api/errors,client`, `engine/messages,query`, `hooks/context`(Phase 3 retrofit) |
| Phase 4 添加的 log events | — | **2** (tool_truncated / reactive_truncate),inventory 8 → 10 |
| Phase 4 添加的 CLI flags | — | **2** (--tool-result-cap / --no-auto-truncate) |
| Phase 4 添加的 Settings fields | — | **2** (tool_result_cap / auto_truncate) |
| Phase 4 添加的 error subclasses | — | **1** (PromptTooLongFailure) |

**Phase 3 retro prediction**: "Phase 4 比 Phase 3 短一半" — 实际 **短 4 倍**(6 commits vs 32)。原因:Phase 4 是 horizontal capability,Phase 3 把 hook + observability 基础设施都备齐了;Phase 4 只是它们的 tenant。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **T1 — Token counter** | `tiktoken` + bytes//4 fallback 两条路径 / LRU cache(32)/ 单 public function。**Phase 4 唯一会被频繁调用的 hot path,设计成无状态** |
| **T2 — Layer 1 (per-tool truncation)** | `head_tail_truncate` 纯函数 + `TruncateToolResultHook` 类(PostToolUse,Codex 风格 head+marker+tail)+ `tool_truncated` log。**dogfood Phase 3 hook 系统** —— 0 engine 改动 |
| **T3 — Layer 2 (reactive prompt-too-long)** | `PromptTooLongFailure(RequestFailure)` + 7 provider phrasings + `drop_oldest_tool_pair` 纯函数 + engine `while True` retry loop with `_REACTIVE_TRUNCATE_MAX=3`。**engine 内置**,不走 hook — loop control flow 跟 horizontal capability 不同 |
| **T4 — CLI / Settings + e2e smoke** | 2 flags / 2 settings / 1 conditional 默认 hook 注册 / 3 个 e2e smoke。**flag → settings → hook registration 链** 跟 Phase 3 logging 模式完全一致(3-tier 优先级)|
| **T5 — Retro** | Coverage 97%(超 95% gate);此文件 |

---

## 3. Framework-level 主题

### 3.1 双层防御 vs 三级渐进 —— 抄哪家 + 哪家不抄

Phase 4 boundary 锁了 **两层** 不三层。原因(WebSearch + 实测得出):

| 来源 | 方案 |
|---|---|
| **OpenHarness REFERENCE §16** | 3 级:Microcompact / Session Memory / Full Compact(LLM-as-summarizer)+ Reactive 前 900 + 后 500 |
| **OpenAI Codex** | per-tool 10k tokens(head+tail+marker)+ 服务端 encrypted compaction(用户拿不到)+ Reactive |
| **OpenHarness 项目(我们)** | **Layer 1 借 Codex per-tool** + **Layer 2 借 OpenHarness reactive** —— **不做 LLM-as-summarizer** |

为什么不做 LLM-as-summarizer?
- **复杂度爆炸**:summarize 自己要调 LLM → 加一次同步调用 → 失败/超时/cost 都要处理 → 实质上是另一个 sub-engine
- **80/20 覆盖**:Layer 1+2 已覆盖 80% 实际场景(常见死法是 tool_result 太大,而不是历史太长)
- **Phase 5+ 再加不晚**:Boundary doc 明确列出 deferred 4 项,留位

判决:**借两家的长处但不重复发明轮子**。Codex 的 per-tool 是 specific-context optimization(代码场景);OpenHarness 的 reactive 是 general fallback;两者**互补 + 简单**。

### 3.2 Compaction 该走 hook 还是 engine 内置 —— 看是横向能力还是 loop control

Phase 3 retro §5 当时预测:"Compaction is horizontal capability...挂在哪个 lifecycle 位置先想清楚"。Phase 4 给的答案:**两层各走一条路**。

```
Layer 1 (per-tool truncation)    → PostToolUse hook
   - 触发点:tool 执行完,output 在手
   - 决策本质:tool output 是不是太大
   - 横向能力:跟其他 user hook(log / sanitize / cost-track)同位
   - 用户可禁用(--no-auto-truncate)

Layer 2 (reactive retry)         → engine 内置(run_query while loop)
   - 触发点:stream_message 抛 PromptTooLongFailure
   - 决策本质:整个 conversation 太长 — 哪个 turn 丢掉
   - loop control flow:retry 逻辑跟 turn 计数 / 终止条件耦合
   - 用户不可禁用(framework 自保护)
```

判决:**抽象选择由"决策语义和 loop 的耦合度"决定**,不是审美。Layer 1 决策只看 tool result,不影响 turn 流程 → hook 干净;Layer 2 决策影响是否进下一轮 / 是否消耗 turn budget → engine 必须知道。

### 3.3 一个 retrofit 揭示的 Phase 3 设计 gap

Phase 4 T2 build 时发现:`PostToolUseContext` **没有 `tool_use_id` 字段**。结果:
- hook 想 log "我 truncate 了哪个 tool 调用" → 只能拿 `tool_name`,**同名多调用区分不开**
- `tool_dispatch` / `tool_complete` / `tool_truncated` 三个 log event 想在 trace 上**关联** → 缺关联键

修复:Pre/Post ToolUseContext 都加 `tool_use_id: str` 字段(additive but breaking — 10 个 test 构造点更新)。

判决:**framework field 决策要在第一个真消费者出现前就压测**。Phase 3 给 hook 设计 context 时没有具体消费者,凭"看起来够用"裁字段 → P4 来了一个真消费者立刻翻车。下次新设 context dataclass,**至少跑过 2 个具体 use case 的字段需求**再锁。

### 3.4 渐进式 sub-unit 拆分有时反而过细 —— Phase 4 的 T2/T3 合并 commit

Plan 标 T2 拆 3 sub-units(2a 纯函数 / 2b hook 类 / 2c log event),T3 拆 4(3a error class / 3b pair-drop / 3c engine integrate / 3d bounded)。实际 build 时 **每 task 合并成 1 个 commit**。原因:

- T2:2a 是 2b 的实现细节,2c 是 2b 的副作用 → **commit 3 个会让 reviewer 看 3 次同一段语义,不如一次看完整图**
- T3:同理,3a-3d 是一个完整 reactive truncation feature 的不同切面

vs Phase 3 T4 hook 系统(8 个 sub-commits)— 那里每个 sub-unit 引入**新 module 文件**(events.py / result.py / context.py / registry.py / executor.py),每个独立编译/测试,所以分开 commit 有信息密度。Phase 4 T2/T3 都在已有 module 里加东西,**不分**更读得明白。

判决:**sub-unit 是规划单元,commit 是审计单元**。规划阶段拆细让心智清晰;build 时如果发现 sub-unit 间没法分别 review,合并 commit 更诚实。下次 plan 不强求 1 sub-unit = 1 commit。

### 3.5 Pattern match 列表的演化策略

`_PROMPT_TOO_LONG_PATTERNS` 现在装了 7 种 phrasing(OpenAI / Anthropic / Qwen / generic)。为什么不用 regex?

```python
# 我选的:
_PROMPT_TOO_LONG_PATTERNS = (
    "context_length_exceeded",
    "context length exceeded",
    "maximum context length",
    "prompt is too long",
    "range of input length",
    "input length",
    "input is too long",
)

def _is_prompt_too_long(message: str) -> bool:
    lowered = message.lower()
    return any(p in lowered for p in _PROMPT_TOO_LONG_PATTERNS)

# 没选的:
_PROMPT_TOO_LONG_RE = re.compile(
    r"context[\s_]?length|prompt.{0,10}too long|input.{0,10}length"
)
```

理由:
- **可加性**:5+ provider phrasings 是个长尾,每次出新 provider 加一行字符串比改 regex 直观
- **可读性**:list[str] 是 ops 文档级的清单,regex 是密码
- **性能**:7 个 substring 搜索 + lower 远比一个 regex 编译+match 便宜
- **debug**:test 失败时看是哪个 pattern 没匹中 / 测试覆盖每个 phrasing 都对应一行字面 string,定位快

判决:**列表 > regex 在 "已知有限集 + 长尾加新成员" 场景**。Regex 用在 "无限可能空间,需要语法压缩"。这是个反复出现的原则,Phase 3 sanitize / Phase 4 prompt-too-long 都验证过。

### 3.6 Reactive truncation 的 4 个 invariants

T3 build 时识别出 4 个独立 invariants,每个一条 test:

1. **Recovery 真能恢复** —— 1 次 PTL + 1 次 truncate → 成功(`TestRecoverySucceeds`)
2. **Bounded 真能 bound** —— max+1 次 PTL → re-raise(`TestBoundedRetries`)
3. **Pair drop 不消耗 turn budget** —— 1 turn 内多次 retry 不让 LoopLimitExceeded 误触(`TestMaxTurnsBoundary`)
4. **失败 attempt 不污染输出** —— event stream 只看到成功 attempt 的事件(`TestEventOrder`)

**第 4 条最容易漏写但最 subtle**。`async for event in stream_message: yield event` 在 PromptTooLong 抛之前可能已经 yield 了几个 event,这些事件**已经离开 generator 了**,test 必须 verify caller 没看到它们。Implementation 上是"失败 attempt 不 emit terminal event,所以 caller 没办法把它当 turn 计入"—— 但这是 Lucky 不是 设计。明确测才能锁死。

判决:**重要 invariants 一条一个 test,不要混在一个测试里**。Phase 4 的 T3 测试矩阵是这条原则的范例。

---

## 4. Phase 4 的契约预测 —— Phase 5/6 会验证什么

### 4.1 reactive retry 的 PreApiCall hook 限制

Phase 4 boundary 显式标了:**PreApiCall hook 在 reactive 重建 request 时不会重跑**。意味着如果 user 用 PreApiCall 做"memory injection",reactive truncation 之后**memory 段落丢失**(被覆盖)。

Phase 5+ 可能加:
- 选项 A:reactive 重建后再跑 PreApiCall
- 选项 B:把 PreApiCall 拆成 "data hook"(每次 request 都跑)和 "decision hook"(每 turn 一次)

Phase 4 等观察具体 user pain 再选。

### 4.2 LLM-as-summarizer 真需要时该怎么加

Phase 5+ 如果发现 Layer 1+2 不够(典型场景:对话已经压平到没 tool pair 可 drop,但 user prompt 本身就太长),要加 Phase 3 missed 的第 3 层 —— **Full Compact**。怎么加?预测路径:

```python
# 新 hook:CompactionHook(LLM-as-summarizer)
class CompactionHook:
    async def __call__(self, ctx: PreApiCallContext) -> HookResult | None:
        if estimate_total_tokens(ctx.request) > self.threshold:
            summary = await self.summarize(ctx.request.messages, ...)
            new_request = ctx.request.model_copy(update={
                "messages": [
                    ConversationMessage(role="user", content=[TextBlock(text=summary)]),
                    *ctx.request.messages[-RECENT_KEEP_N:],
                ]
            })
            return HookResult.modify_request(new_request)
        return None
```

仍然是 hook!**Phase 3 hook 系统 + Phase 4 二层防御 + Phase 5+ summary hook = 完整 compaction stack**。

### 4.3 Phase 6 sub-agent 怎么和 compaction 互动

Sub-agent 每个有自己的 `run_query` 实例,意味着 Layer 2 reactive **per-agent 独立工作**(各自 `messages` / 各自 retry 计数)。Layer 1 hook **如果 registry 在 parent context 上**会被 child 继承 → 子 agent 也自动有 truncation。这正是 hook 模式的红利。

预测:Phase 6 不需要为 sub-agent 单独加 compaction,只需保证 `hook_registry` 跟 sub-agent context 一起传递。

---

## 5. 给 Phase 5 的 input

1. **不要重新讨论 compaction 的 two-layer 决定** —— Phase 4 已经验证够用。Phase 5 想加 LLM-summarizer 直接走 PreApiCall hook,additive 加。
2. **`tool_use_id` 是真有用** —— Pre/Post ToolUseContext 加它的决定不要回滚。Phase 5/6 hook 全靠它做 trace correlation。
3. **`_PROMPT_TOO_LONG_PATTERNS` 是长尾** —— Phase 5 新增 provider(Anthropic-native client / DeepSeek / ...)时,**在这个列表里加 phrasing,不要重发明轮子**。
4. **PreApiCall + reactive truncation 的 interaction** 是已知 limitation,Phase 5 要做 memory injection 时先解这个题。

---

## 6. Phase 4 最浓缩的 1 句

> Phase 4 把 "messages 无限累加" 升级到 "messages 有 budget 受控":Codex 的 per-tool head/tail truncation 截单点;OpenHarness 的 reactive prompt-too-long retry 兜底全局。**核心一句:framework 不写 LLM 摘要逻辑(那是 user 的 hook 业务),只保证 LLM 永远收得到能跑的 request。**

---

## 7. Pointers

- Phase 4 boundary: [`decisions/10-phase-4-boundary.md`](../decisions/10-phase-4-boundary.md)
- Phase 4 plan: [`tasks/phase-4-plan.md`](../tasks/phase-4-plan.md)
- Phase 3 retro(对照 framework-level 主题结构): [`learnings/phase-3.md`](./phase-3.md)
- 6 个 P4 commits:`git log --oneline | grep -E "P4-T|phase-4"`
- Codex compaction 来源: [OpenAI Developers — Compaction guide](https://developers.openai.com/api/docs/guides/compaction)
- OpenHarness 3 级压缩参考: [`REFERENCE.md`](../REFERENCE.md) §16
