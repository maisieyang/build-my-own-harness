# Phase 7 Final Boundary — Production Polish & SPEC v1 Closeout

> Status: locked at Phase 7 entry, 2026-05-19.
>
> Scope note: **last mile to SPEC §1 "production deliverable"**.
> After 16 prior phases of architecture + tools + safety +
> extensibility + REPL, the framework is feature-complete by SPEC's
> tier definitions but lacks user-facing documentation, 3 missing
> CLI subcommand series (`oh tools` / `oh config` / `oh hooks`), a
> PyPI artifact, a tutorial, and the meta-retro SPEC §4.7 explicitly
> named ("我从 0 构建生产级 harness 的 7 个 Phase").
>
> **This boundary doc locks SPEC v1 scope.** Phase 8+ items (the 11
> deferred candidates in §6) become separate phases if pursued.
> Once Phase 7 closes, the project hits its **2026-04 SPEC's stated
> 2-3 month horizon**.

## Triggering observation

Two artifacts make the Phase 7 boundary obvious:

1. **The status review on 2026-05-19** quantified the gap:
   - SPEC §2 lists 12 CLI commands; only 3 ship (`oh ask` / `oh chat`
     / `oh --version`)
   - SPEC §1 names "production deliverable" as goal #1 — no PyPI
     artifact exists; installation is `git clone` + `uv sync`
   - SPEC §4.7 names the meta-retro as a deliverable — never written
   - README is structured as a per-phase development log (~1100
     lines, ordered Phase 1 → 2 → ... → 6+), unreadable as user
     onboarding

2. **The 16-phase split vs original 7-phase plan**: every phase past
   Phase 5 (5b/5c/5d/5e/5f/6/7a/7b/7c/8/6+) bundled an explicit
   "defer polish to Phase 7" decision. The deferrals stacked. Phase
   7 is now the catch-all that closes all of them OR explicitly
   names them as Phase 8+ candidates (§6).

The phase is **doc-heavy, not architecture-heavy** — but the
boundary discipline matters more here than in any prior phase
because the temptation to scope-creep is highest ("while I'm
polishing, let me also add Anthropic client / memory / Layer-3
compaction / ...").

## Decisions

### D25.1 — README split: user-facing vs development log

Current README is ~1100 lines, ordered as per-phase narrative
sections accumulated commit-by-commit since Phase 1. Rewrite as a
**user-facing README** (≤ 300 lines) answering in order:

1. What this is (one paragraph)
2. Quickstart (5-minute path: install → set 2 env vars → run example)
3. Key features (bullet list with links to `docs/development-log.md`
   for retrospective detail)
4. CLI reference (terse listing post-T2)
5. Configuration (env var table)
6. Architecture at a glance (1 paragraph + diagram link)
7. Development (local-dev quickstart)
8. License (MIT)

**Move the per-phase narratives verbatim** to a new
`docs/development-log.md`. The framework-builder retrospective
material (interpretive value for the project author + portfolio
piece) stays preserved; the user-facing README stops being a
development log.

**Why split rather than collapse**: the per-phase narratives ARE the
"capability training" artifact SPEC §1 named. Deleting them violates
SPEC §1.2. But they're useless to a new user trying to do
`oh ask "hello"` in 5 minutes — they answer "how did the framework
evolve?", not "what does it do?".

### D25.2 — Subcommand scope: 3 of the 5 missing series

SPEC §2 listed 5 missing subcommand series (`oh tools` /
`oh mcp` / `oh /<slash-command>` / `oh skill run` / `oh config`).
Phase 7 ships 3; defers 2.

**In scope** (high UX value, contained scope):
- `oh tools list` + `oh tools show <name>` — list/inspect registered
  tools. Offline-introspectable (no API key). ~80 LoC.
- `oh config show` + `oh config edit` — print effective Settings;
  open the config file. The `edit` path requires settling whether
  to add TOML config-file support to Settings (~30 additive LoC) or
  document env-var-only. ~120 LoC.
- `oh hooks list` + `oh hooks describe <name>` — list built-ins +
  discovered plugins; describe one (docstring + event). ~80 LoC.

**Deferred** (each becomes its own Phase 8+ candidate):
- `oh mcp add/list` — adding `add` means persisting MCP server
  config to a file, which couples with the unsettled TOML decision
  in D25.2 above. Defer to Phase 8b after the config-file path
  stabilizes.
- `oh skill run <name>` — skills today are LLM-facing (`LoadSkill`
  tool). Direct CLI invocation is a different mental model — it
  conflicts with "skills are knowledge the LLM looks up, not commands
  the user runs". Defer indefinitely until use case clarifies (may
  never).

**Why these three**: each is **introspectable** (read-only, doesn't
need API key, doesn't require running the loop). All three answer
"what is this framework doing right now / what does it have / how is
it configured" — the questions a new user has after install. The
deferred two are write-side or mode-conflicted.

### D25.3 — Distribution: PyPI as primary; no install.sh

**PyPI is the primary distribution channel**:

- `hatchling build` → wheel + sdist
- `uv publish` to TestPyPI first; then real PyPI on user
  authorization
- README documents both `uv tool install openharness` and
  `pip install openharness` (uv-native + pip fallback)

**No `install.sh` shell script** in this phase. Reasons:

- Adds maintenance burden (per-platform bash variants;
  fork/curl/wget detection; signature verification)
- PyPI + `pip` already solves the install problem for the SPEC §1
  target audience (Python developers studying harness internals)
- If demand surfaces from non-Python users, ship as a Phase 8+
  separate decision

**Real PyPI publish is user-authorized only** (gating rule):

- Agent prepares the wheel + sdist via `uv build`
- Agent runs the TestPyPI dry run (`uv publish --publish-url
  https://test.pypi.org/legacy/`)
- Agent verifies fresh-venv `pip install -i https://test.pypi.org/
  simple/ openharness==0.1.0 && oh --version` works
- **Real `uv publish` to production PyPI requires the user to fire
  the button**. Irreversible action; never autopilot.

### D25.4 — Version: `0.1.0` as the first PyPI release

`pyproject.toml` `[project] version = "0.1.0"`.

**Why 0.1.0 and not 1.0.0**:

- Signals "pre-1.0 — API may break" — gives room for SPEC §1's
  "study source" target users to expect interface churn as the
  framework iterates
- Semver discipline kicks in from here: 0.x → 0.(x+1) for breaking
  API changes; 0.1.x for non-breaking. 1.0.0 happens when the API
  is committed-stable (post-Phase 8+ at earliest)

**Why not 0.0.1**:

- 0.0.x is conventionally "this is a prototype, expect everything
  to break". The framework has 1240 tests + 97% coverage + mypy
  strict + 22 architectural decisions. **It's not a prototype.**
- 0.1.0 says "this works for what it claims; the API is still
  pre-committed but the implementation is real"

Going forward (post-Phase 7):

- Conventional Commits already in use → enables `git-cliff` or
  similar auto-changelog later. Not in this phase.
- CHANGELOG.md format follows [Keep a Changelog]
  (https://keepachangelog.com/) — high-level user-facing release
  notes; the detailed retrospective lives in `docs/development-log.md`.

### D25.5 — License: MIT

`LICENSE` file at repo root contains the standard MIT license text.
`pyproject.toml` `[project] license = {text = "MIT"}` references it.

**Why MIT**:

- **Maximum permissiveness** — consistent with SPEC §1's "study
  source" target user (anyone studying the codebase can incorporate
  parts into their own work without GPL viral concerns)
- **Matches the FOSS norm** for Python developer tools (Typer /
  Pydantic / aiodocker / structlog / uv itself — all MIT or
  permissively-licensed)
- **Compatible with commercial use** — if anyone wants to ship a
  product on top, no friction

**Considered and rejected**:

- Apache 2.0 — adds patent grant + NOTICE file requirement;
  marginal benefit over MIT for a 1-person project with no
  patentable claims
- GPL / AGPL — viral license; conflicts with "study source +
  incorporate" target user

**No CLA** — the project is a 1-person learning exercise; CLA is
overkill and adds friction for any future contributor.

---

## Cross-cutting invariant

Phase 7 is **doc-heavy + small additive subcommand work**. The
invariant for THIS phase is unusual — it's not "zero diff to layers"
(like Phase 5d-6+ enforced) because we're explicitly adding 3 new
subcommand surfaces. Instead the invariant is:

### A. Zero diff to engine + abstraction layers

`permissions/`, `hooks/`, `engine/`, `observability/`, `mcp/`,
`compaction/`, `skills/`, `commands/`, `bundles/`, `markdown_store/`,
`tools/`, `execution/`, `protocols/`, `prompts.py` — all stay at
zero diff vs Phase 6+ close.

The 3 new subcommands (`oh tools` / `oh config` / `oh hooks`) are
**pure read-only introspection** — they consume the existing
registries, settings, and hook catalogs. They never construct a
QueryContext, never call `run_query`, never dispatch tools.

### B. Allowed diffs (additive only)

- `cli.py` — 3 new Typer command series (~200 LoC)
- `pyproject.toml` — metadata polish (version, description, urls,
  classifiers, license)
- `config/settings.py` — possibly extend with TOML config-file
  support if T2's `oh config edit` decision goes that way
  (~30 additive LoC; D25.2 unresolved sub-decision)
- New top-level files: `LICENSE`, `CHANGELOG.md`
- New `docs/` directory: `docs/development-log.md`,
  `docs/tutorial.md`
- New `examples/` directory: sample command/skill/bundle/hook
- `README.md` — full rewrite (the only existing file allowed
  destructive change in this phase; old content moves to
  `docs/development-log.md`)

### C. No new dependencies

Phase 7 ships with the existing `pyproject.toml` dependency list.
No `prompt_toolkit`, no new lib for `oh config edit` (use stdlib
`tempfile` + `subprocess` for editor invocation), no new lib for
TOML reading if we go that route (Python 3.11+ ships `tomllib`
stdlib; we're already 3.10+ minimum but TOML write is rare enough
that we can use `tomli-w` only if D25.2 sub-decision picks TOML).

### D. Test gates stay green

- 1240 tests still passing after Phase 7
- Coverage ≥ 95% maintained
- mypy --strict clean
- ruff check + format clean
- New subcommand tests cover happy + error paths via `CliRunner`

---

## Test invariant

`tests/execution/test_invariant.py` is NOT extended in Phase 7 —
no new framework identifiers warrant zero-ref enforcement (the
subcommand implementations are CLI-local).

But Phase 7 adds:
- `tests/cli/test_subcommands.py` — new file covering `oh tools` /
  `oh config` / `oh hooks` happy + error paths

---

## Phase 7 DoD checklist

(Mirrors `tasks/phase-7-final-plan.md` §4.T5 — restated here so the
boundary doc is self-contained.)

- [ ] README rewritten (≤ 300 lines, user-facing structure)
- [ ] `docs/development-log.md` preserves all moved Phase 1-6+
      narratives verbatim
- [ ] `oh tools list/show` shipped + tested
- [ ] `oh config show/edit` shipped + tested
- [ ] `oh hooks list/describe` shipped + tested
- [ ] `pyproject.toml` metadata complete (version 0.1.0, license,
      urls, classifiers, keywords, authors)
- [ ] `LICENSE` file at repo root (MIT text)
- [ ] `CHANGELOG.md` with 0.1.0 release notes
- [ ] TestPyPI dry-run install succeeds in fresh venv
- [ ] `docs/tutorial.md` + `examples/` directory present
- [ ] `learnings/phase-7.md` meta-retro present
- [ ] All Phase 6+-era tests still passing (1240+); new
      subcommand tests added; total ≥ 1255 expected
- [ ] mypy --strict + ruff clean
- [ ] **PyPI publish** — user fires the button; not part of agent
      autopilot

---

## Risks specifically NOT mitigated (Phase 8+)

This is the catch-all for everything previously deferred plus the
new deferrals introduced by Phase 7's scope discipline. Each item
gets its own boundary + plan if pursued.

| Deferred | Estimated phase | Driver |
|---|---|---|
| **Anthropic native client** | Phase 8a | `protocols/` is Anthropic-shape;just needs an `AnthropicApiClient`. ~150 LoC. Unlocks prompt-caching / extended-thinking / computer-use features. |
| **LLM auto-compaction (Layer 3 summarization)** | Phase 4.5 | Phase 4 shipped Layer 1+2 only. Long-conversation `oh chat` needs Layer 3 to keep history bounded without losing semantic context. |
| **Memory system (basic)** | Phase 5g | SPEC Tier 2 ⭐⭐. YAML-frontmatter `~/.openharness/memory/*.md` with simple keyword retrieval. Reuses Phase 8 `markdown_store/`. |
| **Keyring auth + multi-profile** | Phase 7a-small | SPEC §2 explicitly named "Later phases may add". Multi-API-key switching. ~80 LoC. |
| **`oh mcp add/list`** | Phase 8b | Couples with D25.2's deferred TOML config-file question. |
| **`oh skill run <name>`** | Indefinite defer | Conceptual conflict with skills-as-LLM-knowledge model. May never ship. |
| **REPL polish** — `/save` / `/load` / multi-line / `/mode` mid-session | Phase 6++ | Per Phase 6+ retro §4. `prompt_toolkit` dependency for multi-line. |
| **`_build_query_context` refactor** | Phase 9 | `_run_ask` + `_run_chat` share ~150 lines of bootstrap. Rule-of-three triggers when 3rd consumer appears. |
| **Firecracker substrate** | Phase 7d | Different shape from gVisor (microVM, not OCI runtime swap). Per Phase 7c retro §4. |
| **Background tasks + cron** | Phase 10 | SPEC Tier 3 low priority. Not a SPEC §1 deliverable. |
| **`HookSpec` metadata** (version / description / source) | Phase 8c | Additive to `HookSpec` dataclass. Per Phase 5e retro §5. |
| **`oh hooks list` schema completeness** depends on the above | Phase 8c | Same. |
| **`oh tools list --runtime`** showing per-tool execution substrate | Defer | Subcommand UX polish. Not blocker. |

---

## Pointers

- Plan with task breakdown: [`tasks/phase-7-final-plan.md`](../tasks/phase-7-final-plan.md)
- SPEC §1 production-deliverable goal: [`SPEC.md`](../SPEC.md)
- SPEC §2 CLI command surface (12 listed, 3 shipped, 3 added here, 2 deferred indefinitely): [`SPEC.md`](../SPEC.md)
- SPEC §4.7 meta-retro: named as Phase 7 deliverable
- Status review triggering this boundary: conversation 2026-05-19
- Phase 6+ retro flagging `_build_query_context` refactor: [`learnings/phase-6plus.md`](../learnings/phase-6plus.md) §3.6
- Phase 5e retro flagging `oh hooks list`: [`learnings/phase-5e.md`](../learnings/phase-5e.md) §5
- Phase 7c retro flagging Firecracker: [`learnings/phase-7c.md`](../learnings/phase-7c.md) §4
- Architecture-tier reference: [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2 (Tier 0/1/2/3)
