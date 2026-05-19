# Phase 7 Final — Production Polish & Release

> Phase 7 boundary doc: **TBD — write `decisions/23-phase-7-final-boundary.md` at kickoff to ratify the decisions in §3 below**.
> Triggering review: [project status review on 2026-05-19](../learnings/) (16 phases shipped, 1240 tests, no user-facing docs, no PyPI artifact).

## 1. Overview

This is the **SPEC §1 closeout** — the original spec set a 2-3 month
horizon ending with "Phase 7 打磨与发布". After 16 phases of core
engineering (architecture + tools + safety + extensibility + REPL),
the framework is feature-complete by the original SPEC's tier
definitions, but lacks the **last mile to "production-grade"**:

- User-facing documentation (README is currently a per-phase log)
- CLI subcommands beyond `oh ask` / `oh chat` (SPEC §2 listed 12;
  only 3 ship today)
- Distribution path (no PyPI artifact; only `uv sync` from clone)
- Tutorial / quickstart / examples
- Final retrospective on the whole project

**This phase locks the SPEC v1 boundary**. Future phases (Anthropic
native client, LLM auto-compaction, memory system, Firecracker
substrate, etc.) are explicitly out of scope and become Phase 8+
candidates if pursued.

**Total scope**: ~1-2 weeks, 5 capabilities, ~10-15 commits, ~600 LoC
production (subcommands) + ~1500 LoC docs (README rewrite + tutorial).

---

## 2. Triggering observation

The project is mid-air for SPEC's two stated goals (SPEC §1):

| Goal | Status |
|---|---|
| **Production deliverable** — a Python CLI with mypy strict / ruff / pytest / CI / pre-commit / coverage gates | ✅ Engineering quality;❌ no published artifact, no user-facing docs |
| **Capability training** — domain expert + product engineer | ✅ 28 retros + 22 decision docs + 18 subsystems |

The first goal is **2 weeks of work away from done**. The second
goal is well-served but missing the **summary artifact** ("我从 0
构建生产级 harness" 复盘) the SPEC §1.7 explicitly named as Phase 7's
output and the strongest portfolio piece.

Without Phase 7:
- A new user clones the repo and faces a README that reads as a
  per-phase development log — they can't tell "what is this thing,
  why would I use it, how do I run it" in under 5 minutes
- The 5 missing SPEC §2 subcommands (`oh tools list/show`, `oh
  config show/edit`, `oh mcp add/list`, `oh hooks list/describe`,
  `oh skill run`) force users to read source + edit env vars to
  introspect / configure the framework
- The framework can't be `pip install`-ed; only `uv sync` from a
  clone

---

## 3. Decisions to ratify at kickoff

Promote to `decisions/23-phase-7-final-boundary.md` before T1 starts.

### D25.1 — README split: user-facing vs development log

Current README is ~1100 lines, ordered as Phase 1 → 2 → 3 → ... 6+
narratives. Replace with a **user-facing rewrite** (≤ 300 lines)
that answers in order: What / Why / Quickstart / How it works /
Configuration / FAQ. Move the per-phase narratives **verbatim** to
a new `docs/development-log.md` so the framework-builder retro
material isn't lost.

### D25.2 — Subcommand scope: 3 of the 5 missing series

Ship the 3 highest-value missing subcommand series; defer 2.

**In scope**:
- `oh tools list` + `oh tools show <name>` — list registered tools
  + show one's schema
- `oh config show` + `oh config edit` — print effective Settings
  + open the config file in $EDITOR
- `oh hooks list` + `oh hooks describe <name>` — list framework
  built-ins + discovered plugins; describe one

**Out of scope** (defer to Phase 8+):
- `oh mcp add/list` — MCP server config currently goes through
  Settings.mcp_servers env var; adding `oh mcp add` requires
  writing to a config file. This is a meaningful UX project but
  not blocker for SPEC v1.
- `oh skill run <name>` — skills today are LLM-facing (`LoadSkill`
  tool). Direct CLI invocation is a different mental model;
  potentially conflicting with the skill-as-knowledge framing.
  Defer until use case clarifies.

### D25.3 — Distribution: PyPI + `uv tool install`

Primary distribution channel is PyPI:

- `hatchling build` to produce wheel + sdist
- `uv publish` (or `twine upload`) to TestPyPI first, then PyPI
- README documents `uv tool install openharness` and
  `pip install openharness` paths
- **No `install.sh` shell script** in this phase — adds maintenance
  surface for marginal UX gain; defer if user demand surfaces

The PyPI publish step itself (the irreversible `uv publish` to
real PyPI) requires explicit user authorization and **is not part
of the auto-pilot work** — agent prepares the artifact + TestPyPI
dry run; user fires the production publish.

### D25.4 — Versioning: `0.1.0` for first release

First PyPI release is `0.1.0` — signals "not yet 1.0 stable API".
Going forward:

- Semver discipline: breaking change → minor bump until 1.0
- Conventional Commits already in use → enables `git-cliff` or
  similar for auto-changelog later (not in this phase)

`pyproject.toml` `[project] version` bumps once during T3.

### D25.5 — License: MIT

MIT chosen for:
- Maximum permissiveness (consistent with the "study source" target
  user identified in SPEC §1)
- Compatible with downstream commercial use without GPL viral
  concerns
- Matches the FOSS norm for Python developer tools (Typer / Pydantic
  / aiodocker are all MIT or compatible)

Add `LICENSE` file at repo root; reference in `pyproject.toml`.

---

## 4. Task list

### P7-T1: User-facing README rewrite + Quickstart 🔜 NEXT

**Description**: Replace the phase-log README with a user-facing
rewrite. Move existing narrative to `docs/development-log.md`.

**Acceptance**:
- [ ] `docs/development-log.md` created — contains the current
  README sections from "Phase 3 features" through "Phase 6+ features"
  verbatim (moved, not copied)
- [ ] `README.md` rewritten with this structure (≤ 300 lines):
  - **What this is** — one paragraph defining OpenHarness
  - **Quickstart** — 5-minute path to first `oh ask` (install,
    set `OPENHARNESS_API_KEY` / `OPENHARNESS_BASE_URL`, run example)
  - **Key features** — bullet list of capabilities (tool loop /
    sandbox / hooks / bundles / etc.) with links into
    `docs/development-log.md` for retrospective detail
  - **CLI reference** — terse listing of `oh ask` / `oh chat` /
    `oh tools` / `oh config` / `oh hooks` (will land after T2)
  - **Configuration** — env var reference table
  - **Architecture at a glance** — 1 paragraph + diagram(or
    `learnings/` link)
  - **Development** — local-dev quickstart(`uv sync`, `pre-commit
    install`, `pytest`, etc.)
  - **License** — MIT
- [ ] Existing README content that doesn't fit (Phase 1/2 setup
  details, dev workflow log) goes to `docs/development-log.md`
- [ ] No broken internal links after the move(`grep` for old
  README anchors in `learnings/` and `decisions/`)

**Files**:
- `README.md` (rewrite)
- `docs/development-log.md` (new — moved content)

**Sub-units**:
- 1a — Audit current README + extract narrative blocks to dev-log
- 1b — Draft new README structure + sections
- 1c — Wire up internal links, verify nothing breaks

---

### P7-T2: CLI subcommand completion (`oh tools` / `oh config` / `oh hooks`)

**Description**: 3 subcommand series filling the SPEC §2 gap.

**Acceptance**:

- [ ] `oh tools list`:
  - Prints `name` + `description` (one line each) for every tool in
    `create_default_tool_registry()`
  - Plain text by default; `--format json` for jq-friendly output
  - Doesn't require API key (offline-introspectable)

- [ ] `oh tools show <name>`:
  - Prints `name`, `description`, `is_read_only`, `trust_source`,
    `input_schema` (JSON-pretty)
  - Unknown name → exit 1 + "Unknown tool: ..." stderr with available
    catalog

- [ ] `oh config show`:
  - Prints effective `Settings` (post-env-var resolution) as a
    table or `--format json`
  - Redacts `api_key` (shows `***` + last 4 chars)
  - Doesn't require API key

- [ ] `oh config edit`:
  - Opens `$EDITOR` (or `nano` fallback) on
    `~/.openharness/config.toml` — creates the file with a
    commented-out template if it doesn't exist
  - NB: pydantic-settings doesn't read a TOML file by default —
    decide in this task whether to add TOML support (additive
    Settings work, ~30 LoC) or pick env-var-only documentation

- [ ] `oh hooks list`:
  - Prints `BUILTIN_HOOKS` keys + events (e.g.
    "audit_log → PostToolUse")
  - With `--enable-plugin-hooks`, also lists discovered
    entry-point + filesystem plugins

- [ ] `oh hooks describe <name>`:
  - Prints the hook's event + docstring (read via `inspect.getdoc`)
  - Unknown name → exit 1 with catalog

- [ ] Tests:
  - Each subcommand has happy + error-path tests via `CliRunner`
  - Existing `oh ask` / `oh chat` tests pass unchanged

**Files**:
- `src/openharness/cli.py` (+ ~200 LoC subcommand implementations)
- Possibly `src/openharness/config/__init__.py` if we add TOML support
- `tests/cli/test_subcommands.py` (new)

**Sub-units**:
- 2a — `oh tools list/show` + tests
- 2b — `oh config show/edit` + tests (settle TOML question)
- 2c — `oh hooks list/describe` + tests
- 2d — README CLI reference section wires up

---

### P7-T3: Packaging + Distribution metadata

**Description**: Polish `pyproject.toml` for PyPI release;
TestPyPI dry run.

**Acceptance**:

- [ ] `pyproject.toml` `[project]` section polished:
  - `version = "0.1.0"`
  - `description`, `readme = "README.md"`, `license = {text = "MIT"}`
  - `authors` (your name + email)
  - `urls` — repository, issues, changelog
  - `classifiers` — Development Status, Intended Audience,
    Programming Language, License, Operating System
  - `keywords`
  - `requires-python = ">=3.10"` (already set per SPEC §4)
  - `entry-points` — `oh = openharness.cli:main` (verify already
    correct)

- [ ] `LICENSE` file at repo root (MIT text)

- [ ] `CHANGELOG.md` initial version covering 0.1.0 — point to
  `docs/development-log.md` for detail; CHANGELOG itself is high-
  level user-facing release notes

- [ ] **TestPyPI dry run**:
  - `uv build` produces `dist/openharness-0.1.0.tar.gz` +
    `openharness-0.1.0-py3-none-any.whl`
  - `uv publish --publish-url https://test.pypi.org/legacy/`
    pushes to TestPyPI
  - Fresh venv: `pip install -i https://test.pypi.org/simple/
    openharness==0.1.0` → `oh --version` works
  - Documented in retro

- [ ] **PyPI publish — GATED**:
  - Real PyPI push (`uv publish` to production) requires explicit
    user fire-the-button. Agent prepares; user decides.

**Files**:
- `pyproject.toml` (polish)
- `LICENSE` (new — MIT text)
- `CHANGELOG.md` (new)

**Sub-units**:
- 3a — `pyproject.toml` metadata + LICENSE + CHANGELOG
- 3b — TestPyPI dry run
- 3c — User decides on real PyPI publish

---

### P7-T4: Tutorial + Examples

**Description**: Walked-through learning path + sample artifacts
users can copy.

**Acceptance**:

- [ ] `docs/tutorial.md` — 3 progressive scenarios:
  1. **First query**: `oh ask "what's in /tmp"` → walk through
     output, what Bash + Read did
  2. **Authoring a slash command**: drop `~/.openharness/commands/
     review.md`, run `oh ask "/review"`, see expansion
  3. **Read-only mode via bundle**: drop `~/.openharness/bundles/
     read-only.md` + the command that triggers it, demonstrate
     `deny_writes` hook blocking a Bash write attempt

- [ ] `examples/` directory at repo root:
  - `examples/commands/review.md` — sample slash command
  - `examples/skills/python-testing.md` — sample skill
  - `examples/bundles/read-only.md` — sample bundle
  - `examples/hooks/cost_guard.py` — sample filesystem plugin hook
    illustrating `@hook_spec("PreApiCall")`
  - `examples/README.md` — "how to install these examples" guide

- [ ] Tutorial cross-references README + dev-log for deeper detail

**Files**:
- `docs/tutorial.md` (new)
- `examples/` (new directory + 4-5 sample files)

---

### P7-T5: Final retro + DoD closeout

**Description**: The "我从 0 构建生产级 harness" retro SPEC §4.7
explicitly named. Also serves as portfolio artifact.

**Acceptance**:

- [ ] `learnings/phase-7.md` — meta-retro covering the entire
  16-phase journey:
  - **Quantified outcome** — 1240 tests / 18 subsystems / 22
    decision docs / 28 retros / ~10K production LoC / ~21K test LoC
    over N weeks
  - **What the SPEC originally locked vs what actually shipped** —
    the 7-phase → 16-phase split, why bonuses (5d/5e/5f/7c/8/6+)
    happened, which originally-planned items were deferred
    (Anthropic client, LLM compaction, memory, keyring, cron)
  - **Top 5 framework-level lessons** drawn from individual retros
  - **Top 3 Python-specific lessons** for the "capability training"
    goal SPEC §1 named
  - **Honest "what I'd do differently"** — at least 3 concrete
    items (e.g., factor `_build_query_context` earlier; ship
    smaller releases; choose Anthropic native earlier?)
  - **What's outstanding** — Phase 8+ candidates with brief
    rationale for each

- [ ] Phase 7 DoD checklist:
  - [ ] README rewritten + dev-log preserved
  - [ ] 3 subcommand series shipped + tested
  - [ ] PyPI artifact validated on TestPyPI
  - [ ] Tutorial + examples present
  - [ ] `learnings/phase-7.md` retro present
  - [ ] Full test suite still 1240+ passing, coverage ≥95%
  - [ ] mypy --strict clean, ruff clean

- [ ] **Optional but recommended**: blog post / dev.to / personal
  site draft of the "我从 0 构建生产级 harness" article based on
  this retro

**Files**:
- `learnings/phase-7.md` (new)
- `tasks/phase-7-final-plan.md` (this file — DoD closeout)

---

## 5. Explicitly NOT in scope (Phase 8+ candidates)

Recorded here so the boundary doesn't drift mid-phase. Each item
gets its own boundary + plan if pursued.

| Deferred item | Estimated phase | Rationale |
|---|---|---|
| **Anthropic native client** | Phase 8a | `protocols/` shape is Anthropic-native already;just needs an `AnthropicApiClient` impl of `SupportsStreamingMessages`. ~150 LoC. Real value when user wants prompt-caching / extended-thinking / computer-use features that DashScope doesn't expose. |
| **LLM auto-compaction (Layer 3)** | Phase 4.5 | Phase 4 ships Layer 1 (per-call truncation) + Layer 2 (PromptTooLong retry). Layer 3 (summarize old turns) is its own research project (prompt engineering, message-pair invariants). Important for long-conversation `oh chat` use cases. |
| **Memory system (basic)** | Phase 5g | YAML-frontmatter `~/.openharness/memory/*.md` with simple keyword retrieval. Reuses `markdown_store/` from Phase 8. SPEC Tier 2 ⭐⭐ priority. |
| **Keyring auth** | Phase 7a (small) | SPEC §2 explicitly names "Later phases may add keyring-backed profile management". Multi-profile API key switching. ~80 LoC. |
| **`oh mcp add/list`** | Phase 8b | MCP server config currently env-var only. Adding `add/list` means persisting to a config file (TOML/YAML). Couples to D25.2 deferred subcommand decision. |
| **`oh skill run <name>`** | Defer indefinitely | Skills are LLM-facing knowledge (the `LoadSkill` tool path). Direct CLI invocation is a conceptual conflict — defer until clear use case. |
| **REPL polish** — `/save` / `/load` / multi-line input / mode-switching | Phase 6++ | Per Phase 6+ retro §4. `prompt_toolkit` dependency required for multi-line. |
| **`_build_query_context` factor** | Phase 9 polish | `_run_ask` + `_run_chat` share ~150 lines of bootstrap. Per Phase 6+ retro §3.6 — refactor when 3rd consumer appears (`oh server`? `oh batch`?). |
| **Firecracker substrate** | Phase 7d | Different shape from gVisor (microVM, not OCI runtime swap). Substrate class needed. Per Phase 7c retro §4. |
| **Background tasks + cron** | Phase 10 | SPEC Tier 3, low priority. Not a SPEC §1 deliverable. |
| **`oh hooks list` schema completeness** — version/source/description | Phase 8c | Requires extending `HookSpec` dataclass (additive). Per Phase 5e retro. |

---

## 6. Checkpoints (per CLAUDE.md GREEN→review→commit)

- **After T1**: human review of new README — does it answer
  "what / why / how" in ≤ 5 minutes for a new reader? Does the
  dev-log preserve the framework-builder narrative for portfolio
  use?

- **After T2 (per subcommand)**: usability — does `oh config show`
  actually help a new user understand their config without reading
  source?

- **After T3**: TestPyPI install in fresh venv — does `oh --version`
  work? Does `oh ask` work end-to-end?

- **Before T3c (real PyPI publish)**: USER FIRES THE BUTTON. The
  agent prepares the artifact, runs the TestPyPI dry run, and
  confirms readiness. The actual `uv publish` to PyPI is
  user-authorized only — agent never publishes autonomously.

- **After T5**: full project DoD review. If anything missing →
  loop back; if green → tag `v0.1.0` and close Phase 7.

---

## 7. Pointers

- Status review that triggered this plan: (conversation 2026-05-19)
- Original SPEC Phase 7: [SPEC.md §1](../SPEC.md), [ARCHITECTURE.md §4.7](../ARCHITECTURE.md)
- 5 missing subcommand series specified in: [SPEC.md §2](../SPEC.md)
- Phase 6+ retro flagging the inline bootstrap refactor candidate: [`learnings/phase-6plus.md`](../learnings/phase-6plus.md) §3.6
- Phase 5e retro flagging the `oh hooks list` candidate: [`learnings/phase-5e.md`](../learnings/phase-5e.md) §5
