# loop-runtime L2 — 权限·loop 策略（声明式规则引擎）实现 plan

> **上游**：`loop-runtime-plan.md`（epic capability 地图，L2 行）+ 本模块 interview-me 已确认意图。
> **参照系**：`loop-runtime-autopilot-reference.md`（上游缺 acceptEdits 档 / `denied_commands` 默认空 / Bash 前缀匹配可被绕）。
> **纪律**：TDD 是脊梁。本模块执行流程**特殊**——见 §6：主 loop 写失败测试（RED，人确认）→ GREEN 苦力交 Claude Code `/goal` 自循环 → 末尾人审 diff 收 gate。测试是 spec，`/goal` 条件含"不准弱化断言"。**留档不删。**

---

## 0. 这个模块交付什么（一句话）

在 L1 fail-closed 基线上，给 `TierBasedPermissionChecker` 织进一层**声明式、对齐 Claude Code 的权限规则**（`permissions.allow/deny/ask`，precedence **deny > ask > allow**），让 `oh -p` 无头 loop **按声明安全地干活**——命中 allow 的 mutating 放行、命中 deny 拦、**无命中 + mutating + 无头 → fail-closed DENY**；不可逆红线（Tier1）坐在所有规则**之上、不可覆盖**。

**七个锁定立场**（interview 输出，作不可漂移验收基线）：
1. **形态 = 规则引擎，不是加档**。`acceptEdits` 收编为规则预设（`Edit(*)/Write(*)→allow`），不是新枚举 case。
2. **precedence = deny > ask > allow**（对齐 CC）。
3. **红线在规则之上**：Tier1 sensitive paths 不可被任何 allow 规则/mode 覆盖。
4. **L2 只做逻辑闸**：物理隔离（worktree/sandbox）归 L7，**只留 seam 不建**。
5. **fail-closed 默认**：无头 + mutating + 无 allow 命中 → DENY（**注意这是行为变更**，见 §1 承重点）。
6. **Scope = 务实子集**：`Edit/Write` glob + `Bash` 命令前缀 + `Read` + 三列表 + 优先级 + Tier1 红线 + **单一 settings 源**。
7. **engine 尽量不动**：规则逻辑落在 `permissions/`，复用 query.py 现有 Decision 消费链（`Decision.ASK→mode 解释`、`Decision.DENY→喂回 LLM`）。

**Out of scope**（写死，越界即停）：四层 settings merge（user/project/local/enterprise）、`WebFetch(domain:)` specifier、`mcp__server__tool` specifier（三者**留 seam 不建**）、物理隔离(L7)、L3 验证闸、L4 loop、eval（L2 确定性可测）。

---

## 1. 落点 + 一处承重设计点（动手前必读）

**现有 `TierBasedPermissionChecker.evaluate()` 决策序**（`tier_based.py:303-371`）：
```
1. Bash 灾难 deny-list           → DENY
2. Tier1 hardcoded sensitive     → DENY   ← 红线
3. Tier2 user globs(deny_paths)  → DENY
4. Tier3 mode-based(写/执行出 cwd) → ASK
5. fallthrough                   → ALLOW  ← ⚠ 见承重点
```
**ASK 的终局**在 `query.py:525-537`：`ASK + AUTO → 执行`；`ASK + 非AUTO → DENY-with-hint`（L1 T5 锁定的 fail-closed）。

### ⚠ 承重点：fallthrough 现在是 ALLOW，in-cwd 改文件**当前直接放行**
Tier3 只拦"写/执行**出 cwd**"。一个 mutating 工具（Edit/Write/Bash）**在 cwd 内**今天走 fallthrough = **ALLOW**——这是 v1 交互态既定 UX。立场 5 的"mutating fail-closed"是个**行为变更**：无头时 in-cwd mutating 无 allow 命中也要 DENY。**不能全局翻 fallthrough**，否则破交互态 + 2167 测试。

→ **解法（T0 定稿，T5 落地）**：fail-closed 的 fallthrough **按 posture 门控**——只在"无头 loop posture"下 mutating fallthrough→DENY；交互 DEFAULT 维持 in-cwd ALLOW。posture 怎么表达（复用 `-p` 旗标 / 新增一个 loop-mode 信号 / settings 字段）是 T0 要消解的缝。

### L2 织进去后的目标决策序（T4 落地，T0 定稿细节）
```
1. Bash 灾难 deny-list                       → DENY
2. Tier1 红线（不可覆盖，永在 allow 之上）     → DENY
3. 所有 deny（Tier2 + rules.deny）            → DENY   ┐
4. rules.ask                                 → ASK    ├ deny>ask>allow
5. rules.allow（显式放行，短路 Tier3）         → ALLOW  ┘
6. Tier3 mode-based(出 cwd)                   → ASK
7. fallthrough：mutating+无头 → DENY；否则 ALLOW
```
关键不变量：**rules.allow（步5）在 Tier1（步2）之后** → allow 永远撬不动红线；**所有 deny（步3）在 rules.allow（步5）之前** → deny>allow 成立。

---

## 2. 任务（垂直切片 · 按依赖排 · 每个 RED 先行）

### T0 — 缝勘探（只读，无码改 · 解四处设计缝）
- **干什么**：消解动手前存疑的四点，各给"代码出处 + 结论"：
  1. **posture 怎么表达**（§1 承重点）：fail-closed fallthrough 的门控信号——复用 L1 的 `-p`？新 loop-mode？settings 字段？（看 `cli.py` 怎么把 `-p` 透传到 context / settings）
  2. **规则语法子集定稿**：`Edit/Write/Read(glob)` 用现成 `_glob_match`；`Bash(prefix)` 前缀语义（CC 警告可绕——明确我们的边界，不假装安全）；裸 `ToolName`（无括号）= 该工具任意调用？还是要求 `ToolName(*)`？
  3. **`deny_paths`(Tier2) vs 新 `permissions.deny` 关系**：共存（Tier2 留旧、新块并行）还是收编？（看 settings.py:399 + tier_based 步3）
  4. **acceptEdits 预设落点**：是 settings 里的具名预设展开成规则，还是 `--permission-mode acceptEdits` 旗标映射成 `Edit/Write(*)→allow`？
- **产出**：本文件追加《T0 缝勘探结论》，据此校准 T1/T4/T5/T6 验收。
- **验收**：四问各一行出处+结论；不写码。

### T1 — `Settings.permissions` schema + 解析（最薄切片）
- **RED**：`test_settings_permissions_block`：load 一个带 `permissions.{allow,deny,ask}`（三个 `tuple[str,...]`）的配置，断言字段就位、默认空 tuple。先见红。
- **GREEN**：给 `Settings` 加 `permissions` 块，env 解析比照 `deny_paths`（`NoDecode` + validator）。
- **验收**：三列表可加载、默认空；现有 settings 测试不破。
- **质量门**：`mypy --strict` + `ruff`。

### T2 — 规则语法 parser（`ToolName(specifier)` → 结构化 Rule）
- **RED**：`test_rule_parse`：覆盖 `Edit(src/**)`、`Bash(npm run test:*)`、`Read(*)`、裸 `ToolName`（按 T0 结论）、非法串报错。先见红。
- **GREEN**：parser 产出 `Rule{tool, specifier, kind}`；specifier 按工具分派（file=glob / Bash=prefix）。
- **验收**：各形态解析正确；非法串明确报错（不静默吞——对照 autopilot `_looks_available` 静默筛教训）。

### T3 — 规则匹配器 + precedence（核心 · 纯函数）
- **RED**：`test_rule_match_precedence`：① deny 命中压过 allow 命中（deny>ask>allow）；② Bash 前缀命中/不命中；③ glob 命中/不命中；④ 多规则特异性。先见红。
- **GREEN**：`match_rules(tool, args, rules) -> DecisionResult | None`（None = 无命中，交回 Tier 链）；纯函数，**不碰 checker**。
- **验收**：precedence 表全绿；纯函数无副作用。

### T4 — 织进 `TierBasedPermissionChecker`（承重集成 · 不破回归）⭐
- **RED**：`test_checker_rule_layer_order`：按 §1 目标决策序断言——① Tier1 红线压过 rules.allow（allow 撬不动红线）；② rules.deny 压过 rules.allow；③ rules.allow 短路 Tier3（显式放行 out-cwd 写）。**外加：全量 `uv run pytest -q` 2167 旧测试必须绿**（回归闸）。先见红。
- **GREEN**：在 `evaluate()` 按 §1 序插入规则层；Tier1 仍最先（红线）；规则层在 Tier2/3 之间按 precedence 排。
- **验收**：新序测试 + **2167 旧测试全绿**；这是本模块**最大风险切片**。
- **checkpoint ①**：声明式规则能改 mutating 工具的放行结果，红线不可破。

### T5 — fail-closed fallthrough（立场 5 · §1 承重点落地）
- **RED**：`test_failclosed_headless_mutating`：① 无头 posture + mutating + **无 allow 命中** → DENY（不是 ALLOW）；② **同条** mutating + 有 `Edit(*)→allow` → ALLOW；③ **交互 DEFAULT** + in-cwd mutating → 仍 ALLOW（不破既定 UX）；④ read-only 工具无论 posture → ALLOW。先见红。
- **GREEN**：按 T0 定的 posture 信号门控 fallthrough——无头+mutating+无命中→DENY；其余维持 ALLOW。
- **验收**：四条断言全绿；交互态行为零变更。
- **checkpoint ②**：无头 loop 默认安全（fail-closed），声明 allow 才放手。

### T6 — acceptEdits 预设 + 端到端红线（立场 1/3 收口）
- **RED**：`test_accept_edits_preset`：acceptEdits（按 T0 落点）→ `Edit/Write` 放行、**Bash 仍按 fail-closed 拦**；`test_redline_unoverridable_e2e`：即便配 `Edit(~/.ssh/**)→allow`，写 `~/.ssh/x` **仍 DENY**（红线压过显式 allow）。先见红。
- **GREEN**：acceptEdits 预设展开成规则；红线 e2e 验证步2在步5之前。
- **验收**：acceptEdits 只放编辑不放命令；红线 e2e DENY。
- **checkpoint ③**：Success 全量达成——写一份 `permissions` 声明 → `oh -p` 吃它，三种结果（allow/deny/fail-closed）+ 红线不可破全坐实。

---

## 3. 验收（整模块 done 的判据）

```bash
# 写一份 permissions 声明（allow Edit、deny 某 glob），oh -p 吃它：
oh -p "改 src/foo.py" --output-format json     # 命中 Edit(src/**)→allow：放行，正常跑
oh -p "改 src/foo.py"                          # 无 allow + mutating + 无头 → 该 Edit 被 DENY（JSON 记下）
# 配 Edit(~/.ssh/**)→allow 仍写不进 ~/.ssh（红线 e2e）
uv run pytest -q && uv run mypy --strict src/ && uv run ruff check
```
**全程确定性可测，不走 eval**（对照 `loop-runtime-plan.md §5`：L1/L2 确定性）。

---

## 4. 依赖图 + 顺序
```
T0(勘探) → T1(settings) → T2(parser) → T3(matcher) → T4(织入·回归闸) ─┬─ checkpoint①
                                                                      ├─ T5(fail-closed) ── checkpoint②
                                                                      └─ T6(acceptEdits+红线 e2e) ── checkpoint③
```
T5、T6 都依赖 T4（集成完才能谈门控与端到端）。

---

## 5. 留 seam 不建（务实子集的边界 · 写明防玩具化误判为"漏了"）
- 四层 settings merge：单一源即可；parser/schema 留 list 形态，将来 merge 只需并集，不返工。
- `WebFetch(domain:)` / `mcp__server__tool` specifier：parser 的工具分派留 default 分支（裸匹配），加 specifier 类型即扩展，不改架构。
- 物理隔离(L7)：L2 放行 mutating 时**不**落 worktree；爆炸半径兜底是 L7 的事，L2 只在文档点明"显式 allow = 你信任它在真工作树上改"。

---

## 6. 执行流程（本模块特殊 · `/goal` 实验）
1. **`/plan` 完成**（本文件）= 可度量的靶。
2. **主 loop 写 RED + 人确认**：每个 T 的失败测试由主 loop 写、跑、人亲眼见红（守住"测试是 spec / 先见红"）。
3. **GREEN 交 `/goal`**：`claude -p "/goal 让 tests/permissions/ 的新测试转绿且不准弱化断言、所有旧测试通过、mypy --strict src/ clean、ruff clean"`（配 `/auto`）。`/goal` 自循环填实现。
4. **末尾人审 diff 收 gate**（CLAUDE.md solo gate）：对照本 plan + 红测试审一遍再 commit。
> ⚠ Haiku 判官只看对话呈现的信息，**验证不了"先见过红"也挡不住弱化断言**——所以步2（人写红+见红）和步4（人审 diff）是不可省的人类闸，`/goal` 只接管步3的 GREEN 苦力。这本身也是对 epic L3/L4（`/goal` ≈ 原生实现）的一次 dogfood 观察，回填进 epic plan。

---

## T0 缝勘探结论（2026-06 · 只读核实完毕）

| 缝 | 结论 | 代码出处 |
|---|---|---|
| **缝1 posture（承重）** | `print_mode: bool` 旗标已存在于 CLI 层，但**只**用于退出码映射+output_format 校验，**未透传**进 `_run_ask`/`QueryContext`/checker（`_run_ask` 只收 `output_format`）。**定稿：把现有 `print_mode` 当 `headless` posture，构造时传给 checker**——`TierBasedPermissionChecker(registry, settings, headless=print_mode)`，fallthrough 逻辑全留在 checker 内。**不新增 PermissionMode 枚举值**（守立场1 不加档）；**engine/query.py 一行不动**（守立场7）。 | `cli.py:1627`(print_mode 定义)、`:1875,1993`(仅这两处用)、`:833`(checker 构造点，在 `_run_ask` 内)、`:451`(_run_ask 只收 output_format) |
| **缝2 规则语法** | **file 工具**(`Edit/Write/Read(glob)`)复用现成 `_glob_match`(fnmatch + `dir/**`)；**`Bash(prefix)`**=命令前缀匹配，CC 语法尾 `:*` 表前缀（解析时剥掉），匹配 `command.startswith(prefix)`——**明写边界：Bash allow 规则是便利不是安全墙**（CC 自己警告前缀可被 `;`/`&&`/子shell 绕），真正护栏仍是步1 Bash 灾难 deny-list + Tier1；**裸 `ToolName`**(无括号)规范化为 `ToolName(*)`(任意调用)。 | `tier_based.py:64`(_glob_match)、autopilot reference `:166-167`(Bash 前缀可绕教训) |
| **缝3 deny_paths vs permissions.deny** | **共存，不收编**——`deny_paths`(Tier2)原样留着保 2167 测试，新 `permissions.deny` 是规则层 deny，二者同在 deny band（步3）按序检查。未来统一记为 follow-up，**本模块不做**（回归闸安全优先）。 | `settings.py:399`(deny_paths)、`tier_based.py:344`(Tier2 检查点) |
| **缝4 acceptEdits 落点** | **不是新 PermissionMode 枚举值**（守立场1）。是**规则预设**——helper 把 `Edit(*)→allow, Write(*)→allow` 展开后 prepend 进 allow 列表；**Bash 不在预设内**（仍 fail-closed 拦）。surface 为 CLI 旗标 `--accept-edits`（或 settings `permissions.preset`）。 | `checker.py:99-101`(PermissionMode 仅 DEFAULT/AUTO/DRY_RUN) |

**补充（schema 命名）**：Settings 已有 `permission_mode`(posture) + `deny_paths`(Tier2)。新块为对齐 CC **可移植性**（本模块目标），用嵌套 `permissions: {allow, deny, ask}`（CC 同构）。与 `permission_mode` 并存——一个管 posture、一个管规则，关注点不同；T1 注释点明防混淆。

**据此校准后续任务**：
- **T1**：schema = 嵌套 `permissions` model（`allow/deny/ask: tuple[str,...]`），env 比照 `deny_paths` 的 `NoDecode`+validator；与 `permission_mode` 并存。
- **T4**：checker 构造加 `headless: bool` 参；fallthrough 用它门控；engine 不动。
- **T5**：posture 信号 = `print_mode` → checker `headless`；交互态 `headless=False` → fallthrough 维持 ALLOW（零回归）。
- **T6**：acceptEdits = 规则预设 helper，非枚举；`--accept-edits` 旗标。

— 2026-06 L2 plan（TDD 脊梁 · 规则引擎 B · 务实子集 · 留档不删 · **T0 已勘探**）
