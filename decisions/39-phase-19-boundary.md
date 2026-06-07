# Decision 39 — Phase 19 Boundary (CCPluginLoader · M2 of CC Skill 接入)

> Created 2026-06-06 · 中文
>
> 配套读物：
> - [`38-phase-18-boundary.md`](./38-phase-18-boundary.md) — M1 (G2)
>   slash-skill triggering
> - [`learnings/phase-18.md`](../learnings/phase-18.md) §3 — M2
>   predictions（`synthesize_skill_envelope` 零 diff，
>   `engine.slash_skill` 提前加进 forbidden imports list）
> - [`decisions/24`](./24) Phase 9 PluginLoader — 上游契约，本 phase
>   扩展点
> - finance-skills 仓 `mybank-credit-risk/plugins/credit-report-reviewer/`
>   — dogfood fixture（CC plugin shape 实物）

---

## 〇、Why this doc

Phase 18 关掉了 G2（slash trigger）。现在用户用 finance-skills 的
`parse-credit-report` 体验是：

```bash
cp /path/to/finance-skills/mybank-credit-risk/plugins/credit-report-reviewer/\
skills/parse-credit-report/SKILL.md \
   ~/.openharness/skills/parse-credit-report.md
```

每装一个 CC plugin 都要 cp + 改路径 + 重复 N 次（finance-skills 一个
plugin 自带 4 个 skill）。Phase 19 = M2 = **CCPluginLoader** = G1 收口，
让用户改为：

```bash
cp -r /path/to/finance-skills/mybank-credit-risk/plugins/credit-report-reviewer \
      ~/.openharness/plugins/credit-report-reviewer
```

一次 cp，整个 plugin 含 4 个 skill + 自带的 `.mcp.json` 立刻可用，
`/credit-report-reviewer__parse-credit-report` 立即触发。

### G1 具体由 3 个子 gap 组成（M2 scope）

| Gap | CC 格式 | OH 现状 | M2 怎么补 |
|---|---|---|---|
| G1.1 manifest | `.claude-plugin/plugin.json` (JSON, 4 字段: name/version/description/author) | `manifest.yaml` (YAML, name/version/description/components/hooks/mcp_servers) | loader 扩展：检测 `.claude-plugin/plugin.json` → 走 CC 解析；不存在则回退 `manifest.yaml` |
| G1.2 skill 文件位置 | `skills/<n>/SKILL.md` (目录形态 + 文件名固定) | `<n>.md` (平铺 + 文件名 = skill 名) | loader 扫描 plugin 目录的 `skills/*/SKILL.md`，合成 `PluginManifest.skills` 列表 |
| G1.3 内嵌 MCP | `.mcp.json` (`{"mcpServers": {...}}` 标准 MCP schema) | `manifest.yaml` 顶级 `mcp_servers:` | ~~loader 读 `.mcp.json` 注入 `PluginManifest.mcp_servers`，schema 已等价~~ **REVERSED by D39.9** — `.mcp.json` 退出 M2 scope（OH MCP layer 是 stdio-only，CC `.mcp.json` 全部 HTTP+OAuth2，transport 不匹配）。**M2 静默忽略 `.mcp.json`**；HTTP MCP transport 扩展归独立 phase |

**M3 (Phase 20) 范围内（本 phase 不做）**：`agents/<n>.md`
declarative sub-agent（含 `tools:` 白名单 — 跟 OH permission Tier 映射
需另一个 §六 audit）。

### G1.4 / G1.5 / G1.6 不在 M2 范围（deferred）

- G1.4 — `marketplace.json`（plugin 集合元数据，多 plugin fan-out 入口）
- G1.5 — `oh plugins list` 子命令实装（当前 cli.py 提到但未实现，本
  phase 顺手补 OR deferred — 见 D39.7）
- G1.6 — CC partner-plugin matrix（spglobal / lseg 那种 vendor 分发
  形态）

### 工作量预估

- src: +120 LoC（plugins/model.py + plugins/loader.py 扩展；不新增模块）
- tests: +200 LoC（CC manifest 解析 + skills 目录扫描 + .mcp.json
  + 双格式同 plugin 名冲突）
- 0.5 day calendar

---

## 一、Capability scope

**新增能力**：

- `~/.openharness/plugins/<plugin-name>/` **同时识别** 两种 plugin 形态：
  - **CC 形态**：含 `.claude-plugin/plugin.json` + 可选 `skills/<n>/SKILL.md`
    目录树。~~+ 可选 `.mcp.json`~~ **D39.9 撤回** — `.mcp.json`
    M2 静默忽略
  - **OH 形态**：含 `manifest.yaml`（Phase 9 既有契约）
- CC plugin 的 skill 经 `oh chat` `/<plugin-name>__<skill-name>`
  触发，复用 Phase 18 的 synth envelope helper（**helper 本身零改动**，
  Phase 18 retro §3 prediction）
- `oh plugins list` 输出包含 `source format (cc|oh)` 标识 — 仅在 D39.7
  ratify "ship now" 时生效
- 观测：`plugin_discovered` INFO 事件新增 `format=cc|oh` 字段

**影响文件**：

- `plugins/loader.py` — `PluginLoader.discover` 增加 CC 形态识别 +
  scaffolding 合成 `PluginManifest`（~50 LoC）
- `plugins/model.py` — 新增 `parse_cc_plugin_json(path)` +
  `_scan_cc_skills_dir(path)` + `_parse_mcp_json(path)`（~70 LoC）
- `observability/logging.py` — `plugin_discovered` 事件 payload 加
  `format` 字段（~2 LoC）
- 测试新增 `tests/plugins/test_cc_loader.py` + `tests/plugins/test_cc_model.py`

**保留 — 完全不动**：

- `engine/slash_skill.py` — Phase 18 retro §3 高置信预测：M2 应**零
  diff**。T1 加 `openharness.plugins` 到 architecture isolation 测试的
  forbidden list，防止反向 import 偷偷溜进来（**proactive guard**）
- `skills/store.py` / `skills/model.py` — CC skill 解析完成后通过既有
  `parse_skill` 同一路径进 `LayeredStore`，下游全透明
- `commands/expand.py` / `commands/store.py` — CC plugin.json **没有
  commands 字段**；M2 不合成、不模拟。CommandStore 走 Phase 5b 路径不变
- `bundles/` — 同理，CC 无 bundles 概念
- `hooks/` — CC plugin **没有** OH 那种 Python-hook 引用机制（CC 的
  hook 是 settings.json 配的 shell command，跟 OH HookSpec 不映射；本
  phase 不引入）
- `permissions/` — 无 tier 映射工作（M3 才需要）
- Phase 5b/c/d/16/17/18 全部契约不动
- OH `manifest.yaml` 格式继续支持，**不替换**，两种格式并行
- `~/.openharness/plugins/<n>/` 发现根目录不变（D39.3 ratified）

**不在 phase 范围（M3 / 后续 phase）**：

- ❌ CC `.mcp.json` 解析（D39.9 反转 D39.5 — OH MCP layer stdio-only
  vs CC HTTP+OAuth2，独立 phase 处理）
- ❌ CC `agents/<n>.md` declarative sub-agent（M3 / Phase 20）
- ❌ CC `agents:` 中 `tools:` 白名单到 OH permission Tier 的映射
- ❌ CC `marketplace.json` 多 plugin 聚合（fan-out 入口）
- ❌ CC partner-plugin 分发矩阵（spglobal / lseg）
- ❌ `~/.claude/plugins/<n>/` 双根目录扫描（D39.3）
- ❌ `oh ask "/<skill>"` 单 turn 支持（仍 D38.6 deferred）
- ❌ `{args}` substitution into skill body（D38.3 仍 deferred）
- ❌ Phase 18 §2 `/skills` 多行 description 渲染 polish（独立 UX phase，
  D39.5 ratify "merge or split"）
- ❌ 新 system_prompt section
- ❌ 新 permission Tier 或 Tier 重映射
- ❌ 任何 settings.json schema 改动
- ❌ 任何新 typer flag（外部 CLI surface 不变）

---

## 二、决策 D39.1–D39.8（待 ratify）

### D39.1 — 单 PluginLoader 双格式检测（不引入 CCPluginLoader 类）

**Recommended**：扩展现有 `PluginLoader.discover()`，按文件存在性
路由：

```
for entry in <plugins_dir>.iterdir():
    if (entry / ".claude-plugin" / "plugin.json").is_file():
        manifest = parse_cc_plugin(entry)        # 新分支
    elif (entry / "manifest.yaml").is_file():
        manifest = parse_manifest(entry / "manifest.yaml")  # 原 Phase 9 路径
    else:
        continue  # silent skip — D39.4
    # 两条路径都返回 PluginManifest，下游 fan_out 不区分
```

**Rationale**：
- CC 与 OH 都被翻译成同一 `PluginManifest` dataclass — 下游
  `fan_out`、`oh plugins list`、observability 全部不需要改
- 不引入 `enable_cc_plugins` 类的 settings flag — 用户行为本来就是
  "把 plugin 放进 plugins/ 目录"，格式探测应在那一层透明完成
- 一个 plugin 同时有 `.claude-plugin/plugin.json` + `manifest.yaml`
  时：**CC 优先，OH 跳过**（plugin 作者一旦标了 CC，就以 CC 为权威；
  并发存在意味着混用，warn 一行；见 D39.6）

**Alternatives 不选**：
- (a) 新增 `CCPluginLoader` 类 + `enable_cc_plugins` flag：双 loader
  → 双 discovery 路径 → 命名冲突需新规则 → 不必要复杂
- (b) 把 CC plugin 转译成 OH `manifest.yaml` 写盘：副作用 + 单向不可
  逆 + 跟 D38.5 "UI action vs LLM action" 精神冲突（用户期望 read-only）

### D39.2 — 复用 `PluginManifest` dataclass（不引入 `CCPluginManifest`）

**Recommended**：CC plugin 解析后构造同一个 `PluginManifest`：

```python
PluginManifest(
    name=plugin_json["name"],
    version=plugin_json["version"],
    description=plugin_json["description"],
    source_path=<plugin-dir>,
    author=plugin_json.get("author", {}).get("name"),
    license=None,        # CC plugin.json 没字段
    homepage=None,
    keywords=(),
    openharness_version_min=None,
    dependencies=(),
    commands=(),         # CC 没 commands 概念
    skills=tuple(        # 扫描 <plugin>/skills/*/SKILL.md
        ComponentRef(file=f"skills/{name}/SKILL.md")
        for name in <discovered>
    ),
    bundles=(),
    hooks=(),
    mcp_servers=tuple(   # 从 <plugin>/.mcp.json 读
        _parse_mcp_json(<plugin>/".mcp.json")
        if (<plugin>/".mcp.json").is_file() else ()
    ),
)
```

**Rationale**：
- 下游所有消费者（`fan_out`，`oh plugins list`，observability）已经
  按 `PluginManifest` 写好；引入 `CCPluginManifest` 等于翻倍下游代码
  路径
- CC plugin 比 OH 少的字段（license/homepage/keywords/dependencies/
  commands/bundles/hooks）天然映射到 None / 空 tuple — dataclass
  默认值已支持
- `__post_init__` 的 name/version/description 三个 invariant 对 CC
  plugin 同样适用（plugin.json 都要求这三字段）

**Alternatives 不选**：
- (a) 新 `CCPluginManifest` 类 + `Union[PluginManifest, CCPluginManifest]`
  到处分发：信息密度不增，代码量翻倍
- (b) 在 `PluginManifest` 加 `source_format: Literal["cc","oh"]` 字段：
  observability 需要这个区分 → 但 D39.8 改成 INFO 事件 payload 字段
  即可，dataclass 不必扩

### D39.3 — Discovery 根目录不变（`~/.openharness/plugins/`，**不**加 `~/.claude/plugins/`）

**Recommended**：M2 期间 plugin 发现根仍是
`~/.openharness/plugins/<n>/`（user-global）和 `.openharness/plugins/<n>/`
（project-local），与 Phase 9 D24.5 完全一致。CC 用户**手动 cp**
plugin dir 进去。

**Rationale**：
- D38.7 的同款哲学：M1 期间 SKILL.md 也是手动 cp 进 `skills/` —
  forcing function 是"用户 cp 一次后立刻体验回路通畅"，证明 G1+G2
  整体跑通；不主动扫 `~/.claude/plugins/` 是因为：
  - CC 的发现机制是 `~/.claude/plugins/` + settings.json marketplace
    交叉，OH 此 phase 不引入 marketplace 概念
  - 用户对"OH 在读哪些目录"有自主权 — silently 扫一个外部工具的目录
    会成 surprise
  - cp 命令一行写得清楚 + 可逆 + 与 D38.7 节奏一致
- 真有用户需求"OH 直接读 CC 目录"，未来加 `Settings.plugin_dirs:
  list[Path] = [...]` 即可，零阻断的渐进式扩展

**Alternatives 不选**：
- (a) 双根扫描 `~/.claude/plugins/` + `~/.openharness/plugins/`：用户
  从 CC 切到 OH 的最简体验 — 但破"OH 读写自己的目录"心智模型，且
  CC 目录约定（含 marketplace.json 多 plugin）M2 不实装，扫了也不
  完整支持
- (b) Settings `plugin_dirs` 列表：M2 不需要新增 settings — 推迟到真
  有 driver 时

### D39.4 — Plugin dir 缺 manifest = silent skip（与 Phase 9 行为一致）

**Recommended**：`~/.openharness/plugins/<dir>/` 既没
`.claude-plugin/plugin.json` 也没 `manifest.yaml` → 静默跳过（已是
Phase 9 行为）。

**Rationale**：
- 用户的 plugins 目录可能 cp 进了无关内容（草稿、README、`.git`）；
  warning spam 没有意义
- 真出问题（写错 plugin 但目录不被发现），D39.7 的 `oh plugins list`
  会立即暴露 — 不是 silent failure
- 与 OH `manifest.yaml` 缺失行为一致 — 一条 fault tolerance 规则
  覆盖两种格式

**Anti-scope**：本 phase **不**加 `<dir>/SKILL.md` 单 skill 形态识别
（CC 顶级 SKILL.md 不被认作 plugin — 用户继续走 D38.7 单文件路径放
`~/.openharness/skills/<n>.md`）

### D39.5 — ~~`.mcp.json` 在 M2 中**包含**~~ — **REVERSED by D39.9** (2026-06-07)

> ⚠️ **此决策已于 T1.1 实施开始前被撤回。** 原始 Recommended 文本保留
> 作为方法论审计记录（"我们考虑过、ratify 过、然后在实施前发现 schema
> 假设错误而撤回"）。新生效决策见 **D39.9 — `.mcp.json` 退出 M2 scope**。

**Recommended（已撤回）**：M2 解析 `<plugin>/.mcp.json` 并合成
`PluginManifest.mcp_servers`。Schema 等价 — `{"mcpServers": {server_id:
{...}}}` 直接映射到 OH 既有 `McpServerConfig` dataclass。

**Rationale（已撤回）**：
- finance-skills 实物里 `credit-bureau-connectors` plugin 就是
  `.mcp.json` only（4 个字段 plugin.json + 3 MCP servers）；不支持等
  于 M2 dogfood 范围只能覆盖一半实际 plugin 形态
- `_parse_mcp_servers` 已存在（Phase 9）— 仅多一个 JSON → dict 翻译
  helper，~20 LoC，scope 增量小
- 推迟到 M2.5 等于多一次 ratify ceremony — 单一 phase 拿掉就好

**为什么撤回（D39.9 详述）**：boundary doc 起草时未审 OH MCP 层实际
transport 覆盖。OH 的 `McpServerConfig` + `McpClientPool` 严格 stdio-only
（D15.1, Phase 5）；finance-skills `.mcp.json` 全用 HTTP + OAuth2。
schema **不**等价 — 是不同 transport。强行映射要么需要扩展 OH MCP 层
（独立 phase 的体量），要么需要 partial-skip HTTP 条目（破 "schema 等价"
本意）。

### D39.6 — 双 manifest 共存 = CC 优先 + warn

**Recommended**：`<plugin-dir>/` 同时有 `.claude-plugin/plugin.json`
AND `manifest.yaml` →
- 取 CC 作为权威
- emit WARN 事件 `plugin_dual_manifest`，payload: `plugin_dir`,
  `picked: "cc"`, `ignored: "manifest.yaml"`
- OH manifest.yaml 整体跳过

**Rationale**：
- 用户写出双 manifest 多半是迁移过程中状态（CC plugin import 半路），
  WARN 而不是 ERROR — 让用户看到+决定要不要清理
- CC 优先是因为：CC 是新格式 + plugin 作者一旦提交 CC，意味着 CC
  是 source of truth
- 不允许两份"事实"同时为真 — 那是导致 D24 plugin conflict error 的
  同款问题

**Anti-scope**：本 phase **不**做 "合并两份 manifest 字段" — 那是
mojibake 来源

### D39.7 — `oh plugins list` 子命令实装（in scope, ship）

**Recommended**：M2 顺手实装 `oh plugins list`（cli.py 现有注释已经
提到但未落地）：

```
$ oh plugins list
NAME                          FORMAT  VERSION  SKILLS  MCP_SERVERS
credit-report-reviewer        cc      0.1.0    4       0
credit-bureau-connectors      cc      0.1.0    0       3
my-old-plugin                 oh      1.0.0    2       1
```

输出格式：text default + `--format json` for jq pipelines（沿 Phase
14 `oh memory list` 的形态）。

**Rationale**：
- M2 的 dogfood 验证需要"用户怎么确认 plugin 被发现了"的入口 — 不能
  靠 `ls ~/.openharness/plugins/` 倒推
- D39.8 的 INFO 事件是 log-only，普通用户不会开 `--log-level INFO`
- ~30 LoC（typer command + format helper）

**Alternatives**:
- (a) 推迟到独立 phase：scope creep 概率 — `oh plugins list` 是
  M1/M2 都缺的 introspection 入口，作为方法论缺口而非具体需求被
  defer，未来很难"自然" surface
- (b) 极简 text only，无 `--format json`：可接受，但 Phase 14 已经
  统一了 `--format` 模式 — 偏离会破一致性

**Anti-scope**：本 phase **不**实装 `oh plugins show <name>`、
`oh plugins enable/disable`、`oh plugins refresh`。仅 list。

### D39.9 — `.mcp.json` 退出 M2 scope（撤回 D39.5，2026-06-07 T1.1 开始前）

**Chosen**：M2 **不**解析 `.mcp.json`。`parse_cc_plugin` 始终返回
`PluginManifest.mcp_servers=()`。当 plugin 目录含 `.mcp.json` 时：

- M2 **静默忽略**（不 emit WARN — 类比 D39.4 plugin dir 缺 manifest =
  silent skip 哲学：用户的 plugin 目录本来就可能有 M2 不识别的副产物）
- `oh plugins list` 的 `MCP_SERVERS` 列对该 plugin 显示 `0`
- 用户若需 HTTP MCP server，仍可走 OH 既有 `OPENHARNESS_MCP_SERVERS_*`
  env 配置（stdio-only），或等 `.mcp.json` HTTP 支持单独 phase 落地

**Rationale**（撤回根因 + 处理路径）：

1. **撤回根因**：D39.5 起草时声称 ".mcp.json schema 等价于 OH
   `mcp_servers:`"。T1.1 开始前 grep `src/openharness/mcp/` 才发现：
   - OH `McpServerConfig.__post_init__` 严格要求 `command` 非空 tuple
     → HTTP `.mcp.json` 条目（`{type: http, url, auth}`，无 command）
     直接 `ValueError`
   - OH `McpClientPool` 唯一 transport 路径是
     `mcp.client.stdio.stdio_client`（client.py:50/132）
   - D15.1 (Phase 5) 明文锁死 stdio-only 范围
   - finance-skills `credit-bureau-connectors/.mcp.json` 3 个 server
     全部 HTTP + OAuth2 — 与 OH 当前 transport 覆盖完全不交

2. **D39 §六 audit 漏审**：原表把 mcp 列为 `unchanged` —— 实际是
   "M2 *尝试* 扩展，但 transport 不匹配会硬碰 `McpServerConfig` 的
   contract"。这是 boundary-doc-time 的具体缺失：审 layer 时只看了
   是否有 import 路径，没看 transport 覆盖矩阵。教训记入 retro §2。

3. **为什么 silent ignore 而不是 partial parse + WARN**：
   - WARN spam 对每个有 `.mcp.json` 的 plugin 都发一次，包括以后
     M3 / M4 引入 HTTP 支持后还没升级的旧 OH 版本 — 不合 D39.4 哲学
   - 用户的预期是"M2 不识别 .mcp.json"（M2 boundary doc 这么写了）；
     silent 是 honest，partial 是 misleading
   - 真正想用 HTTP MCP 的用户会在 `oh plugins list` 看到 0 servers
     + 决定走 env 配置或等后续 phase

**M2 dogfood 范围变化**：

- ✅ `credit-report-reviewer` plugin（4 个 skill，0 个 MCP）— 完整跑通
  T4 dogfood
- ⚠️ `credit-bureau-connectors` plugin（0 个 skill，3 个 HTTP MCP）—
  **不**进 T4 dogfood：plugin 本身会被 discover（plugin.json 解析成功），
  `oh plugins list` 显示 `MCP_SERVERS=0`；这是 honest reporting，但不
  是端到端 .mcp.json 验证。dogfood retro §1 显式标注此 plugin 的
  partial loading 状态

**Anti-scope（D39.9 加严）**：
- 本 phase **不**加 `_parse_mcp_json` helper
- 本 phase **不**扩 `McpServerConfig` 加 HTTP type
- 本 phase **不**改 `McpClientPool` / `client.py`
- 本 phase **不** emit `mcp_http_transport_unsupported` 类型 WARN

**未来 phase**（deferred）：
- 独立 phase（提案 D40 / Phase 20+）扩展 OH MCP 层支持 HTTP + OAuth2
  + 等价 transport；该 phase 完成后才有意义再加 `_parse_mcp_json`
- Open frontier 第 9 项（`.mcp.json` 处理）以此 D39.9 reversal 为
  reference 锚定

---

### D39.8 — Observability：`plugin_discovered` 加 `format` 字段（不引入新事件）

**Recommended**：复用 Phase 9 既有事件（如不存在则在 T1 同步加），
payload 增加 `format: "cc" | "oh"`：

```json
{"event": "plugin_discovered",
 "plugin_name": "credit-report-reviewer",
 "version": "0.1.0",
 "format": "cc",
 "source_path": "/Users/.../credit-report-reviewer",
 "skills_count": 4,
 "mcp_servers_count": 0,
 "level": "info"}
```

D39.6 的双 manifest 则用单独 `plugin_dual_manifest` WARN 事件。

**Rationale**：
- `plugin_discovered` 的语义跟 plugin format 正交 — 一个事件 + 一
  field 比两个事件更紧凑
- D39.6 是异常情况，单独事件好抓 + payload schema 不与正常路径混
- 跟 D38.5 `slash_skill_invoked` 的 `synthetic: true` 字段同款思路：
  forcing function = 把"什么路径"用 payload 字段固化下来，便于审计

---

## 三、Acceptance criteria

Phase 19 GA 需要满足：

### 3.1 Dogfood validation：finance-skills 整个 plugin 跑通

```bash
# 准备
rm ~/.openharness/skills/parse-credit-report.md  # 清掉 Phase 18 M1 dogfood 单文件
cp -r /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-report-reviewer ~/.openharness/plugins/credit-report-reviewer
# 可选 — cp credit-bureau-connectors 验证 D39.9 静默忽略 .mcp.json：
cp -r /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-bureau-connectors ~/.openharness/plugins/credit-bureau-connectors

# 验证
oh plugins list
# 期望输出（D39.9 撤回 .mcp.json 解析后）：
#   credit-report-reviewer        cc  0.1.0  4 skills  0 mcp
#   credit-bureau-connectors      cc  0.1.0  0 skills  0 mcp   ← 0 mcp 是
#                                                                D39.9 honest
#                                                                reporting

oh chat
>>> /skills
# 期望 4 行（4 个 credit-report-reviewer skills + namespaced）：
#   credit-report-reviewer__parse-credit-report     解析央行...
#   credit-report-reviewer__apply-credit-rules      ...
#   credit-report-reviewer__cross-verify-application  ...
#   credit-report-reviewer__draft-credit-finding    ...

>>> /credit-report-reviewer__parse-credit-report 申请号12345
# 期望 LLM 回应与 Phase 18 dogfood 等价（同一 skill body）；
# INFO 事件 slash_skill_invoked payload 含
# skill_name="credit-report-reviewer__parse-credit-report",
# synthetic=true
```

### 3.2 全仓 regression green

- `uv run pytest -q --no-cov` 全绿（预期 +30 新 test，0 旧 test 改动）
- `engine/slash_skill.py` **零 diff**（Phase 18 retro §3 预测 +
  T1 proactive guard 兑现）
- `skills/store.py` / `skills/model.py` **零 diff**
- 新增 unit test cover：
  - D39.1 双格式检测（plugin.json 命中 / manifest.yaml 命中 / 都没有
    → silent skip / 都有 → CC 优先 + WARN）
  - D39.2 `PluginManifest` 复用：CC plugin 解析后字段映射正确（author
    嵌套 → str，license/homepage/keywords/dependencies/commands/
    bundles/hooks 默认空）
  - D39.4 silent skip 行为对 invalid plugin dir（README 文件、`.git`
    目录、空目录）
  - **D39.9 negative test**: plugin 目录含 `.mcp.json` → `parse_cc_plugin`
    返回 `mcp_servers=()`；不 emit WARN；不 import / 不依赖
    `McpServerConfig` 任何 HTTP-shape 字段。Test 同时 grep
    `plugins/model.py` 源码确认无 `.mcp.json` 字面量引用（forcing
    function — 跟 D38 §六 `compact.py` 的 `synth_` 检查同款思路）
  - D39.6 双 manifest WARN 事件 emit + ignored manifest 不进 fan_out
  - D39.7 `oh plugins list` 输出 4 columns text + `--format json`
  - D39.8 `plugin_discovered` INFO 事件 payload 含 `format`

### 3.3 §六 Wiring audit 预测落地验证

- `engine/slash_skill.py` 测试新增 forbidden import: `openharness.plugins`
  + 子模块（proactive guard per Phase 18 retro §3）
- `services/compact.py` 零 diff（Phase 18 forcing function 测试自动
  pass — CC plugin 解析过程不产生 synth 块）
- `services/snapshot|session_memory` 零 diff
- compaction L1-L4 透明性测试不需要新增 — CC plugin 不引入新 envelope
  shape

### 3.4 文档同步

- `CHANGELOG.md` 加 Phase 19 entry
- `learnings/phase-19.md` retro 完整（§1 What worked / §2 What missed /
  §3 Predictions for M3 / §4 Abstractions tested / §六 verdict mapping）
- D39 retro §3 必须回答：M3 (DeclarativeAgent) 触发路径预测是否在
  Phase 19 实施过程中得到任何"提前预演"（比如 plugin.json 的 author
  字段是否影响 M3 agents 的 author 提取）

---

## 四、Anti-scope

本 phase **不做**：

1. ❌ CC `agents/<n>.md` declarative sub-agent — M3 / Phase 20
2. ❌ CC `tools:` 白名单到 OH permission Tier 映射 — M3
3. ❌ CC `marketplace.json` 多 plugin 聚合 fan-out 入口
4. ❌ CC partner-plugin matrix（spglobal/lseg 那种 vendor 分发结构）
5. ❌ `~/.claude/plugins/<n>/` 第二发现根（D39.3 严控）
6. ❌ Settings 引入 `plugin_dirs: list[Path]` 多根机制
7. ❌ `oh ask "/<skill>"`（仍 D38.6 deferred）
8. ❌ Phase 18 §2 `/skills` 多行 description 渲染 polish（独立 phase）
9. ❌ `{args}` substitution into skill body
10. ❌ `oh plugins show / enable / disable / refresh` — 仅 `list`
11. ❌ 改 `engine/slash_skill.py`（一行 diff 都不允许 — 改 = 失败 → 回
    boundary doc 加 D39.10+）
18. ❌ **`.mcp.json` 解析任何 entry**（D39.9 撤回 D39.5；M2 静默忽略）
19. ❌ **扩 `McpServerConfig` / `McpClientPool` / `client.py`**（D39.9）
20. ❌ emit `mcp_http_transport_unsupported` 或其它 .mcp.json 相关
    WARN/INFO 事件（D39.9 选 silent ignore 而非 partial parse + WARN）
12. ❌ 改 `skills/store.py` / `skills/model.py`
13. ❌ 改 `commands/expand.py` / `commands/store.py`
14. ❌ 改 `bundles/` 任何文件
15. ❌ 引入新 dep / 改 pyproject.toml
16. ❌ 新 system_prompt section
17. ❌ 新 settings.json schema / typer flag

---

## 五、Implementation contract（informative — capability 级 plan 留 tasks/phase-19-plan.md）

**新文件**：

- `tests/plugins/test_cc_loader.py`（~120 LoC，D39.1/D39.4/D39.6 路由
  + 双格式冲突）
- `tests/plugins/test_cc_model.py`（~70 LoC，D39.2 字段映射 + D39.9
  `.mcp.json` silent-ignore negative test）
- `tests/cli/test_plugins_list.py`（~60 LoC，D39.7 子命令）

**改造文件**：

- `src/openharness/plugins/model.py`：
  - 新增 `parse_cc_plugin(plugin_dir: Path) -> PluginManifest | None`
  - 新增内部 `_scan_cc_skills_dir(plugin_dir: Path) -> tuple[ComponentRef, ...]`
  - ~~新增内部 `_parse_mcp_json(path: Path) -> tuple[McpServerConfig, ...]`~~
    **D39.9 撤回** — 不加 `_parse_mcp_json` helper；`parse_cc_plugin`
    始终设 `mcp_servers=()`
- `src/openharness/plugins/loader.py`：
  - `PluginLoader.discover()` 加双格式 dispatch（D39.1） + D39.6
    WARN
- `src/openharness/observability/logging.py`：
  - 事件 inventory 加 `plugin_discovered` (INFO) + `plugin_dual_manifest`
    (WARN) — Phase 9 如果已有 `plugin_discovered`，仅扩 payload schema
- `src/openharness/cli.py`：
  - 新增 `plugins_app = typer.Typer()` + `@plugins_app.command("list")`
    实装（~30 LoC，沿 `oh memory list` Phase 14 形态）
- `tests/engine/test_slash_skill_envelope.py`：
  - `TestArchitectureIsolation.FORBIDDEN_MODULE_PREFIXES` 加
    `"openharness.plugins"`（proactive guard，1 LoC）

**不动**：

- `engine/slash_skill.py`（FORBIDDEN to touch — Phase 18 retro
  prediction validator）
- `skills/store.py` / `skills/model.py`
- `commands/`、`bundles/`、`hooks/`、`permissions/`
- `services/compact.py` / `services/snapshot.py` / `services/session_memory.py`
- `engine/query.py`
- `prompts/`
- `memory/`
- `eval/`

---

## 六、Wiring audit

Phase 19 M2 contract 跨以下 runtime layer。每层 verdict 必须 explicit：

| Layer | Verdict | Reasoning |
|---|---|---|
| **plugins/loader** | **requires extension** | D39.1 双格式 dispatch + D39.6 dual-manifest WARN。最大变更点；本 phase 主要工作 |
| **plugins/model** | **requires extension** | D39.2 复用 `PluginManifest` + 两个新 parser helper (CC plugin.json / skills 目录扫描)。~~原有 `.mcp.json` helper~~ **D39.9 撤回** |
| **mcp/config + client** | **unchanged** (D39.9 enforced) | OH MCP layer 严格 stdio-only（D15.1 Phase 5 锁定）。CC `.mcp.json` HTTP+OAuth2 transport 不匹配 `McpServerConfig` 当前 schema；M2 静默忽略 `.mcp.json`，不读、不映射、不 WARN。HTTP transport 扩展归独立 phase |
| **observability** | **requires extension** | `plugin_discovered` payload 加 `format` 字段 + `plugin_dual_manifest` WARN 事件 |
| **CLI subcommand surface** | **requires extension** | D39.7 新增 `oh plugins list` 子命令；现有命令 + typer flags **不变** |
| **engine/slash_skill** | **unchanged** (load-bearing prediction) | Phase 18 retro §3 高置信预测 — CC plugin 经 `parse_skill` 进 SkillStore 后，slash trigger 路径**完全透明**。T1 加 `openharness.plugins` 到 forbidden imports list 作为 proactive guard |
| **skills/store + model** | **unchanged** | CC SKILL.md 经 `parse_skill` 同一入口；下游 `SkillStore.discover` 不区分来源（CC plugin / OH plugin / 单文件三路汇入） |
| **commands/expand + store** | **unchanged** | CC plugin.json 无 commands 概念；M2 不合成、不模拟。Phase 5b 路径完全保留 |
| **bundles** | **unchanged** | 同理，CC 无 bundles 概念 |
| **hooks** | **unchanged** | CC plugin 的 hook 是 settings.json 配的 shell command（跟 OH HookSpec 不映射），本 phase 不引入 |
| **permissions/tier_based** | **unchanged** | 无 Tier 映射工作（M3 才需要） |
| **services/snapshot + session_memory + compact** | **unchanged** | CC plugin 加载是 bootstrap-time 行为；不进 conversation history，不产生新 envelope shape |
| **API client** | **unchanged** | bootstrap-time，不动 |
| **memory (Phase 16/17)** | **unchanged** | 无交叉 |
| **eval substrate** | **unchanged** | 无交叉 |
| **Phase 18 SkillStore consumer (`_run_chat`)** | **unchanged** | namespaced skill name 走同一 `/<name>` resolver，slash_skill helper 透明处理 |

**Conclusion**：

- **3 `requires extension`**（plugins/loader + plugins/model +
  observability）— 都是 M2 的 core 工作面，预期内
- **1 `requires extension` 来自 CLI**（D39.7 `oh plugins list` 子命令）
  — 可拆为 D39.7 ratify "ship now or defer"
- **11 unchanged** — 大多数 OH 内部模块对 CC plugin loader **完全不
  知情**
- **0 `requires bypass`** — 与 Phase 18 完全不同（M1 因 D38.5 UI vs LLM
  action 区分故意 bypass 2 层；M2 是单纯 bootstrap-time 数据翻译，
  无 bypass 必要）
- **0 `requires verification`** — Phase 18 T3 已把 synth envelope
  → compaction L0-L4 关系锁死 + forcing function 自动 guard，本
  phase 不引入新 envelope shape，无 verification work

按 CLAUDE.md 规则："≥ 3 requires extension 或多个 bypass = 重新 ratify
scope"。本 phase = 3 (or 4 with D39.7) extension，**全部位于 plugins/
+ observability + 一个新 CLI 子命令**（同一概念簇 — "plugin loader
扩展 + introspection 入口"），不是分散 leak。**符合 cleanup-sized
phase 的 wiring 形态**。

如果实施中发现 `parse_skill` 需要适配 CC SKILL.md（比如 Phase 17 T1
没考虑到的 frontmatter 边缘情况）—— 立即回 boundary doc 加 D39.9 处
理；不允许在 `parse_skill` 加 `if path.parent.name == "skills":` 类
特判。

---

## 七、References

- [Phase 18 boundary](./38-phase-18-boundary.md) — M1 (G2) 完整契约
- [Phase 18 retro](../learnings/phase-18.md) — §3 M2 predictions
  (`engine.slash_skill` 零 diff)
- [Phase 17 boundary](./37-phase-17-boundary.md) — `parse_skill`
  接受 CC frontmatter (D37.1/D37.2) 的上游契约（M2 直接复用）
- [`decisions/24`](./24) — Phase 9 plugin loader 原始契约，本 phase
  的扩展点
- finance-skills 仓 `mybank-credit-risk/plugins/credit-report-reviewer/`
  — CC plugin shape 实物 dogfood fixture
- finance-skills 仓 `mybank-credit-risk/plugins/credit-bureau-connectors/`
  — `.mcp.json`-only plugin dogfood fixture（D39.5 验证用）
- finance-skills 仓 `mybank-credit-risk/.claude-plugin/marketplace.json`
  — marketplace 概念展示（M2 不实装，仅 reference）
- `src/openharness/plugins/loader.py:280` — `PluginLoader` 扩展入口点
- `src/openharness/plugins/model.py:143` — `PluginManifest` dataclass
  复用对象
- [[feedback-design-for-strong-model]] — D39.7 `oh plugins list`
  ship-now 抉择背书（不预期"用户能 grep 出来" 弱模型化路径）
