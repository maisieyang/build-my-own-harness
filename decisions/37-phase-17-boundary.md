# Decision 37 — Phase 17 Boundary (Memory Substrate Cleanup + Methodology Evolution)

> Created 2026-06-06 · 中文
>
> 配套读物：
> - 起源（Phase 16 retro §3 deferred frontier）：[`learnings/phase-16.md`](../learnings/phase-16.md)
> - 上一 phase 的 boundary：[`36-phase-16-memory-pivot-boundary.md`](./36-phase-16-memory-pivot-boundary.md)
> - 第一性原理：[`docs/ideas/memory-first-principles.md`](../docs/ideas/memory-first-principles.md)

---

## 〇、Why this doc

Phase 16 ratified 6 fork point + invariant 完整 pivot 到 CC memory
模式，但 **boundary D36 §五 explicit anti-scope 把 frontmatter parser
+ Memory model 排除在外**——结果 dogfood 暴露 3 个 wiring gap：

- **Gap A**：Tier 3 permission 没识别 memory_dir（**T5 已 fix**，commit 50bc5fe）
- **Gap B**：Phase 10 `Memory` model 强制 `id` field，CC 写的 frontmatter 没有 → parser 警告丢弃
- **Gap C**：`FilesystemMemoryStore.discover()` 把 MEMORY.md 当 memory 文件 parse → 警告

Gap B + C **不阻塞 Write 通路**（production write 不走 parser），但
**阻塞 `oh memory list/show`**——因为 list/show 走 discover() 拿
memory 视图，而 discover 把新 memory 全 warning-discarded 了。

Phase 17 收尾这两条 + 顺手真删 T2 留的 Phase 11 extraction safety
net（6 commits 没 rollback，证明 deprecation 完成），并把 Phase 16
retro 提出的 **§六 Wiring audit** discipline 写进 CLAUDE.md 作为
未来所有 phase boundary doc 的默认结构。

这是 **cleanup + methodology evolution** phase，不是新 capability。
工作量预估 50-100 LoC + tests + 0.5 day calendar。

---

## 一、Capability scope

**影响**：

- `memory/model.py` `Memory` dataclass + `parse_memory` 函数（放宽 `id` 要求）
- `memory/store.py` `FilesystemMemoryStore.discover()`（skip MEMORY.md）
- `services/extract.py`（**整文件删除**）
- `engine/query.py`（删 `_maybe_extract_memories` + QueryContext 三字段）
- `config/settings.py`（删 `ExtractionSettings` 整 class）
- `cli.py`（删 `--no-extract` flag；改 `oh memory list/show` 渲染）
- 相关测试（`tests/services/test_extract.py` / `test_e2e_phase11.py` 删除；`tests/memory/test_*.py` 适配新 schema）
- **CLAUDE.md**（加 §六 Wiring audit 描述）

**保留**：

- Phase 16 的 6 个 fork point invariants（D36.1-D36.6）不动
- Phase 16 T1-T5 commits 不动
- `FilesystemMemoryStore.add_or_update`（LLM Write tool 底层）不动
- Memory 类型枚举（user/feedback/project/reference）不动
- Per-project sha1 path（D28.1）不动
- Tier 3 + memory_dir 例外（T5 commit 50bc5fe）不动

**不在 phase 范围**：

- 重新设计 frontmatter schema（要么放宽 id，要么完整重设——本 phase 选前者）
- `[[slug]]` link 的 harness 端解析（D36.14 仍 prompt-only）
- Memory GC / 老化（仍 deferred）
- Plugin / skill 触发 memory 写入

---

## 二、决策 D37.1-D37.6

### D37.1 — `Memory.id` field 放宽为 optional + 自动 hash 生成

**Chosen**：
- `Memory` dataclass：`id` field 改为 `id: str | None = None`
- `parse_memory(path)`：frontmatter 缺 `id` 时**自动生成** `id =
  sha1(name + str(path.resolve()))[:16]`，不报错
- 已有文件的 `id` 字段保留 + 优先使用
- discover() 不再因 missing `id` 丢弃文件

**Rationale**：
- CC 写的 frontmatter（D36.10 mandate）就是没有 `id`——Phase 17 是
  contract 完整化，不是放宽校验严格度
- `id` 仍是 in-memory dict key 用途（无 ID 冲突）；自动 hash 让冲突
  概率 ~2^-64
- 老文件已有 `id` 不受影响

**Alternatives 不选**：
- (a) 完全 drop `id` 字段——会破坏 `MemoryStore.get_by_id` 等接口
- (c) 允许 None 不生成——`MemoryStore` 现有 dict 实现需要 hashable key

### D37.2 — 其他 Phase 10/11 字段保持 optional with defaults（不删字段）

**Chosen**：`importance` / `use_count` / `last_used_at` / `tags` /
`scope` / `created_at` / `updated_at` 全部保留在 `Memory` dataclass，
parser 缺失时填默认值（0 / None / [] / "private" / now()）。

**Rationale**：
- Phase 17 scope 是"放宽接受 CC 文件"，不是"重新设计 schema"
- 删字段会 break 老 frontmatter files——D28.5 保留 backward compat
- `importance` / `use_count` / `last_used_at` 现在 unused（relevance.py
  + extraction deprecated）但删字段后还原难度高
- 未来如果有"完整 schema 重设"需求，独立 phase 处理

**Anti-scope**：本 phase **不** 删任何 field，**不** 加 deprecation
注释（保 model 文件干净）。

### D37.3 — Phase 11 extraction 完整删除

**Chosen**：删除以下全部：

| 文件 / 符号 | 处理 |
|---|---|
| `services/extract.py` | **整文件删除** |
| `engine/query.py` `_maybe_extract_memories` | 删函数 + 调用点 |
| `engine/context.py` `QueryContext.extract_enabled` / `extract_max_records` / `extract_timeout_s` | 删三字段 |
| `engine/query.py` 调用点对 QueryContext 这三字段的读取 | 删 |
| `config/settings.py` `ExtractionSettings` class | **整 class 删除** |
| `config/settings.py` `Settings.extraction` field | 删 |
| `cli.py` `--no-extract` flag | 删 typer.Option 定义 + 所有相关 wire 代码 |
| `tests/services/test_extract.py` | **整文件删除** |
| `tests/services/test_e2e_phase11.py` | **整文件删除** |
| `tests/config/test_compact_extraction_settings.py` | 删 ExtractionSettings 相关 test class（CompactSettings 保留） |

**Rationale**：
- T2 commit message 显式写 "a future cleanup phase will delete the
  module entirely" —— Phase 17 就是那个 phase
- 6 commits without rollback = deprecation 完成
- `--no-extract` 删除是 breaking CLI change，但单人项目 + T2 已
  deprecated + 文档记录 = 符合 CLAUDE.md "Avoid backwards-
  compatibility hacks"
- 删 vs 留 deprecated comment：留 comment 是 backward-compat shim，
  违反 CLAUDE.md 纪律

**Anti-scope**：本 phase **不** 删 `services/summarize.py`（compact
还在用）、**不** 删 `services/compact.py`（Phase 4 capability）。

### D37.4 — `oh memory list/show` 适配 D36.10 三字段渲染

**Chosen**：
- `oh memory list`：显示 `name / description / type`（按字母序 sort）
- `oh memory show <name>`：显示 frontmatter + body
- `oh memory path <name>`：保持现状（显示 file path）

字段 fallback：
- 缺 `description` → 显示 `(no description)`
- 缺 `type` → 显示 `(unknown)`

**Rationale**：
- 跟 D36.10 frontmatter 三字段对齐
- Phase 16 写的 memory 现在能被 list 看到了
- 老 file 的额外字段（tags / use_count）不在 list 里显示（避免噪声）；
  show 子命令完整展示

**Anti-scope**：本 phase **不**加 `oh memory add/edit/remove`（D36
明确 LLM 自己写，不需要 CLI 写入路径）。

### D37.5 — `FilesystemMemoryStore.discover()` skip MEMORY.md by filename

**Chosen**：discover() 的 `glob("*.md")` 结果过滤掉 filename 等于
`MEMORY.md` 的项目（case-sensitive 精确匹配）。

**Rationale**：
- 按 D36.10 invariant MEMORY.md 是 reserved index file name，不允许
  作 memory body 文件
- Hard-coded skip 比"检测是否有 frontmatter"更明确（后者把"忘写
  frontmatter 的 memory"也 silently skip 掉，bug 难查）
- One-line filter，极低风险

**Forcing function**：unit test 验证：dir 含 MEMORY.md + 1 个有效
memory → discover() 返回 1 个 memory，no warning emitted。

### D37.6 — CLAUDE.md 加 §六 Wiring audit 进项目方法论

**Chosen**：CLAUDE.md "The four-step phase loop" 段之后插入一段新
描述：**所有新 phase boundary doc 必须包含 §六 Wiring audit 章节**，
列出 contract 跨的每个 runtime layer + 一句话 verdict（unchanged /
requires extension / requires bypass）。

候选 layer 默认 checklist：
- permissions（三层 + memory_dir 例外）
- hooks（pre/post-tool-use, pre/post-api-call, on-error）
- snapshot writer / session_memory checkpoint
- compaction L1-L4
- observability（logger names + WARN events）

**Rationale**：
- Phase 16 retro §3 meta-lesson："boundary anti-scope catches
  deliberate scope creep, NOT invisible cross-layer collisions"
- Gap A 的 D28.1 vs P3-T3.3c 碰撞如果有 §六，可以在 boundary doc 时
  发现，不用等 dogfood
- Cost ~10-20 lines per future boundary doc；payoff 是节省一次
  dogfood-driven hotfix

**Forcing function**：CLAUDE.md 修改后**本 doc（D37）自身就要有
§六**作 reference implementation（见下面 §六）。

---

## 三、Acceptance criteria

Phase 17 GA 需要满足：

### 3.1 dogfood validation：re-run T5 同 prompt

- 用 `oh chat` 跑 Phase 16 T5 同样的 user 自介绍 prompt
- 跑完检查：
  - `~/.openharness/memory/<project-hash>/MEMORY.md` 存在且有 pointer
  - `~/.openharness/memory/<project-hash>/user_role_and_values.md` 或
    类似 .md 存在且 frontmatter 合法
  - **`oh memory list` 显示新写的 memory**（D37.4 的可见证据）
  - **启动 log 无 `memory_missing_id` warning**（D37.1 验证）
  - **启动 log 无 `memory_missing_frontmatter` on MEMORY.md
    warning**（D37.5 验证）

### 3.2 全仓 regression green

- `uv run pytest -q` 绿（预期约 2050 个——删 ~50 个 Phase 11 extraction
  test，加 ~20 个 Phase 17 新 test）
- 新增 unit test cover D37.1（auto-id generation） + D37.3（Settings 没
  ExtractionSettings 字段） + D37.4（list 渲染） + D37.5（discover skip
  MEMORY.md）

### 3.3 删除完整性 grep

- `grep -rn "extract_memories_from_turn\|ExtractionSettings\|_maybe_extract_memories\|--no-extract" src/openharness/` → **zero hits**（D37.3 verification）

### 3.4 CLAUDE.md 改动 + boundary doc 演示

- CLAUDE.md "The four-step phase loop" 后有 §六 Wiring audit 段
- 本 D37 doc 的 §六（下方）是 reference implementation
- 一句话 verdict per layer 形态固定下来

---

## 四、Anti-scope

本 phase **不做**：

1. ❌ 不重新设计 frontmatter schema（schema 还是 D36.10 + Phase 10
   legacy fields；只是 parser 更宽松）
2. ❌ 不删任何 Memory model field（D37.2）
3. ❌ 不加 deprecated comments 给删掉的代码（CLAUDE.md "avoid removed-
   code comments"）
4. ❌ 不引入 `oh memory add/edit/remove`（D36 anti-scope 6 — LLM 是 writer）
5. ❌ 不动 Tier 1 / Tier 2 permissions
6. ❌ 不改 sandbox 路径处理
7. ❌ 不动 hook / snapshot / compaction layers（per §六 wiring audit 下面）
8. ❌ 不引入新 dep / 不动 pyproject.toml

---

## 五、Implementation contract（informative — capability 级 plan 在 tasks/phase-17-plan.md）

**新增**：无新代码文件，本 phase 是 cleanup-heavy。

**改造**：
- `memory/model.py` — Memory id optional + auto-generation in parse_memory
- `memory/store.py` — discover() 加 MEMORY.md filter
- `engine/query.py` — 删 _maybe_extract_memories 函数和调用
- `engine/context.py` — 删 QueryContext.extract_* 三字段
- `config/settings.py` — 删 ExtractionSettings + Settings.extraction
- `cli.py` — 删 --no-extract option；改 oh memory list/show 渲染
- `CLAUDE.md` — 加 §六 Wiring audit discipline 描述

**删除**：
- `services/extract.py`（整文件）
- `tests/services/test_extract.py`（整文件）
- `tests/services/test_e2e_phase11.py`（整文件）

**不动**：
- 所有 Phase 16 commits (78f2a90 / b5be31c / 9780a95 / 0b6b912 / 50bc5fe / be22459)
- `prompts/memory.py`, `prompts/system.py`, `cli.py` 的 `_load_memory_index_for_injection`
- `memory/store.py` 的 `add_or_update` + atomic write 路径
- `memory/relevance.py`（保留作 deprecated module + 单测——仅 algorithm reference）

---

## 六、Wiring audit（NEW — methodology demonstration per D37.6）

Phase 17 cleanup contract 跨以下 runtime layer。每层 verdict 必须
explicit：

| Layer | Verdict | Reasoning |
|---|---|---|
| **permissions/tier_based** | **unchanged** | Phase 17 不改 Tier 1/2/3 逻辑；T5 已加的 memory_dir 例外（commit 50bc5fe）保留 |
| **hooks** | **unchanged** | 删除 `_maybe_extract_memories` 不影响 hook chain（extraction 从来不通过 hook 触发，是 engine 直接 await） |
| **snapshot writer** (services/snapshot.py) | **unchanged** | snapshot 不依赖 extraction 或 Memory model schema；记录的是 conversation messages + tool_metadata，对 frontmatter 完全 agnostic |
| **session_memory checkpoint** (services/session_memory.py) | **unchanged** | 同 snapshot，不依赖 ExtractionSettings 或 Memory id |
| **compaction L1-L4** (services/compact.py) | **unchanged** | compact 内部 LLM 调用用 summarize() 不用 extract_memories_from_turn；删除路径不影响 |
| **observability** | **requires update** | 删 `memory_missing_id` + `memory_missing_frontmatter` 两个 WARN 事件名（不再触发）；删 extraction 相关 INFO 事件（`extract_skipped` / `extract_failed` / `extract_complete`） |
| **API client** | **unchanged** | LLM 调用 surface 不受影响 |
| **CLI subcommand surface** | **requires extension** | `--no-extract` 删除是 breaking change（接受，per D37.3）；`oh memory list/show` 渲染逻辑改（per D37.4） |
| **Eval substrate** (focus_state + memory_decision) | **unchanged** | Eval 走独立 infer 路径，不调 Memory model 或 ExtractionSettings |
| **Skill / Plugin / Sub-agent** | **unchanged** | 三个子系统不引用 extraction 或 Memory.id |

**Conclusion**：cleanup scope 与 8 个其他 runtime layer 完全 disjoint；
只 observability + CLI 需要被动跟随删除。**没有跨层 collision 风险**。

**如果未来 phase boundary doc 的 wiring audit 出现 ≥ 1 个层 "requires
bypass" 或多层 "requires extension"**——说明 contract 跨层影响大，
要重新 ratify scope（或 split phase）。Phase 17 单层 extension（CLI）
+ 单层被动更新（observability）= 干净 cleanup phase 的正常形态。

---

## 七、References

- [Phase 16 retro](../learnings/phase-16.md) — §3 deferred frontier 是本 phase 起源
- [`36-phase-16-memory-pivot-boundary.md`](./36-phase-16-memory-pivot-boundary.md) — 上游 contract
- [`docs/ideas/memory-first-principles.md`](../docs/ideas/memory-first-principles.md) — 推导链
- CLAUDE.md — 加 §六 discipline 后是 forcing function（本 phase T5）
- [[feedback-design-for-strong-model]] — 删 extraction safety net 的 anti-fallback 哲学背书
