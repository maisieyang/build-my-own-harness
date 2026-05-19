# Phase 8 Implementation Plan — `markdown_store/` Extraction

> Phase 8 boundary: [`decisions/19-phase-8-boundary.md`](../decisions/19-phase-8-boundary.md).
> Triggering retros: [`learnings/phase-5d.md`](../learnings/phase-5d.md) §3.5, [`learnings/phase-5e.md`](../learnings/phase-5e.md) §5.

## Overview

Pure internal refactor extracting the shared markdown+frontmatter+
filesystem-store machinery into a new `markdown_store/` package.
Public API stays byte-identical; all existing tests pass unchanged.

**Total scope**: ~1 day, 5 capabilities, ~7 commits, net ~20 LoC
change (~300 new + ~280 removed).

## Task list

### P8-T1: `markdown_store/` package foundation 🔜 NEXT

**Description**: Build the new shared module + its tests. No callers
yet.

**Acceptance**:
- [ ] `markdown_store/constants.py` — `NAME_PATTERN` regex + `FRONTMATTER_FENCE` literal
- [ ] `markdown_store/parse.py`:
  - `split_frontmatter(text) -> tuple[str | None, str]` (exact behavior of the three duplicates)
  - `read_frontmatter_dict(path, *, logger_name) -> tuple[dict[str, Any] | None, str]` returning `(frontmatter, body)`. Logs per-domain warnings on each failure mode (`<logger_name>_read_failed`, `<logger_name>_missing_frontmatter`, etc.).
- [ ] `markdown_store/store.py`:
  - `MarkdownDocument` Protocol (`name: str`, `source_path: Path`)
  - `FilesystemMarkdownStore[T]` generic with `parser` + `log_event_prefix` constructor kwargs
  - `EmptyMarkdownStore[T]` generic sentinel
- [ ] `markdown_store/__init__.py` exports
- [ ] Tests `tests/markdown_store/`:
  - `split_frontmatter` happy + 4 error paths
  - `read_frontmatter_dict` happy + per-error-path log events
  - Generic store with a stub `_FakeDoc` dataclass + parser
  - Project-overrides-global merge order
  - Empty store sentinel

**Files**: `src/openharness/markdown_store/{__init__,constants,parse,store}.py`, `tests/markdown_store/{__init__,test_parse,test_store}.py`.

---

### P8-T2: Refactor `commands/` to use `markdown_store/`

**Description**: Replace duplicated code in `commands/model.py` +
`commands/store.py` with imports from `markdown_store`. Existing
tests pass unchanged.

**Acceptance**:
- [ ] `commands/model.py`:
  - Delete local `_NAME_PATTERN`, `_FRONTMATTER_FENCE`, `_split_frontmatter`
  - Use `markdown_store.NAME_PATTERN` in `Command.__post_init__`
  - Rewrite `parse_command` to call `read_frontmatter_dict(..., logger_name="command")` and keep the domain-specific field extraction (description, mode)
- [ ] `commands/store.py`:
  - `EmptyCommandStore` becomes `class EmptyCommandStore(EmptyMarkdownStore[Command]): pass`
  - `FilesystemCommandStore` becomes thin subclass of `FilesystemMarkdownStore[Command]` fixing `parser=parse_command`, `log_event_prefix="command"`
  - `CommandStore` Protocol stays (it's the public contract; not derived from the generic)
- [ ] **No test modifications**. All 100+ existing `tests/commands/` tests pass unchanged.

---

### P8-T3: Refactor `skills/` to use `markdown_store/`

**Description**: Same pattern as T2 for skills.

**Acceptance**:
- [ ] `skills/model.py` refactored (delete local pattern/fence/split, use markdown_store; preserve `version` field extraction)
- [ ] `skills/store.py` refactored
- [ ] All `tests/skills/` tests pass unchanged

---

### P8-T4: Refactor `bundles/` to use `markdown_store/`

**Description**: Same pattern as T2/T3 for bundles.

**Acceptance**:
- [ ] `bundles/model.py` refactored (preserve 4 override field extractions)
- [ ] `bundles/store.py` refactored
- [ ] All `tests/bundles/` tests pass unchanged

---

### P8-T5: Cross-cutting invariant verification + README + retro

**Description**: Verify the refactor preserved all public surfaces +
write up the experience.

**Acceptance**:
- [ ] Run full pytest suite — all ~1186+ tests pass; coverage ≥95%
- [ ] `tests/execution/test_invariant.py` extended forbidden set with
  `MarkdownDocument`, `FilesystemMarkdownStore`, `EmptyMarkdownStore`,
  `read_frontmatter_dict`, `split_frontmatter`. Verify the
  Phase 5d/5e identifiers continue to enforce zero ref.
- [ ] Formal git-diff vs Phase 5e close: protected dirs (`permissions/`,
  `hooks/`, `engine/`, `observability/`, `mcp/`, `compaction/`,
  `protocols/`, `tools/`, `execution/`) all 0 lines.
- [ ] README "Phase 8 — `markdown_store/` extraction" section
- [ ] `learnings/phase-8.md` retro: rule-of-three; refactor invariant
  shape (API-level zero-diff vs layer zero-diff); generic Protocol +
  subclass-for-naming pattern
- [ ] Phase 8 DoD checklist all green
