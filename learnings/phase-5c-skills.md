# Learnings — Phase 5c (Skills — Lazy-Loaded Expertise)

> Phase 5c 起止 / 2026-05-15(单日,在 Phase 5a MCP 收尾后立即开启)
> 5 capabilities (P5c-T1…T5) / 11 sub-units / 5 commits / 26 new tests / 100% on Skills modules
>
> 本文件**不是** sub-unit 合集 —— 那些 commit message 已经详尽记录。
> 它回答的题:**做完 Phase 5c,关于"扩展机制能不能坍缩成一个 pattern"
> 这件事,学到了什么 framework-level 的东西。**

---

## 1. 数据点

| 维度 | Phase 4 (Compaction) | Phase 5a (MCP) | Phase 5c (Skills) |
|---|---|---|---|
| Capability(task) | 5 (T1-T5) | 7 (T1-T7) | **5** (T1-T5) |
| Sub-units | ~15 | ~20 | **11** |
| 生产代码量 | ~200 行 | ~600 行 | **~170 行** |
| 新增 module | `compaction/` | `mcp/` | **`skills/` + 1 tool** |
| 触碰横切 module | `api/errors` + `engine/query` + `cli` + `settings` | `cli` + `settings` (+1 log 字段) | **`prompts.py` + `cli.py` + `engine/context.py` (+1 字段)** |
| 改 `permissions/` 行数 | 0 | 0 | **0** |
| 改 `hooks/executor` 行数 | 0 | 0 | **0** |
| 改 `engine/query` 行数 | +~20 (Layer 2 retry 分支) | 0 | **0** |
| 改 `observability/logging` 行数 | +1 字段 | +1 字段 | **0** |
| 新增测试 | 50+ | 80+ | **26**(skills/3 文件 + tools/1 + cli/1 + prompts) |
| Phase 修改后总覆盖率 | 96.4% | 96.7% | **97%** |

**关键观察**:Phase 5c 是历史上 **diff 最小的 capability phase** —— 因为它
**几乎所有重活都在复用 Phase 1-4 已经装好的机制**。这不是因为 Skills 简单,
是因为 framework 抽象**做对了**。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P5c-T1 — `skills/` 包基座** | 同 P5-T1 (McpServerConfig) 的形态:frozen dataclass + filesystem discovery + 容错 parser(**absolutely never raise** —— 一个坏 skill 不挂全场)+ Protocol 抽象(`SkillStore`)+ sentinel(`EmptySkillStore`)。**未来插 RemoteSkillStore / VectorSkillStore 都是 Protocol 实现替换,不动 caller。** |
| **P5c-T2 — `LoadSkillTool`** | 一个 BaseTool 子类,构造时注入 SkillStore(同 `McpToolAdapter(client)` 形态)。`is_read_only=True` 让 Tier 3 lax 路径**自动**生效 —— 这条线是"第三次验证 Phase 3 abstraction"的核心证据:permission/checker.py 在 Skills 上**一行不动**。 |
| **P5c-T3 — prompts.py 注入 + CLI bootstrap** | `build_system_prompt(..., *, skill_store=None)` 保持向后兼容(byte-identical to 不传)+ CLI 在 MCP pool 注册之后 / system prompt 构建之前扫描 skill 目录。**Catalog always-on(L3)+ Body lazy load(L4)** 是这个 phase 真正的产品哲学。 |
| **P5c-T4 — End-to-end smoke + invariant verification** | Stub LLM(2 turns:tool_use → end_turn)驱动**真 engine + 真 permission + 真 hook 链** —— 唯一被 stub 的是 LLM 本身。+ 形式化 invariant test:**直接读 4 个"零改动"module 的源码,字符级 grep `LoadSkill` 等 identifier**。如果未来有人不小心 leak,test 立刻挂。 |
| **P5c-T5 — Coverage + retro** | skills/ 3 模块 + load_skill.py **全 100%**。本文件。 |

---

## 3. Framework-level 主题 — Phase 5c 真正学到的

### 3.1 三次兑现的 cross-cutting invariant —— "零改动" 不是 marketing,是验证

Phase 3 boundary doc 留了一个隐藏 acceptance:**Phase 5+ 的扩展点必须不增加
新的 dispatch path**。我们当时不知道这条 invariant 是否会成立——它依赖
hook/permission/observability 抽象做对了。三次独立验证后:

| Phase | 新加什么 | 改 `permissions/checker.py` | 改 `hooks/executor.py` | 改 `engine/query.py` |
|---|---|---|---|---|
| **Phase 5a** (MCP — 外部 tool) | `McpToolAdapter(BaseTool)` | 0 行 | 0 行 | 0 行 |
| **Phase 6 preview** (Sandbox — 外部执行环境) | `ExecutionEnvironment` 抽象 | 0 行(预测) | 0 行(预测) | 0 行(预测) |
| **Phase 5c** (Skills — 外部知识) | `LoadSkillTool(BaseTool)` | **0 行** | **0 行** | **0 行** |

→ **三次独立的 tenant 全部零改动,**这条 invariant 已经从"乐观假设"升级成
"经验证的契约"**。这是这个项目最重要的 framework-level 学习——比任何具体
feature 都重要,因为它是后续无数 feature 的复利底座。

### 3.2 "扩展" 是一个 false abstraction —— 真正的原语是 LLM-as-RPC + tool dispatch

Skills 入口的对话最深的洞察:**LLM 应用栈里所有的"扩展机制"——skill /
MCP / RAG / memory / docs lookup / sub-agent——都是同一个 pattern**:

```
[ Index ]     小,sticky,进 system prompt
[ Lookup ]    LLM 决策,通过 tool 调用
[ Content ]   大,lazy load,进 tool_result(messages)
[ Recurse ]   loaded content 可能引向更多 lookup
```

具体实例对照:

| 实例 | Index | Lookup tool | Content |
|---|---|---|---|
| Skill | system prompt 的 catalog | `LoadSkill(name)` | tool_result |
| MCP | `messages_request.tools` | `tools/call` JSON-RPC | tool_result |
| RAG | vector index / chunk metadata | `RetrieveDocs(q)` | tool_result |
| Memory | `MEMORY.md` 索引 | `Read(memory/x.md)` | tool_result |
| Code nav | dir listing | `Read`/`Grep` | tool_result |
| Sub-agent | agent 名册 + 能力描述 | `Task(agent, input)` | tool_result |

→ **Phase 5c 不是"装 Skills",是用 ~170 行代码再次证明:tool dispatch +
横切配套就是 LLM 应用的"图灵机",一切"扩展"都是它的程序。**

→ 这条洞察的直接推论:**未来加 RAG / cross-session memory / 文档检索 /
sub-agent / API gateway 都是 ~150-200 行级别的事**。框架已经备好。

### 3.3 system prompt vs messages 的角色分工 —— 不是约定,是协议级 split

Phase 5c 让我们第一次清晰看见 LLM stateless 协议**天然的角色分工**:

| 装哪里 | 性质 | 装什么 |
|---|---|---|
| **system prompt** | sticky,每轮都看 | catalog(小,频繁参考) |
| **messages[].content (tool_result)** | dynamic,append-only,可被 Phase 4 compaction 截 | skill body(大,用完可能过气) |

Skills / MCP / RAG / memory 全部**复用这个 split**——不是设计选择,是
"LLM stateless 协议" + "Phase 4 compaction policy" 推出来的必然结论:

- catalog 在 system prompt:LLM 每次决策都要"知道有什么存在"→ 必须 sticky
- body 在 tool_result:只在被引用那几轮起作用 → Phase 4 Layer 1 觉得过气可以
  head/tail 截,无伤大雅

→ Phase 5c 给了我们看清这个 split 是**协议的延伸**,不是 Skills 的专利。
P5c-T3 的 `prompts.py` catalog injection + LoadSkillTool 的 tool_result body
返回**就是这个 split 的具体兑现**。

### 3.4 "永不 raise" 的容错设计 —— bootstrap 阶段的零容忍 + 一容忍

Phase 5c 在两个地方设计了"容错"模式:

| 错误类型 | 行为 | 理由 |
|---|---|---|
| Phase 4 prompt-too-long(运行时) | engine catch + drop + retry(bounded 3 次) | 单点故障在 turn 边界恢复;3 次后 surface 不掩盖 |
| **Skills bootstrap 阶段错误** | `parse_skill` **never raise**,返回 `None` + warning log | **一个坏 skill 不能挂掉整次 `oh ask`**;bootstrap 容错语义跟 runtime 不同 |

→ **bootstrap 阶段的容错"零容忍/一容忍"哲学**:

- 不容忍 raise(会挂掉 CLI)
- 一容忍 skip + warning(一个坏的不影响别的好的)
- 这条规则在 P5-T2 MCP server init 失败时也是一样的(server marked dead,
  pool 继续)

这条规则**不是 Skills 独有**,是 Phase 5+ 多 tenant 时代的通用 bootstrap
原则。Phase 6 sandbox 入口、Phase 7 多 substrate 接入都会复用。

### 3.5 invariant 测试要 **structural**,不是 unit

Phase 5c-T4 的 `TestCrossCuttingInvariant` 是这个 phase 设计上最"meta"的
一块:它**直接读源代码 + 字符级 grep**,确认四个"零改动" module 里**根本
不存在 `LoadSkill` / `LoadSkillTool` / `SkillStore` 等 identifier**。

对比传统的 unit test 验证:

| 测试形态 | 例子 | 局限 |
|---|---|---|
| unit test | `assert checker.evaluate("LoadSkill", ...) == ALLOW` | 只证明"this evaluate call 返回 ALLOW",不证明"checker 没有 LoadSkill 专属分支" |
| **structural test** | `assert "LoadSkill" not in inspect.getsource(checker)` | 直接证明"checker 不知道 LoadSkill 存在" |

→ **invariant 是关于"什么不该存在"的断言;structural test 比 unit test
更直接表达这件事**。Phase 5+ 加新 tenant 时,推荐先写 structural invariant
test(测试驱动 invariant)。

---

## 4. 跟 Phase 5a / Phase 6 preview 的对照

Phase 5c 完成后,**三类外部扩展**的同形态结构清楚浮现:

| 扩展类别 | Phase | 入口 module | 核心 BaseTool | 注入字段 |
|---|---|---|---|---|
| 外部 **tools**(callable function) | 5a | `mcp/` | `McpToolAdapter` | `Settings.mcp_servers` |
| 外部 **execution env**(运行环境) | 6 preview | `execution/` | (无新 tool 类型,改 Bash) | `QueryContext.execution_env` |
| 外部 **knowledge**(expertise text) | 5c ✅ | `skills/` | `LoadSkillTool` | `QueryContext.skill_store` + system prompt catalog |

**未来可预测的扩展也会同形态**:

- **RAG / 文档检索**:`retrieval/` 包 + `RetrieveDocs(BaseTool)` + system prompt 索引
- **Cross-session memory**:`memory/` 包 + `RecallMemory(BaseTool)` + system prompt index
- **Sub-agent**:`agents/` 包 + `Task(BaseTool)` + system prompt agent 名册

→ 每一个都是 ~150-250 行代码。Phase 1-4 的复利在这里持续兑现。

---

## 5. 如果重做 Phase 5c 我会改什么

| 当时做对的 | 当时可以更激进的 |
|---|---|
| 一开始就把 `SkillStore` 做成 Protocol 而不是具体类 —— T2 注入测试受益 | `EmptySkillStore` 也许可以彻底删掉,改成 `Settings.skill_stores: list[SkillStore] = []` 然后 None 处理 —— 现在 `EmptySkillStore` 是个 sentinel,可有可无 |
| `parse_skill` never-raise + warning 一次到位 | 没做 frontmatter schema 严格的 Pydantic 模型 —— 现在是 dict + 手工 isinstance 检查;~~如果以后加更多字段 schema 增长,会想重构成 Pydantic~~ —— 但目前 6 字段(name/description/version + 3 个可选)还远没到那个临界点 |
| `is_read_only=True` 锁在 class attribute 而不是 instance —— Tier 3 评估时不用 instance | Skills 的 hot reload(catalog 在 `oh ask` 之间变化)decisions/12 主动 defer,**这是对的**——但 chat mode (Phase 7+ ?) 时会需要,届时把 `FilesystemSkillStore` 加 `refresh()` 即可 |

---

## 6. 给后续 phase 的 input

### Phase 5b (Slash command) 应该复用的

- **markdown + YAML frontmatter** 格式(Skills 已经验证 PyYAML 路径)
- **global + project 双层 + project overrides** 模式(Skills L2 已经验证)
- `~/.openharness/<X>/` + `.openharness/<X>/` **目录约定**

### Phase 6 (Sandbox) 应该复用的

- **never raise 在 bootstrap 阶段** 的容错哲学(Skill bad file / Sandbox image
  pull 失败都同形态)
- **structural invariant test** 模板(读 `permissions/` `hooks/` `engine/`
  源码 grep forbidden identifier)
- **Protocol + Sentinel default** 模式(Skills 的 `SkillStore` Protocol +
  `EmptySkillStore` → Sandbox 的 `ExecutionEnvironment` Protocol +
  `HostExecution` baseline)

### 未来 phase 普遍要警惕的

- **不要被"加新 dispatch path"诱惑** —— 任何时候想加,先回头看 invariant
  test;能写一个 BaseTool 解决,就不要碰 engine
- **system prompt vs tool_result 的 split 是协议级的**,不是约定 —— 不要试
  图把 catalog 塞 tool_result 或者 body 塞 system prompt

---

## 7. Phase 5c DoD Checklist

- [x] `skills/` 包 100% coverage
- [x] `tools/load_skill.py` 100% coverage
- [x] 全仓覆盖率 ≥ 95% (实际 97%)
- [x] mypy --strict 干净
- [x] ruff check + format 干净
- [x] pre-commit hook 全过
- [x] End-to-end smoke 走完整 dispatch 链路
- [x] **Cross-cutting invariant 形式化验证**(structural test 读 4 个
  module 源码 + MRO 检查)
- [x] README 加 "Phase 5c features — Skills" 章节
- [x] `learnings/phase-5c-skills.md` 写完(本文件)

---

## 一句话

> **Phase 5c 用 ~170 行代码,第三次验证了 "LLM + tool 调用 = harness 的
> 图灵机" 这件事——所有"扩展"都是这台图灵机的程序,不是新的指令集。**
>
> 这条 invariant 在 Phase 5a(MCP)第一次兑现,Phase 5c(Skills)第二次
> 独立兑现,Phase 6 preview(Sandbox)预测第三次。
>
> Framework 抽象做对了,后续每加一种 tenant 都是 ~150-200 行级别的事。
> 这就是 Phase 1-4 复利的真正显形。
