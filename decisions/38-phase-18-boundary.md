# Decision 38 — Phase 18 Boundary (Slash-Skill Triggering · M1 of CC Skill 接入)

> Created 2026-06-06 · 中文
>
> 配套读物：
> - 起源（finance-skills 接入"以始为终"路径分析）：本仓 conversation
>   2026-06-06 早段
> - 上一 phase 的 boundary：[`37-phase-17-boundary.md`](./37-phase-17-boundary.md)
> - 上游契约：Phase 5c 决策（`decisions/12`，特别 L4 invariant）
>   + Phase 5b 决策（`decisions/14`，slash-command dispatch）

---

## 〇、Why this doc

Phase 17 收尾了 memory substrate 并把 §六 Wiring audit 写进
methodology。现在转向**外部 skill 接入**——目标是在 `oh chat` 里
能像 Claude Code 一样直接敲 `/spec /ideate /build` 触发对应 skill。

调研的 finance-skills 仓（CC 格式）和 OH 当前实现存在 3 个 gap：

- **G1**：CC plugin 目录约定（`.claude-plugin/plugin.json` + `skills/<n>/SKILL.md` + `agents/<n>.md`）vs OH `manifest.yaml` 单文件声明
- **G2**：CC `/<skill-name>` 直接触发 skill；OH `/<name>` 只查 CommandStore，skill 必须由 LLM 自决调 `LoadSkill` tool
- **G3**：CC `agents/<name>.md` 声明式 sub-agent（含 tools 白名单）；OH `spawn_agent` 是 LLM 运行时自填 `initial_query`/`system_prompt`

**Phase 18 = M1 = 单解 G2**。M2 (CCPluginLoader) 和 M3
(DeclarativeAgent) 各自独立 phase 处理。

M1 是验证回路的最小单点——用户能在 `oh chat` 里敲 `/parse-credit-report`
看到 skill body 真的被 LLM 调用，整条 CC 体验就跑通了一半。不通的话
后续 plugin loader / declarative agent 都是空中楼阁。

工作量预估 80-120 LoC + tests + 0.5 day calendar。

---

## 一、Capability scope

**新增能力**：

- `oh chat` REPL 内 `/<skill-name> [args]` 触发对应 skill，沿用 Phase 5c
  既有的 `LoadSkill` tool 路径（不发明新触发机制）
- `oh chat` REPL 内 `/skills` 内置命令列出当前可用 skill catalog
  （dogfood validation 入口）

**影响文件**：

- `cli.py` `_run_chat` 函数 resolver 分支（~30 LoC 新增）
- 新文件 `engine/slash_skill.py` 或 `services/slash_skill.py`
  （合成 tool_use+tool_result 信封的 helper，~50 LoC）
- `observability/logging.py` 注册 1 个新 INFO 事件 `slash_skill_invoked`
- 测试：`tests/cli/test_chat_slash_skill.py`（新文件）+
  `tests/engine/test_slash_skill_envelope.py`（新文件）

**保留**：

- Phase 5b CommandStore + `resolve_command_invocation`（D24.3 dispatch）
  完全不动
- Phase 5c `LoadSkillTool` + `SkillStore` + skill catalog 注入
  system_prompt（D14.4-D14.6）完全不动
- Phase 5d Bundle 触发（`Command.mode` 字段）完全不动
- LLM 自决调 `LoadSkill(name=...)` 的路径完全不动——M1 是**新增**触发
  入口，不替换旧入口

**不在 phase 范围**（M2/M3 各自 phase）：

- CC plugin 目录约定（`.claude-plugin/`、`SKILL.md` 目录形态）的 loader
- CC `agents/<n>.md` declarative sub-agent
- CC `.mcp.json` plugin-内嵌 MCP 配置
- CC marketplace.json
- `oh ask` 单次执行支持 `/<skill>`（M1 仅 chat-only，见 D38.6）

---

## 二、决策 D38.1-D38.7

### D38.1 — Resolver 加 SkillStore fallback（不替换 CommandStore）

**Chosen**：`oh chat` REPL 处理 `/`-prefix 输入的顺序：

```
1. built-in (/clear /compact /help /exit /quit /skills)
2. CommandStore.get(name) → 命中走 Phase 5b expand_command 路径
3. SkillStore.get(name)   → 命中走 D38.3 合成信封路径
4. UnknownCommandError    → 提示 "did you mean a skill?" + 列出近似名
```

**Rationale**：
- Command-first 保持 Phase 5b 既有契约不变——用户自写的 command 是
  harness DSL，优先级高于 skill 是合理预期
- Skill fallback 让 CC 体验（`/spec` 直接调用 skill）零迁移成本
- 4 步链路里每一步都是已有概念，不引入新抽象

**Alternatives 不选**：
- (a) Skill-first：违反 Phase 5b 用户对 Command 的优先权预期；同名时
  silently shadow
- (c) Namespace 前缀 `/skill:name`：破 CC 的 `/spec /ideate /build`
  零负担 UX 承诺

### D38.2 — 合成形态：tool_use(LoadSkill) + tool_result(body) 信封

**Chosen**：`/<skill-name> args` 命中 SkillStore 时，往 history 追加
**三条消息**：

```python
[assistant] content=[ToolUseBlock(name="LoadSkill",
                                   input={"name": "<skill-name>"},
                                   id="synth_<uuid7>")]
[user]      content=[ToolResultBlock(tool_use_id="synth_<uuid7>",
                                     content=<skill.body>)]
[user]      content=[TextBlock(text=<args>)]   # 若 args 非空
```

ID 用 `synth_` 前缀以便 observability / snapshot 后期识别"非真实 tool
执行产生的 envelope"。

**Rationale**：
- **保 Phase 5c L4 invariant**：skill body 在 messages[] 作 tool_result
  存在，不进 system_prompt
- 对 LLM 完全透明——看到的是标准 tool_use/tool_result 对，跟 LLM 自决
  调 LoadSkill 时见到的字节结构一致
- compaction L1-L4 + snapshot 不需特判：信封跟真实 tool_use 一样的
  schema
- 用户的 args 作为后续 user message——保留 CC "now apply to: <args>"
  的语义

**Alternatives 不选**（用户已 ratify）：
- 单条 user 消息 inline body：破 L4 invariant
- system_prompt 拼接：跟 Phase 5d Bundle 的 `system_prompt` override
  路径冲突；compact L4 不保留

### D38.3 — Args 放在 user message 尾部，不 substitute 进 skill body

**Chosen**：

- args 非空 → 追加一条 user TextBlock，内容 = args 原文
- args 为空 → 不追加 user message；信封停在 tool_result，等 LLM 自主
  对 skill body 作出反应

**Rationale**：
- CC 的 `SKILL.md` 格式**没有 `{args}` 占位符**（看 mybank 那批
  SKILL.md 验证）——substitute 进 body 需要扩展 schema 才合理，
  M1 不动 schema
- 尾部追加方案 = 通用 fallback，与 LLM 自决调 LoadSkill 后用户继续
  发消息的体验**完全对齐**
- 用户日后若想要 args 替换进 body 的语义，可以在 SKILL.md 加 `{args}`
  并在 M2/M3 引入支持——不破 M1 接口

**Anti-scope**：M1 **不** 实现 `{args}` substitution；**不** 把 args
塞进信封的 tool_result（保 tool_result == skill.body 原文）。

### D38.4 — `/skills` 内置命令：列出 catalog（dogfood 入口）

**Chosen**：`oh chat` REPL 加一个 built-in `/skills`：

- 输出格式：`<name>  <description>` 每 skill 一行，按字母序
- 来源：`SkillStore.discover()`（Phase 5c 既有 API）
- 空 catalog → "(no skills installed)"
- 跟 `/help` `/clear` 一样的处理位置（cli.py:1215 附近的 if 分支）

**Rationale**：
- M1 dogfood 验证必需——用户 `cp` 一个 SKILL.md 到 skill 目录后，
  需要 1 个命令确认"它确实被发现了"
- ~5 LoC，零新依赖，零新抽象
- 跟 D38.1 resolver 解耦——`/skills` 是 built-in，先于 CommandStore
  / SkillStore lookup

**Anti-scope**：本 phase **不**加 `/commands`（虽然对称地合理，但
M1 scope 严控；可后续 phase 顺手补）；**不**加 `/skills <name>` show
形式（用户走 `cat ~/.openharness/skills/<name>.md` 即可）。

### D38.5 — Hooks / permissions 对合成信封**不触发**（deliberate bypass）

**Chosen**：合成的 tool_use(LoadSkill) **不**经过：

- `PreToolUseHook` chain
- `PermissionChecker.check`（Tier 1/2/3 evaluation）
- `PostToolUseHook` chain（包括 `TruncateToolResultHook`）
- Tool execution 实际路径（不调 `LoadSkillTool.execute`）

合成 helper 直接从 `SkillStore.get(name)` 拿 `Skill.body` 塞进
`ToolResultBlock.content`。

**Rationale**（first-principles）：

> **Synth envelope 是 UI action，不是 LLM action。Hooks 和 permissions
> 是 gate LLM action 的机制。**

- 用户敲 `/parse-credit-report` —— 是**人在键盘上明确表达意图**的
  瞬间，跟 `oh chat` 里直接 paste 一段长 prompt 是同一类操作
- LLM 自决调 `LoadSkill(name=...)` —— 是 LLM **可能误用**的瞬间，hook
  拦截、permission gate、TruncateToolResultHook 截断都是为这个场景
  设计的
- 这两类事件 share schema（都是 tool_use/tool_result block）但**不
  share semantics**。把 UI action 强制走 LLM action 的 gate 是**概念
  错配**

**衍生收益**：

- TruncateToolResultHook 不会偷偷截掉用户的 skill body —— footgun
  消除（用户对自己写的 expert guidance 长度有自主权，harness 不偷剪）
- `/skill` 即时生效，不被 hook chain 二次 confirm
- 审计需求由 `slash_skill_invoked` INFO 事件单独承担 —— hook 作者写
  `pre_tool_use{name=LoadSkill}` 拦截 LLM 自决，订阅
  `slash_skill_invoked` 监控 UI 调用。**两类事件 = 两类语义，不是噪声
  也不是 duplicate**
- LDAP / per-user skill access 控制：应该在 `SkillStore.get` 层（更早，
  也是发现阶段）实现，不是 tool execution 层。M1 不需要，但日后加
  时位置已经预留好

**Alternatives 不选**：
- 走完整 tool 执行路径：合成 envelope 的初心是"零侧效注入"——走完整
  路径等于把 tool execution machinery 扩成支持 synth call，复杂度
  和 M1 的最小验证回路完全不匹配
- 仅触发 PostToolUseHook：不对称——既然 bypass，就一致 bypass

**Forcing function**：observability INFO 事件 `slash_skill_invoked`
**必须**显式声明这是 synthetic envelope（payload 字段 `synthetic: true`），
便于日后审计"为什么 PreToolUse 没看到这次 LoadSkill"。

### D38.6 — `oh ask` 单次执行**不**支持 `/<skill>`

**Chosen**：M1 范围严控在 `oh chat` REPL。`oh ask "/parse-credit-report"`
仍按原 ask 逻辑直接发给 LLM（按字面 user prompt 处理），**不**做 slash
解析。

**Rationale**：
- M1 的目标是验证回路，chat 已经足够 dogfood
- `oh ask` 是 single-turn 路径——`/skill` 触发 = synth 信封注入
  history = 至少 2 turn（synth + real user input）——跟 ask 单 turn
  契约冲突
- ask 支持 slash 需要单独 ratify "如何把信封 + args 折叠成单 turn"
  这个 sub-question，放 M2/M3 一起讨论

**Anti-scope**：本 phase **不**改 `_run_ask`；**不**改 `ask` 子命令
flags。

### D38.7 — SkillStore 目录约定 M1 内**不**变（仍单 .md 文件）

**Chosen**：M1 期间 `~/.openharness/skills/` 和 `.openharness/skills/`
仍按 Phase 5c 既有约定 = **单 `<name>.md` 文件**。CC 的
`skills/<n>/SKILL.md` 目录形态**不被 M1 识别**——用户接 finance-skills
仓需要手动 cp + 改名 + 提取 body（或等 M2 的 CCPluginLoader）。

**Rationale**：
- M1 scope = G2 only；G1 (plugin / SKILL.md 目录约定 loader) 留给 M2
- 改 FilesystemSkillStore 的 scan 逻辑算 M2 工作
- M1 期间用户 dogfood path：

  ```bash
  cp finance-skills/mybank-credit-risk/plugins/credit-report-reviewer/skills/parse-credit-report/SKILL.md \
     ~/.openharness/skills/parse-credit-report.md
  ```

  这条 cp 命令的存在本身就 forces M2 的 priority——验证完 G2 后立即
  做 G1 才是产品级体验

**Anti-scope**：本 phase **不**改 `FilesystemSkillStore.discover()` 的
扫描 glob；**不**支持 `SKILL.md` 文件名；**不**支持目录形态 skill。

---

## 三、Acceptance criteria

Phase 18 GA 需要满足：

### 3.1 Dogfood validation：finance-skills 真实 skill 跑通

```bash
# 准备
cp /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-report-reviewer/skills/parse-credit-report/SKILL.md \
   ~/.openharness/skills/parse-credit-report.md

# 验证
oh chat
>>> /skills
# 期望输出包含 "parse-credit-report  解析央行个人征信报告..."

>>> /parse-credit-report "申请号 12345"
# 期望：
#   - LLM 回应反映 skill body 内的"央行报告 7 大模块"具体内容
#   - 启动 log 无 warning
#   - INFO 事件 slash_skill_invoked 触发，payload 含 synthetic:true
```

### 3.2 全仓 regression green

- `uv run pytest -q` 绿（预期 +30 个新 test，0 个旧 test 改动）
- 新增 unit test cover：
  - D38.1 resolver order（built-in / cmd / skill / unknown 四路径）
  - D38.2 信封 schema（assistant tool_use + user tool_result + user
    args 三条消息，ID 前缀 `synth_`）
  - D38.3 args 空 / 非空两路径
  - D38.4 `/skills` 输出（empty / non-empty catalog）
  - D38.5 PreToolUseHook **不**被 synth envelope 触发的 negative test
  - 同名 collision：commands/foo.md + skills/foo.md → command 赢

### 3.3 §六 Wiring audit 预测落地验证

- compaction L1-L4 对 synth envelope 透明：构造一个长 skill body 触发
  L1 truncation，确认 synth tool_result 像真实 tool_result 一样被
  截断（compact 不需特判 = D38.2 设计正确的验证）
- snapshot 写入完整捕获三条 synth 消息（snapshot dog食 + 重 resume
  能恢复 history）

### 3.4 文档同步

- `oh chat` 内 `/help` 输出加 `/skills` 一行
- `CHANGELOG.md` 加 Phase 18 entry，链回本 D38 doc

---

## 四、Anti-scope

本 phase **不做**：

1. ❌ 不实现 CC plugin loader（`.claude-plugin/plugin.json` 不读）
2. ❌ 不识别 `SKILL.md` 文件名或目录形态 skill
3. ❌ 不实现 declarative sub-agent（`agents/<n>.md` 不读）
4. ❌ 不引入 `oh ask` 的 slash skill 支持
5. ❌ 不改 `LoadSkillTool` 或 `SkillStore` 内部实现
6. ❌ 不改 Phase 5c 的 skill catalog 注入 system_prompt 逻辑
7. ❌ 不改 Phase 5b 的 `resolve_command_invocation`
8. ❌ 不实现 `/commands` 内置命令（仅 `/skills`，scope 严控）
9. ❌ 不引入 `{args}` substitution 到 skill body
10. ❌ 不动 permission tiers / hook chain 任何代码
11. ❌ 不引入新 dep / 不动 pyproject.toml

---

## 五、Implementation contract（informative — capability 级 plan 在 tasks/phase-18-plan.md）

**新增文件**：

- `src/openharness/engine/slash_skill.py`（或 `services/slash_skill.py`）：
  - `synthesize_skill_envelope(skill: Skill, args: str) -> list[ConversationMessage]`
    返回 2 或 3 条消息（args 空时 2 条）
  - 同名前缀 `synth_` 生成 tool_use_id
  - **不**导入 `tools.load_skill` 或 hook / permission 模块（架构隔离）

**改造文件**：

- `src/openharness/cli.py` `_run_chat`：
  - resolver 分支按 D38.1 顺序扩展
  - `/skills` 内置分支按 D38.4 加在 `/help` 旁边
  - SkillStore 命中分支调 `synthesize_skill_envelope` + 追加到
    history + 触发 `slash_skill_invoked` INFO 事件
- `src/openharness/observability/logging.py`：注册 INFO 事件名

**测试**：

- `tests/engine/test_slash_skill_envelope.py`：D38.2 / D38.3 信封单测
- `tests/cli/test_chat_slash_skill.py`：D38.1 resolver / D38.4
  `/skills` / D38.5 hook bypass / collision 优先级

**不动**：

- `tools/load_skill.py`（LoadSkillTool 不被 synth path 调用）
- `skills/store.py` / `skills/model.py`（discover 逻辑不变）
- `commands/expand.py` / `commands/store.py`
- `permissions/*` / `hooks/*`
- `services/compact.py` / `services/snapshot.py`（透明兼容由信封 schema
  保证）

---

## 六、Wiring audit

Phase 18 M1 contract 跨以下 runtime layer。每层 verdict 必须 explicit：

| Layer | Verdict | Reasoning |
|---|---|---|
| **permissions/tier_based** | **bypass (deliberate)** | D38.5：synth envelope 不走 permission check。安全契约不破（LoadSkill 本来就 Tier 3；skill body 是用户自装文件）。需在 `slash_skill_invoked` INFO 事件标 `synthetic:true` 便于审计 |
| **hooks** (PreToolUse / PostToolUse) | **bypass (deliberate)** | D38.5：synth envelope 不触发 hook chain。包含 TruncateToolResultHook bypass（保 skill body 完整性） |
| **services/snapshot** | **unchanged** | synth 三条消息是标准 `ConversationMessage` schema；snapshot 写入/读出 verbatim，无特判 |
| **services/session_memory** | **unchanged** | 不依赖 messages[] 子项 ID 是否 synth-prefixed |
| **services/compact** (L1-L4) | **requires verification** | synth tool_result 必须能被 L1 truncation 像真实 tool_result 一样处理；估计 token 函数对 synth 块透明。需 3.3 中显式验证 |
| **observability** | **requires extension** | 新增 INFO 事件 `slash_skill_invoked`（payload: skill_name, args_length, synthetic=true） |
| **API client** | **unchanged** | synth 块在发给 LLM 时跟真实 tool_use 字节级相同 |
| **CLI subcommand surface** | **unchanged externally** | `oh chat` 子命令 flags 不变；REPL 内部 resolver 多 1 fallback + 1 built-in |
| **Eval substrate** (focus_state + memory_decision) | **unchanged** | eval 走独立 infer 路径，不解析 messages[] tool_use ID |
| **Phase 5b CommandStore / expand_command** | **unchanged** | D38.1 把 CommandStore 放第 2 优先，命中走原 Phase 5b 路径不变 |
| **Phase 5c SkillStore / LoadSkillTool** | **new consumer** | REPL 新增 `SkillStore.get` 调用点；LoadSkillTool 仍存在但 M1 不被 synth path 调用——LLM 自决调用路径完全保留 |
| **Phase 5d Bundle** | **unchanged** | bundle 触发由 `Command.mode` 字段控制；synth skill envelope 不带 mode field，不触发 bundle |
| **Memory (Phase 16/17)** | **unchanged** | memory 注入路径不读 messages[] 内部 ID |

**Conclusion**：

- 2 个 `bypass (deliberate)`（permissions + hooks）—— D38.5 已 ratify
  并要求 observability forcing function 兜底
- 1 个 `requires verification`（compaction）—— acceptance 3.3 直接验证
- 1 个 `requires extension`（observability）—— 单 INFO 事件
- 1 个 `new consumer`（Phase 5c SkillStore）—— 增量调用不改 contract
- 其余 8 层 unchanged

按 CLAUDE.md 规则："≥ 3 requires extension 或多个 bypass = 重新 ratify
scope"。本 phase = 1 extension + 2 deliberate bypass（且 bypass 是
单一原因——synth envelope 跨过 tool execution machinery，是统一 design
choice 不是分散 leak）。**符合 cleanup-sized phase 的 wiring 形态**。

如果实施中发现 compaction L1 处理 synth tool_result 需要特判——立即
回到本 doc 加 D38.8 处理；不允许在 services/compact.py 加
`if id.startswith("synth_")` 的特判（那是把 synth 概念泄漏到不该知道
它的层）。

---

## 七、References

- [Phase 17 boundary](./37-phase-17-boundary.md) — §六 wiring audit
  methodology 来源
- `decisions/12` — Phase 5c L4 invariant ("skill body 进 tool_result，
  不进 system_prompt")，本 phase 的 forcing constraint
- `decisions/14` — Phase 5b slash command dispatch，本 phase resolver
  扩展的契约基础
- `decisions/24` — Phase 9 plugin loader，M2 (CCPluginLoader) 的上游
- `src/openharness/cli.py:1215-1316` — `_run_chat` 当前 resolver 块
  （扩展点位置）
- `src/openharness/tools/load_skill.py` — LoadSkillTool（M1 不被 synth
  路径调用，但保留作 LLM 自决路径）
- finance-skills 仓
  `mybank-credit-risk/plugins/credit-report-reviewer/skills/parse-credit-report/SKILL.md`
  — dogfood 用例 fixture
- [[feedback-design-for-strong-model]] — D38.5 deliberate bypass 的
  哲学背书（不为弱模型加弱化路径；synth envelope 是强模型 ready 的
  最直接信道）
