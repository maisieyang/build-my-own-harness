# Phase 18 Implementation Plan — Slash-Skill Triggering (M1 of CC Skill 接入)

> Boundary contract: [`decisions/38-phase-18-boundary.md`](../decisions/38-phase-18-boundary.md).
> finance-skills 接入"以始为终"路径分析：本仓 conversation 2026-06-06。

## Overview

**Phase 18 goal**：在 `oh chat` REPL 里让 `/<skill-name> [args]` 像 Claude
Code 一样触发对应 skill。完成后用户能 `cp` 一个 CC `SKILL.md` 到
`~/.openharness/skills/` 然后立即用 slash 调起来——验证 G2 整条触发
回路通畅。M2 (CCPluginLoader / G1) 和 M3 (DeclarativeAgent / G3) 留给
后续 phase。

**Cross-cutting invariant** (per D38 §六 Wiring audit)：

- `permissions/` — 零 diff（bypass 是 deliberate 且 contained 在
  slash_skill 路径，不触碰 tier_based 代码）
- `hooks/` — 零 diff（同上）
- `services/snapshot.py` + `services/session_memory.py` — 零 diff
- `services/compact.py` — 零 diff（T3 显式 verify 透明兼容）
- `prompts/` — 零 diff（skill catalog 注入 system_prompt 路径不动）
- `protocols/` — 零 diff（synth envelope 复用既有 ConversationMessage /
  ToolUseBlock / ToolResultBlock）
- `tools/load_skill.py` — 零 diff（LLM 自决路径完整保留；synth 路径
  bypass 而非替换）
- `tools/` 其他 — 零 diff
- `skills/store.py` + `skills/model.py` — 零 diff（discovery 逻辑不变；
  D38.7 CC SKILL.md 目录形态留 M2）
- `commands/expand.py` + `commands/store.py` — 零 diff（Phase 5b 路径完整
  保留，仍是 resolver 第 2 优先）
- `bundles/` — 零 diff（mode 触发路径不动；synth envelope 不带 mode）
- `memory/` — 零 diff
- `config/settings.py` — 零 diff（slash skill 是 always-on，没有 settings
  flag）
- `engine/query.py` — 零 diff（synth envelope 在 history 里看起来跟真
  tool_use/tool_result 完全一致）
- `cli.py` — **extension only**，仅 `_run_chat` 的 REPL resolver 块

Expected net diff：约 **+150 LoC**（新 `engine/slash_skill.py` ~50；
REPL 扩展 ~30；observability event ~5；tests ~70）。

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/38-phase-18-boundary.md`](../decisions/38-phase-18-boundary.md) | D38.1 resolver 顺序 (built-in → CommandStore → SkillStore → Unknown); D38.2 合成形态 (assistant tool_use + user tool_result + user args 三条 synth 消息); D38.3 args 尾部 user message 不 substitute; D38.4 `/skills` 内置命令; D38.5 hooks + permissions deliberate bypass + observability 兜底; D38.6 `oh ask` M1 不支持 (chat-only); D38.7 SkillStore 目录约定 M1 不变 (单 `<name>.md`) |
| [`decisions/12`](../decisions/12) | Phase 5c L4 invariant ("skill body 进 tool_result，不进 system_prompt")——T1 的合成形态必须保此契约 |
| [`decisions/14`](../decisions/14) | Phase 5b slash command dispatch 契约——T2 在其上扩展第 2 优先级 resolver，原路径不动 |

---

## Task list

### P18-T1: Synth envelope helper — `engine/slash_skill.py`

**Description**: 新建一个**纯函数**模块负责合成 D38.2 的三消息信封。
模块**不**导入 `tools.load_skill` / `permissions` / `hooks` / `observability`
任何一个——保架构隔离 + 测试简单。signature 大致：

```python
def synthesize_skill_envelope(
    skill: Skill,
    args: str,
    *,
    tool_use_id_factory: Callable[[], str] = _default_synth_id,
) -> list[ConversationMessage]:
    ...
```

返回 2 条（args 空）或 3 条（args 非空）`ConversationMessage`。ID 工厂
注入便于测试；默认实现 `"synth_" + uuid7()[:12]`。

**Acceptance**:

- [ ] `engine/slash_skill.py` 文件存在，提供 `synthesize_skill_envelope`
  公共函数 + 默认 ID 工厂
- [ ] 三消息 schema 严格匹配 D38.2：
  - msg[0] role=assistant，content=[ToolUseBlock(name="LoadSkill", input={"name": skill.name}, id=<synth_id>)]
  - msg[1] role=user，content=[ToolResultBlock(tool_use_id=<同一 synth_id>, content=skill.body)]
  - msg[2] role=user，content=[TextBlock(text=args)] —— 仅 args.strip() 非空时存在
- [ ] tool_use_id 前缀严格 `synth_`（便于审计 + snapshot 识别）
- [ ] 模块 import 表里**不**出现：`tools.load_skill`, `permissions`,
  `hooks`, `observability`, `cli`（架构隔离 grep 验证）
- [ ] `skill.body` 在 ToolResultBlock 里**原文不截断、不替换占位符、不
  做 `{args}` substitution**（per D38.3）
- [ ] 单测覆盖：args 空 → 2 条；args 非空 → 3 条；同 skill 调两次拿到
  不同 synth_id（uuid factory 工作正常）；ID 注入测试用 `lambda: "synth_test"`
- [ ] 单测覆盖：skill.body 含 markdown 特殊字符 / 多行 / 包含
  `{args}` 字面量 —— 都原样进 tool_result（不被任何模板引擎处理）

### P18-T2: REPL resolver 扩展 + `/skills` 内置 — `cli.py` `_run_chat`

**Description**: 在 `_run_chat` 现有的 `/<input>` 处理块（约 cli.py:1215
开始）扩展 resolver 顺序为 D38.1 的 4 步：built-in → CommandStore →
SkillStore → UnknownCommandError。新增 `/skills` 内置命令位置跟 `/help`
平行。SkillStore 命中时调 T1 的 `synthesize_skill_envelope` 把信封追加
到 `history`，触发 `slash_skill_invoked` INFO 事件，**不**走任何 hook /
permission 路径，继续进 LLM round。Unknown 路径加 "did you mean a
skill?" 提示——拿 SkillStore.discover() 跟 input name 做 difflib 近似
匹配，建议最近的 3 个名字。

**Acceptance**:

- [ ] `oh chat` REPL 处理 `/<name>` 时严格按 D38.1 四级 fallback 路由
- [ ] CommandStore 命中走 Phase 5b `resolve_command_invocation`（既有
  路径完全不动；Phase 5d bundle 触发逻辑保留）
- [ ] SkillStore 命中：
  - 调 T1 helper 合成信封
  - `history.extend(envelope)` 把信封塞进对话
  - emit INFO 事件 `slash_skill_invoked`，payload 字段 `skill_name`,
    `args_length`, `synthetic=true`
  - **不**调 `LoadSkillTool.execute`、**不**走 `PermissionChecker.check`、
    **不**触发 `PreToolUseHook` / `PostToolUseHook`
  - 继续 LLM round（envelope 已含 args，LLM 直接对 envelope 作答）
- [ ] `/skills` 内置命令位置跟 `/help` 平行（cli.py:1215 邻近 if 链
  里加一个分支），输出：
  - 非空：每 skill 一行 `<name>  <description>`（name 左对齐 padding 到
    最长 name 长度），字母序
  - 空：`(no skills installed)`
- [ ] `/help` 输出加 `/skills  show available skills` 一行
- [ ] Unknown `/<name>`：先确认不是 built-in，CommandStore + SkillStore
  都查不到 → typer.echo 形如 `Unknown command: foo`，紧接一行
  `Did you mean a skill? Closest: <name1>, <name2>` （SkillStore 非空且
  difflib 找到 ≥1 match 时）
- [ ] **同名 collision 测试**: 同时存在 `commands/review.md` +
  `skills/review.md` → `/review` 命中 Command（D38.1 顺序）
- [ ] **resolver 单元测试** 覆盖 4 条路径 (built-in / cmd / skill /
  unknown) + collision + `/skills` 输出 + Unknown 的 difflib 建议
- [ ] **整合测试** mock LLM client：发 `/parse-credit-report 申请12345`
  → LLM 收到的 history 含合成三消息 + LLM 的回答覆盖 skill body 关键
  指导文本
- [ ] cli.py 改动仅在 `_run_chat` 内部，外部 typer signature 不变
  （`oh chat` flags 完全不增删）

### P18-T3: Compaction L1-L4 透明兼容性 verification

**Description**: D38 §六 wiring audit 把 compaction 标为 `requires
verification`——synth tool_result 必须能被 L1 truncation / L2 删 reasoning /
L3 老对话丢弃 / L4 LLM summarize 像真实 tool_result 一样处理，**不**需要
在 `services/compact.py` 加 `if id.startswith("synth_")` 特判（特判等于
把 synth 概念泄漏到不该知道它的层，违反 D38 §六 closing 规则）。本任务
显式验证 + 留 regression test。

**Acceptance**:

- [ ] **L1 verification test**：构造一个 skill.body 触发 L1 truncate
  threshold（用 `Skill(body="X" * 100_000)` 之类），通过 T1 helper 合成
  envelope 后 `TruncateToolResultHook` 应用——但 D38.5 已 mandate slash
  路径 bypass hook，所以**期望 L1 看到的是完整 body**。Test 同时验证
  hook bypass + L1 透明
- [ ] **L2 verification test**：synth envelope 经过 L2（删 reasoning 块）
  → 跟真实 tool_result envelope 行为一致（reasoning 块本来就不会出现
  在 synth 里，所以等价于 noop——确认 noop）
- [ ] **L3 verification test**：将 synth envelope 放在 conversation 头部
  + 之后塞够触发 L3 截断的多轮对话 → L3 截断行为对 synth tool_use /
  tool_result 对**保持成对完整**（不能只截 tool_use 留 tool_result，
  或反之；Phase 11 compact 已有此 invariant，确认 synth ID 没破坏 pair
  识别逻辑）
- [ ] **L4 verification test**：mock LLM client，conversation 含 synth
  envelope 触发 L4 LLM-based compact → summary 生成正常（LLM 把 synth
  tool_result 当 expert guidance 引用）；no exception；history 替换后
  仍可继续 turn
- [ ] **services/compact.py 零 diff** verification：`git diff
  src/openharness/services/compact.py` 在本 phase 结束时为空。如果发现
  L1-L4 需要修才能透明，**立刻回头改 D38**（加 D38.8）不允许直接打
  patch
- [ ] 新增 test file `tests/services/test_compact_synth_envelope.py`
  收纳上述 4 个 verification test

### P18-T4: Dogfood — finance-skills `parse-credit-report` 端到端

**Description**: 把 finance-skills 仓 mybank 那条 skill 的 SKILL.md 手
工 cp 到 OH skill dir（D38.7 M1 不识别 CC `SKILL.md` 目录形态，要 cp
+ 改名 + 单文件），然后 `oh chat` 跑 `/parse-credit-report` 验证整条
回路。这是 G2 verification 的 forcing function——不通过 dogfood，T1-T3
单测全绿也不能算 Phase 18 done。

**Acceptance**:

- [ ] 准备步骤可重现：

  ```bash
  cp /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-report-reviewer/skills/parse-credit-report/SKILL.md \
     ~/.openharness/skills/parse-credit-report.md
  ```

- [ ] `oh chat` 启动后 `/skills` 输出包含 `parse-credit-report  解析央行
  个人征信报告（第二代征信系统）...` （description 前缀字面匹配）
- [ ] `/parse-credit-report` 不带 args 触发：
  - LLM 回应反映 skill body 内的"央行报告 7 大模块" / "数据来源严格约束" /
    "授权号验证" 等具体内容
  - 这些点至少 3/5 出现在 LLM 输出里——证明 body 真的进 LLM context
- [ ] `/parse-credit-report 申请号12345` 带 args 触发：
  - history 末尾的 user message 含字面 "申请号12345"（snapshot 验证）
  - LLM 在引用 skill 指导的同时把 "申请号12345" 当 task subject 处理
- [ ] 启动 log 无 warning（特别是 `skill_validation_failed` /
  `skill_missing_description` —— CC SKILL.md 的 `description` 是
  multi-line 形态，确认 Phase 5c parser 接受多行 description）
- [ ] observability log 包含 `slash_skill_invoked` INFO 事件 1 次，payload
  `skill_name=parse-credit-report`, `args_length=10`（"申请号12345" 是 10
  char）, `synthetic=true`
- [ ] **negative dogfood**：把同名 skill 的 SKILL.md 改名成
  `parse-credit-report.md` 后**多写一个**同名 command
  `~/.openharness/commands/parse-credit-report.md`，`/parse-credit-report`
  命中 command 而非 skill（D38.1 collision 优先级现场验证）
- [ ] dogfood 步骤 + 输出关键证据（slash_skill_invoked event payload +
  LLM 回答的 3/5 关键点列表）记录到 `learnings/phase-18.md` retro §1

### P18-T5: CHANGELOG + Phase 18 retro

**Description**: T1-T4 全部 land + dogfood pass 后写 `learnings/phase-18.md`
retro。CHANGELOG 加 Phase 18 entry 链回本 plan 和 D38。retro 重点回答：
(1) D38.2 合成形态（synth tool_use 信封）是不是后续 M2 / M3 的稳定地
基？(2) D38.5 hook + permission bypass 在 dogfood 时有没有暴露任何
hook 作者的 surprise？(3) D38 §六 wiring audit 的 4 个 verdict（2
bypass + 1 verification + 1 extension）是否如预测落地，有没有出现新的
"requires bypass" 层？

**Acceptance**:

- [ ] `CHANGELOG.md` 加 Phase 18 entry：日期 / 标题 / 1-2 句 summary /
  链回 D38 + 本 plan + dogfood 报告位置
- [ ] `learnings/phase-18.md` 按 §1 What worked / §2 What missed / §3
  Predictions for M2 / §4 Abstractions tested 四段结构写
- [ ] §1 What worked 含 T4 dogfood 关键证据 (slash_skill_invoked event +
  LLM 输出 3/5 关键点)
- [ ] §3 Predictions 至少列出：M2 (CCPluginLoader) 接 SKILL.md 目录形态
  时，T1 的 synth envelope helper 应该零改动直接复用；M3
  (DeclarativeAgent) 触发路径很可能是 synth envelope 的 sub-agent 变种
  （`tool_use(Agent)` 代替 `tool_use(LoadSkill)`）
- [ ] §4 Abstractions tested 含一条："UI action vs LLM action 区分"
  在 D38.5 用作 first-principles，dogfood 时是否产生预期外的 hook
  作者困惑（若无 = 区分有效）
- [ ] retro 实测 LoC delta 与 boundary doc 预测 (+150 LoC) 的偏差记录
- [ ] retro §六 verdict 对照：4 个预测层 verdict 是否如期，有否新层
  surfaced

---

## Open frontier (deferred past Phase 18)

按 D38 §一 "不在 phase 范围" + 本 phase 实施中新发现项：

1. **M2 (Phase 19?)**: CCPluginLoader — 读 `.claude-plugin/plugin.json` +
   `skills/<n>/SKILL.md` 目录形态 + `.mcp.json`，把 CC plugin 翻译成
   OH `PluginManifest`。预测复用 T1 synth envelope helper 不需要改
2. **M3 (Phase 20?)**: DeclarativeAgent — `agents/<n>.md` 含 tools 白
   名单的声明式 sub-agent。需要新 §六 wiring audit 决定 `tools` 白名单
   跟 OH permission tier 怎么映射；触发路径预测是 synth envelope 的
   `tool_use(Agent)` 变种
3. **`oh ask` 支持 `/<skill>`**: D38.6 deferred。需要先 ratify "ask
   单 turn 怎么折叠 synth 三消息"——可能等 M2 后一起做
4. **`{args}` substitution into skill body**: D38.3 deferred。等真有
   skill 写出 argument-shaped body 再加；本 phase 不预先优化
5. **`/commands` introspection**: D38.4 对称项，未单独价值 phase；可
   日后任何 phase 顺手补
6. **CC `SKILL.md` 目录形态扫描**: D38.7 deferred 到 M2 整体处理
7. **CC marketplace.json**: 多 plugin 聚合元数据；M2 时一并决定要不要
   引入"marketplace"概念到 OH，还是直接 fan-out 成多 plugin
8. **Per-user / per-skill access control**: D38.5 衍生 note——位置在
   `SkillStore.get`（更早），不在 tool execution 层。M1 不需要，但日后
   加时位置预留

这些 deferred 项待真有 driver 时各自独立 phase 处理。Phase 18 收尾
应该产生一个干净 baseline：synth envelope helper + REPL fallback
作为后续 M2 / M3 的稳定地基。
