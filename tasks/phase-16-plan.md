# Phase 16 Implementation Plan — Memory Architecture Pivot to CC Pattern

> Boundary contract: [`decisions/36-phase-16-memory-pivot-boundary.md`](../decisions/36-phase-16-memory-pivot-boundary.md).
> First-principles derivation: [`docs/ideas/memory-first-principles.md`](../docs/ideas/memory-first-principles.md).
> Eval coverage map (positions this phase): [`decisions/35-eval-coverage-map.md`](../decisions/35-eval-coverage-map.md) §D35.5 P0.

## Overview

**Phase 16 goal**: pivot `oh` memory subsystem to Claude-Code-style
LLM-self-decides architecture across all 6 fork points (storage shape /
index semantics / write trigger / read trigger / linking / scope). Phase
11's secondary-LLM-pass extraction architecture becomes deprecated
(flag-gated off, code preserved as safety net). Phase 10's
`FilesystemMemoryStore` substrate is **kept** as the harness-internal
write path for the new architecture's `Write`-tool calls; LLM 视角的发
现机制变成 session-start MEMORY.md 注入 + LLM 自选 Read。

**Philosophical underpinning**:
[[feedback-design-for-strong-model]] — harness commits to contracts
designed for strong-LLM judgment; current model failures are transient.
**Anti-scope forbids fallback paths designed against current model
weakness** (see boundary D36 §四).

## Cross-cutting invariant

By the time Phase 16 ships:

- `protocols/` — **zero diff** (no new types)
- `tools/` — **zero diff** (D36.12: `Write` + `Edit` already
  sufficient; no new memory tools)
- `memory/model.py`, `memory/store.py`, `memory/paths.py`,
  `memory/team.py`, `memory/usage.py` — **zero diff**（substrate
  保留作 Write 工具底层）
- `engine/query.py` — minimal diff: `_maybe_extract_memories`
  becomes no-op when (default) disabled
- `pyproject.toml` — **zero new deps**
- All Phase 10 既有 `tests/memory/` — **pass with zero assertion
  changes**

Only **new** code surface:
- `src/openharness/prompts/memory.py` (D36.10)
- 在 `cli.py` 的 `build_system_prompt` 调用前注入 MEMORY.md
  (D36.11)
- `evals/memory_decision/` consumer (gating eval per D35.5 P0)

**Deprecated**（保留代码 + 注释，下个 cleanup phase 真删）:
- `memory/relevance.py` (D36.7)
- `services/extract.py` + `engine/query.py` 的 extraction 调用点
  (D36.9)

## Architecture decisions (locked)

| Doc | What it locks |
|---|---|
| [`decisions/36-phase-16-memory-pivot-boundary.md`](../decisions/36-phase-16-memory-pivot-boundary.md) | D36.1-D36.6 Fork 1-6 全部 follow CC；D36.7 relevance.py deprecate；D36.8 MEMORY.md 200-line cap invariant；D36.9 Phase 11 extraction deprecate；D36.10 system prompt memory 段；D36.11 session-start MEMORY.md 注入；D36.12 Write+Edit 是 memory 写入路径（无新工具）；D36.13 team/ 子目录保留；D36.14 `[[slug]]` 仅 prompt 约定 harness 不解析 |
| [`decisions/35-eval-coverage-map.md`](../decisions/35-eval-coverage-map.md) | D35.5 P0 = 决策面 #4 inline 决策；本 phase gating eval 必须落地 |

---

## Task list

### P16-T1: System prompt memory section + session-start MEMORY.md injection

**Description**: 新增 `src/openharness/prompts/memory.py` 常量包含
CC-mirror 的 memory 写作规则段（类型定义 / 何时保存 / 何时不保存 /
两步保存格式 / `[[slug]]` 语法 / MEMORY.md 200 行 cap 说明）。该段
在 `build_system_prompt` 中被拼到 system prompt 末尾。同时在 `cli.py`
的 `_run_ask` / `_run_chat` 路径里，调用 `build_system_prompt` 前
读取 `<memory_dir>/MEMORY.md` 内容（截 200 行）并作为新 kwarg
传入；`build_system_prompt` 把它拼到 memory 规则段之后。MEMORY.md
不存在时注入占位 `(MEMORY.md is empty — no memories yet)` 而非 skip。
OSError 时 WARN log + 注入空占位（session 启动不失败）。

**Acceptance**:

- [ ] `src/openharness/prompts/memory.py` 暴露 `MEMORY_SYSTEM_PROMPT_SECTION`
  常量，结构包含：(a) memory_dir 路径占位、(b) 4 个类型（user /
  feedback / project / reference）定义、(c) "DO save when" 清单、
  (d) "DO NOT save" 清单、(e) 两步保存（`Write` `.md` then `Edit`
  MEMORY.md，强调"MEMORY.md exists → must Edit not Write"）、(f)
  `[[slug]]` 语法说明、(g) 200 行 cap 说明
- [ ] 段内不出现项目特定名字（"OpenHarness" / "oh" / 任何 service
  名）—— 规则段需跨项目复用
- [ ] `build_system_prompt` 新增 kwarg `memory_index_content: str | None`；
  当非 None 时拼到 memory 规则段之后；None 时拼占位
- [ ] `_run_ask` / `_run_chat` 在 `build_system_prompt` 调用前：
  resolve memory_dir → 读 `MEMORY.md`（若存在）→ truncate to first 200
  lines → 作为 kwarg 传入
- [ ] OSError 时不 raise；WARN log `memory_index_read_failed`；注入空占位
- [ ] MEMORY.md 行数 > 200 时 WARN log `memory_index_truncated`（D36.8
  forcing function）
- [ ] 单元测试 cover：(a) memory_dir 为空目录注入空占位；(b)
  memory_dir 含 50 行 MEMORY.md 注入全部；(c) memory_dir 含 300 行
  MEMORY.md 注入 first-200 + WARN；(d) MEMORY.md 不存在注入占位
- [ ] system prompt byte-identical test（新建）覆盖 (a)(b)(c) 三种
  状态，固定 cwd + 固定 MEMORY.md 内容 → 字节稳定

### P16-T2: Phase 11 extraction + relevance.py deprecation

**Description**: 翻转 `ExtractionSettings.enabled` 默认值 `True` →
`False`（D36.9）。`engine/query.py` 的 `_maybe_extract_memories`
保留代码但加 deprecated 注释；当 `extract_enabled=False` 时（new
default）整个 secondary pass 不触发。`memory/relevance.py` 的
`select_relevant_memories` 加 `# DEPRECATED: Phase 16 — LLM does
selection via MEMORY.md index` 注释；从 `engine/` 和 `cli.py` 移除
所有调用点。代码本体保留，下个 cleanup phase 删。

**Acceptance**:

- [ ] `config/settings.py` `ExtractionSettings.enabled` default
  `False`；docstring 注明 Phase 16 deprecated；CLI `--no-extract`
  仍工作（向后兼容；显式开启走 same code path）
- [ ] `services/extract.py` 模块 docstring 顶部加 deprecated 标记 +
  指向 `decisions/36-phase-16-memory-pivot-boundary.md` D36.9
- [ ] `engine/query.py` `_maybe_extract_memories` 函数 docstring 加
  deprecated 标记；行为不变（已经在 `not extract_enabled` 时 early
  return）
- [ ] `memory/relevance.py` 模块 docstring 顶部加 deprecated 标记 +
  指向 D36.7
- [ ] grep `select_relevant_memories\|relevance` in `src/openharness/`
  非 relevance.py 自身 → **zero hits**（call sites 全部移除）
- [ ] 既有 `tests/memory/test_relevance.py` 全套通过（被 deprecated 的
  代码本体仍能正确执行；只是不被生产路径调用）
- [ ] 既有 `tests/services/test_extract.py` + `test_e2e_phase11.py`
  全套通过（同上）
- [ ] 新单元测试：默认 `Settings()` 实例化 → `extraction.enabled is
  False`；显式 `Settings(extraction={"enabled": True})` → fires
  extraction（验证 flag 可逆）

### P16-T3: Gating eval `evals/memory_decision/` (D35.5 P0)

**Description**: 新建 `evals/memory_decision/` 作为决策面 #4 (inline
decision) 的 Stage 1-5 substrate consumer。Dataset 包含 ≥ 6 个
scenarios（preference / correction / project state / reference /
trivial-skip / warm-start with pre-populated MEMORY.md）。Scorer 形态
**跟 focus_state 不同**：检查 LLM 是否 emit `Write` + `Edit` tool_use
blocks、内容合法（frontmatter + index entry）、warm-start 时是否
正确 Edit 而非 destructive Write。复用 D31 substrate（Runner /
Cassette / Stage 3 LLM-judge）；新增 `MemoryDecisionScorer` 实现
`Scorer` Protocol。Dataset_card.md 头部 follow D35.3 三声明
（capability claim / input spec / judgment spec）。

**Acceptance**:

- [ ] `evals/memory_decision/dataset.yaml` schema 跟 focus_state
  不同：每 case 多 `pre_populated_memory_files: dict | None`
  field（warm-start 场景预置），`expected_tool_calls:
  list[dict]` field（应该 emit 哪些 tool name + 关键内容片段）
- [ ] `evals/memory_decision/dataset_card.md` 顶部 3 声明：
  - **Capability claim**：harness 在决策面 #4 上能让 LLM 完成
    judge → Write `.md` → Edit MEMORY.md 链，warm-start 不
    destructive overwrite
  - **Input spec**：N=6 合成 scenarios；2 cold-start + 3
    warm-start + 1 trivial-skip
  - **Judgment spec**：4-state（PASS / PARTIAL-chain-incomplete /
    FAIL-drift / FAIL-overwrite）+ LLM-judge for type 分类语义
- [ ] 新 `MemoryDecisionScorer` 类实现 `Scorer` Protocol，逻辑覆盖：
  (a) Write tool 被调用且 file_path 在 memory_dir 下、(b)
  frontmatter 合法（复用 spike 的 FRONTMATTER_RE）、(c)
  warm-start 时 MEMORY.md 被 Edit 或 Write-with-preservation、(d)
  destructive overwrite detection（warm Write to MEMORY.md
  且 < 50% existing entries preserved → FAIL-overwrite）
- [ ] Type 分类 LLM-judge rubric（≥ 4 个）落到
  `src/openharness/eval/rubrics.py`，跟 focus_state rubric 同
  family；rubric 接受同一 type 的多个 valid 读法（如
  preferences → user OR feedback 都可）
- [ ] CLI `oh eval memory_decision` 启动入口（mirror `oh eval
  focus_state`）
- [ ] **Pass bar**：qwen3.7-max 上 warm-start scenarios PASS ≥ **4/5**
  → contract 摸到阈值，phase 通过
- [ ] **Fallback path 1**：qwen3.7-max < 4/5 → 用 Claude Sonnet（API
  via Anthropic）跑同一 dataset；若 ≥ 4/5 → 定性为 model gap，
  phase 仍可推（写入 `learnings/phase-16.md`）
- [ ] **Fallback path 2**：Claude Sonnet 也 < 4/5 → contract 真有问题
  → 回 [`memory-first-principles.md`](../docs/ideas/memory-first-principles.md)
  重推；phase **不通过**

### P16-T4: Regression discipline + byte-identical locks

**Description**: Phase 10 + Phase 11 既有 198 个 memory-相关 test 全部
pass（含被 deprecated 的 relevance / extract 单测）。新增 byte-identical
test 覆盖 system prompt 注入路径（D36.11）—— 防止后续 prompt 段调整
悄悄漂。所有现有 `tests/prompts/test_byte_identical.py` 风格的 test
保持 pass。

**Acceptance**:

- [ ] `uv run pytest tests/memory/` 全套绿
- [ ] `uv run pytest tests/services/test_extract.py
  tests/services/test_e2e_phase11.py` 全套绿（验证 deprecated
  代码本体仍 functional）
- [ ] `uv run pytest tests/prompts/` 全套绿
- [ ] `uv run pytest tests/cli/` 全套绿（含 `test_memory_subcommands.py`
  / `test_compact_repl.py` 等既有 CLI test）
- [ ] 新 `tests/prompts/test_memory_injection_byte_identical.py`：
  - Fixture A（空 memory_dir）：注入占位字节稳定
  - Fixture B（pre-populated 5 行 MEMORY.md）：注入字节稳定
  - Fixture C（300 行 MEMORY.md）：截 first-200 字节稳定
- [ ] 全仓 `uv run pytest -q` 绿，无新失败

### P16-T5: Dogfood validation + retro starter

**Description**: 用本 phase 实现做至少一次 real session（≥ 5 turns），
观察主 LLM 的 memory 写入行为。Session 类型选"包含 1-2 个 memorable
moment 的工作对话"——比如让 user 描述一个 preference + 做一次
correction + 提到一个 external system。观察：(a) 主 LLM 是否主动判断
该写 memory（无需用户提示）、(b) frontmatter 是否合法、(c)
MEMORY.md 是否被正确 Edit 而非 Write、(d) `[[slug]]` 引用是否被使用。
Session 结果 + 观察写入 `learnings/phase-16.md`。

**Acceptance**:

- [ ] 一次 real session 完成（终端 transcript 保存到
  `learnings/phase-16-dogfood-session.txt` 或类似命名）
- [ ] `learnings/phase-16.md` 写 retro 起草：
  - §1 What worked - 实测看到的好行为（写入触发准、frontmatter
    合法、MEMORY.md 正确 Edit、`[[slug]]` 自然使用之类的具体证据）
  - §2 What missed - 实测看到的 drift / overwrite / 漏写场景
  - §3 Predictions for next phase - phase loop step 4 要求的"对下一
    phase 的预测"
  - §4 Abstractions tested - 哪些 boundary doc 不变量被检验、哪些
    holds、哪些 broke
- [ ] dogfood 中**未观察到 destructive MEMORY.md overwrite**——若
  观察到则 phase **不通过**，回 T3 gating eval 检验是否数据集没
  cover 该场景

---

## Open frontier（推迟到未来 phase）

按 boundary doc §四 anti-scope + first-principles §十一：

1. Memory 冲突解决机制
2. Auto-GC / 老化（MEMORY.md 接近 200 行 cap 时怎么办）
3. 多 session 并发写
4. Team-scope 用户路径（D36.13 保留 `team/` 但未真有 user）
5. Plugin / skill 触发 memory 写入（决策面 #5 自己的 phase）

Phase 17+ 视实际 dogfood 暴露的优先级再决定。
