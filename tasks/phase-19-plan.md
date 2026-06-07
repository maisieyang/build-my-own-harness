# Phase 19 Implementation Plan — CCPluginLoader (M2 of CC Skill 接入)

> Boundary contract: [`decisions/39-phase-19-boundary.md`](../decisions/39-phase-19-boundary.md).
> Phase 18 retro 高置信预测背书: [`learnings/phase-18.md`](../learnings/phase-18.md) §3.
> All 8 D39.x ratified 2026-06-07 (all Recommended defaults accepted).
> **D39.9 added 2026-06-07** (T1.0 → T1.1 transition): D39.5 reversed
> after pre-T1.1 audit revealed CC `.mcp.json` HTTP+OAuth2 transport
> does not fit OH's stdio-only `McpServerConfig` (D15.1). `.mcp.json`
> now silently ignored in M2; HTTP MCP support is a separate future
> phase. See boundary doc D39.9 for full rationale.

## Overview

**Phase 19 goal**：让用户 `cp -r /path/to/cc-plugin-dir
~/.openharness/plugins/<name>` 一次完成 plugin 安装；CC format
（`.claude-plugin/plugin.json` + `skills/<n>/SKILL.md` + 可选
`.mcp.json`）和 OH format（`manifest.yaml`）在同一发现根并行存在；
下游 `SkillStore` / `_run_chat` slash resolver / 既有 `LoadSkillTool`
全部不知道 plugin 来自 CC 还是 OH。

**Cross-cutting invariant** (per D39 §六 Wiring audit)：

- `engine/slash_skill.py` — **零 diff（load-bearing prediction）**。
  Phase 18 retro §3 高置信预测：CC plugin 解析完进 SkillStore 后，
  Phase 18 的 synth envelope helper 不需要改任何字节。如果实施中发
  现要改，**立即回 boundary doc**，不 patch helper
- `skills/store.py` / `skills/model.py` — 零 diff
- `commands/` — 零 diff（CC plugin.json 无 commands）
- `bundles/` — 零 diff（CC 无 bundles）
- `hooks/` — 零 diff（CC hook 是 settings.json shell command，不映射）
- `permissions/` — 零 diff（M3 才需要 Tier 映射）
- `services/snapshot|session_memory|compact` — 零 diff
- `engine/query.py` — 零 diff
- `prompts/` — 零 diff
- `memory/` — 零 diff
- `eval/` — 零 diff
- typer flag / settings schema — 零 diff（D39.7 只**新增** `plugins list`
  子命令，不改任何既有 flag）

Expected net diff（**D39.9 调整后**）：约 **+100 LoC src + +220 LoC tests**:

- T1 plugins/model.py: ~50 LoC (CC plugin.json + skills scan;
  ~~_parse_mcp_json removed by D39.9~~)
  + ~70 LoC tests (含 D39.9 silent-ignore + source-leak forcing-function
  negative tests)
- T2 plugins/loader.py: ~50 LoC (双格式 dispatch + dual-manifest WARN)
  + ~120 LoC tests
- T3 oh plugins list: ~30 LoC CLI + ~60 LoC tests
- T4 dogfood: 0 src，retro §1 evidence only
- T5 retro / CHANGELOG: docs only

Total predicted: **~320 LoC**（D39.9 让 T1 减重 ~30 LoC）vs Phase 18 实
测 ~1200 LoC（含 boundary + plan + retro 三大块 doc 350 LoC）。M2 比
M1 小是因为 boundary 已建立，T1/T2 没有"新概念"工作量，只是格式翻译。

## Architecture decisions (locked 2026-06-07)

| Doc | What it locks |
|---|---|
| [`decisions/39-phase-19-boundary.md`](../decisions/39-phase-19-boundary.md) | D39.1 单 PluginLoader 双格式 dispatch; D39.2 复用 `PluginManifest` dataclass; D39.3 `~/.openharness/plugins/` 单发现根; D39.4 plugin dir 缺 manifest = silent skip; D39.5 `.mcp.json` 在 M2 内（非 M2.5 split）; D39.6 双 manifest 共存 = CC 优先 + WARN `plugin_dual_manifest`; D39.7 `oh plugins list` ship now; D39.8 `plugin_discovered` payload 加 `format` 字段 |
| [`decisions/24`](../decisions/24) | Phase 9 PluginLoader 既有契约（namespace `<plugin>__<component>`、fault tolerance 模型、单根扫描），M2 在其上 strict 扩展，不替换 |
| [`decisions/12`](../decisions/12) | Phase 5c `parse_skill` SKILL.md 解析契约（含 Phase 17 T1 多行 description 支持），M2 复用不动 |
| [`learnings/phase-18.md`](../learnings/phase-18.md) §3 | M2 zero-diff prediction on `engine/slash_skill.py` → T1 第一步 forbidden imports proactive guard 是 P19 retro 的预测验证 |

---

## Task list

### P19-T1: CC plugin format parser — `plugins/model.py`

**Description**: 在 `plugins/model.py` 新增两个 helper 把 CC 形态
plugin 目录翻译成既有 `PluginManifest`（D39.9 撤回原计划的第三个
`_parse_mcp_json` helper）：

```python
def parse_cc_plugin(plugin_dir: Path) -> PluginManifest | None:
    """Read .claude-plugin/plugin.json + scan skills/*/SKILL.md.
    Returns same PluginManifest as Phase 9 OH-format parser.
    None + warning on any error (same fault tolerance model as
    parse_manifest).

    D39.9: ``.mcp.json`` is silently ignored — ``mcp_servers``
    on the returned manifest is always ``()``. HTTP MCP transport
    extension is a separate future phase.
    """

def _scan_cc_skills_dir(plugin_dir: Path) -> tuple[ComponentRef, ...]:
    """Glob plugin_dir/skills/*/SKILL.md → ComponentRef tuples."""
```

D39.2 字段映射（CC plugin.json → `PluginManifest`）：

| CC field | OH PluginManifest field | Default if missing |
|---|---|---|
| `name` (str, required) | `name` | parse fails (WARN + None) |
| `version` (str, required) | `version` | parse fails |
| `description` (str, required) | `description` | parse fails |
| `author.name` (nested object) | `author` (top-level str) | None |
| (CC has no field) | `license` / `homepage` / `keywords` / `dependencies` / `openharness_version_min` | None / `()` |
| (CC has no field) | `commands` / `bundles` / `hooks` | `()` |
| `skills/<n>/SKILL.md` (scan) | `skills: tuple[ComponentRef, ...]` | `()` (empty plugin OK) |
| ~~`.mcp.json -> mcpServers`~~ | `mcp_servers: tuple[McpServerConfig, ...]` | `()` **always** (D39.9 — `.mcp.json` silently ignored regardless of presence) |

**T1.0 — Proactive guard FIRST** (per Phase 18 retro §3): 在
**任何 T1 source code 之前**，先扩展 Phase 18 architecture isolation
test 的 forbidden list：

```python
# tests/engine/test_slash_skill_envelope.py
FORBIDDEN_MODULE_PREFIXES = (
    "openharness.tools.load_skill",
    "openharness.permissions",
    "openharness.hooks",
    "openharness.observability",
    "openharness.cli",
    "openharness.plugins",  # ← NEW in Phase 19 (proactive guard)
)
```

这一行先 commit，确保整个 T1 实施过程中**不可能**让
`engine/slash_skill.py` 误引入 plugins 依赖。Phase 18 retro §3 的预
测验证就靠这一行。

**Acceptance**:

- [ ] T1.0 proactive guard 已 commit 在任何 plugin parser 代码之前
  （已完成 — commit `68c41a0`）
- [ ] `plugins/model.py` 新增 `parse_cc_plugin` 公共函数 + 一个
  internal helper `_scan_cc_skills_dir`
- [ ] `parse_cc_plugin` 返回 `PluginManifest | None`；None 时已
  emit 至少一个 WARN 事件（与 `parse_manifest` 同 fault tolerance
  模型）
- [ ] CC plugin.json 缺 `name` / `version` / `description` 任一 →
  WARN + None
- [ ] CC plugin.json 含 `author` 嵌套 `{"name": "..."}` → 提取到
  `PluginManifest.author` 顶层 str；缺失或非 dict → `None`
- [ ] `_scan_cc_skills_dir` 按字母序返回 `ComponentRef`，路径形态
  严格 `skills/<n>/SKILL.md`；`skills/` 不存在或为空 → 返回 `()`
- [ ] **D39.9 silent ignore**: plugin 目录含 `.mcp.json` →
  `parse_cc_plugin` 返回 `mcp_servers=()`；**不** emit WARN；**不**
  import `McpServerConfig` 或 mcp 模块任何符号
- [ ] **D39.9 forcing function (source-leak guard)**: `plugins/model.py`
  源码不含字面量 `.mcp.json` / `mcp.json` / `mcpServers` 字符串 —
  与 D38 §六 closing rule 的 `compact.py` × `synth_` 同款思路。任
  何 import `from openharness.mcp` 也由 D39.9 anti-scope 第 19 条
  禁止
- [ ] 单测覆盖：
  - 完整 fixture：`name + version + description + author.name +
    skills/*/SKILL.md (3 个)` → 字段全对
  - minimal fixture：仅 `name + version + description` → `skills=()`
    `mcp_servers=()` `author=None`
  - D39.9 negative test：plugin 目录加 dummy `.mcp.json`（任意 JSON
    内容）→ `parse_cc_plugin` 仍返回 `mcp_servers=()`；caplog 无任何
    `mcp_*` event；plugin 其它字段正常 load
  - 嵌套 `author` 非 dict（JSON 错写成 `"author": "string"`）→
    `author=None`，不 crash
  - JSON 解析失败 → None + WARN
  - 必需字段缺失 → None + 对应 WARN（每个字段一个 test）
  - D39.9 source-leak forcing function：grep plugins/model.py for
    `.mcp.json` / `mcpServers` / `mcp.json` 字面量 + `from
    openharness.mcp` import → 必须 0 命中
- [ ] **`engine/slash_skill.py` 零 diff**；T1.0 的 forbidden-imports
  test 持续绿
- [ ] 全仓 regression 绿

### P19-T2: PluginLoader dual-format dispatch — `plugins/loader.py`

**Description**: 扩展 `PluginLoader.discover()` 按文件存在性路由到 CC
或 OH 解析路径（D39.1）；双 manifest 共存时 CC 优先 + emit WARN
`plugin_dual_manifest`（D39.6）。`fan_out` 完全不动 — 两条路径返回
同一 `PluginManifest`。

Dispatch 伪代码（实际在 `discover` 里）：

```python
for entry in <plugins_dir>.iterdir():
    if not entry.is_dir():
        continue
    cc_marker = entry / ".claude-plugin" / "plugin.json"
    oh_marker = entry / "manifest.yaml"
    if cc_marker.is_file() and oh_marker.is_file():
        _logger.warning(
            "plugin_dual_manifest",
            plugin_dir=str(entry),
            picked="cc",
            ignored="manifest.yaml",
        )
        manifest = parse_cc_plugin(entry)
    elif cc_marker.is_file():
        manifest = parse_cc_plugin(entry)
    elif oh_marker.is_file():
        manifest = parse_manifest(oh_marker)
    else:
        continue  # D39.4 silent skip
    if manifest is None:
        continue
    if manifest.name in manifests:
        raise PluginConflictError(...)
    manifests[manifest.name] = manifest
```

`PluginConflictError`（Phase 9 既有）仍按相同语义抛出 — 不区分两个
plugin 来自相同格式还是混合格式。

**Acceptance**:

- [ ] `PluginLoader.discover` 按文件标记路由到正确的 parser
- [ ] CC plugin 命中后下游 `fan_out` 调用 `_fan_out_skills` 复用既有
  路径（namespacing `<plugin>__<skill>` 保持）；CC plugin
  `_fan_out_commands / _fan_out_bundles / _fan_out_hooks` 都 no-op
  （空 tuple）；`_fan_out_mcp_servers` 处理 `.mcp.json` 来源同 OH 来源
- [ ] D39.6 dual-manifest：两文件共存 → emit WARN `plugin_dual_manifest`
  payload `{plugin_dir, picked: "cc", ignored: "manifest.yaml"}` →
  走 CC 路径 → OH manifest.yaml 整体跳过（不在 manifests dict 里出现
  两次）
- [ ] D39.4 silent skip：plugin dir 既无 `.claude-plugin/plugin.json`
  也无 `manifest.yaml` → 跳过，无 WARN（包括 `<plugin>` 本身就是
  README.md / .git / 空目录的情况）
- [ ] 跨格式 plugin name conflict：CC plugin A 名 `x` + OH plugin B
  名 `x` 在不同目录 → 仍触发 `PluginConflictError`（错误信息含两
  目录路径）
- [ ] 单测覆盖：
  - 双格式 dispatch 4 路径（CC only / OH only / 双有 → CC 优先 /
    都没 → silent skip）
  - dual-manifest WARN 事件 emit + payload
  - CC + OH 跨格式同名冲突仍 raise `PluginConflictError`
  - 一个 CC plugin 解析失败不阻断其他 plugin（fault tolerance）
- [ ] 整合测试：用 finance-skills 真实 fixture（cp 到 tmp
  `plugins_dir`）跑 `discover + fan_out`，验证：
  - `credit-report-reviewer` 4 个 skill 全部以
    `credit-report-reviewer__<n>` 形态进 SkillStore catalog
  - `credit-bureau-connectors` 3 个 MCP server 全部进 mcp_servers
    catalog
- [ ] `engine/slash_skill.py` 零 diff；T1.0 forbidden guard 持续绿

### P19-T3: `oh plugins list` subcommand — `cli.py`

**Description**: 实装 `oh plugins list`（D39.7）。新增 `plugins_app =
typer.Typer()`，挂在 `app` 上；`list` 子命令显示发现的 plugin 一行
一个：

```
NAME                          FORMAT  VERSION  SKILLS  MCP_SERVERS
credit-report-reviewer        cc      0.1.0    4       0
credit-bureau-connectors      cc      0.1.0    0       3
my-legacy-plugin              oh      1.0.0    2       1
```

`--format json` 输出 `[{name, format, version, skills_count,
mcp_servers_count, source_path}, ...]`。

实现：`PluginLoader.discover()` + 对每个 manifest 算 skills 数 + mcp
数。**不**调 `fan_out`（避免不必要的 hook import 副作用）。

为支持 D39.7，`PluginManifest` 需要新字段 `source_format: Literal["cc",
"oh"]` —— 或者通过 `source_path` 重新探测。**Recommended**：在
`PluginLoader.discover` 返回的中间结构（不是 PluginManifest dataclass
本身，per D39.2）里加 `_DiscoveredPlugin(manifest, source_format)`
tuple，让 `oh plugins list` 消费这个。`PluginManifest` 保持 D39.2
契约（无 source_format 字段）。

**Acceptance**:

- [ ] `oh plugins list` 命令存在并被 `oh --help` 列出
- [ ] 输出 5 列对齐 text format：NAME / FORMAT / VERSION / SKILLS /
  MCP_SERVERS（按 plugin name 字母序）
- [ ] 空 plugin dir 或 `~/.openharness/plugins/` 不存在 → 输出
  `(no plugins installed)` 一行
- [ ] `--format json` 输出 list of dict，含上述 5 个字段 + `source_path`
- [ ] `oh plugins list` 不会触发任何 hook import 副作用（`fan_out`
  内部 Python hook loading 是 enable-plugins-on 才走的）
- [ ] 单测覆盖：
  - 空 catalog 输出
  - 多 plugin 输出对齐 + 字母序
  - `--format json` 输出 schema
  - CC + OH 混合 plugin 在同一输出里 FORMAT 列正确
  - dual-manifest plugin 标 `cc`（picked）
- [ ] `PluginManifest` 无新增字段（D39.2 复用契约严守）
- [ ] `engine/slash_skill.py` 零 diff

### P19-T4: Dogfood — finance-skills `credit-report-reviewer` 端到端 (+ `credit-bureau-connectors` D39.9 negative)

**Description**: Phase 18 T4 的演进版 — 不再 cp 单 .md 文件，而是
cp 整个 plugin 目录树，验证 4 个 namespaced skill 全部活过来。
**D39.9 调整**：MCP server 部分**不**端到端验证 —
`credit-bureau-connectors` 改作 D39.9 silent-ignore negative
dogfood（plugin 本身被发现，`.mcp.json` 静默忽略）。Forcing
function：不通过 dogfood，T1-T3 单测全绿也不能算 Phase 19 done。

**Acceptance**:

- [ ] 准备步骤可重现：

  ```bash
  rm -f ~/.openharness/skills/parse-credit-report.md  # Phase 18 M1 单文件清掉
  cp -r /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-report-reviewer ~/.openharness/plugins/credit-report-reviewer
  cp -r /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-bureau-connectors ~/.openharness/plugins/credit-bureau-connectors
  ```

- [ ] `oh plugins list` 输出（INFO log 不开）应严格匹配（D39.9 — MCP
  列两个 plugin 都是 0；`credit-bureau-connectors` 的 `.mcp.json`
  silently ignored）：

  ```
  NAME                          FORMAT  VERSION  SKILLS  MCP_SERVERS
  credit-bureau-connectors      cc      0.1.0    0       0   ← D39.9
  credit-report-reviewer        cc      0.1.0    4       0
  ```

- [ ] `oh chat` 启动后 `/skills` 输出包含 4 个 namespaced 名（字母序）：
  - `credit-report-reviewer__apply-credit-rules`
  - `credit-report-reviewer__cross-verify-application`
  - `credit-report-reviewer__draft-credit-finding`
  - `credit-report-reviewer__parse-credit-report`

  每个名后面跟 SKILL.md description 首行（**Phase 18 §2 已知 nit**：
  当前 `/skills` 渲染未做多行折叠 → 4 个 skill 各自的 multi-line
  description 都会铺开；本 phase 不修，retro §2 标注）

- [ ] `/credit-report-reviewer__parse-credit-report 申请号12345`
  触发：
  - LLM 回应反映 Phase 18 dogfood 同款 skill body 内容（4 of 5 anchor
    至少出现）—— 证明 namespaced skill 走 synth envelope 路径
    完全等价
  - INFO 事件 `slash_skill_invoked` payload `skill_name=
    "credit-report-reviewer__parse-credit-report"`, `synthetic=true`,
    `args_length=8`

- [ ] 启动 log 含 `plugin_discovered` × 2 INFO 事件，每个 payload
  含 `format="cc"`（D39.8）；无 `plugin_dual_manifest` WARN；无
  `skill_validation_failed` / `skill_missing_description`

- [ ] dual-manifest negative test：在 `~/.openharness/plugins/
  credit-report-reviewer/` 加一个 dummy `manifest.yaml`（任何最小
  合法内容）→ 重启 `oh chat` → 启动 log 必有 `plugin_dual_manifest`
  WARN payload `picked=cc, ignored=manifest.yaml` → `/skills` 输出
  与不加 manifest.yaml 时**完全相同**（CC 优先）→ 清理 `manifest.yaml`

- [ ] dogfood 步骤 + 关键证据（`oh plugins list` 输出 +
  `plugin_discovered` event JSON + `slash_skill_invoked` event JSON
  + LLM 回应 4-of-5 anchor 表格 + dual-manifest WARN payload）记录
  到 `learnings/phase-19.md` retro §1

### P19-T5: CHANGELOG + Phase 19 retro

**Description**: T1-T4 全部 land + dogfood pass 后写
`learnings/phase-19.md` retro。CHANGELOG 加 Phase 19 entry 链回 D39
和本 plan。retro 重点回答：

1. **`engine/slash_skill.py` 零 diff 兑现验证**：T1.0 proactive guard
   一次都没触发吗？这是 Phase 18 retro §3 高置信预测的实证 ——
   兑现 = 第三次连续验证 abstraction-first compounds (Phase 17 §3.1
   有类似 7a/7b/7c 演示)
2. **`PluginManifest` 复用 vs 新 dataclass**：实施中是否有任何字段
   需要 source_format / format-specific 字段？如果纯通过中间 tuple
   解决 → D39.2 决策正确
3. **D39 §六 wiring audit 兑现**：4 个 extension + 12 unchanged
   （含 mcp/config + client per D39.9）+ 0 bypass + 0 verification —
   是否全部 verbatim 兑现？是否有新 `requires extension` 层 surfaced？
4. **D39.9 反转的方法论教训**：boundary doc §六 audit 起草时为什么没
   抓到 OH MCP transport stdio-only 这个限制？补充检查项给下一个
   phase（M3 / Phase 20）的 §六 audit 模板：跨 module 扩展时，不光检
   查 import 路径，还检查目标 module 的 transport / 协议覆盖矩阵
5. **跟 M3 (Phase 20) 的衔接预测**：M2 解析 `author.name` 嵌套时摸到
   的字段映射模式（嵌套 dict → 顶层 str），M3 解析 `agents/<n>.md`
   时的 `tools:` 白名单字段映射是否能复用同一模式？

**Acceptance**:

- [ ] `CHANGELOG.md` 加 Phase 19 entry：日期 / 标题 / 1-2 句 summary /
  链回 D39 + 本 plan + dogfood 报告位置
- [ ] `learnings/phase-19.md` 按 §1 What worked / §2 What missed / §3
  Predictions for M3 (Phase 20) / §4 Abstractions tested / §六
  verdict mapping table 五段结构写
- [ ] §1 What worked 含 T4 dogfood 关键证据 + `oh plugins list`
  输出 + 4-of-5 anchor 表
- [ ] §1 必须显式声明 `engine/slash_skill.py` 零 diff 兑现（grep
  `git diff main..HEAD -- src/openharness/engine/slash_skill.py`
  结果空字符串）
- [ ] §3 Predictions 至少列出：
  - M3 (DeclarativeAgent) 解析 `agents/<n>.md` 时，`tools:` 白名单
    字段是否能复用 D39.2 的"嵌套 dict → 顶层 str 投射"模式
  - M3 触发路径预测仍是 D38.5 synth envelope 变种（`name="Agent"`），
    Phase 19 完成后 `synthesize_skill_envelope` 是否到了应该泛化为
    `synthesize_tool_use_envelope` 的拐点（Phase 7a 派生 substrate
    Protocol 的同款 forcing function）
- [ ] §4 Abstractions tested 含一条："PluginManifest 作为 cross-format
  共同地基" 的论证 —— CC 和 OH 翻译进同一 dataclass，下游 fan_out 不
  区分；是否在实施中有反例（某个下游消费者需要知道来源 format）？
  无反例 = 抽象有效
- [ ] retro 实测 LoC delta 与 boundary doc 预测 (+120 src / +260
  tests = +380) 的偏差记录
- [ ] retro §六 verdict 对照：16 个预测层 verdict（含 D39.9 新加
  mcp/config + client 层）是否全部如期；有否新层 surfaced。**特别
  说明**：D39.9 反转 D39.5 揭示 Phase 17 / 18 的 100% 兑现节奏在
  Phase 19 起草阶段就已经断了一次（mcp 层漏审）—— Phase 19 §六
  audit methodology 报告需 honest 标注这一"在 boundary doc 起草而非
  实施期被抓"的失误位置（非 forgiving）

---

## Open frontier (deferred past Phase 19)

按 D39 §一 "不在 phase 范围" + 本 phase 实施中新发现项：

1. **M3 (Phase 20)**: DeclarativeAgent — `agents/<n>.md` 含 `tools:`
   白名单的声明式 sub-agent。需要新 §六 wiring audit 决定 `tools`
   白名单跟 OH permission Tier 怎么映射；触发路径预测是 synth
   envelope 的 `tool_use(Agent)` 变种 (Phase 18 retro §3 + Phase 19
   retro §3 双重 referenced)
2. **`marketplace.json`**: 多 plugin 聚合元数据；待真有用户需要"一次
   cp 整个 marketplace"时再处理（M2 dogfood 验证后再判断 driver
   是否充分）
3. **`~/.claude/plugins/<n>/` 双根扫描**: D39.3 deferred；
   `Settings.plugin_dirs: list[Path] = [...]` 形态预留好
4. **`oh ask "/<skill>"`**: D38.6 仍 deferred；M3 触发路径定下来后
   一并 ratify
5. **`/skills` 多行 description 渲染 polish**: Phase 18 §2 nit；M2
   实测后如果 dogfood UX 难以忍受可独立 phase（D39.5 ratify "defer"，
   是 cleanup-sized 而非 M2 内）
6. **`{args}` substitution into skill body**: D38.3 仍 deferred
7. **`oh plugins show <name> / enable / disable / refresh`**: D39.7
   严控 scope，仅 `list`；refresh 在 hot-reload 需求出现前不做
8. **CC settings.json hooks (shell command)** → OH HookSpec 映射：M2
   显式不处理；如果有用户 driver 再独立 phase
9. **CC partner-plugin matrix (spglobal/lseg)**: 真有 partner-build
   分发需求再单独 phase
10. **REPL substrate UX 三件套 — alias + tab completion + multi-line paste**:
    Phase 18 `/parse-credit-report` 单文件路径 vs Phase 19
    `/credit-report-reviewer__parse-credit-report` plugin 路径在敲键
    成本上有明显差距（前者 21 字，后者 44 字）。Driver = 2026-06-07
    用户实跑 credit-report-reviewer dogfood：
    - 直接发问"为什么是这样的格式" → alias / tab needs
    - 一次性 paste 整段假征信报告**被按行切成 4 片**进 `input()`，
      LLM 在 4 个连续 turn 上做 graceful accumulator + acknowledge，
      最终 parse 出来还是对的 —— 但**键盘 round-trip 4 倍开销**

    三条解法**同一个 root cause**（`input()` 是裸 stdin，太基础）：
    - **alias 机制**：当 namespaced 名在所有已装 plugin 中**全局
      唯一**时，允许敲短名（D38.1 resolver 加 fuzzy fallback；如有
      冲突则提示用户 disambiguate）。需 ratify "全局唯一"的检测时机
      （bootstrap vs 每次 resolver 调用）+ 冲突时的 UX
    - **REPL tab 完成**：`prompt_toolkit` 替代 `input()`；接 catalog
      作为 completer
    - **Multi-line paste**：`prompt_toolkit` 的 bracketed-paste 模式
      让一次 paste 进 LLM 一个 turn，不是按行切成 N 段
    后两条**共享同一个 substrate 切换**（`input()` → `prompt_toolkit`），
    打包做掉。alias 是 resolver 层的独立小动作。

    建议 Phase 20 (M3) **不**碰这个 UX 议题（M3 已经在 declarative
    agent / tools whitelist 上做大体量决策，加 UX scope 会破
    cleanup-sized phase 形态），单列 Phase 21 候选。Phase 21 可能
    拆两份提交：(10a) resolver-level alias、(10b) REPL substrate
    替换 + tab completion + multi-line paste

这些 deferred 项待真有 driver 时各自独立 phase 处理。Phase 19 收尾
应该产生一个干净 baseline：CCPluginLoader 透明集成进 Phase 9
PluginLoader 框架 + `engine/slash_skill.py` 零 diff 兑现作为 M3 的
稳定地基。
