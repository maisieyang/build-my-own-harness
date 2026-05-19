# Learnings — Phase 6+ (`oh chat` multi-turn REPL)

> Phase 6+ 起止 / 2026-05-19(单日,接 Phase 7c retro 后开启)
> 3 capabilities (P6+-T1…T3) / ~5 commits / ~330 行生产代码
> (cli.py +280 + protocols/stream_events.py +15 + engine/query.py +10
> + commands wiring) / 9 新增 tests / coverage 持平
>
> 本文件回答的题:**多轮对话的 state hand-off 用什么形态最好 ——
> 新 stream event vs return value vs mutable kwarg;以及 engine 这种
> "yield 事件流" 的 generator 怎么暴露 final state。**

---

## 1. 数据点

| 维度 | Phase 7c(runtime kwarg) | **Phase 6+(多轮 REPL)** |
|---|---|---|
| Capability | 3 | **3** |
| 生产代码 | ~30 行 | **~330 行**(cli.py 主体 _run_chat) |
| 新 protocols | 0 | **1 个 stream event(ConversationCompleteEvent)** |
| 新 engine 代码 | 0 | **~10 行**(yield event at exit) |
| 改其他层 | 0 | **0**(engine + protocols 加新东西,但 hooks/permissions/etc 0 行) |
| 新测试 | 9 | **9**(3 engine + 6 chat CLI) |
| Phase 修改后总 tests | 1230 | **1239** |
| 时间 | 半天 | **大半天**(REPL bootstrap 主要时间在 inline) |

**关键观察**:Phase 6+ 是 **first user-facing 新 surface**(`oh chat`
不是 7c 那种「加 flag 改单字段」)。但它通过 **一个新 stream
event** 让 engine 跟 REPL 解耦 —— engine 完全不知道 REPL 存在,
只是多 emit 一个结束 event;REPL 完全不动 engine。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P6+-T1 — `ConversationCompleteEvent`** | 新 stream event 类(`messages: list[ConversationMessage]`)+ engine 在 end_turn 退出前 yield 一次。**`oh ask` 完全不知情**(其 renderer 不处理这个 event,直接 ignore)。3 个新 engine tests;5 个 downstream tests 改了 assertion 因为 event count +1。 |
| **P6+-T2 — `_run_chat` REPL** | `asyncio.to_thread(input, ">>> ")` 让 sync `input()` 跟 async 主循环共存。每 turn:`input` → 拦截 `/exit`/`/clear`/`/help` 内置 → 走 Phase 5b slash command resolution → 走 Phase 5d bundle resolution(只 first turn)→ run_query + 包装 generator 捕获 `ConversationCompleteEvent.messages` → 用作 next turn 的 initial_messages。**~280 行 inline bootstrap**(跟 `_run_ask` 70% 重叠 —— 留作 Phase 9 polish refactor)。 |
| **P6+-T3 — invariant + README + retro** | `TestPhase6PlusConversationCompleteInvariant`(34 个 protected modules 0 ref `ConversationCompleteEvent`)。Formal git-diff vs 7c close:13 个 protected dir 0 行。README 加 6+ section + 本文件。 |

---

## 3. Framework-level 主题 — Phase 6+ 真正学到的

### 3.1 ⭐ 新 stream event 是 generator-based engine 暴露 final state 的正确形态

`run_query` 是 `AsyncIterator[ApiStreamEvent]`。多轮 REPL 需要在
generator 结束时拿到 final messages list。三个候选方案,我考虑了
也写在 boundary doc D24.2:

| 方案 | 优点 | 缺点 |
|---|---|---|
| **新 stream event(选)** | 跟现有 event taxonomy 一致;`oh ask` 自动 ignore | 改 protocols + engine(都是 additive) |
| Generator return value | 没有新类型 | `async for` 吞掉 `StopAsyncIteration` —— 需要手动 `__anext__` 循环抓 return value,丑 |
| Mutable kwarg(`output_messages: list = ...`)| engine 一行改 | 写-only param,所有权不清,async 反模式 |

**新 event 的关键优点**:它是 **opt-in** —— REPL 关心就处理,
`oh ask` 不关心就 ignore(`_stream_render.py` 的 isinstance chain
不匹配新 type 就跳过)。这是 framework extensibility 的标准 pattern:
新功能要么加 opt-in 参数,要么加 opt-in event 类型。

**通用经验**:**generator 的 final state 暴露给 caller**,优先级:
新 yielded event > generator return value > mutable param。事件流
是 "broadcast";其他两个是 "tight coupling"。

### 3.2 `asyncio.to_thread(input, ">>> ")` 让 sync input() 跟 async REPL 共存

REPL 是天然 sync(`input()` 是阻塞 sys call)。但 `_run_chat` 是
async(因为 `run_query` 是 async)。直接 `input()` 会阻塞 event
loop,导致后台任务(网络 stream / Docker exec)饿死。

`asyncio.to_thread(input, ">>> ")` 把 sync 调用扔到 thread pool。
event loop 继续跑其他 coroutine(在 chat 里其实没别的,但**未来
扩 background**(自动 compaction、metrics emit、watchdog)就不会
broken)。

也尝试过 `aioconsole.ainput()`,需要新依赖。`asyncio.to_thread`
是 stdlib(3.9+),够用。

**通用经验**:async 程序里调 sync I/O,**永远走 thread pool**。
否则就是 event loop 饿死,bug 难定位(看起来一切正常,只是慢 /卡)。

### 3.3 First-turn-only bundle resolution(D24.4)是 MVP 的正确范围

`oh ask` 是 per-invocation:每次 `oh ask /review args` 都重新 resolve
bundle → 构 QueryContext。但 `oh chat` 是一个 session 多 turn:

**真问题**:用户 turn 1 输入 `/review args`(触发 code-review bundle),
turn 2 输入 `now write tests` —— turn 2 应该用 code-review bundle
吗?

3 个候选:
1. **Bundle locks at first turn**(选):turn 1 设的 mode 用到 session 结束
2. Bundle resets each turn:每 turn 重新 resolve(用户每次都得加 /review)
3. `/mode <name>` 显式切换:中途换 bundle

选 1 因为:
- 跟用户心智模型一致(进 review mode = 整个 session 都是 review)
- 实现最简单(QueryContext 在 session 外构造,inside loop 复用)
- 不引入新概念(没有 mode-switching 状态机)

选 3 需要中途**重建 QueryContext**(切 system prompt + 切 registry
+ 切 hook_registry),非平凡。defer。
选 2 用户体验差(每 turn 重输入 mode prefix)。

**通用经验**:多轮交互的 stateful 决策应该在 **session 级别**,
不是 **turn 级别**。turn-level state machine 容易爆复杂度。

### 3.4 Generator 包装捕获事件 —— 标准 stream interceptor pattern

`_capture` 是包装 generator:

```python
async def _capture(events_iter):
    nonlocal captured
    async for ev in events_iter:
        if isinstance(ev, ConversationCompleteEvent):
            captured = ev.messages
        yield ev
```

它做两件事:
1. 旁路捕获 `ConversationCompleteEvent.messages`(REPL 用)
2. 透明 forward 所有 events(`render_stream` 用)

**关键**:一个 generator 可以同时被多个 consumer 看到吗?**不能**
(generator 是单消费者)。所以必须 wrap —— 一个 generator 看,另一
个 generator forward。

这个 pattern 在 unix shell 里叫 `tee`。在 async generator 里叫
"middleware generator"。

**通用经验**:async generator 流式 data 要同时被「分析」+ 「forward」,
永远写一个包装 generator。不要 `events = list(events_iter)` 把整个
流 buffer 起来(失去 streaming 语义 + 延迟尖峰)。

### 3.5 Engine + protocols 加东西不算违反 invariant

Phase 5d/5e/5f/7c/8 都强调 "engine + protocols 0 diff"。Phase 6+
**改了 engine 和 protocols**(虽然都是 additive)。这破坏 invariant
吗?

不,因为:
- **invariant 一直是「不改 contract,加 新 contract OK」** —— protocols
  加新 event type 是 additive(`ApiStreamEvent` union 加一个),不
  破坏旧 callers
- **engine 加新 yield 是 additive** —— 旧 caller(`oh ask`)的事件 loop
  不变,只是多 iterate 一个新 type(被 isinstance chain 忽略)
- **真正禁止的是**"改 hook executor 跑 6 个事件而不是 5 个"、"改
  permission_checker decide() 签名"、"改 ToolRegistry.get() 返回
  类型" —— 这些是 contract change

**通用经验**:invariant 的精确表述是 **"public surface stays
byte-identical to non-aware callers"**。新加东西如果不影响 not-aware
callers,就是 additive,不违反。

`oh ask` 没改一行代码,行为完全跟 Phase 7c 一样 —— **这是
invariant 真正测试的对象**。

### 3.6 Inline bootstrap vs factored helper —— 不在引入新功能的同时 refactor

`_run_chat` 跟 `_run_ask` 有 ~150 行 bootstrap 重叠。两个选择:

1. 先 refactor `_run_ask` 抽出 `_build_query_context()` helper,然后
   `_run_chat` 也用它
2. 先 inline 重复,**Phase 9 polish refactor**

我选 2。理由跟 Phase 8 的 rule-of-three 一样:**重复一次 OK,出现
第二次(本来就是 P6+)还能容忍,出现第三次(P9 hypothetical 比如
`oh server`)再抽**。

更重要的:**「引入新功能」和「refactor」不要同 commit**。混在一起
出 bug 难判断是新功能 broken 还是 refactor broken。Phase 6+ 是新
surface,refactor 是它自己的项目,等 Phase 9 polish。

**通用经验**:每个 phase 做**一件新事**。如果 phase 进行中发现需要
refactor 才能 ship,先 ship,把 refactor 写进 retro 作为下个 phase
候选。

### 3.7 Test fixture stub `ApiStreamEvent` 模拟 conversation event

T2 测试需要模拟 engine emit `ConversationCompleteEvent`。stub:

```python
async def _capturing_run_query(initial_messages, context):
    captured_initial_messages.append(list(initial_messages))
    assistant = ConversationMessage(role="assistant", content=[TextBlock(text="reply")])
    yield ApiMessageCompleteEvent(message=assistant, ...)
    yield ConversationCompleteEvent(messages=[*initial_messages, assistant])
```

stub 不跑真 engine,但 emit 正确的 event 序列。CLI loop 看到的
behavior 跟跑真 engine 一样。

这是 **interface-driven testing**:test 不验证 engine 内部逻辑(那是
engine 自己的 test),只验证 CLI loop 跟 engine **接口**对接的部分。

**通用经验**:cross-layer test 应该 stub 邻接层的 **interface**,
不 stub interface 的具体实现。stub interface 让 test 跑得快、跨平台
稳、不依赖被测层的 internal 状态。

---

## 4. Phase 6+ 没做的

| 不做 | 理由 |
|---|---|
| 多行输入(paste support) | 需要 `prompt_toolkit` 或自实现 line-continuation parser。Phase 9 candidate。 |
| `/save <file>` / `/load <file>` | session 持久化 schema 需要 versioning 设计。Phase 9 candidate(随多行输入一起)。 |
| 中途 `/mode <name>` 切换 | §3.3 — 需要重建 QueryContext。Phase 6+.1 candidate(等真有用户需求)。 |
| Tab completion / history recall(↑↓ 翻历史) | 需要 `readline` 或 `prompt_toolkit`。stdlib only 是 D24.1 的明确决定。Phase 9。 |
| 用户体验细节:`>>> ` 颜色 / typing indicator / 流式 token 速率显示 | 渲染层 polish,Phase 9。 |
| `oh chat` 测试覆盖 LoopError / OnError hook 调用 | 单元测试 happy path 够了;error path 已有 `_run_ask` 的覆盖,REPL share 同样 chain。 |
| `_build_query_context` helper refactor 从 `_run_ask` + `_run_chat` 抽公共 | §3.6 — Phase 9 polish。 |
| 自动 conversation 压缩(turn 10+ 时 summarize 老 turns) | 本身是一个研究项目;Phase 4 的 hook-based per-call truncation 已经处理 tool output size,whole-conversation summarization 是另一回事。 |

---

## 5. 给下一阶段的人

- **Phase 9 polish** 候选:`_build_query_context` 抽公共 helper(让
  cli.py 从 ~1000 行降到 ~600 行)。当下一个 surface(`oh server`?
  `oh test`?)出现时一起做 rule-of-three refactor。
- **Multi-line input** 用 `prompt_toolkit`,~500KB 依赖。带来的副作用:
  fish-style autosuggest / arrow-key history / completion / undo。是
  unix REPL 的标配。`oh server` 没这个需求,**当 multi-line 真痛的
  时候再加**。
- **Session persistence**(`/save`/`/load`):schema 需要 forward-
  compat —— ConversationMessage 字段会演化(Phase 8 加了 mode,
  Phase 5e 加了 plugin hooks 的副作用 state),save format 必须
  version 化。建议:`{"version": 1, "messages": [...], "metadata": {...}}`
  with skip-unknown-fields on load。
- **`/mode <name>` 切换** 需要在 session 中途重建 QueryContext。
  非平凡但可行:把 QueryContext 构造从 `_run_chat` outer scope 提到
  inner-loop 顶部,每 turn 用 current `effective_*` 重建。但要小心
  hook 状态(已经 register 的 hook 在 QueryContext 替换后失效吗?
  Phase 5d 的 hook_registry clone pattern 是不是 idempotent?)。
- **REPL 性能**:`input()` 通过 `to_thread` 是 OK 但有 thread pool
  overhead(~微秒级别)。对 `oh chat` 不是问题(用户输入慢)。但如
  果做 `oh batch`(从文件读 N 条 prompt 自动跑)就要考虑 batched
  iteration 而不是 thread-per-input。

---

> **本 Phase 一句话总结**:
>
> 多轮对话的 state hand-off 是 **新 stream event** —— `oh ask` 不
> 知情(ignore the event),`oh chat` 解锁多 turn(consume the event)。
> Engine + protocols 加新 type 不算违反 invariant,因为 not-aware
> callers byte-identical。`asyncio.to_thread` 让 sync `input()` 跟
> async REPL 共存;generator-wrap pattern 让一个事件流既渲染又被
> 内省。**第一个用户 facing 新 surface 在这个 framework 抽象之上**,
> 用的成本(15 LoC engine + 1 new event)兑现了之前所有 phase 的
> 抽象红利。
