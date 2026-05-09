# Learnings — Phase 1 + 2 联合复盘(cross-phase synthesis)

> Phase 1 + 2 / 起止日期：2026-04-26 – 2026-05-09 / 用时 ~2 周
> 11 capabilities (P1-T1…T5 + P2-T1…T6) / 9 module retros / 7 decisions + D6-D12 sub-decisions
>
> 本文件**不是**各 module retro 的合集（那些请见 `learnings/01-10-*.md`）。
> 它回答 phase-1.md 当时回答不了的题：**Phase 1 的契约预测，被 Phase 2 真实
> 使用过一遍后，对在哪、错在哪、还需要演进什么。**

---

## 1. 数据点（合并 Phase 1 + 2）

| 维度 | Phase 1 | Phase 2 | 合计 |
|---|---|---|---|
| Capability(task) | 5 | 6 | **11** |
| Module retros | 4（[01-04](.)） | 5（[06-10](.)） | **9** |
| Decision records（顶层） | 5（[01-05](../decisions)） | 2（[06](../decisions/06-phase-2-boundary.md) boundary + [07](../decisions/07-base-tools.md) base-tools） | **7** |
| Sub-decisions（D{N}.{M}） | — | D6.1-D6.5 + D7.1-D7.5 + D8.1-D8.9 + D9.x + D10.x + D11.x + D12.x ≈ **35+** | 35+ |
| 总测试数 | 173 | +179 | **352**（351 passed + 1 integration skipped） |
| 总覆盖率 | 92.83% | — | **94.76%**（gate 70%） |
| 总 commits | 43 | 35 | **78** |
| 工作流元-pivot | 1（spec-heavy → capability-driven） | 0 | 1 |
| Plan 重拆 | 2（P1-T2 字段级合并 / P1-T3 split） | **0** | 2 |
| 跨 Phase 的契约收紧 | — | 2（`QueryContext.tool_registry: object → ToolRegistry` 在 P2-T2.2e；`permission_checker: object → PermissionChecker` 在 P2-T6.6b） | 2 |
| `protocols/` 在 Phase 2 期间被改的 commits | — | **1**（仅 `stream_events.py` 加 ToolExecution{Started,Completed} + `__init__.py` re-export） | 1 |

---

## 2. 每个 task 的 1-line takeaway

> P1-T1…T5 的 takeaway 见 [phase-1.md §2](./phase-1.md)；这里只列 P2 新增。

| Task | 一句话总结 |
|---|---|
| **P2-T1 — Engine skeleton** | "**契约先行、body 后填**"——`QueryContext` 6 字段 + `messages.py` 4 个纯函数 + `run_query` typed stub 全部就位，loop body 留给 P2-T4，**协作者全部独立发育**；用 `object` 占位 + 行内 marker 让"未来收紧"是 grep-able 而非散落 |
| **P2-T2 — Tool 系统抽象** | **`BaseTool[InputT]` 4 slot 契约**（name / description / input_model / execute）+ `Generic[InputT]` 解 LSP 违规 + Pydantic `model_json_schema()` 自动生成 LLM catalog——把"远程过程调用的 IDL"和"本地函数的 handler"绑在同一个对象里 |
| **P2-T3 — 5 个基础工具** | 把同一抽象在 5 个领域具象化，暴露**"IO 模型 × output shape" 二维空间**——同步文件 IO（Read/Write/Edit）vs 子进程 async（Bash/Grep）；output 是数据 / 回执 / 命令输出三种意图；哨兵字符串 `(empty)` / `(no matches)` 防 LLM 误判 |
| **P2-T4 — run_query body** | **76 行心跳** = `while + stop_reason 续 + max_turns 兜底 + 4 recovery 路径 + serial dispatch`；事件流从 7 类提案退到 5 类直通，对照 OpenHarness REFERENCE 后简化方案胜出——**通过实现发现需求 > 凭空臆测事件设计** |
| **P2-T5 — System prompt** | function-driven 装配（`build_system_prompt(tools, env) -> str`）——Phase 2 注入 catalog + env，**Phase 3/4 可在同一函数里加 personalization / memory**，签名是 load-bearing 契约 |
| **P2-T6 — 权限 + CLI loop 接入** | 把所有零件接成 `oh ask` end-to-end + **DRY_RUN 单点 bypass**（dispatcher 层短路 permission/execute，工具层 0 感知）+ **5 except 错误 UX**（每条带 hint）+ **3 个 module-level 测试缝**（`_load_settings` / `_build_client` / `run_query`） |

---

## 3. Phase 1 的契约，Phase 2 实测下来怎样

> 这一章是 phase-1.md 当时下不了的判决——P1 时所有契约都靠"猜未来怎么用"，
> Phase 2 把它们逐一压测了。

### 3.1 `protocols/` —— 反腐败层的二次实测

**判决：thesis 比 phase-1.md §3.1 当时的判决更强。**

Phase 1 retro 已确认 T3 / T4 期间 `protocols/` 一行没动。Phase 2 走完 6 个
capability，`protocols/` 只被改了**1 个 commit**（`c264c92 feat(protocols):
P2-T4.4a ToolExecution events`），且改动是**纯 additive**：在 `ApiStreamEvent`
discriminated union 加 `ToolExecutionStartedEvent` / `ToolExecutionCompletedEvent`
两个新成员 + `__init__.py` 加两行 re-export。**`content/messages/usage/tools/
requests.py` 五个核心文件 0 行改动**——P1 设计的 wire 层在 Phase 2 完整需求下
仍然不需要演进。

**ContentBlock 四变体的实际使用密度**（按 P2 代码 grep）：`TextBlock` 在 user
message 构造里用一次；`ToolUseBlock` 在 `extract_tool_uses` 抽取 + 派发链上多次；
`ToolResultBlock` 在 `append_tool_results` 反复构造；`ImageBlock` Phase 2 **0 次
使用**——预留给 Phase 5+ 的 multi-modal。**最忙的两类（ToolUseBlock /
ToolResultBlock）就是 P1-T2 时未来负载最不确定的两类**——P1 押对了。

含义：反腐败层"实现新成员时上层一行不动"在 Phase 1 是 thesis，在 Phase 2 是
**经过 6 个 capability 反复使用后的事实**。这条原则可以直接进 SPEC 永久章节。

### 3.2 `api/` —— retry 边界 + AsyncIterator 形状

**判决：AsyncIterator 直通形状对，retry 边界清晰。**

P1 押的是 `stream_message(req) -> AsyncIterator[ApiStreamEvent]` 能被 loop / renderer
直接消费而**不需要适配**。Phase 2 实测：`engine/query.py` 的 loop 里 `async for
event in context.api_client.stream_message(request): yield event` 是**字面意义的
直通**——engine 不加工任何 API 事件，只在 tool dispatch 边界自己产生新事件。
P1 押对。

Retry 边界在 Phase 2 没被实测压力——Qwen via DashScope 在 P2 testing 期间稳定，
未观察到 429/5xx 触发 retry。但**这正是设计想要的状态**：retry 在 transport 层
处理，loop 看不见；只有 transport 真的失败到放弃才上抛。

`cli.py` 的 5 except 分支：`ValidationError`（配置错）/ `AuthenticationFailure`
（401/403）/ `RateLimitFailure`（429）/ `RequestFailure`（其他 API 错）/
`OpenHarnessApiError`（兜底，包括 `LoopLimitExceeded`）。**Phase 2 testing 实际
触发的是 `ValidationError`（手动 unset env vars 验证 hint）**；其余 4 条是
"防御性即用即保留"。`LoopLimitExceeded` 落兜底分支是**事后看的妙招**——它的
message 已经名 named `--max-turns` 作为 remediation，不需要专属分支也读得通。

含义：Phase 1 的"5 个分类异常 + 1 个根类"体系在 Phase 2 全 working，Phase 3 加
`ToolError` / `PermissionError` 时可以**继承同样的 root**，cli.py 兜底分支自然
覆盖。

### 3.3 `config/Settings` —— 4 级优先级实际只用到 3 级

**判决：File 层（XDG config）是 dead code，可以放进 Phase 3 review 时删。**

P1-T4 设计的优先级是 CLI > ENV > File > Default。Phase 2 加了
`OPENHARNESS_PERMISSION_MODE` 字段。实际测试覆盖：

- **CLI 级**：`--model` / `--max-tokens` / `--auto` / `--dry-run` 都走 override 路径，**实测每条都用过**
- **ENV 级**：`.env` 文件 + `OPENHARNESS_*` 是默认运行路径，实测主路径
- **Default 级**：`max_tokens=1024` / `model=qwen-plus` / `permission_mode=DEFAULT` 三个默认值都用过
- **File 级**：XDG `~/.config/openharness/config.toml` —— **Phase 2 全程零使用，没有任何代码路径或测试触达它**

含义：File 级是 P1-T4 设计时为了"production 完备性"提前加的——但 1 人项目下这层
冗余。Phase 3 boundary 决策时考虑：**直接砍掉 File 层 → 优先级简化为 3 级**，
或保留作为 Phase 5 多 profile 的伏笔。

`OPENHARNESS_*` provider-neutral 前缀的事后判断：**对**。Phase 2 加 permission_mode
环境变量直接套用，没有"诶我应该用 DASHSCOPE_PERMISSION_MODE 吗"的瞬间。
provider-neutral 是天然的扩展边界。

### 3.4 `engine/QueryContext` —— D7.1 6 字段一次到位的赌注

**判决：赢得很彻底。**

D7.1 当时选"6 字段一次到位 + `object` 占位"对抗"先 4 个、后面 task 再补"。
Phase 2 后段实测：

- **0 次结构改动**（没加新字段、没删字段、没改字段名）
- **2 次类型收紧**：P2-T2.2e（`tool_registry: object → ToolRegistry`）和
  P2-T6.6b（`permission_checker: object → PermissionChecker`），两次都是
  grep `tighten to` marker → 改一行 → mypy/test 自动验证
- 后续 P2-T2/T3/T4/T5/T6 直接 `from openharness.engine import QueryContext`
  使用，**没有任何"诶我需要再加个字段"的瞬间**

**反事实代价**（如果走"先 4 个"路线）：每个后续 capability 都要先改 QueryContext
（加字段 + 改测试 + 改 import 链 + 改 docstring），至少多 4 次跨模块 commit，
而且每次都污染 P2-T1 的提交历史。D7.1 一次性付清这个 cost，零摊薄。

`system_prompt: str`（D7.5 持有结果，不持有 callable）实测：Phase 2 内 prompt 是
启动期一次性 build，**没有任何"想动态切 prompt"的瞬间**。Phase 4 加 memory
（每轮注入 user memory excerpt）会重新评估这条——但即使那时也可以"上层每轮重建
context"实现，不需要 callable。

含义：**"骨架先行、字段一次到位"是 framework 起步阶段的最优策略**——尤其当
框架要被自己人立刻使用、需求清晰时。可迁移到 Phase 3 任何"枢纽数据结构"的设计
（如 hooks 的 PreToolUseContext）。

### 3.5 `_stream_render` —— append-only 的兑现 + 自然演进

**判决：append-only 是对的；双层截断是 emergent 的好设计。**

P1 D5.5 选 append-only 而不是 Rich live re-render。Phase 2 加了 `ToolExecution
Started/Completed` 两条新事件类型的渲染（`[Tool] arg=v\n` + `[Tool] → output\n`）。
**append-only 的承诺被新事件继承**：

- **pipe-friendly 仍然成立**：`oh ask "..." | tee` 输出干净；`oh ask "..." > out.txt`
  stdout 只有 LLM text + tool 行，stderr 才是诊断
- **retry 走 stderr 的决定 zero 反悔**——Phase 2 cli.py 没出现"想合一起"的瞬间，
  因为 retry 是 transport 内部状态，不是用户期望的"答案数据"
- 新加 tool 事件渲染 = `out.write(...)` + flush，与 P1 渲染逻辑同形态——**没有触发
  对 append-only 哲学的重审**

**双层截断（Bash 12k chars vs renderer 500 chars preview）是 emergent 的好设计**：

- Bash 工具自身在 `MAX_OUTPUT_CHARS = 12_000` 截断，喂给 LLM 的内容上限
- renderer 在 `MAX_OUTPUT_PREVIEW = 500` 再截一次，喂给终端的内容上限
- **同一份 tool output 在两个观察者眼里有不同的截断**——LLM 拿 12k（够决策），用户
  看 500（够监督），代码注释直接讲明白：`the full output is still in the
  ToolResultBlock that goes back to the LLM -- this is purely UI hygiene`
- 这条 P1 plan 里没明写，Phase 2 自然涌现——**append-only + 多观察者** 的组合天然
  允许"每个观察者按自己预算截断"

含义：append-only 不只是"渲染策略"，是 **observer-multiplication** 的允许条件——
事件流可以被任意数量的观察者各自消费、各自决策。Phase 3 加 logger / Phase 4 加
session persistence 时直接复用同一事件流。

---

## 4. Phase 2 的"心跳"，实际形状 vs 预设

> 三轮关于 loop / event / tool 的对话（参见对话副产品的关键瞬间）所达成的设计，
> 在 P2-T4 落地后真实长成什么样。这一节是设计 → 代码的事后判决。

### 4.1 事件流：7 类提案 → 5 类直通（"消费者驱动"的胜利）

讨论起步时一度提议 8 类事件（RunStart / TurnStart / TurnEnd / RunEnd / TextDelta /
ToolStart / ToolEnd / Error）+ 严格的顺序 invariant。对照 OpenHarness REFERENCE §5.5
后简化为最终方案：

- **API 层 3 类**：`ApiTextDeltaEvent` / `ApiRetryEvent` / `ApiMessageCompleteEvent`
- **Engine 层 2 类**：`ToolExecutionStartedEvent` / `ToolExecutionCompletedEvent`
- **Error 走 Python 异常上抛**到 `cli.py` 的 5 个 except 分支（不进事件流）

**退到简化方案的真实理由**：

实际消费者（`render_stream`）从第一个 TextDelta 起手、在 generator close 时收尾——
**它从来不问"run 是不是开始了"**，迭代器本身就是这个信号。RunStart / RunEnd 是
仪式性事件，加了不影响 UX，不加也不缺什么。同理 TurnStart 在 terminal 渲染里被
"text 自然流 + tool 行打印"的交错隐含表达，没必要显式。

OpenHarness 跑了 4 年没加这两类事件——是个工业级的"够用"证据。

**反例（什么时候会需要）**：

- **多 query 并行 dashboard**：N 个 `oh ask` 实例并跑，UI 要把事件归到 session，
  必须有显式 RunStart 携带 `run_id`
- **持久化 / replay**：`SessionSnapshot`（REFERENCE §36.4）需要边界事件来 bracket
  "这次 run 该 dump 哪段"
- 两个都是 Phase 5+ 场景；reversibility 完整保留（discriminated union 加成员是 additive）

**洞察**：**事件设计是消费者驱动的**。我们直到写 `render_stream` 才发现 renderer 已经
有自然的起止语义。**通过实现来发现需求 > 凭空臆测事件设计**。

### 4.2 串行 tool 派发（D6.3）实测

D6.3 选 `for tool_use in tool_uses: await execute(...)` 而不是 `asyncio.gather` ——
"correctness first，parallel 留给 Phase 3"。

**实测观察**：

- Phase 2 testing 期间几乎所有 turn 只产 1 个 `tool_use` block。**没观察到 3+ 的情况**
- 多 tool_use 真要出现的场景几乎都是只读类："读 X 和 Y 然后总结"——**这恰是 parallel
  最有价值的场景**

**串行意外带来的 UX 红利**：

- 终端输出严格按时间顺序：`[Bash] command='...'` → `[Bash] → output` → 下一个 tool 起
- **renderer 不需要事件配对逻辑**（按 id 匹配 ToolStart 和 ToolEnd），因为它们一定相邻
- 如果走 parallel，`render_stream` 要么需要重排缓冲，要么需要 per-tool 缩进——
  代码复杂度跳一档

**第一次"我想 parallel"的场景**：

- `oh ask "what's in src/openharness/{cli,_stream_render,engine/query}.py"` ——
  三个独立 read，串行体感慢
- 这也是 parallel 的天然测试 case：三个独立读，无 inter-dependency
- Phase 3 加 `is_read_only` 时是合适的实施时机

### 4.3 4 条 recovery 路径在真实 LLM 行为下的触发频率

`_dispatch_one` 的 4 条 recovery 在 manual testing 下的定性观察：

| Recovery | 实测频率 | LLM 真实反应 |
|---|---|---|
| **Tool not found** (`tool not found: Foo`) | **极少**（catalog 给对就 ≈ 0） | 给 explicit "use Bogus tool" prompt 才触发；LLM 立即从 catalog 选另一个 |
| **ValidationError** (`invalid input for X: ...`) | **最常触发** | 通常下一轮就修对（int 给成 string、漏字段、格式错） |
| **Permission denied** (`permission denied: X`) | **罕见**（Phase 2 只有硬编码 deny-list） | Phase 3 完整权限算法上线后会成主路径 |
| **Tool 自身 is_error** | **频繁**（file not found / non-zero exit） | 通常自动修，比如 Read 失败 → 改路径 / 转 Grep |

**给 Phase 3 hooks 设计的核心输入**：**4 条 recovery 都是"LLM 自己处理，harness 只塑造
反馈消息"的循环**。Phase 3 hooks（`PreToolUse` 拒绝、`PostToolUse` 修饰）应该保持
这个 shape——hook 拒绝产生的 ToolResult 形状要和这 4 条一致，而**不是引入新的异常类型
让 LLM 不知道怎么处理**。

> **抽象出的原则**：**Hooks 是 dispatcher 拦截器，不是 error 引入者**。

### 4.4 DRY_RUN 单点 bypass 的设计 pattern 价值

实现 7 行：

```python
if context.permission_mode is PermissionMode.DRY_RUN:
    output = f"would call {tool_use.name} with {tool_use.input}"
    is_error = False
else:
    output, is_error = await _dispatch_one(...)
```

**双向价值**：

1. **Tool 这一层完全不感知 DRY_RUN**——Read/Write/Edit/Bash/Grep 五个工具的代码里
   **没有任何 `if dry_run` 分支**。单点 bypass = 单点责任。这是"安全外置"原则的具体落地：
   tool 实现专注做事，模式控制集中在 dispatcher 层
2. **LLM 在 synthetic result 上能跑完整多轮**——`oh ask --dry-run "rewrite my README"`
   实测：LLM 看到 `would call Read with {...}` 后，仍然能基于 placeholder 推进到
   "would call Edit with {old=..., new=...}"——**LLM 的 reasoning chain 在占位数据上
   依然成立**

**Pattern reuse for Phase 3**：

- Phase 3 hooks 的 `PreToolUse` 拒绝路径，输出形状直接复用 DRY_RUN 模式：synthetic
  ToolResult 喂回 LLM
- "permission denied 后让用户确认"的交互流，也可以走同形态——hook 暂停 + ask user +
  resume 或 reject
- **DRY_RUN 是这条 pattern 的 prototype**；Phase 3 把它泛化成 "any single-point
  dispatch interception"

---

## 5. Tool 落地的 IO × output shape 对照

> P2-T3 的 5 工具不是"5 个独立实现"，是 BaseTool 抽象在 5 个领域的具象化。

### 5.1 IO 模型分类

| Tool | IO 类型 | async 处理 | 失败的"主战场" |
|---|---|---|---|
| Read | 同步文件读 | `asyncio.to_thread` 卸到线程池 | 文件不存在 / 太大 / 不是文件 |
| Write | 同步文件写 | `asyncio.to_thread` | 路径越界 / 父目录缺失 |
| Edit | 同步读 + 同步写 | 2× `to_thread`（非原子） | old_str 不存在 / 路径越界 / 非 UTF-8 |
| Bash | 异步子进程 | 原生 `await create_subprocess_shell` | 超时 / 非 0 退出 / 输出过量 |
| Grep | 异步子进程（ripgrep） | 原生 async + 8MB stream limit | 无匹配 / 超时 |

**两条横切学习点**：

- **Python `asyncio` 没有"真异步文件 IO"**——所有看似异步的文件操作底层都是线程池。
  从 TS / Node 过来易误以为 `await fs.readFile()` 是真异步;Python 这条要显式
  `asyncio.to_thread`,不会魔法
- **`asyncio.create_subprocess_*` 是 OS 原生事件驱动 IO**——子进程通过 PIPE 通信能直接接
  asyncio 的事件循环（epoll / kqueue），这才是真异步
- **阻塞 IO 卸载（`to_thread`）即使在 D6.3 串行模式下也值得做**——为 Phase 3 parallel
  tools 埋下基线。如果 Read 直接同步读，Phase 3 上 parallel 时所谓"并行"会退化成串行

### 5.2 Output shape 的三个问题

每个工具的 output 设计都是回答这三个问题：

1. **LLM 拿到 output 后要看到什么？**
   - Read → 文件全文（数据）
   - Edit → 操作回执（"replaced N occurrence(s)"）
   - Bash → 命令输出原文
   - **insight**：output 不是"工具内部状态"，是"LLM 决策下一步的最小信息"

2. **超出预算时怎么办？**
   - Read → reject + 提示用 Grep（部分内容无价值）
   - Bash → 截断 + 标记（部分内容仍有价值）
   - **insight**：reject vs truncate 由"部分输出对 LLM 的有用性"决定

3. **边缘情况（空 / 错 / 无）怎么避免歧义？**
   - 哨兵字符串：`(empty)` / `(no matches)` / `(no output)`
   - **insight**：空字符串 LLM 易误判为"工具没跑"；显式哨兵区分"跑了 + 真的空"

### 5.3 三件 flagged 不一致 / TODO

（列出走完三个工具后发现的痕迹，Phase 3 里要决策）

- **Bash 缺 `(no output)` 哨兵**——OpenHarness REFERENCE A.3 有，我们没；建议 Phase 3 production hardening 时补
- **Edit 不是原子写**——`read_text` + `write_text` 中间崩会截断文件；Phase 3 换 `tempfile + os.rename`
- **`is_read_only` 没加到 BaseTool**——REFERENCE §6.1 有；Phase 3 加 parallel tool 时一起加（影响 PermissionChecker 接口 + parallel 分类）

### 5.4 Decode error 的"读软写硬"原则

| Tool | 策略 | 为什么 |
|---|---|---|
| Read / Bash | `errors="replace"` 软失败（U+FFFD 占位） | 读取容忍坏字节，LLM 看占位符就知道是二进制 |
| Edit | `try/except UnicodeDecodeError` 硬拒绝 | 修改路径不能软失败——损坏字节再写回去就把文件烧了 |

**抽象出的原则**：**读路径软失败，写路径硬失败**。可迁移到 Phase 3 任何 IO 工具。

---

## 6. LLM-as-RPC-client 视角（对话副产品，需要沉淀）

> 这一节是 Phase 2 期间在与 Claude 的几轮对话中浮现的视角，**不在任何单个 module
> retro 里**。它是"OpenHarness / Claude Code / 任何 production harness 本质上是一个
> 为奇怪客户端定制的 RPC 框架"这个洞察的结构化记录。

### 6.1 完整映射表

| RPC 概念 | 我们项目里 | 状态 |
|---|---|---|
| Wire format / IDL | `protocols/{content,messages,tools}.py` | ✅ Phase 1 |
| Transport | `api/client.py` 流式 SSE | ✅ Phase 1 |
| Service registry | `tools/base.py: ToolRegistry` | ✅ P2-T2 |
| Service handlers | 5 个具体 BaseTool 子类 | ✅ P2-T3 |
| Server dispatch loop | `engine/query.py: run_query` | ✅ P2-T4 |
| Server-side auth / interceptor | `permissions/checker.py` 最小版 | ✅ P2-T6 |
| Server-side middleware | Hooks（7 lifecycle 事件） | ☐ Phase 3 |
| Application errors | `ToolResult(is_error=True)` 喂回 LLM | ✅ P2-T4 |
| Protocol errors | `OpenHarnessApiError` 体系上抛 | ✅ Phase 1 |
| Federated registries | MCP（跨进程 registry） | ☐ Phase 5 |
| 嵌套 RPC（server 也是 client） | 子 Agent | ☐ Phase 6 |
| Wire history compaction | Auto-Compaction | ☐ Phase 4 |
| 重试 / 退避 | `api/retry.py`（transport 层 only） | ✅ Phase 1 |

**ARCHITECTURE.md §2 的 7 个 Phase 顺序，本质上就是"build a custom RPC framework，
but for an unusual client"的合理实现路径**：先 wire（协议）→ transport（API）→
dispatch（loop）→ middleware（hook / permission）→ federated（MCP）→ nested
（子 Agent）→ polish。

### 6.2 这个 client 有什么"奇怪"

| 普通 RPC client | LLM 这个 client |
|---|---|
| 编译时按 IDL 生成 stub，调用前已经类型安全 | **运行时读 schema**（JSON Schema 字符串），可能调错名 / 给错参数 |
| Service discovery 是单独的 lookup 步骤 | **每次请求把整个 service catalog 推给 client**（`ApiMessageRequest.tools=[...]`）——push，不是 pull |
| 一次决策 = 一次 call | **一次决策可发 N 个 call**（一个 turn 多个 `tool_use` block）；response 必须按原序打包成 ONE user message 回去 |
| 客户端逻辑在客户端进程 | **客户端"逻辑"在 LLM 推理里**，我们这边只是在做 server-side dispatch。本地 while 循环不是 client driver，是 **dispatch loop** |
| description 是给开发者看的注释 | **description 在 runtime 被一个概率模型读**——它**就是 prompt** |

### 6.3 Metaphor 在这几个地方完全断掉（真正的洞察）

不是细节差异，是 **RPC 范式根本不覆盖**：

**1. Wire format 不丢，沉淀成对话历史**

普通 RPC：client 发请求 → server 回响应 → **包丢弃**。下次调用从零开始。

LLM-as-RPC：每次调用的 `ToolUseBlock` 和 `ToolResultBlock` 都**沉淀在
`messages: list[ConversationMessage]` 里**——而这个 list 就是下次调用的 prompt。
**包不丢，变成历史**。含义：

- `tool_use_id` 是这条 wire 的主键——把 request 和 response 在对话流里粘住
- context window 是有限的 wire-history 缓冲区——这就是为什么 harness 要做
  Compaction（Phase 4）
- `messages.py` 的纯函数 helper 不是工具函数，是 **append-only RPC log 的 reducer**

**2. 错误是 payload，不是协议层信号**

普通 RPC 里 `is_error=true` 大概对应 application error，client 拿到后可能 throw、
可能重试。

我们这里：`ToolResult(is_error=True)` 被打包进 `ToolResultBlock` 喂回 LLM——
**错误成了 LLM 的输入观察**，LLM 自己决定怎么办。在 RPC 视角下的翻译就是：
**应用层错误从 protocol 退化成 payload**。**LLM 就是 retry / fallback policy**。
我们不需要写。

**3. 没有 client identity，只有 model identity**

普通 RPC 有 client auth（token / mTLS）。我们这里 LLM 是被信任的"决策者"，**真正的
auth 在 API key**（server 那边），不在 tool call 那边。所以权限模型是**按 tool + args
检查**，不是"哪个 client 能调哪个 service"——这个翻转决定了 PermissionChecker 的
接口长相（`evaluate(tool_name, args, ctx)`，而不是 `evaluate(caller, tool)`）。

**4. 一次 RPC 既流式响应又有副作用调用**

普通 RPC 是 "send request → get response" 两段。

我们这里一次 turn 内，LLM 边产生 text（给用户看）边产生 tool_use（给我们 dispatch），
**两种输出在一条流里交错**。这是为什么 `ApiStreamEvent` 同时有 `AssistantTextDelta`
和 `ToolExecutionStarted`——一个 turn 在事件流上是混合的。普通 RPC 没这个形态。

**5. User 是 dispatch loop 的所有者，是第三个 actor**

LLM 不会"取消"它自己的 tool_use。但**用户**可以 Ctrl+C 打断整个 loop。这是 harness
多出来的 actor，RPC 模型里没有它的位置。

### 6.4 这条视角的工程价值

**以后看 Phase 3+ 的任何新 capability，先问"对应 RPC 框架的哪个概念？"** 比从零想
"这是个什么东西"快。具体例：

- Hook lifecycle = server-side middleware
- MCP = federated tool registry across processes（service mesh 的轻量版）
- 子 Agent = nested RPC，server 也成了 client（递归客户端）
- Skills / Plugins = client-side 注入新 service handlers
- Memory = 长期持久化的 session state（外置 wire history）
- Auto-Compaction = wire history 的 garbage collection

**所有这些 Phase 3-6 的概念，在 RPC 框架里都有对应物**——我们不在发明新东西，
只是给一个奇怪 client 实现成熟的工程模式。这条认知是 retro 最有迁移价值的一句。

---

## 7. 工作流（capability-driven）实测

> CLAUDE.md 拍板"spec 颗粒度停在 capability，sub-task 颗粒度由 agent 在 runtime 决定"。
> 这是 Phase 1 中段从"spec-heavy（micro-cycle 拆到字段级）"主动 pivot 而来的。
> Phase 1 + 2 走完，可以判决这个工作流到底好不好用。

### 7.1 Spec 颗粒度（capability，不到 sub-task）实测

| 阶段 | Plan 重拆次数 | 备注 |
|---|---|---|
| Phase 1 早期（pivot 前） | 1 次（P1-T2 sub-units 2e-1 / 2e-2 字段级过细，强制合并） | spec-heavy 副作用 |
| Phase 1 中后段（pivot 后） | 1 次（P1-T3 split 3c → 3c.1 + 3c.2） | 实施中发现 anti-corruption layer 值得独立 |
| Phase 2 全程 | **0 次** | 6 个 capability 都按 spec 落地 |

**反证为正证**：Phase 2 没出现重拆。这是 capability 颗粒度 + Three-Axis 模板组合的
**正向证据**——产品决策在 capability 入口先拍透，sub-unit 拆分由实施期 agent 决定，
两层各司其职。

### 7.2 Three-Axis 模板的 ROI 分布

P2 6 个 capability × Three-Axis kickoff，产生的决策号：D6（boundary 顶层）+ D7.1-D7.5
（P2-T1）+ D8.1-D8.9（P2-T2）+ D9.x（P2-T3）+ D10.x（P2-T4）+ D11.x（P2-T5）+ D12.x
（P2-T6）。

**最高 ROI 的 Three-Axis**：

- **P2-T1 D7.1**（QueryContext 6 字段一次到位 vs 增量）——一次决策避免了 P2-T2 / T6
  各一次"加 QueryContext 字段"的污染，至少省 4-5 commit
- **P2-T2 D8.1**（BaseTool ABC vs Protocol）——决定了所有后续 tool source（base /
  MCP / plugins）必须显式继承的契约线，Phase 5 MCP 不会跑偏

**最像"过度仪式"的 Three-Axis**：

- **P2-T5（system prompt assembly）**——许多决策在没有真实 LLM 调用前其实拍不准
  （如：tool catalog section 的措辞）。Three-Axis 在这里更像"占位预留"而不是
  "产品决策"。**洞察**：prompt-engineering 类 capability 的 Three-Axis 不应深度展开，
  而应留 placeholder 等真实使用反馈

### 7.3 GREEN 后先 review 再 commit

按 auto-memory `feedback_review_before_commit`，"GREEN 不立即 commit"是固化的工作模式。
Phase 2 实施情况：

- 每个 sub-unit 在 GREEN 之后都做了 walkthrough 对照（验收条件 vs 代码 vs 测试名）
- 至少 2 次在 walkthrough 阶段发现并修正：一次是 ToolResult.metadata 的 mutable default
  testing，一次是 _stream_render 的 retry-to-stderr 决策的实测验证
- **这套节奏的真实成本**：每个 capability 多 15-30 分钟。回报：commit 信息和代码意图
  100% 对齐，未来读 git log 不需要"猜当时在想什么"

### 7.4 框架构建者心态的 hold

按 auto-memory `feedback_framework_builder_moment`（Phase 1 P1-T4 2f 后切换），
Phase 2 全程的对话样本：

- **LLM-as-RPC 视角的浮现**——出现在工具实现讨论里，自然产物，不是被引导
- **事件流形状的 7→5 简化**——讨论始终在"消费者需要什么"层级，没掉到"具体哪个字段
  叫什么"
- **Tool 三种 IO 模型对比**——讨论"output shape × IO 模型"二维空间，而不是
  "Read 加什么字段"

**潜在 risk**：Phase 3 引入 hooks（一个低层、细节密集的扩展点），心态会被再次测试。
hooks 的具体事件名 / hook payload 字段 / hook chain 顺序——这些都容易掉到细节级。
**Phase 3 boundary discussion 必须先把 hooks 的"产品形态"拍掉**（7 事件 vs 简化 vs
Protocol-based），才动手。

---

## 8. 跨 Phase 可迁移的 architecture pattern

| Pattern | 起点 | 已迁移到 / 具体例 |
|---|---|---|
| **Frozen dataclass + 类型收紧 marker**（`object` 占位 + 行内 `# tighten to X` 注释） | P2-T1 D7.2（QueryContext） | P2-T2.2e 收紧 `tool_registry: object → ToolRegistry`；P2-T6.6b 收紧 `permission_checker: object → PermissionChecker`。**两次都是 grep `tighten to` 找位置**，无遗漏 |
| **Stub 自销毁模式** | P2-T1 D7.4（`engine/query.py`：`# noqa: ARG001` + `# type: ignore[unreachable]`） | P2-T4 4d 实施时这两个 marker 自动失效（参数被使用、yield 可达），lint 强制清理——**stub 状态被编码进 lint 规则**，忘了清就报警 |
| **反向断言锁住 API 边界** | P2-T1（`test_messages_helpers_are_not_re_exported`） | TODO Phase 3 hooks/permissions 公开 API 时复用——任何"想保持小公开 API"的模块都该有 |
| **Pure-function reducer for messages history** | P2-T1 D7.3（`messages.py` 4 个纯函数） | run_query 实测：`messages = append_*(messages, ...)` 重赋值 5+ 次；**Phase 4 Compaction 的天然 seam**（`messages = compact(messages)` 不会撞 mutation） |
| **三测试缝**（`_load_settings` / `_build_client` / `run_query`） | P2-T6 cli.py | 同形态可用于任何 component-orchestrating 模块；缝设在 module-level 函数让 monkeypatch.setattr 工作 |
| **决策号 D{N}.{M} → docstring → code 的链路** | Phase 2 全程 | `tools/base.py` docstring 引用 D8.1-D8.7；`engine/query.py` 引用 D6.1/D6.3/D7.4。**改代码的人第一眼看见决策的 why** |
| **测试名 = 产品契约** | Phase 1 P1-T3 → P1-T4 | Phase 2 全部 test 命名延续；具体例：`test_metadata_default_is_per_instance`（mutable-default 陷阱）/ `test_list_tools_returns_caller_owned_copy`（ownership） |
| **反腐败层"实现新成员时上层一行不动"** | P1 §3.1（T3 + T4 期间 protocols/ 不动） | Phase 2 again：P2-T4 加 ToolExecutionStarted/Completed 是 additive，未触发 protocols 现有类型修改 |
| **Module-level pure function helpers**（不要 over-class） | P2-T1 D7.3 | 反 over-engineering 的样本；ToolRegistry / PermissionChecker 都是 class（有状态/接口语义），messages helpers 是函数（纯转换） |
| **错误信息带"下一步建议"** | P1-T4 5 except 分支的 hint | P2-T3 工具错误信息延续：`"file too large: ...; use Grep on huge files instead"` 把 fallback 写进错误本身 |

---

## 9. Python 学到了什么（续 phase-1.md §3 + Phase 2 新增）

> 跨 Phase 的 Python 模式合集，TS 出身视角，去重 phase-1.md 已写过的内容

### 9.1 async 真相

- **`asyncio.to_thread(...)` 是文件 IO 唯一可移植解**——Python 没"真异步文件 IO"，
  pathlib / open() / read_text 都是同步;`aiofiles` 底层也是线程池
  ```python
  text = await asyncio.to_thread(path.read_text, encoding="utf-8")
  ```
- **`asyncio.create_subprocess_*` 是真异步**（OS 事件驱动，epoll/kqueue 监听 fd）
- **`asyncio.wait_for(...)` 不是同步 `signal.alarm`** —— 是 per-task 的 cancel，不会
  影响 event loop 上其他 task
- **async generator (`async def f() yield`)** 是 stream + 事件流的统一抽象，consumer
  用 `async for` 消费，loop 用 `yield` 产出
- **stub 模式：`raise NotImplementedError; yield  # type: ignore[unreachable]`** ——
  没有 yield 的话 Python 把 f 当 coroutine，caller 只能 `await`，不能 `async for`

### 9.2 typing 真相

- **`TypeVar(bound=BaseModel) + Generic[T]`** 是解 LSP 违规的标准方法（子类 narrow
  参数类型）：
  ```python
  InputT = TypeVar("InputT", bound=BaseModel)
  class BaseTool(ABC, Generic[InputT]):
      async def execute(self, args: InputT, ...) -> ToolResult: ...
  class Read(BaseTool[ReadInput]):  # InputT 钉死成 ReadInput
      async def execute(self, args: ReadInput, ...) -> ToolResult: ...
  ```
- **abstract attribute 模式**：class-level annotation 无 default + mypy strict 抓
  子类未填（Phase 1 retro 没提，Phase 2 BaseTool 用上）：
  ```python
  class BaseTool(...):
      name: str           # ← 无 default,子类必填
      description: str
  ```
- **`from __future__ import annotations` + `TYPE_CHECKING` 块**：让类型只在编译期，
  运行时 `pathlib` 都不导入，启动更快、循环依赖更少
- **`Annotated[..., Field(discriminator="type")]`** 是 Pydantic v2 discriminated union
  标准写法

### 9.3 Pydantic v2 实战

- **`model_validate(dict)`** 是 LLM JSON → typed args 的**边界守门员**——LLM 不可信，
  Pydantic 是第一道闸，校验失败 → ValidationError → ToolResult(is_error=True)
- **`model_json_schema()`** 自动生成 JSON Schema：BaseTool → ToolSpec → LLM catalog
  的 schema 自动产，不用手写
- **`Field(description=...)`** 串进 JSON Schema 给 LLM 看，**不是给 dev 看的 docstring**
  ——这是 schema-as-prompt 的工程入口
- **`Field(ge=1)` / `min_length=1`** 是 server-side 校验，挡 LLM 的 bullshit
- **三件套契约层标配**：`frozen=True / extra="forbid" / validate_assignment=True`
  ——拒绝多字段、改了字段也 validate

### 9.4 子进程编程

- **两阶段终止 SIGTERM → grace → SIGKILL**：礼貌优先 + 蛮力兜底
  ```python
  process.terminate()                                          # SIGTERM
  try:
      await asyncio.wait_for(process.wait(), timeout=2.0)
  except asyncio.TimeoutError:
      process.kill()                                           # SIGKILL
      await process.wait()                                     # ← reap,避免 zombie
  ```
- **`process.wait()` 是必需的**（避免 zombie），`process.communicate()` 内含 wait
- **`stderr=asyncio.subprocess.STDOUT`** 合并双流给 LLM 简化认知（一条流，不分 stdout/
  stderr）
- **UTF-8 `errors="replace"`** 软失败让二进制污染不崩（U+FFFD 占位）

### 9.5 dataclass + 不变量

- **可变默认值陷阱**：用 `field(default_factory=dict)`，不是 `= {}`（后者所有实例
  共享同一个 dict）
- **`frozen=True` 是 runtime-enforced 不可变**：`result.is_error = True` 触发
  `FrozenInstanceError`
- **`@dataclass(frozen=True)` 配合 `field(default_factory=...)`** 的组合：契约层
  对象的标配长相

### 9.6 测试 + 工具链

- **`monkeypatch.setattr(module, "func_name", stub)`** 替换 module-level 函数
  （seams 设在 module level 就为这个）
- **`pytest_asyncio` 自动模式**：`async def test_*` 不需要 `@pytest.mark.asyncio`
- **`@pytest.mark.integration` + `pytest -m "not integration"`** 把"真 API"测试 gate
  起来
- **`pyproject.toml` 里 `[tool.coverage.report] exclude_lines`** 让 stub 的
  `raise NotImplementedError` 不算未覆盖

---

## 10. 留给 Phase 3 的"包袱清单"

> 不是 TODO list，是"现在不做但 Phase 3 必须决策的事"。按 Phase 3 boundary 拍板时
> 的依赖顺序排序。

### 10.1 必拍的 Phase 3 决策（决定 boundary，先于工程债）

按"决定后续 capability 形态的影响力"排序：

1. **Hooks 设计范围** ⭐⭐⭐ —— 7 事件 × 4 类型（OpenHarness 完整版）vs 简化
   （只 PreToolUse + PostToolUse）vs Protocol-based（让用户自定义事件名）。
   **影响 Phase 4/5/6 所有扩展点**——memory / mcp / skills 都靠 hooks 挂；这条不
   拍清，后面三个 Phase 都会回头修
2. **权限算法粒度** ⭐⭐⭐ —— 完整 9 步算法 vs 增量（先 hardcoded sensitive paths
   再补）vs deny-list + hooks（让用户写 rule）。**决定用户对安全的控制颗粒**
3. **`is_read_only` + parallel tool** ⭐⭐ —— Phase 3 上还是留 Phase 6？建议 Phase 3
   就上：5 行代码加 `is_read_only` 属性，PermissionChecker 接口受益（read 默认 ALLOW），
   parallel 是后续 incremental
4. **异常层级扩展** ⭐⭐ —— 现有 `OpenHarnessApiError` 之外加 `ToolError` /
   `PermissionError` / `HookError` 体系？还是统一兜底？前者类型更安全，后者 cli.py
   兜底分支不用动
5. **Retry hardening 范围** ⭐⭐ —— cost cap / per-tool retry / circuit breaker
   做哪些？已有 `RetryPolicy` 是 transport 层 only，需要 application 层 retry 吗？
6. **可观测性深度** ⭐ —— structlog 接 stdlib `logging` / 自家 event logger /
   OpenTelemetry。建议 structlog（Python 生态主流，零依赖以外的中间件）

### 10.2 工程债（Phase 3 入口的 batch 处理）

按 Phase 3 capability 入口的"warmup batch"思路批处理：

**Phase 1 carryovers（应该早做掉）**：
- [ ] 显式定义 `class SupportsStreamingMessages(Protocol)`（learnings/03 #3）
- [ ] `_FAST_POLICY` 抽到 `tests/api/conftest.py`
- [ ] `_translate_openai_error` 单独 test file
- [ ] CI 显式加 `-m "not integration"` flag
- [ ] `decisions/00-env.md` 记录代理端口陷阱
- [ ] Pin `.pre-commit-config.yaml` ruff hook 版本，消除版本飘移

**Phase 2 期间发现的（Phase 3 production hardening 时合并处理）**：
- [ ] **Bash `(no output)` 哨兵**（OpenHarness REFERENCE A.3 有，我们漏了）——简单 fix
- [ ] **Edit 原子写**（`tempfile.NamedTemporaryFile + os.rename`）——production
  必备，配合 Phase 3 hardening 一起做
- [ ] **`is_read_only` 加到 BaseTool**（同 §10.1 #3）——和决策点 #3 一起落

**配置层重审**：
- [ ] **Settings File 层**（XDG config）—— Phase 2 0 使用，Phase 3 决定砍掉还是保留
  作 Phase 5 多 profile 伏笔（见 §3.3）

---

## 一句话沉淀

> **Phase 1 把 chambers 装好，Phase 2 让 heart beat 起来。**
>
> 回头看这 78 个 commit，每一行代码都对应得上一条契约层决策（`D{N}.{M} → docstring
> → code` 的链路），每一条决策都对应得上一个 capability 入口的 Three-Axis 讨论，
> 每一个 capability 都对应得上 ARCHITECTURE.md 的一格 tier。
>
> 这就是**"框架构建者"的工作**长什么样——**不是写代码，是写契约；不是 ship 功能，
> 是 ship 一条可被未来自己 grep 出来的判断链**。Phase 3-7 沿着这条链继续走就行。

---

## 写完后的 checklist

- [x] §1 数据点表中 TBD 已填（78 commits / 352 tests / 94.76% / D6-D12 sub-decisions）
- [x] §2 P2-T1…T6 1-line takeaway 已填（去 phase-1.md 看 P1 部分）
- [x] §3 五个子节都已写实测判断（不是 TODO）
- [x] §4 事件流 / D6.3 / 4 recovery / DRY_RUN 都已填实例
- [x] §5 IO × output shape 表格 Write/Grep 行已填
- [x] §6 RPC 视角的"5 个不能套用的差异"用结构化段落总结
- [x] §7 capability-driven workflow 用真实 git log 数据验证
- [x] §8 Pattern 表的"已迁移到"列填完（每条带具体 commit 或文件锚）
- [x] §9 Python 学习点没有抄 phase-1.md 的内容（去重，且配代码片段）
- [x] §10 包袱清单按 Phase 3 boundary 决策的需要排序
- [x] 最后一句沉淀写出来
- [x] 文件首行"用时 ~2 周"已填

**人看一遍后做的事**：
- [ ] 跑 `git log` 把还没填的具体 commit hash（如果有）补上
- [ ] §3.5 双层截断的"实际 UX 体感"——你跑 `oh ask "list /tmp"` 时如果有特别的观察，加进去
- [ ] §6.3 第 5 条（user 是第三个 actor）—— Phase 2 没实现 Ctrl+C 处理，事后看是不是 Phase 3 该做的事
- [ ] 整体 review 一遍，把"我"换成你的语气（现在多用第三人称 / 被动，可以改更个人化）
