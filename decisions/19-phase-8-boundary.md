# Phase 8 Boundary — `markdown_store/` Extraction

> Status: locked at Phase 8 entry, 2026-05-19.
>
> Scope note: **rule-of-three refactor**. Phase 5b (commands), 5c
> (skills), 5d (bundles) all repeat the same markdown-with-YAML-
> frontmatter + global/project two-layer filesystem-store shape. Each
> retro explicitly deferred the extraction to "Phase 8 when the third
> tenant arrives" — 5d retro §3.5 + 5e retro §5 both name this phase.
>
> Phase 8 is a **pure internal refactor**: public API (`parse_command`
> / `FilesystemSkillStore` / `Bundle` / etc.) stays byte-identical;
> only the duplicated implementation details migrate to a shared
> `markdown_store/` package.

## Triggering observation

Three counts of:

- `_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")` (exact)
- `_FRONTMATTER_FENCE = "---"` (exact)
- `_split_frontmatter(text)` (~25 lines, nearly identical)
- `parse_X(path) -> X | None` outer structure (file read → split →
  YAML parse → mapping check → ~5-8 field validations → dataclass
  construct → try/except ValueError → warning log)
- `FilesystemXStore.{__init__, discover, get, _scan, _merge_dir}` (~70
  lines, the only domain-specific bit is `parser = parse_X`)
- `EmptyXStore` sentinel (~10 lines each)

1045 total LoC across `{commands,skills,bundles}/{model,store}.py`.
Most of that is duplication — the domain-specific parts (Skill's
version field, Command's mode field, Bundle's 4 override fields,
plus per-dataclass __post_init__ rules) are ~200 LoC each, so the
extractable shared code is ~400-500 LoC.

## Decisions

### D21.1 — New top-level `markdown_store/` package

Lives at `src/openharness/markdown_store/` — peer to
`commands/`, `skills/`, `bundles/`. Not nested under any one of them
because all three depend on it equally.

```
src/openharness/markdown_store/
├── __init__.py     (public exports)
├── constants.py    (NAME_PATTERN, FRONTMATTER_FENCE)
├── parse.py        (split_frontmatter, read_frontmatter_dict)
└── store.py        (MarkdownDocument Protocol,
                    FilesystemMarkdownStore[T], EmptyMarkdownStore[T])
```

### D21.2 — Public API stays byte-identical

After refactor, all existing imports continue to work:

```python
from openharness.commands import (
    Command, parse_command, FilesystemCommandStore, EmptyCommandStore,
)
from openharness.skills import Skill, parse_skill, FilesystemSkillStore
from openharness.bundles import Bundle, parse_bundle, FilesystemBundleStore
```

The names, signatures, and observable behavior are unchanged. The
refactor moves implementation, not interface.

### D21.3 — Generic store with parser callback

`FilesystemMarkdownStore` is `Generic[T]` where `T` satisfies
:class:`MarkdownDocument` Protocol (`.name: str` + `.source_path:
Path`). Constructor takes a `parser: Callable[[Path], T | None]`.

Each domain's existing `FilesystemXStore` becomes a thin subclass
that fixes the parser:

```python
# commands/store.py (after refactor)
class FilesystemCommandStore(FilesystemMarkdownStore[Command]):
    def __init__(
        self,
        *,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        super().__init__(
            global_dir=global_dir,
            project_dir=project_dir,
            parser=parse_command,
            log_event_prefix="command",
        )
```

The subclass exists for **two reasons**: (1) preserves the public
class name so existing callers (`isinstance(store, FilesystemCommandStore)`,
mypy annotations) keep working; (2) hides the parser kwarg from
callers — they shouldn't know how the store is parameterized.

### D21.4 — `parse_X` becomes a thin wrapper over `read_frontmatter_dict`

The repeated outer logic (read file, split frontmatter, parse YAML,
check mapping, log on each failure mode) consolidates into a single
helper:

```python
def read_frontmatter_dict(
    path: Path, *, logger_name: str
) -> tuple[dict[str, Any] | None, str]:
    """Returns (frontmatter_dict | None, body).
    None signals "skip this document; warning already logged."
    """
```

Each `parse_X` then:
1. Calls `read_frontmatter_dict(path, logger_name="<domain>")`
2. Returns None if dict is None
3. Extracts domain-specific fields with domain-specific logging
4. Constructs dataclass; catches ValueError; returns None on miss

This keeps domain-specific validation (each parser's ~5-8 field
checks + dataclass `__post_init__`) where it belongs while killing
the 25+ LoC outer scaffolding triplicate.

### D21.5 — Log event names preserve backward-compat

Each refactored parser must emit the SAME log event names as before
(`command_missing_name`, `skill_missing_description`,
`bundle_yaml_parse_failed`, etc.). Existing log consumers (`jq`
filters, dashboards) must not break.

`read_frontmatter_dict` takes `logger_name` so its internal warnings
use the right per-domain prefix (`command_read_failed`,
`skill_read_failed`, `bundle_read_failed`).

### D21.6 — No behavior changes; tests pass unchanged

This is a refactor invariant: **every existing test in
`tests/commands/`, `tests/skills/`, `tests/bundles/` passes without
modification**. If any test fails because of behavior drift, the
refactor is wrong — fix the refactor, not the test.

Test modifications allowed in Phase 8:
- New tests under `tests/markdown_store/` exercising the shared
  primitives directly (parser edge cases, generic store with stub
  parser, EmptyStore behavior).
- NO modifications to existing per-domain tests.

---

## Cross-cutting invariant

Phase 8 is a refactor; "zero diff" doesn't apply in its usual form.
Instead the invariant is **API-level zero-diff**:

- Public class names stay (`FilesystemCommandStore`, `EmptyCommandStore`,
  `FilesystemSkillStore`, etc.).
- Public function names stay (`parse_command`, `parse_skill`,
  `parse_bundle`).
- All public dataclasses keep all their fields + `__post_init__`
  semantics.
- Log event names stay (per D21.5).
- All existing tests pass without modification (per D21.6).

Zero-diff to layers OTHER than commands/skills/bundles is preserved:

- `permissions/` — 0 lines
- `hooks/` — 0 lines
- `engine/` — 0 lines
- `observability/` — 0 lines
- `mcp/` — 0 lines
- `compaction/` — 0 lines
- `protocols/` — 0 lines
- `tools/` — 0 lines (depends on commands+skills+bundles via
  catalog injection, but doesn't import internals)
- `execution/` — 0 lines
- `cli.py` — 0 lines (uses public API only)

The diff is contained to:
- new `markdown_store/` (~300 LoC)
- `commands/model.py`, `commands/store.py` (refactored, net ~100 LoC reduction)
- `skills/model.py`, `skills/store.py` (refactored, net ~100 LoC reduction)
- `bundles/model.py`, `bundles/store.py` (refactored, net ~80 LoC reduction)

Net effect: ~300 LoC added (new shared module), ~280 LoC removed
(deduplication), ~20 LoC net change. The win is **DRY**, not size.

## Risks specifically NOT mitigated (Phase 8+)

- `LoadSkillTool` (consumes `Skill.body`) is unchanged; tool layer
  doesn't see the refactor.
- `apply_bundle_to_context` (consumes `Bundle.system_prompt` etc.)
  is unchanged.
- `markdown_store/` doesn't yet support non-`.md` extensions; if
  Phase 5f's filesystem hook plugins want `.py` discovery, that's
  a different shape and gets its own helper (not this module).

---

## Pointers

- Phase 5d retro §3.5 (rule-of-three trigger): `learnings/phase-5d.md`
- Phase 5e retro §5 (Phase 8 candidate confirmed): `learnings/phase-5e.md`
- Three sources being refactored: `src/openharness/{commands,skills,bundles}/{model,store}.py`
