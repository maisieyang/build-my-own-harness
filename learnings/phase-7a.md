# Learnings — Phase 7a (ExecutionEnvironment Abstraction)

> Phase 7a 起止 / 2026-05-16(单日,Phase 6 retro 后立即开启)
> 4 capabilities (P7-T1…T4) / ~6 sub-units / 5 commits / ~32 new tests
> ~150 lines of production code / 100 % on `execution/` modules
>
> 本文件**不是** sub-unit 合集 —— commit message 已详尽记录。
> 它回答的题:**做完 Phase 7a,关于"substrate 抽象"这件事,学到了什么
> framework-level 的东西。**

---

## 1. 数据点

| 维度 | Phase 5a (MCP) | Phase 5b (Commands) | Phase 5c (Skills) | Phase 6 (Sub-agent) | Phase 7a (Substrate) |
|---|---|---|---|---|---|
| Capability | 7 | 5 | 5 | 6 | **4** (T1-T4) |
| Sub-units | ~20 | ~10 | 11 | ~15 | **~6** |
| 生产代码量 | ~600 行 | ~140 行 | ~170 行 | ~200 行 | **~150 行** |
| 新增 module | `mcp/` | `commands/` | `skills/` + 1 tool | `tools/spawn_agent.py` only | **`execution/`(2 文件)** |
| 触碰横切 module | `cli` + `settings` | `cli.py` only | `prompts.py` + `cli.py` + `engine/context.py` | `engine/context.py` + `engine/query.py` (3 lines) + `tools/base.py` + `observability/` + `settings` + `cli` | **`engine/context.py` (+1 field) + `engine/query.py` (+1 kwarg) + `tools/base.py` (+1 field) + `tools/bash.py` (body refactor only)** |
| 改 `permissions/` | 0 | 0 | 0 | 0 | **0** ✓ |
| 改 `hooks/` | 0 | 0 | 0 | 0 | **0** ✓ |
| 改 `observability/` | 0 | 0 | 0 | 1 helper added | **0** ✓ |
| 改 `engine/query.py` 业务 dispatch 逻辑 | 0 | 0 | 0 | 3 lines additive | **1 kwarg additive** |
| 改 `mcp/` | 0 | 0 | 0 | 0 | **0** ✓ |
| 改 `compaction/` | 0 | 0 | 0 | 0 | **0** ✓ |
| 改 `skills/` | n/a | 0 | n/a | 0 | **0** ✓ |
| 改 `commands/` | n/a | n/a | 0 | 0 | **0** ✓ |
| 改 `protocols/` | 0 | 0 | 0 | 0 | **0** ✓ |
| 新增测试 | 80+ | ~40 | 26 | ~70 | **~32**(host / context field / fake-substrate swap / invariant) |
| Phase 修改后总 tests | — | — | — | 995 | **1023+**(净增 13,其中 13 个旧 BashTool 测试 **一行不动**通过 → 行为 parity 锁死) |

**关键观察**:Phase 7a 是历史上**新增 module 数最少**(仅 `execution/`
2 文件)同时**最低 LoC** 的 capability phase。这不是因为问题简单,是因为
Phase 6 已经把"additive field 模板"做透,Phase 7a 在它上面只是**第二次
应用同一个模式**。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P7-T1 — `execution/` 包基座** | `ExecutionEnvironment(Protocol)` 单方法 + `ProcessResult` 三字段 + `HostExecution` body 从 Bash extracted byte-identical + 模块级 singleton。最关键的设计决定:**shell-string 而不是 list[str]** —— 否则切换 `/bin/sh` → `/bin/bash` 破坏 parity。 |
| **P7-T2 — Additive fields wire** | `QueryContext.execution_env`(default HostExecution singleton)+ `ToolExecutionContext.execution_env`(default None)+ engine 一行 kwarg additive。**1010 个现有测试一行不动通过**——直接证明 additive-field discipline。 |
| **P7-T3 — BashTool refactor** | execute() body delegated to `ctx.execution_env.run_command(...)`,fallback 到 `_HOST_EXECUTION` 当 ctx 字段 None。**所有 13 个现有 BashTool 测试一行不动通过 + 4 个新 substrate-swap 测试**——load-bearing assertion(D17.2 behavior parity)成立。 |
| **P7-T4 — Invariant verification + retro** | Structural test:22 个 protected module 不引用 `ExecutionEnvironment/HostExecution/ProcessResult/_HOST_EXECUTION` identifier。**Formal git-diff vs Phase 6 close**:8 个 zero-diff subsystem + engine/query.py 仅 1 行 additive kwarg。 |

---

## 3. Framework-level 主题 — Phase 7a 真正学到的

### 3.1 第四次 invariant 兑现 —— **"identity transform" 是最强 invariant proof**

Phase 6 retro §3.1 提出 sub-agent 是"first 新控制流形状"成功 invariant。
Phase 7a 提供了**性质上不同的另一种证明**:**identity transform**。

`HostExecution.run_command` 的 body 是从 `BashTool.execute` **byte-identical
extracted** 的。在数学意义上,它是 identity function —— 输入输出完全等价
于原始 Bash 直接调用。然后:

> **All 13 existing BashTool tests pass UNCHANGED after refactor.**

这一条比"引入新功能后所有测试还通过"**强**得多:它直接证明 substrate 层
的引入**对 LLM-facing 行为零影响**。任何行为漂移都会立刻在 BashTool 测试
表现出来,无法藏。这是"behavior parity as invariant proof"模式 ——
比"absent of regressions"严格一档。

→ 这个模式适合**所有 refactor / 抽象提取**任务:先提取一个 identity
transform,验证 parity,再做差异化 substrate(7b Docker)。比"一步直接
做 Docker substrate"安全得多 —— 出问题时知道是 Docker 实现的 bug,不是
抽象本身的 bug。

### 3.2 Layered extension model 在 Phase 7a 的强化版

Phase 5b retro §3.1 提出 layered extension model(Layer 0/1/2/3,每层
不污染上层)。Phase 7a 强化了**反过来的方向**:**每层只让需要的 tool
付出成本**。

具体:Phase 7a 加了 `ToolExecutionContext.execution_env`,但只有
**BashTool**(arbitrary code execution 入口)读它。Read/Write/Edit/Grep
继续直接 `pathlib.Path.read_text` —— 它们不需要 substrate 抽象,**就不
受其影响**。MCP/LoadSkill/SpawnAgent 也是 —— 它们的 execute body
delegate 到自己的远程/解析逻辑,根本不消费 execution_env。

→ Layered extension model 的核心**不是"加层"**,是**"每层只让真正需要
的 consumer 承受 cost"**。Phase 7a 装了一个新 layer 但**4 个 base tool +
3 个扩展 tool 一行不改**就是这个模型的精确实现。

→ 反例(我们没踩的坑):如果设计成"所有 tool 必须通过 ExecutionEnvironment
执行所有 I/O",Read 就要重写 `read_text` → 走 substrate,Grep 就要重写
遍历 → 走 substrate,整个 tools/ 都要改。Phase 7a 没掉这个坑,因为
**抽象只暴露给真正需要它的消费者**。

### 3.3 Phase 6 → Phase 7a 模板复用的"复利显形"

数一下 Phase 6 → Phase 7a 完全复用的模式:

| Phase 6 元素 | Phase 7a 完全复用 |
|---|---|
| `ToolExecutionContext.parent_query: QueryContext \| None = None` | `ToolExecutionContext.execution_env: ExecutionEnvironment \| None = None` |
| `QueryContext.agent_depth: int = 0` field | `QueryContext.execution_env: ExecutionEnvironment` field |
| `engine/query.py` `ToolExecutionContext(cwd=..., parent_query=context)` | `engine/query.py` `ToolExecutionContext(cwd=..., parent_query=..., execution_env=context.execution_env)` |
| `SpawnAgent.execute` is the only consumer | `BashTool.execute` is the only consumer |
| Structural invariant test scans protected modules | Structural invariant test scans protected modules(同样 9+13 modules) |
| `dataclasses.replace(parent, ...)` 保留所有未指定字段 | 同样的机制让 sub-agent 自动继承 parent's `execution_env` —— **零代码** |

Phase 6 花了 6 个 task 把这个模板树立。Phase 7a 用 4 个 task 复用它。
**抽象的复利在这里以 task 数减少 33% 显形**。

→ 这是 framework "stable plateau" 的实测验证(Phase 6 retro §3.1 提出
的判断)。Phase 7a 没有发明任何新模式,**只是把已有模板再用一次**,
而且更熟练。

### 3.4 ⚠️ 设计踩坑:`run_command(cmd: list[str])` vs `run_command(command: str)`

P7-T1 boundary doc 最初写的是 `run_command(cmd: list[str], env, stdin)`
—— 6 个参数,exec-style 接口。然后读 BashTool 源码发现:

- Bash 用 `create_subprocess_shell(args.command, ...)` —— 单字符串,
  ``/bin/sh -c`` 语义
- 改 list[str] 接口意味着 BashTool 要 `cmd=["bash", "-c", args.command]`
- 但 `/bin/bash` ≠ `/bin/sh` —— 不同 shell,不同 expansion 规则,**parity
  会破**

修复:把接口改成 `command: str`,删除 `env` / `stdin`(Bash 不用)。
**接口收窄反而是正确的**——因为消费者只有一个(BashTool),不需要"未来可能
有其他 consumer"的预设。

inline 修正了 boundary doc(D17.1),记录了 reasoning:

> Shell-string interface (NOT exec-style ``list[str]``) because:
> - Current ``Bash`` calls ``create_subprocess_shell(args.command, ...)``
>   which is ``/bin/sh -c "..."`` semantics
> - HostExecution must preserve this byte-identically — switching to
>   ``cmd: list[str]`` would mean ``["bash", "-c", args.command]`` and
>   change the shell from ``/bin/sh`` to ``/bin/bash``, breaking parity

→ **教训**:抽象接口的形状要由**当前消费者的实际需求**决定,不是"未来
可能的更多消费者"。Phase 7b Docker substrate 接受同样的字符串(`docker
exec sh -c "..."`),完全 OK。如果未来某天真有 consumer 需要 exec-style,
**到那时再加另一个接口** —— 不要现在就预设。

→ 这是"YAGNI in abstraction design" 的具体兑现。Phase 7a invariant
test 已经验证了 4 个 allowed module 都用了正确的 identifier;扩展接口
是 future change,**有了具体 use case 再做**。

### 3.5 `ProcessResult.output`(merged)vs `(stdout, stderr)` —— 同样的 YAGNI

同样的判断在 ProcessResult 设计上出现一次:

- 最初写法:`ProcessResult(stdout: str, stderr: str, exit_code: int)`
- 修正:`ProcessResult(output: str, exit_code: int)` —— 单一字段
- 理由:current Bash 用 `stderr=asyncio.subprocess.STDOUT` 在**pipe 层**
  合并 → 保留了 chronological order;Python-层分开两个 buffer 会改变
  ordering 语义

→ **保持现有行为的"原子性"** 也是 abstraction 设计的重要考虑。如果原始
实现有副作用顺序保证(merged pipe 给的 chronological 保证),抽象不应
**默默打破**这个保证。

### 3.6 Phase 7a 的 "phase 拆分" 决策本身值得复盘

User 在入口选择了 **Phase 7a (abstraction-only) 而不是 full Phase 7
(含 Docker)**。这个选择基于:

- **framework 学习价值** > Docker 工程
- **避免 dependency 复杂度** —— `docker` SDK / macOS 嵌套 VM / 跨平台
  CI 都不是 framework 抽象的部分
- **HostExecution 的 identity transform 就是抽象的最强 proof**

事后看这个选择**完全正确**:

| | Phase 7a (实际) | 如果做 full Phase 7 |
|---|---|---|
| LoC | ~150 | ~400 |
| 时长 | 1 天 | 3-5 天 |
| 新依赖 | 0 | docker SDK |
| 跨平台测试问题 | 无 | macOS Docker Desktop, CI Linux gate |
| Invariant proof 强度 | 同 full(identity transform 已经证明完) | 同 7a |
| 真实可用 sandbox 功能 | 无 | 有 |

→ Phase 7a 在 **抽象验证价值 / 工程成本** 的曲线上是最优拐点。Phase 7b
真要做时是独立 ~250 LoC,**不会有 7a 工作要 redo**(那是抽象做对了的
另一个证明)。

**通用教训**:遇到"big feature with substantial engineering"时,问一句
"能不能拆出 abstraction-only 版本先做?"。如果可以,**先做**——抽象验证
是更高密度的 learning,具体实现是后续 plug-in 工作。

---

## 4. 跟 Phase 5a / 5b / 5c / 6 的整体对照

| 扩展类别 | Phase | 接触层级 | 主要 consumer | "新概念" |
|---|---|---|---|---|
| External tools (callable function) | 5a ✅ | Layer 2 | tool catalog | MCP server |
| Pre-LLM transform | 5b ✅ | Layer 0(cli.py) | user input | slash command |
| External knowledge | 5c ✅ | Layer 1+2 | LLM via catalog + tool | skill |
| External control flow (recursion) | 6 ✅ | Layer 2 (BaseTool) | SpawnAgent.execute only | sub-agent |
| **External substrate (执行环境)** | **7a ✅** | **Layer 2 (BaseTool 内部)** | **BashTool.execute only** | **substrate / ExecutionEnvironment** |
| External execution backend (Docker / gVisor / 远程) | 7b 待入 | 同 7a + 新 substrate plug-in | 同 7a | Docker / 容器 |

观察:Phase 7a 跟 Phase 6 都是**Layer 2 内部装入"新概念"**(catalog 不变,
但具体 tool 的 execute body 内部多了一个 indirection)。**这是稳定平台
期的 signature**:新能力都通过 BaseTool 子类的 execute 内部 plumbing 完成,
框架其它层零感知。

→ 至此 **Layer 0-3** 四个层级全部有实例验证(Layer 3 在 sub-agent 的
"嵌套 run_query 即 Layer 2 内部递归" 一文中也算覆盖到了)。Phase 5d
ModeBundle 才会进入真正的 cross-layer 场景。

---

## 5. 如果重做 Phase 7a 我会改什么

| 当时做对的 | 当时可以更激进的 |
|---|---|
| 接口收窄到 `(command, cwd, timeout)` —— YAGNI 没踩过度设计的坑 | 没有为 Phase 7b 留 `lifecycle` 方法(spawn/teardown)—— 容器需要状态。但这个**正是 7a 抽象的限制点**,等 7b 入口再扩展 Protocol。inline-记录在 retro 让 7b 知道 |
| `HostExecution` body byte-identical extracted —— behavior parity 用最干净方式证明 | `_HOST_EXECUTION` module singleton 模式 vs `default_factory=HostExecution`—— 都行,前者轻微更高效但意义不大。选了 singleton,在 retro 记录 |
| inline-修正 boundary doc D17.1(`list[str]` → `str`)—— 不假装 boundary doc 是石板 | inline 修正策略需要在 CLAUDE.md 一句话:**boundary doc 是入口 snapshot**,实际实现 diverge 时应该 inline-amend 而不是 retro-only 记录(避免 retro 跟 boundary 永久不一致) |
| Phase 7a 选择 abstraction-only 而非 full Docker | 没有 explicit `FakeExecutionEnvironment` 在 `execution/` 包里(只在 test 文件)——但实际不需要 fix:test 用的 fake 不属于 production code,不应该 ship |

---

## 6. 给后续 phase 的 input

### Phase 7b (real Docker substrate) 应该做的

- **复用 `ExecutionEnvironment` Protocol 不动**(D17.1 单方法)。Docker
  substrate 接受同样的 `command: str` —— `docker exec sh -c "..."` 完美
  兼容
- **如果需要 lifecycle**(container spawn/teardown),**在 Protocol 上加
  方法**:`async def __aenter__/.__aexit__` 或独立 `start/stop`。同
  ``McpClient`` 形态(P5-T2)。当前 7a Protocol 不带这个 —— 因为
  HostExecution 是无状态的;Docker substrate 需要时再加,**这是 forward
  extension 不是 breaking change**
- **跨平台 CI 策略**:Linux gate(原生 Docker)+ macOS 本地手动(Docker
  Desktop)。pytest mark + env var 模式,参考 Phase 1 integration test
- **Settings + CLI flag**:`--sandbox` / `--sandbox-network=bridge|none` /
  `--sandbox-memory=512m` / `--sandbox-image=name`。Settings 字段
  `sandbox_*`
- **保持 invariant**:Phase 7b 不动 permissions / hooks / engine / 其它
  tools。**只增加 `execution/sandbox.py`** + Settings + CLI

### Phase 5d (ModeBundle) 应该警惕的

- ModeBundle 是 first **cross-layer** tenant(同时动 Layer 0 commands +
  Layer 1 prompts + Layer 2 hooks + permission overlay)。Phase 7a 的
  "abstraction-first" 模式适用:**先抽 `ModeBundle` 数据结构**(纯数据
  + Protocol),再做具体 application points
- 注意:Phase 7a 证明 invariant test 可以**字段级别**精确(检查
  `execution_env` 字段而不是 `Execution` 单词)。ModeBundle 的 invariant
  test 要做得**更精**——只有 cli.py 启动期 + ModeBundle 自身 module
  允许引用 `ModeBundle` identifier

### 未来 phase 普遍要警惕的

- **接口收窄(YAGNI in abstraction design)** —— 不要为"未来可能的
  consumer"加 method/参数。当前 consumer 不需要的字段就不加
- **identity transform 是最强 invariant proof** —— refactor / 抽象提取
  任务先做 identity 版,验证 parity,再做差异化
- **boundary doc 不是石板** —— 实现 diverge 时 inline-amend,不要让
  retro 跟 boundary 永久不一致

---

## 7. Phase 7a DoD Checklist

- [x] `ExecutionEnvironment(Protocol)` + `ProcessResult` 在 `execution/base.py`
- [x] `HostExecution` 包装当前 Bash 行为 byte-identical
- [x] `QueryContext.execution_env: ExecutionEnvironment` 默认 HostExecution
  singleton —— 现有所有 1010 测试通过
- [x] `ToolExecutionContext.execution_env: ExecutionEnvironment | None`
  additive 字段
- [x] `engine/query.py` 一行 additive kwarg(`execution_env=context.execution_env`)
- [x] `BashTool.execute` delegate 到 `ctx.execution_env.run_command(...)`
- [x] **13 个现有 BashTool 测试一行不动通过**(behavior parity 锁定)
- [x] 4 个新 substrate-swap 测试通过 (FakeExecutionEnvironment 注入)
- [x] **形式化 git-diff invariant verification**(against Phase 6 close):
  - permissions/ → 0 lines
  - hooks/ → 0 lines
  - observability/ → 0 lines
  - mcp/ → 0 lines
  - compaction/ → 0 lines
  - skills/ → 0 lines
  - commands/ → 0 lines
  - protocols/ → 0 lines
  - engine/query.py → 1 line additive (kwarg)
- [x] Structural invariant test(22 个 protected module + 4 forbidden
  identifier + 反向 allowed 校验)
- [x] `execution/` 模块 100% coverage
- [x] 全套覆盖率 ≥ 95%
- [x] mypy --strict 干净 / ruff 干净 / pre-commit 全过
- [x] README "Phase 7a — ExecutionEnvironment" 章节
- [x] `learnings/phase-7a.md`(本文件)
- [x] `tasks/phase-7-plan.md` DoD closeout

---

## 一句话

> **Phase 7a 用 ~150 行代码,通过 identity transform 完成了第四次 invariant
> 兑现 —— 而且是性质上最强的一次**:不只"加新功能后旧测试还通过",而是
> **"重构后旧测试一行不改一个不挂"** —— 行为 parity 直接证明抽象的引入
> 对 LLM-facing 层零影响。
>
> 同时验证了 Phase 6 retro 提出的 "framework 达到稳定平台期" 判断:Phase
> 6 → Phase 7a 完全复用 additive-field 模板,task 数下降 33%。复利在显形。
>
> Phase 7b(real Docker)成了纯 plug-in 任务,~250 LoC 独立 phase,
> 不需要碰 7a 任何代码。这就是"先做对抽象,再做工程实现"的正确顺序。
