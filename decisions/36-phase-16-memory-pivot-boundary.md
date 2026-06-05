# Decision 36 — Phase 16 Memory Architecture Pivot (Boundary)

> Created 2026-06-05 · 中文
>
> 配套读物：
> - 第一性原理推导：[`docs/ideas/memory-first-principles.md`](../docs/ideas/memory-first-principles.md)
> - Eval 决策面 map：[`35-eval-coverage-map.md`](./35-eval-coverage-map.md)（本 phase 属 P0 = 决策面 #4 inline 决策）
> - Phase 10 memory 起源：[`25-phase-10-boundary.md`](./25-phase-10-boundary.md)
> - Phase 11 extraction 起源：[`26-phase-11-boundary.md`](./26-phase-11-boundary.md)
> - Spike：[`scripts/spike_memory_capability.py`](../scripts/spike_memory_capability.py)

---

## 〇、Why this doc

`oh` Phase 11 给 memory 加了写入路径（`extract_memories_from_turn` secondary
LLM pass，每 turn 机械触发），是当时基于 qwen-plus 弱主 LLM 的合理工作
around。

2026-06-05 用户经过完整推理（[`docs/ideas/memory-first-principles.md`](../docs/ideas/memory-first-principles.md)
§10）明确：**模型会越来越强，harness 是底层，模型不行就换更好的，
不要降 contract**（[[feedback-design-for-strong-model]]）。

按这条哲学，Phase 16 把 memory 架构 pivot 到 **Claude Code 模型**——
6 个 fork point 全部 follow CC，不为当前 model 弱留 fallback。本 boundary
doc 把这个 pivot 的不变量、改造范围、acceptance criteria、anti-scope
固化下来。

这是 `oh` 历史上**第一次**显式按 "design-for-strong-model" 哲学做架构
决策——之前每次 phase 都是"今天 model 能做什么 / 我们设计什么 hold 住"，
本 phase 是"contract 设成什么 / model 要够到"。这两个方向相反，记录下来。

---

## 一、Capability scope

本 phase 影响 `oh` 的 memory 子系统：

**Pivot 的能力**（被改造）：
- **何时写 memory** — 从 Phase 11 extraction（机械每 turn + secondary LLM 判断）
  改成 inline 主 LLM 自决
- **如何让 LLM 知道有什么 memory** — 从无索引改成 MEMORY.md 索引 + session-start 注入
- **谁决定读哪条 memory** — 从 [`memory/relevance.py`](../src/openharness/memory/relevance.py)
  harness 侧 ranking 改成 LLM 自选

**保留的能力**（不动）：
- [`FilesystemMemoryStore.add_or_update`](../src/openharness/memory/store.py) — 仍是 atomic write 底层
- Per-project scope via sha1（Fork 6 已一致）
- Frontmatter schema（`name` / `description` / `metadata.type`）
- Memory 类型枚举（`user` / `feedback` / `project` / `reference`）
- Team-scope 子目录（[`team/`](../src/openharness/memory/team.py)）—— 见 D36.13

**不在本 phase 范围**（推迟到未来）：
- Memory 冲突解决机制
- Auto-GC / 老化
- 多 session 并发写
- Plugin / skill 触发 memory 写入

---

## 二、决策 D36.1-D36.14

### D36.1 — Fork 1 storage shape：多文件 + 索引（follow CC）

**Chosen**：保留 `<memory_dir>/*.md` 多文件结构（Phase 10 已是），**新增** `<memory_dir>/MEMORY.md` 作为索引文件。

**Rationale**：单文件方案不允许独立 atomic 更新单条 memory。多文件 + 索引让 "一条 memory = atomic 实体" 成立，可以独立 link、deprecate、refactor。详见 [memory-first-principles §三](../docs/ideas/memory-first-principles.md#三fork-point-1存储形态--单文件-vs-多文件)。

**Action**：无 schema 变化（Phase 10 已是多文件）；新增 MEMORY.md 落地见 D36.10。

### D36.2 — Fork 2 索引语义：context 注入点（不是数据库）

**Chosen**：`MEMORY.md` 是**上下文注入点**，不是查询索引。session 启动时 harness 把 MEMORY.md 文本注入 system prompt，让 LLM 看到 "有什么 memory 存在 + 一句话 hook"，**不做相关性 ranking、不做查询匹配**。

**Rationale**：LLM 不会主动查询数据库。"它知道什么 memory 存在" 必须在它的视野里。索引 = LLM 视野。详见 [memory-first-principles §四](../docs/ideas/memory-first-principles.md#四fork-point-2索引是数据库-vs-上下文注入点)。

**这一条排除了 glob discovery 作为 LLM-visible 接口** —— `FilesystemMemoryStore.discover()` 仍存在但仅 harness 内部用，LLM 视角看不到。

### D36.3 — Fork 3 写入触发：inline 主 LLM 自决（follow CC）

**Chosen**：memory 写入决策**完全由主对话 LLM 在主 turn 中自决**。不机械触发，不开 secondary LLM pass。主 LLM 看上下文 → 决定该写 → inline 调用 `Write` 工具落盘 + `Edit` 工具更新 MEMORY.md。

**Rationale**：判断"什么值得记"是高度上下文敏感的，只有主对话 LLM 当下有完整上下文。详见 [memory-first-principles §五](../docs/ideas/memory-first-principles.md#五fork-point-3写入触发--何时决定写-memory)。

**Trade-off explicitly accepted**：完全押注主 LLM 判断质量。当主 LLM 弱 → 漂；当主 LLM 强 → 几乎不漂。按 [[feedback-design-for-strong-model]] 哲学，这个押注**有意接受**。

**Action**：见 D36.9 — Phase 11 extraction deprecate。

### D36.4 — Fork 4 读取触发：索引启动注入 + body 按需 Read（follow CC）

**Chosen**：
1. session 启动时 harness 把 MEMORY.md 全文注入 system prompt（subject to D36.8 200 行 cap）
2. LLM 看到相关条目后自己调 `Read` 工具拿 body
3. **harness 不做 relevance ranking、不做 top-K 选择、不预加载 body**

**Rationale**：索引 token 成本固定且小（~几百 token）；body 按需。冷成本恒定 + 热成本按相关性付。详见 [memory-first-principles §六](../docs/ideas/memory-first-principles.md#六fork-point-4读取触发--何时载入-memory)。

**Action**：见 D36.7 — `memory/relevance.py` deprecate。

### D36.5 — Fork 5 Linking：`[[slug]]` 引用（follow CC）

**Chosen**：memory body 中允许 `[[name]]` 引用，name = 另一条 memory 的 frontmatter `name:` slug。harness 不做 link 解析（LLM 自己看到 link、自己决定要不要 Read）。允许指向尚未存在的 memory（forward reference 不报错）。

**Rationale**：显式数据库式查询对 LLM 不友好；markdown link 语法是 LLM 原生友好的。Forward reference 允许 memory 网络渐进生长。详见 [memory-first-principles §七](../docs/ideas/memory-first-principles.md#七fork-point-5linking-形态)。

**Action**：在系统提示 memory 写作规则中加入 `[[slug]]` 范例。无 harness 解析代码。

### D36.6 — Fork 6 Scope：per-project sha1（已一致，无变化）

**Chosen**：维持 [`memory/paths.py`](../src/openharness/memory/paths.py) 的 `~/.openharness/memory/<basename>-<sha1(cwd)[:12]>/` 方案。

**Rationale**：跟 CC 的 `~/.claude/projects/<hash>/memory/` 同形态。Per-project 让 memory 池保持高相关性。详见 [memory-first-principles §八](../docs/ideas/memory-first-principles.md#八fork-point-6scopeper-project-vs-user-global)。

**Action**：无变化。

### D36.7 — `memory/relevance.py` deprecate

**Chosen**：[`memory/relevance.py`](../src/openharness/memory/relevance.py) 的 `select_relevant_memories` + 相关 ranking 逻辑 **deprecate**：
- 代码保留（暂不删），加 `# DEPRECATED: Phase 16 — LLM does selection via MEMORY.md index` 注释
- 移除 CLI / engine 的所有调用点
- 下一个 cleanup phase 真删

**Rationale**：D36.4 把 selection 完全交给 LLM；harness 侧 ranking 是冗余的。**保留 contract 的纯度比保留实现的方便更重要**——按 [[feedback-design-for-strong-model]] anti-scope，不留 fallback 路径。

**Alternatives 不选**：
- (a) 保留 relevance.py 做 harness 内部 ranking（不暴露给 LLM）——会变成 contract 漏点：将来有人 wire 回 LLM-visible 路径，不变量破裂
- (b) 立即删 relevance.py——增加本 phase 风险面，cleanup 应该是独立 phase

### D36.8 — MEMORY.md 200 行 cap（invariant）

**Chosen**：MEMORY.md 严格遵守 CC 同款 cap：
- **总行数 ≤ 200 行**
- **每行 ≤ 150 字符**（推荐，非强制）
- 超过 200 行时 harness 注入时 truncate 后部分，**LLM 看到 first-200**

**Rationale**：cap 是 token budget 而不是 readability 偏好——超过 cap 就不能每 session 注入了。CC 自己的 prompt 明确写出来。

**Forcing function**：harness 注入逻辑用 `splitlines()[:200]` 实现；超过 cap 在启动日志里 WARN（`memory_index_truncated`）。

**未来 hook**：cap 到了之后需要"老化"或"分卷"策略——本 phase 不做，记入 [memory-first-principles §十一](../docs/ideas/memory-first-principles.md#十一这份文档不回答的问题) 的 deferred frontier。

### D36.9 — Phase 11 extraction deprecate（flag-gated off）

**Chosen**：[`services/extract.py`](../src/openharness/services/extract.py) 的 `extract_memories_from_turn` 在 Phase 16 后：
- 代码保留（不删）
- `ExtractionSettings.enabled` 默认 `False`（之前是 `True`）
- 调用点（`engine/query.py:436` `_maybe_extract_memories`）保留但变成 no-op when disabled
- 加注释 `# DEPRECATED: Phase 16 — superseded by inline LLM memory write`

**Rationale**：[[feedback-design-for-strong-model]] anti-scope 明确禁止 mechanical trigger 兜底。但代码保留作"未来如果用户回滚 LLM 自决方案"的安全网（一行 env var flip 即可恢复）。**下一个 cleanup phase 真删**。

**关键不变量**：Phase 16 GA 后**绝不能**默认 `True` 重新开启——这会破坏 contract（两个写入路径并存会产生重复 memory + 矛盾）。

### D36.10 — System prompt memory 写作规则段（新增 contract）

**Chosen**：[`prompts/`](../src/openharness/prompts/) 系统提示新增一个 memory 写作规则段，**严格 mirror CC 当前的 prompt 内容**：

```
# auto memory

You have a persistent, file-based memory system at <memory_dir>/.
This directory already exists — write to it directly with the Write tool.

[规则段 — 类型定义 / 何时保存 / 何时不保存 / 两步保存格式 / [[link]] 语法 / MEMORY.md 200 行 cap]
```

**Rationale**：CC 的"何时写"判断**完全在 system prompt**——LLM 没有这段规则就不会按 CC pattern 行为。本 phase 必须 import 这段 contract。

**版本管理**：prompt 段作为常量在 [`prompts/memory.py`](../src/openharness/prompts/memory.py)（新建），跟 CC 当前 prompt 同步是显式 manual 决定（不 auto-pull）。每次 CC prompt 演进 → 我们决定是否 follow。

**字段约束**：禁止把项目特定细节硬编码到这段（比如不要提 `oh` / OpenHarness）。规则段需要在不同项目复用。

### D36.11 — Session-start MEMORY.md 注入（新增 contract）

**Chosen**：[`cli/_run_ask`](../src/openharness/cli.py) 和 [`cli/_run_chat`](../src/openharness/cli.py) 路径在 `build_system_prompt` 调用前：
1. 解析 cwd → memory_dir（[`memory/paths.py`](../src/openharness/memory/paths.py) `get_project_memory_dir`）
2. 读 `<memory_dir>/MEMORY.md` 若存在
3. 截 200 行
4. 拼到 system prompt 后部（D36.10 规则段之后）

**Rationale**：CC pattern 的核心是"LLM 一上来就看到索引"——这是 Fork 2 的具体实现。

**失败处理**：
- MEMORY.md 不存在 → 注入 "(MEMORY.md is empty — no memories yet)" 占位（让 LLM 知道索引机制存在，只是空的）
- Read OSError → WARN log，注入空占位（不让 session 启动失败）

### D36.12 — Tools `Write` + `Edit` 是 memory 写入路径（无新工具）

**Chosen**：Phase 16 **不引入新工具** —— memory 写入完全走现有 `Write` 和 `Edit`。LLM 写 `<memory_dir>/<slug>.md` 用 `Write`，更新 `<memory_dir>/MEMORY.md` 用 `Edit`。

**Rationale**：CC 自己就是这么做的——`Write` 写 body，`Edit` 加 index 行。引入专门 `WriteMemory` 工具会增加 surface area + 让 LLM 学一个新 tool name，没好处。

**Trade-off**：LLM 可能误用 `Write` overwriting MEMORY.md（spike S5 warm 暴露过）——靠 D36.10 系统提示规则段约束（"use Edit for MEMORY.md, never Write"）。这是 prompt-level 约束，不是 harness-level 防御——按 design-for-strong-model 接受。

### D36.13 — Team-scope 子目录的归宿

**Chosen**：Phase 11 D29.10 引入的 `<memory_dir>/team/` 子目录在 Phase 16 **保留**：
- LLM 写 team 类型 memory 时仍落入 `team/`
- MEMORY.md 索引扁平包含 team memory（路径 `team/<slug>.md`）
- Session 启动只注入 root 的 MEMORY.md（team memory 通过该索引可见）

**Rationale**：Team-scope 不属于 CC（CC 是单用户工具），但 `oh` 已有这个概念。**deprecate 它会破坏 Phase 11 D29.10 既有 user**。保留不影响 Fork 1-6 不变量。

**未来 hook**：若 team-scope 真的有 user → 重新评估"是否需要单独的 team-MEMORY.md"。本 phase 单 MEMORY.md 涵盖。

### D36.14 — `[[slug]]` 解析不做（仅 prompt 语法）

**Chosen**：D36.5 引入 `[[slug]]` 但 **harness 不解析**——纯 prompt 层约定。LLM 看到 `[[foo-bar]]` 自己决定要不要 Read `foo-bar.md`。

**Rationale**：Harness 介入解析会变成"harness 替 LLM 决定 link 跟不跟"——回到 D36.4 拒绝的 ranking 角色。CC 也不解析。

---

## 三、Acceptance criteria

Phase 16 GA 需要满足：

### 3.1 Gating eval（[`35-eval-coverage-map.md`](./35-eval-coverage-map.md) D35.5 P0）

新建 `evals/memory_decision/` consumer：
- **Capability claim**：在决策面 #4（inline 决策）上验证 LLM 能按 CC pattern 完成 (judge → Write `.md` → Edit MEMORY.md) 链
- **Input spec**：≥ 6 个 scenarios：preference / correction / project state / reference / trivial (skip) / warm-start with pre-populated MEMORY.md content injected
- **Judgment spec**：4-state outcome（PASS / PARTIAL-warm-chain-incomplete / FAIL-drift / FAIL-overwrite），LLM-judge for type 分类语义，programmatic check for index 完整性

**Pass bar**：
- qwen3.7-max 上 warm-start scenarios ≥ **4/5 PASS**（参考 CC 在 Opus 估 4-5/5）
- 不通过 → 用 Claude Sonnet 跑同一 dataset 验证 contract 在更强 model 上 hold
- 都不通过 → contract 真有问题，回到 [memory-first-principles](../docs/ideas/memory-first-principles.md) 重新推

### 3.2 既有测试零回归

- `tests/memory/` 全套通过（Phase 10 + 11 既有 198 个 test，除明确为 extraction 标 deprecated 的）
- `tests/cli/test_memory_subcommands.py` 通过（`oh memory list` 等 CLI 子命令保留）

### 3.3 系统提示 byte-identical 验证

- [`tests/prompts/test_byte_identical.py`](../tests/prompts/test_byte_identical.py) 风格的新 test：固定 cwd + 固定 MEMORY.md 内容 → 生成的 system prompt 字节稳定
- 防止后续 prompt 段改动悄悄漂

### 3.4 Dogfood pass

- 用本 phase 实现做至少 1 个 real session（typical 长度 ≥ 5 turns），观察：
  - 主 LLM 是否主动写出 memory（无需用户提示）
  - 写入的 frontmatter 是否合法
  - MEMORY.md 是否被正确更新（无 destructive overwrite）
- 该 session 结果写入 [`learnings/phase-16.md`](../learnings/phase-16.md) 作为 retro 起点

---

## 四、Anti-scope（按 [[feedback-design-for-strong-model]] explicit 禁止）

本 phase **不做**：

1. ❌ **不引入 glob discovery 作 LLM-visible 接口**——harness-internal 保留，LLM 视角必须走 MEMORY.md 索引
2. ❌ **不留 mechanical trigger 兜底**——Phase 11 extraction `enabled=False` 默认；不暴露 fallback 路径
3. ❌ **不加 auto-GC / 老化**——CC 不加，本 phase 保持一致
4. ❌ **不引入 memory 冲突解决机制**——延后
5. ❌ **不引入并发写锁**——延后
6. ❌ **不实现 `[[slug]]` 的 harness 端解析**——纯 prompt 语法
7. ❌ **不引入新工具**——`Write` + `Edit` 已足够
8. ❌ **不向 user-global 池 spillover memory**——per-project 不变

每一条 anti-scope 都是"如果做，就会破坏某个 fork point 的不变量"——这些是 design-for-strong-model 的具体表达：harness 不替弱模型擦屁股。

---

## 五、Implementation contract 改造范围（informative）

**新增**：
- [`src/openharness/prompts/memory.py`](../src/openharness/prompts/memory.py) — D36.10 系统提示段
- session start 注入逻辑（D36.11 — `build_system_prompt` 内或前置）
- gating eval：`evals/memory_decision/dataset.yaml` + `evals/memory_decision/dataset_card.md`

**改造**：
- [`config/settings.py`](../src/openharness/config/settings.py) — `ExtractionSettings.enabled` default `True` → `False`（D36.9）
- [`engine/query.py`](../src/openharness/engine/query.py) `_maybe_extract_memories` — 加 deprecated 注释，no-op when disabled

**deprecate（保留代码）**：
- [`memory/relevance.py`](../src/openharness/memory/relevance.py) — D36.7
- [`services/extract.py`](../src/openharness/services/extract.py) — D36.9
- 所有调用点（CLI / engine）改 no-op 或移除调用

**不动**：
- `FilesystemMemoryStore.add_or_update`
- frontmatter parser / Memory model
- Team-scope `team/` subdir handling
- per-project sha1 path resolver

---

## 六、References

- [memory-first-principles](../docs/ideas/memory-first-principles.md) — 推导出 Fork 1-6 选择的第一性原理过程
- [`35-eval-coverage-map.md`](./35-eval-coverage-map.md) D35.5 — 把本 phase 落在 P0 决策面 #4
- [`25-phase-10-boundary.md`](./25-phase-10-boundary.md) — memory subsystem 起源
- [`26-phase-11-boundary.md`](./26-phase-11-boundary.md) — extraction 起源（本 phase deprecate 对象）
- [`scripts/spike_memory_capability.py`](../scripts/spike_memory_capability.py) — cold + warm spike，生成本 phase 押注的数据基础
- [[feedback-design-for-strong-model]] — 哲学声明，本 phase anti-scope 的来源
- [[feedback-honor-meta-doubt-signal]] — 推导过程中复用的 methodology discipline
