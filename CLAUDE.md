# CLAUDE.md

## Where things live

| 路径 | 内容 |
|---|---|
| `REFERENCE.md` | HKUDS v0.1.7 reverse-engineered spec —— alignment target |
| `decisions/NN-*.md` | Boundary doc —— D-numbered decisions + §六 wiring audit |
| `decisions/_template_lean.md` | 新 phase boundary 起点（copy 它） |
| `tasks/phase-X-plan.md` | (历史) D01-D39 时代单独的 plan 文件；D40+ 不再写，Tasks 进 boundary doc |
| `learnings/phase-X.md` | Phase retro |
| `learnings/*.md` | 其它 free-form 思考 note |
| `docs/ideas/` | Curiosity-driven exploration |
| `CHANGELOG.md` | User-facing release notes |

## Phase loop

1. **Boundary doc** (`decisions/NN-*.md`) —— D-numbered decisions + §六 wiring audit + Tasks (T1..TN with per-task acceptance)
2. **Execute** —— agent runtime 推每个 task 的 sub-tasks
3. **Retro** (`learnings/phase-X.md`) —— §六 verdict 对照实测、predictions for next phase

## Rules

### R1. §六 Wiring audit 必须 in 每个 boundary doc

列每个跨的 runtime layer + verdict (`unchanged` / `requires extension` / `requires bypass` / `requires verification`)。retro 时逐项 falsify。

≥ 3 `extension` 或多个 `bypass` → contract 跨太多层，回头重 ratify scope 或 split phase。

### R2. Spec 颗粒度停在 capability，不下到 sub-task

✅ `oh ask streaming + error messages + integration tests behind real API key`
❌ `4a Settings → 4b mock client → 4c real client → 4d integration test`

Sub-task decomposition 是 agent runtime 的事。

### R3. Stop and ask —— 三 trigger

- **External contract** —— 公开 API / env var / 新依赖 / package 外可见
- **Irreversible** —— file deletion / schema migration / force-push / public interface 改名
- **Capability description is wrong** —— boundary doc 的前提站不住

前两条 blast radius，第三条 epistemic honesty。

### R4. Never auto-commit on GREEN

测试 GREEN 后、`git commit` 之前，walkthrough diff vs acceptance criteria 每条 bullet 对照。human sign off 才 commit。不允许 "I'll commit it" 不出示 diff。

### R5. 新 boundary doc copy `decisions/_template_lean.md`

目标 ≤ 230 行/篇（含 Tasks section）。D01-D39 老 doc 不重写。
D40+ 不再单独写 `tasks/phase-X-plan.md` —— Tasks 进 boundary doc §五。

### R6. 文档容器分工

- `decisions/` 是 **machine-readable contract** —— AI 当 context 读，human ratify decisions
- `learnings/` 是 **human reflection** —— human 写给未来的 human
- `docs/ideas/` 是 **curiosity** —— 闪现时记，要继续探索时回头

Narrative 思考、case study、跟外部对比 → 进 `learnings/` 或 `docs/ideas/`，**不**塞 boundary doc。
