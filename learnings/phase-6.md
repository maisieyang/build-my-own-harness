# Learnings — Phase 6 (Sub-agent — Recursive Tool Dispatch)

> Phase 6 起止 / 2026-05-16(单日,Phase 5b retro 后立即开启)
> 6 capabilities (P6-T1…T6) / ~15 sub-units / 7 commits / ~70 new tests
> ~200 lines of production code / 100 % on tools/spawn_agent.py
>
> 本文件**不是** sub-unit 合集 —— commit message 已经详尽记录。
> 它回答的题:**做完 Phase 6,关于"agent loop 作为递归原语"这件事,
> 学到了什么 framework-level 的东西。**

---

## 1. 数据点

| 维度 | Phase 5a (MCP) | Phase 5c (Skills) | Phase 5b (Commands) | Phase 6 (Sub-agent) |
|---|---|---|---|---|
| Capability | 7 | 5 | 5 | **6** (T1-T6) |
| Sub-units | ~20 | 11 | ~10 | **~15** |
| 生产代码量 | ~600 行 | ~170 行 | ~140 行 | **~200 行** |
| 新增 module | `mcp/` | `skills/` + 1 tool | `commands/` | **`tools/spawn_agent.py` only** |
| 触碰横切 module | `cli` + `settings` | `prompts.py` + `cli.py` + `engine/context.py` | `cli.py` only | **`engine/context.py` + `engine/query.py`(3 lines) + `tools/base.py`(1 field) + `observability/`(1 helper) + `settings`(1 field) + `cli`(1 kwarg)** |
| 改 `permissions/` | 0 | 0 | 0 | **0** ✓ |
| 改 `hooks/` | 0 | 0 | 0 | **0** ✓ |
| 改 `engine/query.py` 业务 dispatch 逻辑 | 0 | 0 | 0 | **0** ✓(只 +3 行 additive:1 import + 1 with-stmt + 1 kwarg) |
| 改 `mcp/` | 0 | 0 | 0 | **0** ✓ |
| 改 `compaction/` | 0 | 0 | 0 | **0** ✓ |
| 改 `protocols/` | 0 | 0 | 0 | **0** ✓ |
| 新增测试 | 80+ | 26 | ~40 | **~70**(static / happy / depth / loop limit / defensive / nesting / invariant / e2e) |
| Phase 修改后总覆盖率 | 96.7% | 97% | 97% | **97%+**(995 passed) |

**关键观察**:Phase 6 是历史上**最深入触动框架抽象**的 capability phase
(改了 engine + tools/base + observability + context),但仍然**做到了**
permissions / hooks / engine 业务 dispatch / mcp / compaction / protocols
**六个核心 module 零改动**。所有改动都是**additive**——加 optional 字段、
加 helper、加 import,没有任何 isinstance 分支或专门 dispatch 路径。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P6-T1 — Settings + QueryContext fields** | 同 P5-T1 / P5c-T1 形态:加 `Settings.max_agent_depth` + `QueryContext.agent_depth` + `max_agent_depth`。`0` 是合法值(把 spawn 完全关掉)—— 跟 `--no-skills` / `--no-commands` 不同,这里通过**数值边界**实现 kill-switch,比 flag 更优雅。 |
| **P6-T2 — `ToolExecutionContext.parent_query` additive field** | 一行字段 + 一行 engine 改动 —— 整个 phase 在 engine dispatch 上的所有动作。Default `None` 让所有现有 tool 一行不动地工作;`SpawnAgent` 通过 `dataclasses.replace(parent_query, agent_depth=...)` 构建 sub-context。**这就是"在 engine 加最少东西支撑递归"的极限**。 |
| **P6-T3 — `SpawnAgent` BaseTool subclass** | Phase 6 的心脏。`execute` 做四件事:depth check / 构建 sub_context(dataclasses.replace)/ 驱动 `run_query` 收集事件 / 提取 final text。**整个 recursion 在一个 BaseTool 里完成**,engine 一行不知道有递归。 |
| **P6-T4 — Observability 嵌套** | `bind_run` 自检 contextvars 是否已绑 → 自动 stash `parent_run_id`。新加 `bind_agent_depth` 让 `run_query` 自然嵌套。**踩了一个坑**:`structlog.contextvars` 是 set/unset 语义不是 stack —— 退出时 `unbind_contextvars` 会清掉而不是恢复 parent,得手动重新 bind。这条**在 §3.4 单独展开**。 |
| **P6-T5 — CLI bootstrap + INVARIANT VERIFICATION ⭐** | `create_default_tool_registry()` 加 `SpawnAgent()`,从此 `oh ask` 默认就有 `Agent` 工具。**形式化 invariant verification** 通过:`git diff` 对照 Phase 5c close 显示 permissions / hooks / mcp / compaction / protocols 全 0 行 diff,engine/query.py 只 3 行 additive。 |
| **P6-T6 — End-to-end smoke + retro** | Stub LLM 驱动**两个独立的 run_query**(parent + sub-agent),验证整条链路:parent emits Agent tool_use → SpawnAgent.execute 进入 sub-agent's run_query → sub-agent end_turn → SpawnAgent 提取 text → parent's tool_result 携带该 text → parent's turn 2。**观测层验证**:sub-agent 的 log 事件真带 `parent_run_id` + `agent_depth=1`。 |

---

## 3. Framework-level 主题 — Phase 6 真正学到的

### 3.1 第三次 invariant 兑现 —— "新控制流形状" 也是 tenant

Phase 5a (MCP) 是 "外部 tool source";Phase 5c (Skills) 是 "外部 knowledge";
Phase 5b (Commands) 是 "pre-LLM CLI transform"。这些都是**横向扩展** ——
加新数据来源、新输入通道。

**Phase 6 Sub-agent 是首个"纵向扩展"** —— 不是加新数据,是改变 control
flow shape(`run_query` 调用自己)。看到 invariant 也对这个能成立,意义
比前几次都大:

> Phase 3 的抽象做对了**到这样一个程度**:连"agent loop 把自己变成一个 tool"
> 这种递归控制流变形,都能不污染 dispatch / permission / hook / observability
> 任何一层。

历史上"递归 / 子 agent"功能往往是大改造(LangGraph subgraphs、Codex
sub-agent、claude-agent-sdk 的 Task tool 都有专门的递归处理逻辑)。本项目
做到了**用一个 BaseTool 子类装下整个 sub-agent**,engine 一行不知道,
**完全靠 Phase 3 的横切抽象 + Phase 2 的 BaseTool 接口 + Phase 4 hook
chain 复利**承接。

→ 这是 Phase 1-5 复利的**最高密度兑现**。

### 3.2 "Tool dispatch is the LLM's syscall interface" —— 升级版

Phase 5c retro §3.2 提出 "LLM-as-RPC + tool dispatch = harness 的图灵机"。
Phase 6 把这条洞察**强化一档**:

> **`run_query` 本身也是这台图灵机的一个 syscall**。

具体来说:

| 扩展类别 | 是 BaseTool 子类? | execute 内部做什么 |
|---|---|---|
| Read/Write/Edit/Grep | ✓ | host fs 操作 |
| Bash | ✓ | host subprocess |
| MCP tool(`McpToolAdapter`) | ✓ | JSON-RPC over stdio |
| LoadSkill | ✓ | 读 markdown + 返回 body |
| **SpawnAgent** | ✓ | **重入 run_query** |

Sub-agent 跟 host fs read、跟 subprocess spawn、跟 JSON-RPC call、跟
markdown file read **在框架眼里是同一类操作**:都是"`execute` 体内的副作用"。
框架不知道也不关心它内部是 ctypes、subprocess、还是 LLM-driven loop。

→ Phase 6 最深的领悟是:**框架抽象的好不好,看新机制能不能借现有 tool
接口 hostage 进来**。Sub-agent 通过 ~200 行借住了进来。

### 3.3 `dataclasses.replace` 是 "context isolation" 的精确表达

D16.2 锁的"sub-agent 继承大部分字段,只覆盖三个"——这条决策在 Python
里有**精确的语言级表达**:

```python
sub_context = dataclasses.replace(
    parent,
    system_prompt=...,    # 覆盖
    max_turns=...,        # 覆盖
    agent_depth=...,      # 覆盖
)
# 其它 11 个字段自动继承
```

不需要手动列举继承的字段(易漏)、不需要 inheritance chain(QueryContext
是 frozen dataclass,没有继承)、不需要 `**kwargs` magic(类型不清)。
`dataclasses.replace` 的语义就是"复制 + 选择性覆盖",**和 D16.2 决策一一
对应**。

更深的:这暴露了 frozen dataclass 设计**为 Phase 6 留好的位**。如果当时
QueryContext 用 mutable class 或者 dict,Phase 6 这步会复杂得多。**Phase 2
做对的事,在 Phase 6 持续产生复利**。

### 3.4 ⚠️ 踩坑:`structlog.contextvars` 不是 stack 语义

P6-T4 实现 nested `bind_run` 时踩了一个**值得记录**的坑。最初写法:

```python
@contextmanager
def bind_run(run_id: str | None = None) -> Iterator[str]:
    rid = run_id or new_run_id()
    existing = structlog.contextvars.get_contextvars()
    parent_rid = existing.get("run_id")
    bind_contextvars(run_id=rid, parent_run_id=parent_rid)
    try:
        yield rid
    finally:
        unbind_contextvars("run_id", "parent_run_id")  # ❌ wrong
```

测试挂了:**parent 的 run_id 在 sub-agent 退出后也消失了**。

原因:`structlog.contextvars.bind_contextvars` 和 `unbind_contextvars` 是
**set / unset 语义**,不是 push / pop 语义。当 sub-agent 调
`bind_contextvars(run_id=R2)`,它**覆盖**了 outer 的 `run_id=R1`(不是
push)。退出时 `unbind_contextvars("run_id")` **彻底清掉** `run_id` 这个
key(不是 pop 回 R1)。

正确写法:**手动恢复 parent 的 binding**:

```python
finally:
    if parent_rid is not None:
        bind_contextvars(run_id=parent_rid)  # 显式恢复
        unbind_contextvars("parent_run_id")
    else:
        unbind_contextvars("run_id")
```

→ **教训**:外部库的 contextvar 工具往往不是 stack。要嵌套语义,得自己实现
stack-like 行为。如果未来 contextvars 用 plain Python `contextvars.Token` 也是
同理 —— `var.set()` 返回 token,`var.reset(token)` 才是 pop。

这条踩坑**记进了 `bind_run` 的 docstring 内联**(commit `172ff0b`):

```python
# ``structlog.contextvars`` has set/unset semantics, NOT stack —
# ``unbind_contextvars("run_id")`` would erase the binding instead
# of restoring the outer ``bind_run``'s value. Restore explicitly:
```

→ 未来嵌套 contextvar 类似机制时(per-request session id / per-call
trace span 等)直接复用这个模式。

### 3.5 "递归的边界" 是 `BaseTool` 内部的事,不是 engine 的事

Phase 6 入口时一个潜在的设计陷阱是把 depth bound 放进 engine
`_dispatch_one`:

```python
# ❌ 错误的设计
async def _dispatch_one(tool, args, context):
    if context.agent_depth >= context.max_agent_depth:
        return refused
    ...
```

这违反 invariant —— engine 必须 depth-agnostic。**正确做法**:depth 检查
完全在 `SpawnAgent.execute` 内部:

```python
# ✓ 正确
async def execute(self, args, exec_ctx):
    parent = exec_ctx.parent_query
    if parent.agent_depth + 1 > parent.max_agent_depth:
        return ToolResult(is_error=True, output="max agent depth ...")
    ...
```

含义:**"我能不能递归" 是 SpawnAgent 自己关心的事,跟 engine 无关**。如果
未来加另一种递归原语(比如 `Workflow` tool 跑子工作流,有自己的 depth
规则),它在自己的 `execute` 里实现自己的 depth check —— engine 不需要为
任何递归形态加分支。

→ 这就是 Phase 5b retro §3.1 提出的"layered extension model" 在递归场景
下的具体兑现:**新机制的边界 / 约束都在自己的 layer 内**,不向 engine 渗透。

### 3.6 Structural invariant test 第三次进化 —— 加了 isinstance 全包扫描

Phase 5c invariant test 检查 5 个 module 不引用 `LoadSkill*`。
Phase 5b 扩展到 9 个 module。
Phase 6 **再扩展**:在前 9 个 module 基础上,**加了一条全包扫描**(`pkg_root.rglob("*.py")`)
确认**任何** Python 文件里都没有 `isinstance(.., SpawnAgent)` 分支:

```python
def test_no_isinstance_branch_on_SpawnAgent_anywhere(self) -> None:
    for py_file in pkg_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        # 检查 isinstance( + SpawnAgent 不出现在同 ~100 字符内
        ...
```

理由:invariant 的本质是"任何代码都不能 special-case 这个 tool"。如果只
检查 9 个 module,新增的某个奇怪 module(比如某天加了 `analytics/`)
里 isinstance 分支会逃过检查。**全包扫描**是 future-proof 保险。

→ structural invariant test 的"网"应该越来越大,直到覆盖整个包。Phase 7+
新加任何 module,这套全包扫描自动覆盖。

---

## 4. 跟 Phase 5a / 5b / 5c / 7 preview 的整体对照

| 扩展类别 | Phase | 接触层级 | LLM 可见? | "新概念" |
|---|---|---|---|---|
| Pre-LLM transform | 5b ✅ | Layer 0(cli.py) | 不可见 | 无 |
| External knowledge | 5c ✅ | Layer 1+2 | catalog + tool | "skill" |
| External tools | 5a ✅ | Layer 2 | tool catalog | "MCP server" |
| **External control flow** | **6 ✅** | **Layer 2(BaseTool)** | **tool catalog** | **"sub-agent" 但只在 SpawnAgent 内部** |
| External execution env | 7 preview | Layer 3 | 不可见 | "sandbox" |
| (future) ModeBundle | 5d | Layer 0+1+2 | 部分可见 | cross-layer |
| (future) RAG / cross-session memory | 后续 | Layer 1+2 | catalog + tool | 无新概念 |

观察:Phase 6 Sub-agent 是**首个 Layer 2 内部装入"新概念"** 的 phase ——
catalog 里多了 "Agent" 这个工具名,LLM 学会用它。但 framework 内部
**没有任何文件知道 "sub-agent" 是个特殊概念**,它在所有层都被当成一个
普通 BaseTool。

→ **概念存在于 LLM 看到的世界里(catalog 字符串),不存在于 framework 代码里**。
这是 Phase 6 留给后续 phase 的最重要 architectural insight。

---

## 5. 如果重做 Phase 6 我会改什么

| 当时做对的 | 当时可以更激进的 |
|---|---|
| `SpawnAgent.__init__` 接受 `tool_filter` 参数但 ignore —— forward-compat 留位,**没有膨胀 5d 工作** | `SpawnAgent.__init__` 用 `name="Agent"` 当默认实例,但允许用户实例化变种(`ResearchAgent` 等不同 system_prompt)—— 这个设计**对了**,但**没有写 e2e 测试**验证多变种共存。Phase 7+ 真用上时补 |
| Depth check 完全放进 `SpawnAgent.execute`,engine 不知道有 depth 概念 | `max_agent_depth=0` 作为 kill-switch 是优雅,但**少了一个 CLI flag** `--no-sub-agents` 让用户不用改 env var 也能临时关。Phase 7+ 加 |
| `dataclasses.replace` 显式列覆盖字段,继承的字段不列 | 没有把 `bind_run` 嵌套 + `bind_agent_depth` 抽成一个 `bind_run_query(context)` 复合 helper —— **可读性会更好**。但目前两个 `with` 串联也清楚,Phase 7+ 看时机重构 |
| `structlog.contextvars` 不是 stack 语义这条坑**显式记进了 docstring** | 没把这条经验**形式化成 helper** —— 类似 `stacked_bind_contextvars(...)` 通用版本。如果未来还有别的 contextvar 需要嵌套,会想要这个 helper。Phase 7+ |

---

## 6. 给后续 phase 的 input

### Phase 5d (ModeBundle) 应该警惕的

- ModeBundle 涉及 cross-layer(slash + skill + permission + hook)——
  Phase 6 的"概念只存在于 catalog,不在代码里"原则**不一定直接适用**,
  因为 ModeBundle 本质是**组合多个 tenant 的元数据**。Phase 5d 入口的
  Three-Axis 重点要讨论:这套元数据的"主"在哪儿?(我倾向:`bundles/`
  新 module,跟 `commands/` 同层)
- 不要让 ModeBundle 的元数据 leak 进 `engine/query.py` 任何分支 ——
  即使是 cross-layer 组合,**触发点应该在 `cli.py` 解析阶段就完成**,
  engine 只看 resolved 后的 QueryContext

### Phase 7 (Sandbox / ExecutionEnvironment) 应该复用的

- Phase 6 的 `ToolExecutionContext.parent_query` 是**最小 additive 扩展**
  的模板。Phase 7 加 `ToolExecutionContext.execution_env: ExecutionEnvironment
  | None = None` 是同形态
- Phase 6 的 `bind_agent_depth` helper 模式可以复用:Phase 7 可能要绑
  `execution_env` 字段到 log 事件(host / sandbox / mcp)
- ⚠️ contextvars 嵌套坑 Phase 6 已经踩过 —— Phase 7 任何嵌套 substrate
  状态用 `bind_run` 的"显式恢复"模式,**别再踩**

### 未来 phase 普遍要警惕的

- **新机制装进 BaseTool 是默认路径**,只有当 BaseTool 真的装不下才考虑
  动 engine。Phase 6 证明"递归 control flow" 这种激进概念都能装下,
  几乎没有什么装不下
- **`dataclasses.replace` 是 frozen dataclass 的隐藏礼物** —— context
  isolation / immutable state mutation / config layering 全都靠它
- **结构性 invariant test 的"网"越来越大** —— Phase 6 加了全包扫描,
  Phase 7+ 可以加更多(比如检查"任何文件都不 import engine.context.QueryContext
  except 已 whitelist 的几个")

---

## 7. Phase 6 DoD Checklist

- [x] `Settings.max_agent_depth` + `QueryContext.agent_depth` + `max_agent_depth`
  + CLI propagation
- [x] `ToolExecutionContext.parent_query` additive field
- [x] `engine/query.py` 一行 dispatch 改动(`parent_query=context`)
- [x] `SpawnAgent(BaseTool[SpawnAgentInput])` 完整实现 — depth check /
  sub_context build / run_query consume / text extraction / error paths
- [x] `bind_run` 嵌套检测 + `bind_agent_depth` helper
- [x] `engine/query.py` `with bind_run(), bind_agent_depth(...)`
- [x] `create_default_tool_registry()` 加 `SpawnAgent()`
- [x] CLI 测试覆盖 `Agent` tool 在 catalog 中 + `max_agent_depth`
  propagation
- [x] **形式化 invariant verification(`git diff` against Phase 5c close)**:
  - permissions/ → 0 lines
  - hooks/ → 0 lines
  - mcp/ → 0 lines
  - compaction/ → 0 lines
  - protocols/ → 0 lines
  - engine/query.py → 3 code-line additions only
- [x] Structural invariant test(9 protected module + 7 forbidden
  identifier + 全包 isinstance 扫描)
- [x] E2E smoke:parent → SpawnAgent → sub-agent → tool_result 全链路
- [x] Observability nesting 端到端验证(sub-agent 真带 `parent_run_id`
  + `agent_depth=1`)
- [x] README "Phase 6 features — Sub-agent" 章节
- [x] `learnings/phase-6.md` 写完(本文件)

---

## 一句话

> **Phase 6 用 ~200 行代码 + 3 行 engine additive 改动,验证了 Phase 3
> 抽象的最强声明:连"agent loop 把自己变成一个 tool"这种递归控制流变形,
> 都能不污染 dispatch / permission / hook / observability 任何一层。**
>
> 跨过 Phase 6 这道坎之后,framework 真正"知道"它在做什么 —— Skills / MCP /
> Commands / Sub-agent 都验证完毕,Phase 7 Sandbox 只需要在已有抽象上加一个
> execution substrate plug-in,不需要新的 invariant 故事。
>
> 这就是 Phase 1-5 复利的**最高密度兑现**:加越大的能力,改越少的代码。
