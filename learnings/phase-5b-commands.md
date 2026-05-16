# Learnings — Phase 5b (Slash Commands — User-Facing UX Shortcuts)

> Phase 5b 起止 / 2026-05-16(单日,Phase 5c retro 后立即开启)
> 5 capabilities (P5b-T1…T5) / ~10 sub-units / 6 commits / ~40 new tests
> ~140 lines of production code / 100 % on commands/ modules
>
> 本文件**不是** sub-unit 合集 —— commit message 已经详尽记录。
> 它回答的题:**做完 Phase 5b,关于"pre-LLM 扩展"这件事,
> 学到了什么 framework-level 的东西。**

---

## 1. 数据点

| 维度 | Phase 5a (MCP) | Phase 5c (Skills) | Phase 5b (Commands) |
|---|---|---|---|
| Capability | 7 | 5 | **5** |
| Sub-units | ~20 | 11 | **~10** |
| 生产代码量 | ~600 行 | ~170 行 | **~140 行** |
| 新增 module | `mcp/` | `skills/` + 1 tool | **`commands/`(5 文件)** |
| 触碰横切 module | `cli` + `settings` | `prompts.py` + `cli.py` + `engine/context.py` | **`cli.py` only**(+1 except arm + 1 flag) |
| 改 `permissions/` | 0 | 0 | **0** |
| 改 `hooks/` | 0 | 0 | **0** |
| 改 `engine/query.py` | 0 | 0 | **0** |
| 改 `engine/context.py` | 0 | +1 字段 | **0** ⭐ |
| 改 `prompts.py` | 0 | +1 kwarg + 1 helper | **0** ⭐ |
| 改 `tools/__init__.py` | 0 | +1 export | **0** ⭐ |
| 改 `observability/logging.py` | +1 字段 | 0 | **0** |
| 新增测试 | 80+ | 26 | **~40** |
| Phase 修改后总覆盖率 | 96.7% | 97% | **97%** |

**关键观察**:Phase 5b 是历史上**触碰横切 module 数量最少**的 capability
phase —— **只动了 `cli.py`,所有 LLM-facing 层零改动**。这不是因为 Slash
Commands 简单,是因为它**结构性地比 Skills / MCP 都更靠前**:它 vanish
before LLM-facing infrastructure ever sees the prompt。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P5b-T1 — `commands/` 包基座** | 跟 Skills T1 结构镜像 —— 同 YAML frontmatter parser + 同 global+project store + 同 never-raise discipline。**没 refactor 成 shared `markdown_store`** —— 等 Phase 7 polish 再做(YAGNI / 2 个实例不足以抽象)。 |
| **P5b-T2 — `expand_command` prompt 解析** | 30 行的纯函数:no-slash 直通 / `/cmd args` 查表 / `{args}` 用 `str.replace` 不用 `str.format`(保留 body 里其它 curly content)/ tail append fallback(args 永不丢失)。 |
| **P5b-T3 — CLI 接线 + `--no-commands` flag** | Slash expansion 是 `_run_ask` 里 `configure_logging` 之后**做的第一件事** —— 后续所有 bootstrap(MCP / Skills / system prompt / hook registry / engine) 都看到已经 resolve 完的 user message。`--no-commands` 是**硬旁路**,不是 EmptyStore 替换 —— 否则会因 EmptyStore 总报 UnknownCommandError 而挂(踩了一次这个坑,代码注释里写了 reasoning)。 |
| **P5b-T4 — 端到端 + 结构性 invariant** | E2E 已经在 T3 `TestSlashCommands` 完成;T4 重点是**结构性 invariant test**(读 9 个 protected module 源码 + 字符级 grep 7 个 forbidden identifier)。比 Skills 的 test 严 —— protected set 多了 `engine/context.py` / `prompts.py` / `tools/__init__.py`。 |
| **P5b-T5 — README + retro** | 本文件 + README 加 "Phase 5b features — Slash Commands" 章节,**重点显式对比 Skills vs Commands 的 role split**(LLM-facing 知识 vs User-facing UX)。 |

---

## 3. Framework-level 主题 — Phase 5b 真正学到的

### 3.1 第四次 invariant 兑现 —— 而且**结构性更强**

Phase 5a + 5c 的 invariant 是"扩展不增加新 dispatch path"。Phase 5b 兑现
的是**更强的版本**:**扩展不增加新 LLM-facing 接触面**。

| Phase | 触碰 `engine/context.py` | 触碰 `prompts.py` | 触碰 `tools/` |
|---|---|---|---|
| Phase 5a (MCP) | 0 | 0 | 0 |
| Phase 5c (Skills) | +1 字段 | +1 kwarg | +1 export |
| **Phase 5b (Commands)** | **0** | **0** | **0** |

Skills 因为是 LLM-facing(LoadSkill tool / catalog 进 system prompt),
**必须**触碰 `prompts.py` 和 `engine/context.py`。Commands 是 pre-LLM —
它在 `cli.py` 解析阶段就完成,LLM 看到的是已经 resolve 完的字符串,
完全不知道有"命令"这个概念。

**含义**:harness 扩展点的"接触层级"是分级的:

```
Layer 0 (CLI input):       Slash commands (Phase 5b)
                           — 解析后消失,LLM 不知道

Layer 1 (system prompt):   Skills catalog (Phase 5c)
                           — LLM 知道有什么可用

Layer 2 (tool catalog):    MCP tools (Phase 5a) + LoadSkill (5c)
                           — LLM 直接调用

Layer 3 (execution env):   Sandbox (Phase 6 preview)
                           — tool 跑哪儿,LLM 不知道
```

**每一层都不污染上面的层**。这才是真正的"扩展机制坍缩成 pattern" —— 不是
"所有扩展都一样",是"每个扩展都在最低必要层加,**不向上层泄漏**"。

### 3.2 "EmptyStore as sentinel" 是错的 —— `--no-flag` 需要**硬旁路**

Phase 5c 给 `--no-skills` 用了 `EmptyStore` 作为 sentinel:相当于"假装没
skill,但代码路径不变"。这在 Skills 上 work 是因为 LoadSkill 即使 store
空也只是"找不到 → 返回 is_error 而不挂掉"。

Phase 5b `--no-commands` 一开始抄了同样的模式 —— **挂了一个测试**:

```python
expand_command("/review args", EmptyCommandStore())
# → raises UnknownCommandError("review", available=[])
```

逻辑上是对的(命令真的没有),但**这违背 `--no-commands` 的语义** ——
flag 的本意是"我有合法 slash 前缀的 prompt,别给我 expand"。

**修复**:`--no-commands` 必须是**硬旁路**(`if not no_commands:
expand_command(...)`),不能走 store 路径。

**通用教训**:`--no-X` flag 的语义有两种,要分清楚:

| 语义 | 实现 | 适用 |
|---|---|---|
| **"feature 不启用"** | EmptyStore sentinel | feature 自身有"不存在 ≠ 错误"的语义(Skills) |
| **"绕开整个 transformation"** | 硬旁路(if/else) | feature 必须存在才有意义(Commands) |

Phase 5d ModeBundle 入口要重新审视这个分类。

### 3.3 Commands vs Skills 的 role split —— 不是"两个 markdown 系统",是两个**完全不同的 audience**

最容易犯的设计错误:看到 Skills 和 Commands 都是 markdown+frontmatter,
就想"统一抽象"。**抽象是错的**。它们的 audience / trigger / lifecycle 不同:

| 维度 | Skills | Commands |
|---|---|---|
| Audience | **LLM**(知识消费者) | **User**(快捷输入者) |
| Trigger | LLM 决策 `LoadSkill(name)` | User 输入 `/<name> args` |
| Resolution stage | Mid-conversation,lazy via tool call | Pre-LLM,在 cli.py 解析 |
| Catalog visible to LLM | YES(system prompt) | NO(LLM 不知道存在) |
| Affects LLM context | Body 进 tool_result | Body 替代 user message |
| 失败模式 | `is_error=True` ToolResult,LLM 自纠 | `UnknownCommandError` exit 1,用户重打 |

把它们硬塞进同一个 abstraction 会**毁掉这个区分**:loaded skill body 不能
"替代 user message",resolved command 不能"等 LLM 决策再调"。

**这就是为什么 Phase 5b 没有 refactor 出 `markdown_store` 共享 helper**:
重叠的是**机制**(YAML parser + filesystem layers),不是**抽象**(audience
+ lifecycle)。Phase 7 polish 可以做机制层共享,**永远不要在 audience
不同的两个系统上做抽象层共享**。

### 3.4 "Pre-LLM transform" 是 framework 第一次出现的概念

Phase 1-5a 所有的扩展都是**LLM-facing**——加 tool / 加 hook / 加 permission
/ 加 substrate / 加 knowledge。Phase 5b 是第一个**pre-LLM** 扩展点:
slash command 在 LLM 还没启动前就消失了。

这开了一个新分类:**"在 LLM 看到 prompt 之前能做什么"**。Phase 7+ 还会
继续填充这层:

- **Prompt history rewriting**(改写历史 messages,例:debug mode 移除
  noisy tool_results)
- **Multi-prompt batching**(`oh batch file.jsonl` 把 N 个 prompt 串成
  一次 session)
- **Templating from external sources**(prompt 来自 Jinja / mustache /
  其它 templating 系统)

这些都是 pre-LLM transforms。Phase 5b `expand_command` 是它们的第一个具体
实例。设计上**这层有个共通的 invariant**:**任何 pre-LLM transform 的结果
都必须是合法的 user message string**。它们不能改 system prompt(那是
LLM-facing 层)、不能改 tool catalog(同样)、不能改 messages 历史(同样)。

→ **Pre-LLM transforms 是一个独立的 fold,不污染任何 LLM-facing 层**。
这是 Phase 5b 留给后续的 architectural insight。

### 3.5 "Refactor opportunity" 在 retro 里**显式 not-doing** 是重要的

Plan + boundary 都明确写了:**Phase 5b 不 refactor 成 shared
`markdown_store`**。retro 里也强调这件事。原因:

- 两个实例不够抽象(YAGNI)
- audience 不同 → 抽象层不应共享(§3.3)
- mid-phase refactor 会膨胀 scope,违背"smallest tenant test"目标

**but** 机制层(YAML parser + filesystem two-layer scan)可以在 Phase 7
polish 共享,留个 `markdown_store/` 包给两个 audience 各自实例化即可。

→ **Retro 里显式记 "not-doing" 跟显式记 "doing" 一样重要** —— 让未来的
自己不要为 "似乎应该重构" 的诱惑动手。Phase 7 polish 看到这条会感谢。

---

## 4. 跟 Phase 5a / 5c / 6 preview 的整体对照

| 扩展类别 | Phase | 接触层级 | LLM 可见？ |
|---|---|---|---|
| **Pre-LLM transform**(user input) | 5b ✅ | Layer 0(cli.py) | 不可见 |
| External **knowledge**(expertise text) | 5c ✅ | Layer 1+2 | catalog + tool |
| External **tools**(callable function) | 5a ✅ | Layer 2 | tool catalog |
| External **execution env** | 6 preview | Layer 3 | 不可见 |
| **(future) prompt history rewrite** | 7+ | Layer 0 | 不可见 |
| **(future) ModeBundle**(模板+权限+hook 组合) | 5d | Layer 0+1+2 | 部分可见 |
| **(future) RAG / cross-session memory** | 后续 | Layer 1+2 | catalog + tool |
| **(future) sub-agent** | 后续 | Layer 2 | tool |

Phase 5b 之后,layer 0(pre-LLM)和 layer 3(execution env)都有实例了。
Phase 5d ModeBundle 会**第一次同时跨多层**(catalog filter + permission
overlay + hook injection by command name),那时需要重新审视 cross-layer
invariant —— 但 5b/5c/6 留下的**单 layer tenant** 模板会是 ModeBundle
设计的起点。

---

## 5. 如果重做 Phase 5b 我会改什么

| 当时做对的 | 当时可以更激进的 |
|---|---|
| `--no-commands` 是硬旁路而不是 EmptyStore — 一次踩坑修了,留了 inline comment 记录 reasoning | `EmptyCommandStore` sentinel 在 Phase 5b 里其实没消费者(`cli.py` 直接 `if not no_commands` 旁路了)—— 严格 YAGNI 角度可以删掉,Phase 5d 真用上再加。**但 Protocol 实现保留 sentinel 是惯例,留着不亏** |
| 把 expand 逻辑做成**纯函数** `expand_command(prompt, store) -> str` 而不是绑在 store 上的方法 — 函数容易测、容易传 fixture,store 的责任是查找,不是 templating | 没有做 `mode:` / `hooks:` frontmatter 字段 forward-compat 的 lint 检查 —— 当前 parser 直接 `parsed.get(...)` 忽略未知字段。Phase 5d 若要加 schema 验证,要 careful 不要在 5b 把 forward-compat 写死 |
| `str.replace` 而不是 `str.format` —— 用户 body 可以放 Python dict 字面量 `{"a": 1}` 不报错 | tail-append fallback 用 newline separator 简单粗暴 —— 如果 body 已经以 `\n` 结尾就不加,否则加一个。**没考虑 body 末尾是非空白字符的 edge case**(`"text"` + `"more"` 拼成 `"text\nmore"`)——这是对的,但没有 test 覆盖这种边缘情况 |

---

## 6. 给后续 phase 的 input

### Phase 5d ModeBundle 应该警惕的

- **不要把 Skills 的 `LoadSkill` tool 和 Commands 的 `/cmd` 强行统一**
  —— audience 不同,抽象层不该共享(§3.3)
- ModeBundle 是 **第一个 cross-layer tenant**(动 layer 0+1+2),retro 时
  要把 cross-layer invariant 写清楚 —— 不能再依赖"单 layer 不污染"的简单规则
- `--no-X` 类 flag 设计时分清楚 "feature 不启用" vs "绕开 transformation"
  两个语义(§3.2)

### Phase 6 Sandbox 应该复用的

- **structural invariant test 模板**(读源码 grep forbidden identifier)
  —— Phase 5b 的版本比 5c 严(protected set 多了 engine/context / prompts
  / tools),Sandbox 可以再扩展(加 `Sandbox` / `ExecutionEnvironment`
  identifier)
- **never-raise bootstrap discipline**(在 Phase 5b 是 parse_command
  返回 None 而不是 raise;Sandbox image pull 失败应该走同样路径)

### Phase 7 polish 应该考虑的

- `markdown_store/` 共享机制层 —— 提取 YAML frontmatter parser +
  two-layer filesystem scan,Skills 和 Commands 各自实例化。**只共享机制,
  不共享 audience 层 abstraction**
- `oh commands list` / `oh skills list` subcommand —— catalog 探索 UX
- README 跟示例 commands/ + skills/ 一起 ship

### 未来 phase 普遍要警惕的

- **每加一种扩展先问:哪一 layer?** —— Layer 0(pre-LLM)/ 1(system
  prompt)/ 2(tool catalog)/ 3(execution env)。每层有不同的
  invariant
- **结构性 invariant test 比 unit test 更有用** —— "什么不该存在"是
  Phase 5+ 多 tenant 时代的核心契约,只能 structural 表达
- **重叠的机制 ≠ 应该抽象的 abstraction** —— Skills/Commands 都用
  markdown+frontmatter,但抽象层应该各管各的(§3.3)

---

## 7. Phase 5b DoD Checklist

- [x] `commands/` 5 个模块全 100 % coverage(__init__ / errors / expand
  / model / store)
- [x] 全仓覆盖率 ≥ 95 %(实际 97 %)
- [x] mypy --strict 干净
- [x] ruff check + format 干净
- [x] pre-commit hook 全过
- [x] End-to-end smoke 在 wire 层验证 slash 解析 + arg substitution
  到达 LLM 的 user message
- [x] **结构性 invariant test**:9 个 protected module + 7 个 forbidden
  identifier,word-boundary 匹配
- [x] **3 个 belt-and-braces test**:Command 不是 BaseTool / commands/
  不 import BaseTool / QueryContext 不带 command_store 字段
- [x] README 加 "Phase 5b features — Slash Commands" 章节,显式对比
  Skills vs Commands role split
- [x] `learnings/phase-5b-commands.md` 写完(本文件)

---

## 一句话

> **Phase 5b 用 ~140 行代码,第四次验证了 cross-cutting invariant —— 而且
> 结构性更强**:slash commands 是 framework 第一个 **pre-LLM 扩展**,
> 它们 vanish before LLM-facing infrastructure ever runs。
>
> Phase 5a/5c 证明"扩展不增加新 dispatch path";Phase 5b 证明"扩展不一定
> 要进入 LLM-facing 层"——**有时候最干净的扩展点是在 LLM 之前**。
>
> Pre-LLM transform 这个概念将是 Phase 7+ 多个 future feature(prompt
> history rewrite / multi-prompt batching / external templating)的共同
> 落脚点。
