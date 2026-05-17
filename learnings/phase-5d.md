# Learnings — Phase 5d (ModeBundle — the first cross-layer tenant)

> Phase 5d 起止 / 2026-05-16…2026-05-17(2 天)
> 5 capabilities (P5d-T1…T5) / ~12 sub-units / 5 commits / ~680 行
> 生产代码(bundles/ 全包)+ ~120 行 cli/commands 接线 / 全 module
> coverage 95%+;total 97.06%
>
> 本文件**不是** sub-unit 合集 —— commit message 已详尽记录。
> 它回答的题:**做完 Phase 5d,关于"layered model holds under真
> cross-cutting load"这件事的实证结果。**

---

## 1. 数据点

| 维度 | Phase 5a (MCP) | Phase 5b (Commands) | Phase 5c (Skills) | Phase 7a (substrate) | **Phase 5d (Bundle)** |
|---|---|---|---|---|---|
| 改动层数 | 1 (tools 通过 adapter) | 1 (cli 单点) | 1 (Catalog+ToolRegistry+SkillStore field) | 1 (engine context +1 field) | **4 (system_prompt + tools + permissions + hooks)** |
| 生产代码 | ~400 行 | ~300 行 | ~350 行 | ~150 行 | **~680 行** |
| Zero-diff 子系统 | engine/hooks/permissions/observability | hooks/permissions/engine/tools | hooks/permissions/engine | permissions/hooks/observability/mcp/compaction/skills/commands/protocols | **permissions/hooks/engine/observability/mcp/compaction/skills/protocols/tools/execution** |
| 触动的横切 module | tools/__init__.py(+register MCP)+ cli.py | cli.py(+expand_command call) | engine/context.py(+1 field skill_store)+ build_system_prompt(+1 kwarg)+ tools/load_skill.py(new) | engine/context.py(+1 field) + engine/query.py(+1 kwarg) + tools/base.py(+1 field) + tools/bash.py(refactor) | **commands/model.py(+1 field mode)+ commands/expand.py(+1 new function)+ cli.py(bootstrap chain + 1 except arm)** |
| Phase 总 tests added | ~30 | ~50 | ~40 | ~32 | **~74** |
| Phase 修改后总 tests | ~600 | ~700 | ~900 | 1023 | **1156 passed + 7 skipped(integration)** |

**关键观察**: Phase 5d 是**第一个同时跨 4 层**的 tenant。Phase 5a/5b/5c/6/7a/7b 每个都只
扩展一个轴 —— 5d 把 Phase 3 layered model 真的扛了一次。结果是
**permissions/ / hooks/ / engine/ / observability/ / mcp/ / compaction/ /
skills/ / protocols/ / tools/ / execution/ 全部 0 行 diff**。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P5d-T1 — `bundles/` package foundation** | `Bundle` frozen dataclass + `parse_bundle`(13 error paths,never raises)+ `FilesystemBundleStore`(global + project,project wins)。**第三次**重复 markdown+frontmatter+two-layer-store 形状(commands / skills / bundles)—— Phase 8 `markdown_store/` 抽出来重构的时机到了,但**没在 5d 做**(避免横切的 abstraction 在第 4 个 tenant 出现前先 ship)。 |
| **P5d-T2 — `BUILTIN_HOOKS` registry** | 2 个 framework-provided hook(`audit_log` + `deny_writes`)+ `resolve_hook` lookup。两个 hook 都接 `HookContext` 全 union 然后 isinstance-narrow —— 因为 `Hook = Callable[[HookContext], ...]` 是 contravariant,函数签名必须吃全 union 才能进 dict。`deny_writes` 用 `parent_query.tool_registry.get(name).is_read_only` 路径 —— P6 加的 `parent_query` field 在这里第二次被复用(第一次是 SpawnAgent)。 |
| **P5d-T3 — `WhitelistRegistry` + `apply_bundle_to_context`** | `WhitelistRegistry` 选 subclass `ToolRegistry`(不是 Protocol composition)—— 因为 `QueryContext.tool_registry: ToolRegistry` 是具体类型,Protocol-wrap 要 widen field type → engine diff → 违反 cross-layer invariant。**Subclass = 唯一保留 zero engine diff 的选项**。`apply_bundle_to_context` 是纯函数,4 个 Layer 各自独立处理 —— Layer 1 replace、Layer 2 wrap、Layer 3a augment、Layer 3b clone+append。 |
| **P5d-T4 — CLI integration + `Command.mode` field** | `resolve_command_invocation` 新 helper 返回 `(prompt, Command|None)`;`expand_command` 变成 P5b backward-compat 薄壳。Bundle 在 base ToolRegistry + base HookRegistry 都装完后 apply,但**在 `build_system_prompt` 之前** —— 这样如果有 whitelist 但没有 system_prompt override,catalog 会反映 effective registry。`UnknownBundleError` 用 `exc.kind` 同时支持 "Unknown bundle" 和 "Unknown hook" 两个 UX。 |
| **P5d-T5 — invariant + README + retro** | `TestPhase5dCrossCuttingInvariant`(46 protected modules / 12 forbidden identifiers)+ git-diff verification(10 protected dirs 全 0 行)+ README "Phase 5d" section + 本文件。**1156 passed + 7 skipped;97.06% coverage**。 |

---

## 3. Framework-level 主题 — Phase 5d 真正学到的

### 3.1 ⭐ "Layered model holds under真 cross-cutting load" —— 第一次实证

Phase 3 设计 layered model(permissions / hooks / observability /
context-passing / tool-base / engine-dispatch)的时候,押的是**未来跨层需求
能通过组合现成 primitive 满足,不用挖空任何一层**。

5a/5b/5c/6/7a/7b 每个都只扩展一个 axis,不能真正验证这个押注 —— "你只
碰一个轴,当然 zero-diff easy。等真有跨 4 轴的 case 来,我看你 layered
model 还能不能扛"。

**5d 就是那个 case**。Bundle 同时:

- 改 system_prompt(Layer 1: prompts.py)
- 过滤 tool catalog(Layer 2: tools.ToolRegistry)
- 加 deny_paths(Layer 3a: permissions.Settings)
- 注 hooks(Layer 3b: hooks.HookRegistry)

而**最终 diff 是**:

```
permissions/   0 lines
hooks/         0 lines
engine/        0 lines
observability/ 0 lines
mcp/           0 lines
compaction/    0 lines
skills/        0 lines
protocols/     0 lines
tools/         0 lines
execution/     0 lines
prompts.py     0 lines
```

加新代码全在 `bundles/`(新包)+ `commands/`(+1 字段 +1 helper)+
`cli.py`(bootstrap chain + except arm)。**Phase 3 的 layered abstraction
扛住了第一次真 cross-cutting load**。

### 3.2 "Bundle as QueryContext factory" —— 组合的具体形状

Bundle 本身**没有任何运行时角色** —— 它纯粹是个 metadata bag。运行时
依然只有 QueryContext 一个东西。Bundle 的工作是在 CLI bootstrap 时把
metadata 翻译成 4 个 QueryContext primitive 的修改。

```
Bundle (metadata)                 QueryContext (运行时)
─────────────────────             ─────────────────────
system_prompt: str   ─────────►   system_prompt: str
tools_whitelist:     ─────────►   tool_registry: ToolRegistry
  tuple[str, ...]                   (wrapped in WhitelistRegistry)
deny_paths:          ─────────►   permission_checker:
  tuple[str, ...]                   TierBasedPermissionChecker
                                    (reads effective Settings.deny_paths)
hook_names:          ─────────►   hook_registry: HookRegistry
  tuple[str, ...]                   (cloned + registered named hooks)
```

`apply_bundle_to_context` 是这个翻译的核心。它**不 import engine**,不
import permissions,不 import hooks executor —— 只 import 这些层暴露的
**构造 primitive**(`HookRegistry`、`ToolRegistry`、`Settings`)和它们
要返回给的 `WhitelistRegistry`。

这是"组合"在 Python 里能拿到的最干净形态:**bundle 不是新层,是把
metadata 翻成现有层的现有 entry point**。

### 3.3 Subclass vs composition —— invariant 决定形状

T3 一开始我想用 Protocol + composition 包 ToolRegistry,因为「composition over
inheritance」是熟知 best practice。但 `QueryContext.tool_registry: ToolRegistry`
是 concrete-typed —— 用 Protocol 必须 widen 这个 field type 到
`SupportsToolRegistry` Protocol → **engine diff** → 违反 zero-diff 约束。

→ **Subclass 是被 invariant 选出来的**,不是 best practice 选出来的。这是
framework-level 决策的一个典型 pattern:不是先选实现技巧,是先看约束允许
什么。Subclass 在这里:

- `isinstance(wrap, ToolRegistry) is True` ✓ engine 不知道
- `super().__init__()` 创建空 `_tools` dict,我们 override 全部 accessor
- `register()` 抛 RuntimeError(wrapper immutable post-ctor)—— 早暴露
  caller bug

「composition over inheritance」是从最大化灵活性出发的;但当**inheritance
是唯一保留 invariant 的方式**时,inheritance 就是对的。

### 3.4 Built-in hooks vs plugin discovery —— 节奏决策

5d boundary 一开始 D19.4 推荐 built-in only,defer plugin discovery 到 5e。
我接受了。事后看正确,理由两条:

1. **真用户需求还没浮现**。没人写过第三方 hook —— `audit_log` 和
   `deny_writes` 是先验合理的(read-only mode + compliance trace 是面
   试常说),但任何"用户 hook 长什么样"的问题都是猜。MVP ship 2 个
   built-in、用户用过、提需求,5e 再加 plugin discovery 才是 informed
   design。
2. **避免 5d 同时做 hook 抽象 + 跨层组合两个新东西**。一次只做一个
   新东西的法则在这里又一次应用:5d 已经是"第一次跨 4 层",再叠 plugin
   discovery 就是同时验证 2 个未验证的东西 —— bug 难定位。

`BUILTIN_HOOKS: dict[str, tuple[HookEvent, Hook]]` 这个 dict shape 留了
plugin extension 的余地:5e 加 `load_hook_plugins() -> dict[str, ...]`
然后跟 BUILTIN_HOOKS 合并就行。

### 3.5 第三次重复 markdown+frontmatter+store —— "rule of three" 触发

`Bundle` / `Skill` / `Command` 三个 dataclass 都:

- `_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")`(完全相同)
- `_FRONTMATTER_FENCE = "---"`(完全相同)
- `_split_frontmatter` 函数(几乎相同)
- `parse_<X>(path) -> X | None` 形状(完全相同)+ ~13 个 error path
- `FilesystemXStore` global+project 两层 + project wins(完全相同)
- `EmptyXStore` sentinel(完全相同)

**应该重构**。第三次出现就是「rule of three」触发点 —— `markdown_store/`
模块出来后,三个 type 都剩下 ~30 行(dataclass + 一个 `parse_extra(parsed,
path)` 钩子函数)。但**没在 5d 做**:

- 5d 已经做的是「第一次 cross-cutting tenant」,不想同时叠 「refactor
  4th time 抽公共模块」—— 一次只做一个新东西。
- 决策 doc 17 §3.5 明确说了这点(同 5b retro 同 5c retro 同步)
- Phase 8 计划做(README 已经标了 markdown_store/ 候选)

### 3.6 `deny_writes` 的「passthrough on missing tool」—— defensive 的边界

`deny_writes` 在 `parent_query.tool_registry.get(name)` 抛 KeyError 时
**不抛 deny,而是 passthrough**。理由:

- 如果 tool 真的没 register(用户 typo),engine 自己会抛 "tool not
  found" error —— 我们 deny 会**遮盖 engine 的真错**
- defensive code 的原则是**不主动制造新错,只 augment 已有错**

这种「边界处选 passthrough」的判断是 framework-builder 的 sensibility:
**hook 是 augment,不是 replace**。如果 hook 替 engine 做 error
reporting,trace 就乱了。

### 3.7 「YAML `|` block scalar 尾巴一个 `\n`」—— 文档/测试 vs 实现

T4 第一次跑 `test_mode_field_applies_all_four_layers` 挂了:

```
expected: "You are a code reviewer. Read-only."
actual:   "You are a code reviewer. Read-only.\n"
```

是 YAML `|` block scalar literal 保留了 trailing newline,完全符合 spec。
修法:`ctx.system_prompt.rstrip() == expected`。这种 trivial 现象在
framework-level 提醒一件事:**用户写 bundle YAML 的时候,会无意中带入
YAML 自己的语法痕迹**(尾 newline、缩进 sensitivity、`>` vs `|`、`null`
vs `~`)—— 我们的 system_prompt 处理路径必须 robust 到这种 noise。当
前实现「逐字 use」是最不 surprising 的策略;但需要 doc 提醒用户。

---

## 4. 5d 没做的(以及为什么)

| 不做 | 理由 |
|---|---|
| Plugin hook discovery | 5e。MVP 用户需求未浮现。 |
| `markdown_store/` 抽公共模块 | Phase 8。一次只做一个新东西。 |
| `tools.blacklist`(对偶 whitelist) | 用户需求未浮现;whitelist 形态更安全(deny-by-default)。 |
| Bundle inheritance(`extends: other-bundle`) | 真用例没出现前先 ship simple model。复杂度 vs 收益不平衡。 |
| Mid-conversation bundle 切换(`/switch-mode review`) | `oh chat` 模式才有意义;`oh ask` 单次 invocation 不需要。 |
| 真 LLM smoke 跑通 4 层 bundle | 需要 API key + 实际花钱;captured-context 测试已经验证所有 4 层 wire 正确。手动 smoke 留给用户自己跑 `oh ask "/review last commit"`(写好 sample bundle 后)。 |

---

## 5. 给下一阶段的人(可能还是我)

- **markdown_store/ 抽公共模块**是 Phase 8 第一个候选。看 5b 5c 5d
  三个 parse_X + FilesystemXStore + EmptyXStore — 几乎逐字相同。
  抽完每个 type 剩 ~30 行,可读性 + 测试覆盖都升一格。
- **Plugin hook discovery (5e)**:`BUILTIN_HOOKS` 是 source of truth,
  plugin 通过 entry-point group `openharness.hooks` 注册 → load 时和
  BUILTIN_HOOKS 合并。collision 处理:plugin 名字 collision base ⇒
  warning + skip(同 store override 哲学反过来 —— framework 比 user
  优先,因为 framework 跑得稳)。
- **`audit_log` 加 `tool_input` 字段**是一个 trivial-but-高价值 follow-up
  —— compliance trace 真正想要的是「user/agent 试图做什么」不只是
  「调用了哪个 tool」。但是有 secret-sanitization 的顾虑(参数里
  可能塞 API key)—— 上 sanitize 之前不加。
- **Bundle 真的能换 SkillStore 吗?** 现在 bundle 不动 skill_store。
  如果想要 "review bundle skip skill catalog",需要再加一层。Phase 5e
  /6 可考虑;但场景不明先不动。
- **`deny_writes` 跑 in dispatch / 跑 in 决策 catalog?** 现在 `deny_writes`
  只在 dispatch 时 PreToolUse 阻拦,但 LLM 看到的 catalog 里 Write 还
  在(only `tools.whitelist` 过滤 catalog)。即:用户写 `hooks:
  [deny_writes]` 但没写 `tools.whitelist`,LLM 会看到 Write,试着调用,
  被 dispatch deny。**这是 by design**(用户两个 axis 独立配),但
  可能 surprise。给用户的建议(在 README 一段补一下):**read-only
  模式建议同时配 whitelist + deny_writes**(double layer = belt and
  braces)。

---

> **本 Phase 一句话总结**:
>
> Phase 3 layered model 押的是"将来跨层需求能纯组合解决"。
> Phase 5a/5b/5c/6/7a/7b 每个都只测一个轴。
> **Phase 5d 跨 4 轴同时做,permissions/hooks/engine/.. 全部 0 行 diff** —
> 押注兑现。
