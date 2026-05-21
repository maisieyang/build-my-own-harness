# PLAYBOOK — 用 Vibe Coding 造一个产品级 LLM Harness

> **TL;DR**:23 天,1 个开发者 + Claude Code,从 0 造出 ~10,800 行生产代码、
> 1274 个测试、95.33% 覆盖率、mypy strict、ruff clean 的 LLM agent harness
> ([OpenHarness from scratch](./README.md))。这个 playbook 不教你怎么写 prompt,
> 教你怎么把 vibe coding **工程化** —— 让 human 守在 contract layer、agent 守在
> implementation、review 守在 commit boundary。可复用的不是项目本身,是后面这套
> 方法论。**不绑工具**,任何 AI 协作环境配合这套方法都能 reproduce。

---

## Part 0 — 为什么这个 playbook 存在

Vibe coding 现在面临两个不公平的待遇。

一边是**神化**:"我跟 AI 聊聊就写出了能跑的 production 代码"。另一边是**妖魔化**:"AI 写的代码根本没法维护"。两个极端都错。

真相是:**vibe coding 是一套工程纪律,不是 prompt-and-pray**。

这套纪律 23 天里造出 17 个 phase 的 OpenHarness 实证:

| 维度 | 实测数字 |
|---|---|
| 时间 | 23 天(2026-04-27 → 2026-05-20) |
| 人员 | 1 个开发者 + Claude Code 协作 |
| 生产代码 | ~10,800 行(`src/openharness/`) |
| 测试代码 | ~21,600 行 / 1274 个测试 / 95.33% 覆盖率 |
| 质量门 | mypy --strict + ruff check + ruff format,全程开启 |
| 文档 trail | 24 个 decision doc + 31 个 retro + 195 commits |
| Subsystem 数 | 18 个(api / engine / tools / hooks / permissions / observability / 等) |

下面所有论断都基于此。

数字不是为了炫耀,是为了**承担说服责任**。如果一套方法论自己 prove 不出 measurable 结果,凭什么让别人花时间读它。

---

# Part I — 方法论(playbook 的真正复用价值)

架构会因领域不同而异(LLM harness vs payment 系统 vs CRUD app 完全不同),但**怎么和 AI 协作**这件事是跨领域的。所以方法论才是 playbook 真正可复用的东西。

## 1. 四步 Phase Loop

每个 phase 严格走四步,不偷工不跳步:

```
1. Boundary doc  → 这个 phase 做什么 / 不做什么 / 必须守住哪些不变量
2. Plan         → capability 级任务清单 + 验收标准
3. Execute      → agent 自主推进 sub-task,human 守在 contract layer
4. Retro        → 学到什么 / 哪些抽象通过测试 / 下一个 phase 预判什么
```

**最反直觉的是**:步骤 1 和 4 在日历上花的时间比步骤 3 多,但步骤 3 把这份投资以**复利**的形式还回来。

OpenHarness 里最强的证据是 Phase 7a/7b/7c 三连:
- **7a**(1 天):把 tool execution 抽成 `ExecutionEnvironment` Protocol,`HostExecution` 是 identity transform(行为零变化,只重构抽象)
- **7b**(1-2 天):基于这个 Protocol 实现 Docker sandbox
- **7c**(半天,~30 行代码):加 gVisor runtime,**是 7b 的 12% LoC**

为什么?7a 一次把成本付清。后两个 substrate 几乎免费。

**这个 pattern 的可复用判据**:任何时候你看到一件事在三个地方做类似的事,先抽抽象再做第二个。第二个不抽,会变成"特殊版本";第三个再抽,会被前两个具体形态污染。

## 2. Spec 在正确的高度 —— capability 不是 sub-task

Plan 必须停在 **capability 级**,绝不下沉到 sub-task。

✅ **正确高度**:
> "P1-T4: `oh ask` streaming 输出 + 人话错误提示 + 集成测试 gated"

❌ **错误高度**(过度规定):
> "4a 实现 Settings → 4b 写 mock → 4c 真 client → 4d 集成测试 → 4e `__init__.py` exports"

错误版本不是错,是**多余**:agent 自己 decompose 也会得到这个分解,而且通常**比你分得更准**(它看的是当下的代码,你看的是 plan 文档里的猜测)。

human 一旦写 sub-task plan,agent 的自主权就被浪费在 busywork 上,**你也不再思考契约,你在思考实现**。这就退化成传统的工单管理。

## 3. Agent 必须主动停下来问的 3 类情况

默认 agent 自主驱动 sub-task。遇到下面 3 类必须升级到 human:

1. **外部契约决策** —— 公开 API 形状、env var 名字、新依赖。任何包边界之外能看到的东西都不是 agent 该自己定的。
2. **不可逆操作** —— 删文件、改 schema、改公开接口、`git filter-repo`、force push。"blast radius"原则:大就升级。
3. **Capability 描述本身错了** —— agent 发现 boundary doc 写的不变量根本守不住,或者 plan 的验收标准互相矛盾。**别糊弄过去,显式 surface "前提错了"再继续。**

前两类是 blast radius 防御,第三类是 epistemic honesty。

## 4. Review 在 commit 之前,不是之后

测试 GREEN ≠ acceptance。Agent 走完一个 capability 后、**`git commit` 之前**,必须做一次 walkthrough:把 diff 跟验收标准逐条对一遍。

这一步是**唯一**能 catch 下面三种 failure 的机制:

- 测试都过了,但 agent 悄悄漏掉了一条验收标准
- 测试写得太松,功能其实是坏的但 GREEN
- 这次 commit 偷渡了不属于这个 capability 的副作用改动

CI 拦不住这些。Coverage gate 也拦不住。只有 human 当场读 diff 才拦得住。

**不允许 "测试绿了我就 commit 了" 这种话**。如果你 delegate 这一步,你就不在 contract layer 上了,你在祈祷。

## 5. Trail —— 单人项目的协作记忆

单人项目最大的坑不是"没人协助",是"过去的你不再帮你"。三周前做的决策,你今天会忘。

OpenHarness 用三个 **append-only directory** 解决这个问题:

| 目录 | 内容 | 写入时机 |
|---|---|---|
| `decisions/` | Boundary docs —— 做什么 / 不做什么 / 不变量 | 每个 phase **开始前** |
| `tasks/` | Capability 级 plan | 每个 phase **开始前** |
| `learnings/` | Per-phase retro —— 学到什么、哪些抽象通过 | 每个 phase **结束后** |

关键不是"未来读者会线性读完它们",关键是**任何时候你想加一个新想法,你立刻知道它该归到哪个文件夹**。这种零摩擦让你在 23 天里完成 17 phase 而不是 5 phase。

没 trail 的项目:决策埋在 commit message 里,3 周后找不到;learning 在你脑子里,过 1 个月忘掉。

---

# Part II — 架构(harness 必须长什么样)

下面这层是 OpenHarness 这个具体项目的总结,不像 Part I 那样跨领域可复用,但**如果你也在造 LLM harness**,这是经过 17 phase 压力测试的 layout。

参考完整 tier 划分:[`ARCHITECTURE.md`](./ARCHITECTURE.md)。

## 6. Tier 0 —— streaming tool loop 核心(必做,~1 周)

不做就不叫 harness:

| Subsystem | 价值 | OpenHarness 落点 |
|---|---|---|
| **Protocols** | wire-level 数据类型,Pydantic v2 + `extra="forbid"` 防 typo | `src/openharness/protocols/` |
| **API client** | 抹平不同 LLM provider 差异,Protocol 抽象 | `src/openharness/api/` |
| **Streaming events** | LLM token / tool use / error 分类输出 | `protocols/stream_events.py` |
| **Engine + agent loop** | `run_query()` —— 一个 `AsyncIterator[ApiStreamEvent]`,LLM 发 `tool_use` 块,harness dispatch tool,append result,直到 `end_turn` | `src/openharness/engine/` |
| **Tool system** | `BaseTool` ABC + `ToolRegistry`,permission check 在 dispatch 前 | `src/openharness/tools/` |
| **5 个内建 tool** | Read / Write / Edit / Bash / Grep | `tools/read.py` 等 |

**最重要的设计选择**:`run_query()` 是 async generator,**事件流即接口**。后面所有功能(REPL / 渲染 / observability)都消费这个事件流,引擎不需要知道它们存在。

## 7. Tier 1 —— 生产硬化(让 Tier 0 真正"生产级",~1 周)

| Subsystem | 价值 |
|---|---|
| **三层权限** | 硬编码敏感路径 + 用户 glob deny + 模式 override(`--auto`/`--dry-run`)|
| **Hook 中间件** | 5 个生命周期事件:`PreToolUse` / `PostToolUse` / `PreApiCall` / `PostApiCall` / `OnError`。Deny / modify / observe 语义 |
| **异常体系** | 区分 retry-able 错误、auth 错误、用户操作错误、bug |
| **重试 backoff** | 指数 + jitter,只对真正 transient 错误重试 |
| **结构化可观测** | structlog JSON 输出 + `run_id` / `turn_id` / `agent_depth` 三层 context binding,`jq` 可重建 trace |
| **Microcompact** | Layer 1: per-tool-result token cap hook;Layer 2: reactive PromptTooLong 重试时丢最老 tool_use/tool_result pair |
| **测试体系** | pytest + pytest-asyncio + 覆盖率 + CI(Python 3.10 + 3.11)|

**别延后的事**:mypy strict、ruff、pre-commit、CI 必须 Phase 1 就开。后期再加严格度,旧代码补不完,变成永久 tech debt。

## 8. Tier 2 —— 可扩展面(选 2-3 个深做)

| 候选 | 学习价值 | OpenHarness 选择 |
|---|---|---|
| **MCP** | stdio JSON-RPC,Anthropic 推动的工业标准 | ✅(Phase 5,3 天)|
| **Slash 命令** | 用户体验的 UX 抓手 | ✅(Phase 5b,2 天)|
| **Skills**(懒加载专家知识) | LLM 自己决定何时展开 context | ✅(Phase 5c,2-3 天)|
| **Sub-agent** | 递归 tool dispatch,context isolation | ✅(Phase 6,2 天)|
| **ModeBundle** | 跨层组合(prompt + tool 白名单 + deny 路径 + hook chain)| ✅(Phase 5d,2 天)|
| **Plugin hook** | entry point + filesystem 双 source | ✅(Phase 5e + 5f)|

**4 个扩展模式**(经过这 6 个 phase 验证):

1. **BaseTool 是 LLM 的 syscall interface** —— 不论 MCP / Skill / Sub-agent / Sandbox,本质都是注册一个 `BaseTool` 实例。dispatch loop 不需要知道这是 federated tool 还是本地 tool。
2. **Additive kwarg** —— `func(name)` → `func(name, plugin_catalog=None)`,默认值 = 旧行为。旧调用者零修改,新功能 opt-in。
3. **Source-agnostic catalog** —— `dict[str, HookSpec]` 不携带 producer 信息(没有 `source: Literal["entry_point", "filesystem"]` 字段)。结果:第二个 producer 是第一个的 60% 成本。
4. **Cross-cutting invariant 验证** —— 每加一个新功能,验证 `permissions/checker.py` / `hooks/executor.py` / `engine/query.py` dispatch 逻辑**零 diff**。验证不过就是抽象做错了,**停下来回头修抽象**,不要硬塞 if-else。

## 9. Tier 3 —— 可以延后的东西

| 候选 | 何时再做 |
|---|---|
| **Docker / gVisor sandbox** | 当真有 untrusted code execution 需求 |
| **多 Provider**(超过 2 个) | 当 anti-corruption layer 真被压测时 |
| **Full LLM-summarization compaction** | 当 Microcompact 真不够时 |
| **Memory system** | 当跨 session 状态真有需求时 |
| **REPL 高级特性**(/save / multi-line) | UX polish 阶段 |

**判断标准**:Tier 0 + Tier 1 + Tier 2 第一个选项,就已经是一个**完整可用的 production harness**。Tier 3 是 nice-to-have,**不是入门必经路径**。

---

# Part III — 五个 framework lesson

这五条在 OpenHarness 里有**量化证据**(不是空泛主张),适用于任何要做扩展性设计的项目。详细论证见 [`learnings/phase-7.md`](./learnings/phase-7.md) §3。

## 10. Abstraction-first 复利

7a Protocol 设计 → 7b Docker substrate → 7c gVisor = **7b LoC 的 12%**。前一阶段把抽象成本付清,后续 implementation 几乎免费。

**判断 abstraction 是否做对的最强信号**:identity transform —— 拿现有代码塞进新抽象,**所有现有测试一行不改 GREEN**。

## 11. Layered model 扛 cross-cutting load

Phase 5d ModeBundle 同时改 4 层(system prompt / tool catalog / permission deny path / hook chain),**11 个 protected directory 零 diff**。

Phase 3 设计 hook / permission / observability 时押的赌注是 "未来跨层需求能通过组合现成 primitive 解决"。Phase 5d 是第一次真实压测,**押对**。

**反过来**:如果落地时必须改任何 "zero-diff" 层 —— 抽象做错了,回头修,不要硬塞。

## 12. Additive kwarg 是扩展稳定 API 的正确形态

Phase 5e 给 `resolve_hook(name)` 加 `plugin_catalog=None`。Phase 5d 写的 6 个 `resolve_hook` 测试 + 17 个 `apply_bundle_to_context` 测试,**没一个需要修改**就 GREEN。

**判据**(三个 yes 才能 additive 扩展,否则就是 breaking change):
- 新 kwarg 是否真的可选?(default = 旧行为)
- Opt-in 行为是否真的扩展?(不是 alias、不是 wrap)
- 现有测试是否 byte-identical 通过?

## 13. Source-agnostic catalog 解锁 plugin

`HookSpec` 只有 `event` + `hook` —— **不带 source 信息**。Phase 5f 加 filesystem source 时,catalog 类型零改、resolve 路径零改、apply 路径零改。

**判断时机**:任何 catalog-style 数据(dict / list / map)出现时,先问 —— 未来会不会有多个 producer?如果可能,catalog 必须 source-agnostic。一旦塞了 producer-specific 字段,所有 downstream caller 都被绑死。

## 14. API-level zero-diff 是 refactor 的正确 invariant

Phase 8 抽 `markdown_store/` 公共模块,**233 个 caller 测试一行不改通过**。

横切扩展的 invariant 是"其他层零 diff"(横向)。Refactor 的 invariant 是"caller 不变,测试不改"(纵向)。两个都是 zero-diff,**保护对象不同**。

**Refactor 真正的 success criteria**:
- 既有测试零修改(API-level zero-diff)
- 既有 caller 零调整(import 路径稳定)
- 总 LoC 净减少

三个都不达成,refactor 就是错的或者过早。Phase 8 等了 5b / 5c / 5d 三次重复(rule-of-three)才抽,**这是 sweet spot**,早抽 over-generalize,晚抽错失复利。

---

# Part IV — 三个反面教训(踩过的坑)

详细见 [`learnings/phase-7.md`](./learnings/phase-7.md) §5。

## 15. 别从 `0.0.x` 起手

`pyproject.toml` 从 Phase 1 就该写 `version = "0.1.0"`,不是 `"0.0.1"`。

`0.0.x` 在 semver 习俗里是 "this is a prototype, expect everything to break"。一个 mypy strict + 95.33% 覆盖率 + 1274 测试的项目**不是 prototype**。给自己的代码做正确的 signaling。

## 16. 别 defer factor-out 决策

OpenHarness 里 `_build_query_context` 这个 factor-out 应该 Phase 6+ 当时做。defer 到后面,后续两个 phase 还在用旧形态,refactor 时多改 2 个地方。

**直觉**:看到 3 个地方做类似的事 → 立刻 factor-out。看到 5 个地方还没 factor → 已经欠了债。

## 17. 别让本地 `.env` 掩盖 CI reality

OpenHarness 公开后 OSS push 触发 GitHub Actions CI,**炸了**。

原因:本地开发机有 `~/.openharness/.env`,带着真 API key。`Settings()` 构造能成功。15 个 `tests/bundles/` 测试本来该 isolation 隔离,但漏一层 `~/.openharness/.env`(Phase 7 才加的 user-global 层),所以本地全过。

**关键 lesson**:**CI badge 是单点最 load-bearing 的 "这数字真" 信号**。本地 pytest 跑出 1268 测试 + 97% 覆盖率,被本地 `.env` 灌"成功"路径 → 实际 CI 上是 1253 个 + 93%。

修复后:1274 tests + 95.33% coverage on CI(Python 3.11)。CI green badge 才是 ground truth,本地数字会骗你。详见 commit `3b5a99e` 和 `9aa9f4b` 的 retro 复盘。

**对策**:从 Phase 1 起,把 CI run 当成"测试是否真过"的唯一来源。本地通过只是 necessary,不是 sufficient。

---

# Part V — 启动你自己的项目(action items)

## 18. Day 1 Todo List

如果你今天要按这个 playbook 启动一个新 harness 项目:

```
□ git init + 选 Python 3.10+
□ pyproject.toml 写 version = "0.1.0"(别 0.0.x)
□ uv + hatchling 起脚手架
□ ruff check + ruff format + mypy --strict 全开,pre-commit 装上
□ pytest + pytest-asyncio + coverage gate 95%
□ GitHub Actions CI:Python 3.10 + 3.11 矩阵,跑 lint + types + tests
□ 三个目录建空 markdown:decisions/ tasks/ learnings/
□ 写 SPEC.md(项目契约:做什么/不做什么)和 ARCHITECTURE.md(tier 划分)
□ Phase 1 boundary doc + plan + 进入 execute
```

第一天什么都不应该跑出来,但**项目骨架的工程基线立住了**。

## 19. 决策框架

启动前回答 5 个问题:

| 问题 | 怎么决 |
|---|---|
| **Tier 0 必做哪些?** | 12 个必做模块([ARCHITECTURE §2](./ARCHITECTURE.md)),不商量,做不出来这些就不叫 harness |
| **Tier 2 选 2-3 个深做?** | MCP + Skills + Sub-agent 是 OpenHarness 验证过的最划算组合 |
| **Tier 3 选 0-1 个?** | 学习目标决定,**别贪多** |
| **从哪个 phase 开始?** | Phase 1 永远是 hello-LLM scaffold,锁死。后面顺序按 tier 走 |
| **CI 不变量是什么?** | 每个 phase 的 boundary doc 第一节就写:哪些层零 diff 是这个 phase 的成功标准 |

5 个回答完,boundary doc / plan / execute / retro 四步循环开始转。

---

# Appendix — 17 phase ship 顺序

实际交付顺序(不是规划顺序;5b / 5d 等是执行中 emerge 的 sub-phase):

| # | Phase | Capability |
|---|---|---|
| 1 | Foundation | 脚手架 + Pydantic protocols + OpenAI-compat client + streaming + CLI |
| 2 | Tool Loop | BaseTool + ToolRegistry + `run_query` + 5 内建 tool |
| 3 | Safety + Observability | 三层权限 + 5 事件 hook + structlog |
| 4 | Compaction | 两层 microcompact |
| 5 | MCP | stdio transport adapter |
| 5c | Skills | 懒加载专家知识 + LoadSkill tool |
| 5b | Slash 命令 | `~/.openharness/commands/*.md` |
| 6 | Sub-agent | 递归 SpawnAgent tool + 深度上限 |
| 7a | Substrate 抽象 | ExecutionEnvironment Protocol + HostExecution identity |
| 7b | Docker sandbox | aiodocker substrate |
| 5d | ModeBundle | 第一个跨层 tenant |
| 5e | Plugin hook(entry point) | source-agnostic catalog 第一个 producer |
| 8 | `markdown_store/` refactor | rule-of-three 之后的公共模块抽取 |
| 5f | Plugin hook(filesystem) | 第二个 producer,60% cost |
| 7c | gVisor runtime | kwarg-not-class 判断,7b 的 12% LoC |
| 6+ | `oh chat` REPL | 多轮对话 + ConversationCompleteEvent |
| 7 | 收尾 | README rewrite + 3 个 introspection subcommand + 打包 + meta-retro |

完整 ship-order timeline 见 [`learnings/phase-7.md`](./learnings/phase-7.md) §2。

---

# 收尾 —— 这套方法论到底属于谁

它不属于 Claude Code,也不属于 OpenHarness。它属于**任何愿意守纪律的 vibe coder**。

工具会变(明天可能是 Cursor,后天可能是别的)。方法论的内核不变:

1. **Human 守在 contract layer,不下沉**
2. **Agent 守在 implementation,自主推进 sub-task**
3. **Review 守在 commit boundary,不是测试 GREEN 之后**
4. **Trail 是单人项目最重要的协作记忆**

这三条加 trail 习惯,够你跟任何 AI 工具配合造任何 production 系统。

23 天可以从 0 造一个 Claude Code 级别的 LLM harness 就是证据。如果你也试这套方法,**带数据回来告诉我**。

---

## Pointers

- [`README.md`](./README.md) — 项目入口 + 用户视角介绍
- [`CLAUDE.md`](./CLAUDE.md) — 公开 case study(双重身份:human-readable case + live agent guidance)
- [`SPEC.md`](./SPEC.md) — 项目契约
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — Tier 划分 + 依赖图
- [`learnings/phase-7.md`](./learnings/phase-7.md) — Meta-retrospective(本 playbook 的量化证据来源)
- [`decisions/`](./decisions) — 24 个 boundary doc(每个非平凡决策一份)
- [`learnings/`](./learnings) — 31 个 retro(每个 phase 一份)
- [`tasks/README.md`](./tasks/README.md) — 17 phase plan trail 导航首页
