# Changelog

All notable changes to OpenHarness are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file is the **high-level user-facing release notes**. For the
per-phase development history (per-phase retrospectives + decision
records + framework-level lessons), see
[`docs/development-log.md`](./docs/development-log.md) and the
[`learnings/`](./learnings) directory.

---

## [Unreleased]

### Added — session goal: `/goal` 续跑式条件循环 (decisions/48)

`oh chat` gains `/goal <condition>`: an independent checker (the L3'
semantic judge, injection-guarded and meta-evaluated) evaluates the
conversation after every reply and auto-continues turns until the
condition holds — completion is decided by a fresh judge call, never by
the working model's self-report. Not-met verdicts feed back as
`[goal checker]`-framed guidance (never echoed as user input); met
clears the goal with a bell plus turns/tokens/elapsed stats. Bare
`/goal` shows status; `/goal clear` (aliases: stop/off/reset/none/
cancel) stops early. A settings backstop (`goal_max_auto_turns`,
default 25) pauses runaway loops — the recommended bound lives in the
condition itself ("or stop after 20 turns"). Goals survive `--resume`
via transcript sentinels (condition restored, counters reset). Judge
model configurable via `goal_judge_model` (defaults to the main model).
Behavior aligned with Claude Code's /goal (design researched from docs
+ binary, see docs/ideas/cc-goal-design-reverse.md).

### Added — plan mode: `/plan` + 审批菜单 (decisions/47)

`oh chat` gains a plan mode: `/plan` clamps the session to read-only
exploration (`Edit`/`Write`/`Bash` denied via a rules preset — the
clamp is the permission layer, not a prompt), the status toolbar shows
`[plan]`, and after every reply the harness renders a 3-option approval
menu (approve / keep planning / discard). Approving returns the session
to default mode without auto-executing the plan; the user decides the
next step (e.g. refine the plan or start `/goal`). A sentinel message
is injected into history so the model knows approval does not mean
"execute now". The model has no exit tool — only a menu choice can
leave plan mode. Menu EOF (non-interactive input) fails closed to
discard; Ctrl+C keeps planning.

### Added — REPL UX: 正门 / `/` 识别层 / 状态行 (decisions/42)

Bare `oh` now enters the chat REPL — one word enters the session
(`oh --help` unchanged). On a real terminal, typing `/` pops a
completion menu of everything dispatchable (built-ins + user commands
+ skills, D38.1 order, descriptions inline), input history persists
per project across sessions, and a status toolbar shows model +
context usage + auto-compact threshold while waiting for input.
Non-TTY invocations (pipes, CI, scripts) keep the legacy `input()`
path byte-for-byte. New dep: prompt-toolkit; retired: gnureadline
(the Phase 14.5 libedit workaround — prompt_toolkit's line editor
handles CJK/ASCII mixed-script width natively).

### Changed

- Default model is now `qwen3.7-max` (was `qwen-plus`; D5.3 update) —
  the model the later-phase evals already target — with its 262,144
  context window registered for compaction thresholds.

### Added — SWE-bench Lite adapter (benchmark track M1, decisions/40)

`oh bench swebench fetch / run` — drive the shipped `oh` CLI over
SWE-bench Lite and emit sb-cli-ready `predictions.jsonl` plus a
per-instance `records.jsonl` (the failure-taxonomy raw material).
The adapter is a *consumer* of the harness (subprocess-driven, D40.2):
headless fail-closed permissions with explicit `Edit/Write` allow rules
(`Bash(*)` only under `--sandbox`, D40.6), memory/snapshot forced off,
answer-leaking dataset fields (gold patch / hidden tests / hints)
firewalled out of prompt, argv, env, and repr (D40.3 红线, sentinel-
tested). Batch runs are serial, failure-isolated, and resumable; model
AND endpoint/key are resolved once at bench cwd and pinned into the
child env so records never lie about what ran (D40.8). Smoke-verified
end-to-end: qwen3.7-max produced a non-empty, `git apply --check`-clean
patch on `psf__requests-2317`.

### Fixed

- `openharness.__version__` drift: hardcoded `0.3.0` while pyproject/
  CHANGELOG were at 0.4.0 — surfaced by the adapter stamping the wrong
  version into `model_name_or_path`.

Phase 19 (M2 of CC Skill 接入) closes G1 — the per-file `cp`
friction Phase 18 M1 left behind. CC plugin directories
(`.claude-plugin/plugin.json` + `skills/<n>/SKILL.md` tree) are now
recognized alongside OH plugin manifests (`manifest.yaml`) under
the existing `~/.openharness/plugins/` root. One `cp -r` per CC
plugin gives you the namespaced skills triggerable via Phase 18 M1's
slash interface. `oh plugins list` adds the introspection entry
point so users can confirm a freshly-dropped plugin tree was picked up.

Phase 18 (M1) entry preserved below.

### Added — CC plugin loader (Phase 19 / M2)

- **Dual-format plugin discovery.** `PluginLoader.discover()` now
  routes per subdirectory by marker-file presence: CC plugins
  identified by `.claude-plugin/plugin.json`, OH plugins by
  `manifest.yaml`. When both markers exist, CC wins and a
  `plugin_dual_manifest` WARN event surfaces the picked/ignored
  pair (D39.6). Subdirectories with neither marker are silently
  skipped (D39.4) — `README.md` / `.git` / drafts don't generate
  log noise.
- **`parse_cc_plugin`.** CC plugin.json fields project into the
  existing `PluginManifest` dataclass (D39.2 — no parallel CC
  dataclass): `name` / `version` / `description` required;
  `author.name` (nested object) flattens to `PluginManifest.author`
  (str). License / homepage / keywords / dependencies / commands /
  bundles / hooks all collapse to `None` / `()` because CC's
  manifest schema doesn't carry them.
- **CC SKILL.md directory tree.** `<plugin>/skills/<n>/SKILL.md`
  files are discovered alphabetically into `PluginManifest.skills`,
  flow through Phase 9's `_fan_out_skills` namespacing path
  (`<plugin>__<skill>` storage keys), and reach `SkillStore` /
  Phase 18's slash resolver byte-identically.
- **`oh plugins list` subcommand.** Read-only 5-column view:
  `NAME / FORMAT / VERSION / SKILLS / MCP_SERVERS`, alphabetical by
  plugin name. `--format json` for jq pipelines.
  `--log-level INFO` surfaces the `plugin_discovered` events that
  fire during discovery (D39.8 observability marker for
  `format=cc|oh`).
- **`plugin_discovered` payload extension.** The bootstrap-time
  discovery event now carries `format` (`cc` or `oh`) +
  `skills_count` + `mcp_servers_count` alongside the existing
  plugin identity fields (D39.8). Auditors can grep
  `format=cc` to confirm CC plugins are loading, or
  `mcp_servers_count=0` on a `.mcp.json`-bearing plugin to verify
  D39.9 silent-ignore is firing as designed.

### Reversed mid-phase — D39.5 → D39.9

- **`.mcp.json` is silently ignored in M2.** D39.5 originally
  ratified parsing CC `.mcp.json` into `PluginManifest.mcp_servers`,
  claiming "schema 等价" to OH's `mcp_servers:` block. Pre-T1.1
  audit caught the gap: OH's MCP layer is **stdio-only** (D15.1,
  Phase 5), but CC `.mcp.json` examples in finance-skills are
  uniformly HTTP+OAuth2. D39.9 reversed the decision before any
  parser code shipped. CC plugins with `.mcp.json` on disk discover
  cleanly but report `MCP_SERVERS=0` in `oh plugins list` — honest
  reporting that the M2 boundary does not include MCP transport
  extension. HTTP MCP support gets its own future phase.

### Notes — what M2 does NOT include

- **CC `agents/<n>.md` declarative sub-agents** — M3 / Phase 20.
  `tools:` whitelist semantics map to OH's tier-based permission
  model in a non-trivial way; Phase 20 boundary doc will need a
  dedicated sub-decision for that mapping.
- **`marketplace.json`** — multi-plugin aggregation manifests
  remain `cp -r` per plugin in M2.
- **`~/.claude/plugins/` second discovery root** — D39.3
  consciously kept M2 single-root; a `Settings.plugin_dirs` list
  is the natural extension when a driver appears.
- **`.mcp.json` parsing** — per D39.9; the file presence on disk
  does not influence the parsed manifest.
- **`oh plugins show / enable / disable / refresh`** — D39.7
  ratified ship-now scope as `list` only; the other introspection
  + control verbs follow when a driver appears.

### Friction surfaced at dogfood

- **`--enable-plugins` flag.** Phase 9 D24.x defaults plugins off
  (security — Python hook modules ship arbitrary code). To trigger
  `/<plugin>__<skill>` in `oh chat` or `oh ask`, pass
  `--enable-plugins`. Single-file skills in
  `~/.openharness/skills/` (the Phase 18 M1 path) don't need this
  flag — only plugin-installed skills do.

### Fixed — D38.8 hotfix (post-Phase-19, user-time)

- **Synth envelope is now always 3 messages.** Phase 18 D38.3's
  empty-args branch (`/<skill>` typed with no args → 2-message
  envelope ending on `tool_result`) provoked HTTP 400 from
  thinking-mode providers (qwen3.7-max observed) with
  `"reasoning_content in thinking mode must be passed back"`. The
  synthesized assistant `tool_use` has no real thinking trace, and
  the 2-message shape gave no structural break to satisfy the
  provider's check. D38.8 reverses D38.3 for the empty-args clause
  only: a trailing user `TextBlock` carrying
  `DEFAULT_EMPTY_ARGS_PLACEHOLDER = "Please apply this skill now."`
  is always appended. Envelope is now byte-shape-identical to the
  args-present case; all known providers accept. The fix is
  protocol-level (no provider-specific patches). Phase 18 T4 and
  Phase 19 T4 dogfoods both used non-empty args so the empty path
  was never exercised end-to-end — the methodology lesson is in
  `learnings/phase-19.md` §2 (e).

### Documentation — Phase 19

- [`decisions/39-phase-19-boundary.md`](./decisions/39-phase-19-boundary.md) —
  scope, the 9 D39 sub-decisions (D39.5 marked REVERSED with
  audit trail preserved), and the §六 wiring audit (16 layers
  predicted).
- [`tasks/phase-19-plan.md`](./tasks/phase-19-plan.md) — T1–T5
  capability-level plan including the T1.0 proactive guard
  (`openharness.plugins` added to `engine/slash_skill.py`'s
  forbidden-imports test before any parser code landed).
- [`learnings/phase-19.md`](./learnings/phase-19.md) — close-out
  retro with dogfood evidence (4-skill workflow surfaced from a
  single trigger via Phase 5c catalog injection), §六 verdict
  mapping (15 of 16 verbatim + the D39.9 self-correction
  discussed honestly), and M3 / Phase 20 predictions.

---

Phase 18 (M1 of CC Skill 接入) — `oh chat` REPL now triggers user-
installed skills via `/<skill-name> [args]` directly, matching the
Claude Code UX. SkillStore lookup is wired as the second resolver
fallback after CommandStore, so existing Phase 5b slash commands
keep priority; an unknown `/<name>` prints a difflib-based
"Did you mean a skill?" hint. The full `parse-credit-report` dogfood
from the finance-skills repo runs end-to-end in <1 second per slash
without any plugin loader yet — drop a single `.md` file into
`~/.openharness/skills/` and it works.

### Added — Slash-skill triggering (Phase 18 / M1)

- **`/<skill-name> [args]` in `oh chat` REPL.** Resolver order:
  built-in (`/exit` / `/clear` / `/compact` / `/help` / `/skills`)
  → user CommandStore → user SkillStore → unknown with closest-
  skill hint via `difflib.get_close_matches`. Skill-name hit
  synthesizes a 2- or 3-message LoadSkill envelope
  (`assistant tool_use(LoadSkill)` + `user tool_result(skill.body)`
  + optional `user TextBlock(args)`) and extends conversation
  history before the next LLM turn. The synth envelope **bypasses**
  hook chain + permission checker + actual tool execution
  (D38.5 deliberate bypass — UI action, not LLM action), and
  emits a single observability INFO event
  `slash_skill_invoked` with `synthetic: true` marker for
  audit / debugging.
- **`/skills` built-in REPL command.** Lists the discovered
  skill catalog as alphabetical `<name>  <description>` rows;
  empty catalog prints `(no skills installed)`. Helps confirm
  a freshly dropped `SKILL.md` was picked up.
- **`engine.slash_skill.synthesize_skill_envelope` helper.**
  Pure function returning the D38.2 envelope shape; importable
  for tests and downstream consumers. Imports nothing from
  `tools/` / `permissions/` / `hooks/` / `observability/` /
  `cli/` — architecture isolation enforced by static-AST check
  in `tests/engine/test_slash_skill_envelope.py`.
- **`synth_<id>` tool-use-id prefix.** Distinguishes
  user-typed `/<skill>` envelopes from real LLM-driven
  `LoadSkill` calls in snapshots, logs, and compaction. The
  marker lives only in the envelope helper + observability
  event payload; `services/compact.py` stays synth-unaware
  (forcing function test asserts no `synth_` literal in
  `compact.py`).

### Notes — what M1 does NOT include

- **CC plugin directory format** (`.claude-plugin/plugin.json`,
  `skills/<n>/SKILL.md` directory shape, `.mcp.json`). M1 only
  reads the existing OH single-`.md` skill format; the
  finance-skills dogfood required one `cp` per skill. CCPluginLoader
  is M2 / Phase 19.
- **CC declarative agents** (`agents/<n>.md` with `tools:`
  whitelist). M3 / Phase 20.
- **`oh ask "/<skill>"`** — single-turn `oh ask` is not extended;
  M1 is chat-only (D38.6 deferred until M2 / M3 clarify the
  single-turn folding shape).
- **`{args}` substitution into skill body.** CC's SKILL.md does
  not use `{args}` placeholders, so M1 doesn't either; args land
  in the trailing user message instead (D38.3). Dogfood
  validated this — LLM correctly read args as "task subject"
  vs body as "expert guidance."

### Documentation — Phase 18

- [`decisions/38-phase-18-boundary.md`](./decisions/38-phase-18-boundary.md) —
  scope, capability list, the 7 D38 sub-decisions, and the §六
  wiring audit (13 layers / verdicts) for M1.
- [`tasks/phase-18-plan.md`](./tasks/phase-18-plan.md) — T1–T5
  capability-level plan + acceptance criteria.
- [`learnings/phase-18.md`](./learnings/phase-18.md) — close-out
  retro: dogfood evidence, 13/13 §六 verdicts held, M2 + M3
  predictions for `synthesize_skill_envelope` reuse.

---

## [0.4.0] — 2026-06-06

Phase 16 (memory architecture pivot to Claude-Code-style LLM-
self-decides) + Phase 17 (memory substrate cleanup + methodology
evolution) cycle, plus one dogfood-driven hotfix discovered
between the two phases. Theme: the harness's writing surface for
durable memory is now the main LLM's responsibility — emitted
inline during the conversation via `Write` + `Edit` tools — not a
per-turn secondary-LLM-pass.

The Phase 11 `extract_memories_from_turn` machinery shipped in
v0.2.0 and refined in v0.3.0 is gone. What replaces it is a
system prompt section (the same prompt Claude Code uses, with the
project's memory dir interpolated) plus a session-start MEMORY.md
index injection that lets the LLM see what memories already exist
without paying for harness-side relevance ranking.

### Added — Claude-Code-style memory architecture (Phase 16)

- **`## Memory` system prompt section** — project-agnostic copy
  with the four type definitions (`user` / `feedback` / `project`
  / `reference`), the two-step write contract (`Write` the `.md`
  body then `Edit` MEMORY.md to add the pointer line), `[[slug]]`
  forward-reference syntax for linking related memories, and the
  200-line cap on MEMORY.md so it fits in every session-start
  injection.
- **Session-start MEMORY.md injection** — `oh ask` and `oh chat`
  both read `<memory_dir>/MEMORY.md` and inject it as
  `### Memory Index` inside the `## Memory` section. `oh chat`
  rebuilds the injection every turn so the LLM sees writes from
  the previous turn the next time it reasons. WARN log when the
  index exceeds 200 lines; quietly fall back to an empty
  placeholder on `OSError`.
- **`evals/memory_decision/` — Stage 1-5 substrate consumer** for
  decision surface #4 (inline decision class side effects) per
  [`decisions/35-eval-coverage-map.md`](./decisions/35-eval-coverage-map.md)
  §D35.5. Six capability-anchored samples (2 cold-start + 3
  warm-start + 1 trivial-skip), five scorers (judgment +
  frontmatter validity + index update + no destructive overwrite
  + memory-type LLM-judge), four new `M-judge-*` rubrics. The
  infer call is multi-turn with real tool execution scoped to
  `/tmp/oh_eval_memory_decision/<case>/` — single-turn proved
  insufficient to discriminate model gap from eval scaffold gap.
- **`oh eval memory_decision` CLI subcommand** — mirrors `oh eval
  focus_state` (`--mode live/record/replay`, `--model`,
  `--no-results`); shares `CassetteStore` + `RunMetadata`
  persistence with focus_state; ships parallel scorer + runner
  for the multi-turn shape.
- **§六 Wiring audit discipline** in CLAUDE.md (methodology
  evolution) — every future phase boundary doc enumerates each
  runtime layer the new contract crosses + a one-sentence verdict
  per layer (`unchanged` / `requires extension` / `requires
  bypass`). [`decisions/37-phase-17-boundary.md`](./decisions/37-phase-17-boundary.md)
  §六 is the reference implementation; the motivating incident is
  the Phase 16 T5 dogfood Gap A (permission tier blocked the
  memory writes).

### Added — Phase 17 schema acceptance + CLI rendering

- **`Memory.id` is optional** — frontmatter omitting the field
  triggers an auto-generated `sha1(name + str(path.resolve()))[:16]`
  id. Phase 10/11 files that DO carry an id keep theirs verbatim
  (frontmatter wins). Deterministic across reparses of the same
  file.
- **CC-schema fields auto-fill** — `parse_memory` now accepts the
  three-field D36.10 shape (name + description +
  `metadata.type`) and fills `scope` → `private`,
  `created_at` → `now()`, `updated_at` → `created_at`,
  `signature` via the existing body-hash. The
  `metadata.type` nested-frontmatter location is accepted
  alongside the Phase 10 top-level `type:` field; top-level
  wins when both exist.
- **`oh memory list` adapted to D36.10** — three text-format
  columns (name truncated to 40 chars, type, description
  truncated to 60 chars) sorted alphabetically by name (case-
  insensitive). The JSON format is unchanged so any consumer
  scripting against `--format json` keeps working.
- **Memory-dir Tier 3 permission exception** — `Write` and
  `Edit` calls targeting paths under
  `get_project_memory_dir(cwd)` bypass the "writes must be
  inside cwd" rule. The exception is narrow: it short-circuits
  only when the path resolves under the deterministic
  per-project memory dir, leaving every other outside-cwd write
  to ASK as before. Closes the dogfood-discovered Gap A.

### Changed

- **`oh memory list` columns** — was 5 (name / type / use_count /
  last_used_at / description), now 3 (name / type / description).
  `use_count` and `last_used_at` are still in `--format json`
  output for backward compat, but the text view dropped them
  because the relevance-tracker machinery that populated them is
  gone.
- **`oh memory list` sort key** — was `(-use_count, name)`, now
  alphabetical `name.lower()`. CC-style memory dirs have a
  longer tail of equally-relevant entries; alphabetical scales
  better than use-count for that distribution.
- **`oh memory list` empty message** — was
  `"(no memories — storage at <path>)"`, now `"(no memories
  yet)"`. The storage-path hint was useful when the user was the
  memory writer (Phase 10); the main LLM is the writer now, so
  the path is information the user no longer needs.
- **Memory model field order** — `id` moved from required-
  positional to the optional-with-defaults section of the frozen
  dataclass. Constructor kwargs are unchanged; only positional
  construction breaks, which no caller used.

### Removed

- **Phase 11 extraction stack** — `services/extract.py`,
  `_maybe_extract_memories` (engine), `QueryContext.extract_*`
  fields, `ExtractionSettings` (config), `--no-extract` CLI flag,
  three test files, the matching `ExtractionSettings` test
  classes. The Phase 11 secondary-LLM-pass that proposed 0-3
  memories per turn is wholly replaced by the LLM's inline
  Write / Edit during the conversation. Code retained as a
  flag-gated safety net through v0.3.x; six commits without a
  rollback in Phase 16 proved deprecation was complete.
- **`select_relevant_memories` and `mark_memory_used` re-exports**
  from `openharness.memory` package — the symbols still exist in
  the deprecated `memory.relevance.py` and `memory.usage.py`
  modules so the algorithm-level unit tests keep passing, but
  the public API stopped re-exporting them in Phase 16.

### Fixed

- **Tier 3 permission tier blocked memory writes** — D28.1 puts
  the memory dir outside cwd (so it can't be accidentally
  committed); P3-T3.3c requires writes inside cwd; D36.10 made
  the main LLM the memory writer. The three intersected as
  "permission denied (requires confirmation): outside project
  root" on every memory-write attempt. The 2026-06-06 dogfood
  surfaced it; commit [50bc5fe] added the deterministic
  memory-dir bypass in `_matches_tier3`.
- **Parser warning-skipped CC-style memory files** — the
  2026-06-06 dogfood showed `oh memory list` displaying zero
  memories even with two CC-shape files on disk. Phase 17 T1
  taught `parse_memory` to accept the CC schema; `oh memory
  list` now shows them.
- **`FilesystemMemoryStore.discover()` warned on MEMORY.md** —
  every session startup logged
  `memory_missing_frontmatter source_path=.../MEMORY.md`
  because the parent class's glob picked up the LLM-visible
  index file and tried to parse it. Phase 17 T3 added a
  one-line filter inside `parse_memory` itself, keeping the
  shared markdown_store substrate content-agnostic.

---

## [0.3.0] — 2026-06-03

Phase 14 (web tools + anti-substitution prompt) + Phase 15
(rich.Live TTY spinner) cycle, plus 6 dogfood-driven patches
collected between v0.2.0 and this release. Theme: turning v0.2.0's
"shipped feature-complete harness" into "shipped harness that
actually feels good when you use it" — every defect listed below
came from real `oh ask` / `oh chat` invocations, not synthetic
tests.

### Added — web tools (Phase 14)

- **`WebSearch` tool** — discover URLs for a topic via a pluggable
  provider; ships with `TavilySearchProvider` (Tavily free tier:
  1000 searches/month, no credit card). `--enable-web` opt-in;
  `OPENHARNESS_WEB__API_KEY` required when ON.
- **`WebFetch` tool** — GET a URL, strip `<script>` / `<style>` /
  `<nav>` / `<aside>` / `<header>` / `<footer>` via BeautifulSoup,
  render the rest to markdown via `markdownify`, truncate at
  `max_chars` (default 10000) with the same `[+N chars truncated]`
  suffix Phase 4 microcompact uses for tool-result clipping.
  Streaming body cap (default 5MB) aborts pathological pages
  mid-fetch.
- **`WebSearchProvider` Protocol** — Tavily as v1 default behind
  the Protocol; future Brave / Serper providers land as siblings
  without touching the `WebSearch` tool.
- **`--enable-web` CLI flag** on `oh ask` + `oh chat` (mirrors
  `--enable-plugins` / `--enable-memory` pattern).
- **Nested `WebSettings`** under `Settings`:
  `OPENHARNESS_WEB__ENABLED`, `OPENHARNESS_WEB__SEARCH_PROVIDER`,
  `OPENHARNESS_WEB__API_KEY` (SecretStr), plus fetch timeout /
  body cap / default char cap tunables.

### Added — system prompt anti-substitution guard (Phase 14, THE bug fix)

- **`web_enabled` three-state kwarg** on `build_system_prompt`:
  `None` (byte-identity branch — Phase 13 callers unchanged),
  `True` (`## Web Access` positive-guidance section), `False`
  (`## No Internet Access` anti-substitution section).
- When `--enable-web` is OFF, the default system prompt now tells
  the LLM explicitly: "you have no internet access; do NOT
  substitute Grep or Read on local files for web queries". This
  fixes the v0.2.0 dogfood defect where an LLM asked for
  "research latest LLM developments" Grep'd local notes and
  confabulated findings with fabricated specs.

### Added — v0.2.0 patch chain (4 bug fixes between v0.2.0 and v0.3.0)

- **`MalformedToolCallFailure`** — defensive JSON parse in
  `_StreamAssembler.finalize()`. When the LLM's `max_tokens` cap
  truncates a tool call's `arguments` string mid-JSON, surface a
  category-specific friendly error instead of a raw
  `JSONDecodeError` traceback. Heuristic on the parser's
  `Unterminated string` message routes the error to the
  `--max-tokens` hint regardless of how the provider labeled
  `finish_reason` (DashScope reports `tool_calls` here, not
  `length`).
- **`oh chat` REPL survives API errors** — broadened `except
  LoopError` to also catch `OpenHarnessApiError`. A single bad
  turn no longer kills the entire session; the user can `/clear`,
  adjust flags, or retry.
- **`readline` enabled in `oh chat`** — side-effect `import
  readline` gives backspace, arrow-key cursor motion, history
  navigation, and Ctrl+R search inside the REPL prompt. Without
  this, raw `input()` echoed characters but ignored erase keys
  (libedit on macOS, GNU readline on Linux; Windows no-ops via
  `contextlib.suppress(ImportError)`).
- **`DEFAULT_MAX_TOKENS`: 1024 → 8192** — Phase 1's 1024 default
  was set when there were no tools; with tool-use ship (Phase 2)
  and especially `Write` / `Agent` tool calls that emit file
  content as the `arguments` JSON, 1024 routinely truncated
  mid-string. 8192 aligns with Claude Code / industry harness
  defaults.

### Added — TTY rendering polish (Phase 15)

- **`rich.Live` spinner for tool calls** — TTY-only animated
  spinner replaces the previous "tool ran, here's the result" lump
  with a per-tool-call spinner that ticks during dispatch and
  collapses into the rendered result on completion. Visual
  feedback the user sees while a `Bash` / `WebFetch` is running.
  Detects TTY via `sys.stdout.isatty()`; non-TTY (CI logs, pipes)
  falls back to the original plain text rendering.
- **Tool output preview whitespace strip** — surrounding
  whitespace stripped from the preview line so multi-line tool
  outputs don't leave a leading blank inside the rendered block.

### Fixed — Phase 14.5 + 14.6 dogfood patches

- **`gnureadline` macOS-only dependency** — the stdlib `readline`
  on macOS is backed by `libedit`, which has a known bug computing
  cursor positions when an input line mixes CJK (wide) and ASCII
  (narrow) characters. Backspace lands at the wrong byte offset
  and the user cannot delete portions of a mixed-script prompt in
  `oh chat`. `gnureadline` (declared `sys_platform == 'darwin'`)
  is imported first in `cli.py`; Linux already ships GNU readline
  natively; Windows still no-ops via `contextlib.suppress`.
- **Web tools default ON with graceful no-key degrade** —
  `WebSettings.enabled` default flipped False → True (mirrors
  Claude Code / Cursor / industry harness defaults). When the
  default-ON path encounters no `OPENHARNESS_WEB__API_KEY`,
  `_maybe_register_web_tools` skips registration silently and
  returns False, and the system prompt falls back to the
  anti-substitution paragraph — new users without a Tavily key
  see v0.2.0 behavior rather than a crash. Explicit
  `--enable-web` + no key still hard-fails with the original
  remediation message.
- **Chat-aware base system prompt (Phase 14.6)** — added one
  sentence to `_BASE_INSTRUCTIONS`: *"Match response length and
  tool use to user intent — don't pre-emptively explore the
  filesystem or invoke tools for greetings or casual messages."*
  Fixes over-eager `oh chat` responses where simple greetings
  ("hi") triggered `ls -la` + workspace exploration + "what
  would you like to work on?" verbosity.

### Added — runtime dependencies

- `markdownify>=0.11,<1.0` — HTML → markdown converter for
  `WebFetch`.
- `beautifulsoup4>=4.12,<5.0` — promoted from transitive (pulled
  by markdownify) to explicit since `WebFetch` uses its API
  directly for the chrome-stripping pre-pass.
- `gnureadline>=8.2; sys_platform == 'darwin'` — macOS-only GNU
  readline binding (Phase 14.5).

### Quality bars

- **~2029 tests passing** on CI (1982 at v0.2.0 → 2029; +47 net
  across Phase 14 + 4 v0.2.x patches + Phase 14.5/14.6 + Phase 15).
- **mypy --strict src/** clean throughout.
- **ruff** check + format clean.
- **≥95% coverage** gate held on Python 3.10 / 3.11.
- 11 protected directories: 10/11 zero-diff between v0.2.0 and
  v0.3.0. `prompts/` is the one exception, holding the
  `web_enabled` kwarg + the `## Web Access` / `## No Internet
  Access` section (Phase 14 D29.6) and the chat-aware
  `_BASE_INSTRUCTIONS` sentence (Phase 14.6) — both explicitly
  documented as user-feedback-driven exceptions to invariant T14-6.
- 6 existing tools (`Read` / `Write` / `Edit` / `Bash` / `Grep` /
  `Agent`) byte-identical.
- `services/summarize.py` + `services/snapshot.py` +
  `services/session_memory.py` + `services/focus_state.py`
  byte-identical.

---

## [0.2.0] — 2026-05-28

Post-v1 extension cycle: 5 phases (Phase 9-13) shipped on top of
the v0.1.0 substrate. The cycle's central thesis — "abstraction-first
compounds" — held under three independent stress tests:
`markdown_store/` (Phase 8 substrate) absorbed memory as its 6th
consumer with zero diff; `services/summarize.py` (Phase 11) was
reused by 7 consumers across Phases 11–13 with zero diff; 11
protected directories saw zero diff across Phases 12–13.

### Added — plugin manifest unification (Phase 9)

- **Unified `~/.openharness/plugins/<name>/manifest.toml`** — a
  single TOML file registers hooks, skills, commands, and bundles
  for one plugin. Supersedes the per-source discovery pattern from
  Phase 5e (entry points) and 5f (filesystem hooks) for plugin
  distribution.
- `--enable-plugins` opt-in flag.
- **Plugin-scoped namespacing** — hook names from a plugin appear
  as `<plugin>__<hook>` to prevent collisions with built-ins or
  other plugins.

### Added — memory subsystem (Phase 10 + Phase 11 extraction)

- **Read path (Phase 10)** — YAML-frontmatter memory files at
  `~/.openharness/memory/*.md` (user scope), with project override
  at `<cwd>/.openharness/memory/`. Three scopes: user / project /
  team. Relevance scoring (meta hits + body hits + importance +
  recency boost); zero-token-hit memories drop before injection.
- `--enable-memory` flag + nested `OPENHARNESS_MEMORY__*` config.
- `oh memory list / show / path` read-only subcommands.
- Per-access atomic `use_count` tracking
  (`tempfile + os.replace`).
- **Write path (Phase 11)** — post-turn LLM extraction writes new
  memories. Signature-dedupe prefers same `name`. Team-scope writes
  pass through a 6-pattern secret scanner (PEM / AWS / GitHub /
  Anthropic / OpenAI / generic) — secrets are silently dropped.
- `--no-extract` flag + nested `OPENHARNESS_EXTRACTION__*` config
  (default OFF for stub-LLM testability; opt-in via flag or env).
- 22-word English stopword list (only subtracted from query, not
  memory body) + tightened surface threshold
  (`meta_hits >= 1 OR body_hits >= 2`).

### Added — summarization substrate + auto-compaction (Phase 11)

- **`services/summarize.py`** — shared LLM-dispatch primitive with
  3-layer retry (asyncio timeout / PTL drop-oldest / streaming
  retry). Defensive list-copy so retries never mutate caller
  messages.
- **4-tier auto-compaction** runs before each LLM call: L0 token
  estimate → L2 deterministic head/tail collapse → L3 session-memory
  checkpoint reuse (1h freshness) → L4 LLM-driven 9-slot full
  compact.
- `--no-auto-compact` / `--compact-threshold` flags + nested
  `OPENHARNESS_COMPACT__*` config.
- `/compact` REPL command — forces L4 full compact regardless of
  threshold.
- **`services/session_memory.py`** — per-turn 5-slot checkpoint
  writer at `~/.openharness/session-memory/<cwd-hash>/`, 12k-char
  cascade cap (conversation pop → artifact pop → hard truncate).
- **New `HookSpec.re_run_on_reactive_rebuild: bool = False` field**
  — hooks that need re-running after a PTL drop-oldest rebuild can
  opt in; the engine reapplies only the marked subset. Closes
  Phase 4's reactive-PTL debt without touching default behavior.

### Added — session snapshot + resume (Phase 12 + Phase 13)

- **Per-turn snapshot writer (Phase 12)** — atomic JSON write to
  `~/.openharness/snapshots/<cwd-hash>/current.json` after every
  assistant turn, capturing full `QueryContext` + message history
  + turn metadata.
- `--resume` (most recent for this cwd) and `--resume-id <id>`
  (specific snapshot) CLI flags; `[resumed: <id>]` banner confirms
  the load.
- `QueryContext.from_snapshot(...)` factory.
- Nested `OPENHARNESS_SNAPSHOT__*` config.
- **History rotation (Phase 13)** — snapshots rotate from
  `current.json` to `history/<git-head>-<utc-ts>.json` on each
  write, with `SnapshotHistorySettings.max_count` and
  `max_age_days` GC policies. Atomicity via the same
  `tempfile + os.replace` pattern as the current-file write (no
  hardlink dependency — works on FAT / Windows mount).
- `oh snapshot list / show / gc` subcommands (mirrors
  `oh memory`); `show current` literal; `show <prefix>`
  prefix-matches git-head with ambiguous-match error.
- **LLM-authored `task_focus_state` metadata (Phase 13)** —
  opt-in `--llm-focus-state` flag triggers an extra LLM call per
  turn that infers a structured focus-state snapshot (current
  task / next step / blockers) via `services/focus_state.py`.
  Default OFF to avoid the stub-LLM testability tax that Phase 11
  extraction surfaced.

### Changed

- `_maybe_write_turn_end_metadata` engine helper became `async`
  to await `infer_focus_state(...)`. Private (`_`-prefix) helper,
  no external caller break; documents an `async`-contagion pattern
  for similar future helpers.
- `build_system_prompt` gained `claude_md_content=...` and
  `memory_manifest=...` additive kwargs — default `None`,
  byte-identical to v0.1.0 callers.

### Quality bars

- **1982 tests passing** on CI (1274 → 1982 from v0.1.0; +708 net
  across Phases 9–13).
- **≥95% coverage** gate held on Python 3.10 / 3.11.
- **mypy --strict** clean throughout.
- **ruff** check + format clean.

---

## [0.1.0] — 2026-05-20

First public release. The SPEC v1 boundary closes here: a feature-
complete Python LLM agent harness with 16 capability phases shipped
over ~3.5 weeks, 1274 tests passing on CI at 95.33% coverage,
mypy strict + ruff clean throughout.

### Added — engine + tool loop

- `oh ask "<prompt>"` — single-shot streaming CLI against any
  OpenAI-compatible Provider (Qwen via DashScope is the default
  test target; OpenAI cloud, DeepSeek, Moonshot all work via
  `OPENHARNESS_BASE_URL` swap).
- `run_query()` async-iterator agent loop driven by Anthropic-shape
  `stop_reason` semantics (`end_turn` / `tool_use` / `max_tokens` /
  `stop_sequence`). Caller's `initial_messages` never mutated.
- 6 built-in tools: `Read` / `Write` / `Edit` / `Bash` / `Grep`,
  plus `Agent` (recursive `SpawnAgent` for sub-task delegation).

### Added — safety + observability

- 3-tier permission system: hardcoded sensitive-path deny + user
  `OPENHARNESS_DENY_PATHS` glob deny + `--auto` / `--dry-run`
  permission mode override.
- Hook middleware — 5 lifecycle events (`PreToolUse`, `PostToolUse`,
  `PreApiCall`, `PostApiCall`, `OnError`) with deny / modify /
  allow result semantics.
- Structured JSON logging via `structlog` with `run_id` / `turn_id` /
  `agent_depth` context binding. `OPENHARNESS_LOG_FORMAT=json` for
  `jq`-friendly trace reconstruction.
- Differentiated error UX — separate handlers for `Configuration` /
  `Authentication` / `RateLimit` / `Request` / `Loop` errors. No
  Python tracebacks in default mode.

### Added — context management

- Layer 1 per-tool-result truncation via `TruncateToolResultHook`
  (tiktoken-counted; default cap 10,000 tokens; configurable via
  `OPENHARNESS_TOOL_RESULT_CAP`).
- Layer 2 reactive `PromptTooLong` retry — drops the oldest
  `tool_use`/`tool_result` pair and retries; bounded by
  `_REACTIVE_TRUNCATE_MAX`.

### Added — extensibility

- **MCP** (Model Context Protocol) — stdio transport adapter
  registers third-party tool servers into the same `ToolRegistry`
  the engine consumes. Configure via `OPENHARNESS_MCP_SERVERS`.
- **Slash commands** — drop a markdown file at
  `~/.openharness/commands/<name>.md` (or project-local
  `.openharness/commands/`); invoke as `oh ask "/<name> args"`.
- **Skills** — lazy-loaded expertise. The LLM sees a catalog;
  calls `LoadSkill` to expand specific entries on demand. Drop
  files at `~/.openharness/skills/<name>.md`.
- **ModeBundle** — compose system prompt + tool whitelist + extra
  deny_paths + named hooks into one named "mode" referenced from a
  slash command's `mode:` frontmatter. First cross-layer composition
  tenant; engine sees a fully-resolved `QueryContext`, never the
  Bundle concept.
- **Plugin hooks** — opt-in via `--enable-plugin-hooks`. Two
  discovery sources:
  - Python entry points (`openharness.hooks` group)
  - Filesystem `*.py` files at `~/.openharness/hooks/`
- 2 framework built-in hooks bundled: `audit_log` (PostToolUse
  compliance trace) and `deny_writes` (PreToolUse read-only mode).

### Added — sub-agent + sandbox

- `SpawnAgent` tool for recursive task delegation with bounded
  depth (`OPENHARNESS_MAX_AGENT_DEPTH`, default 3). Sub-agents
  inherit immutable context via `dataclasses.replace`.
- **Docker sandbox** via `--sandbox` — `aiodocker`-driven container
  with cwd bind-mount, `network=none` by default, cgroup limits
  (memory / CPU / pids).
- **gVisor runtime** via `--sandbox-runtime runsc` — selectable
  OCI runtime for user-space syscall isolation. Other OCI runtimes
  pass through (`kata`, `sysbox`, etc.).

### Added — multi-turn REPL

- `oh chat` — async REPL on top of `oh ask`'s engine. Accumulates
  conversation history across turns via the new
  `ConversationCompleteEvent` stream event.
- Built-in slash commands: `/exit`, `/quit`, `/clear` (reset
  history), `/help`. User slash commands + bundles still work.

### Added — CLI introspection (this release)

- `oh tools list` / `oh tools show <name>` — list / inspect
  registered tools (offline-introspectable).
- `oh config show` — print effective Settings (api_key redacted to
  `***<last-4>`).
- `oh config edit` — open `~/.openharness/.env` in `$EDITOR`;
  creates template if absent. New user-global config layer:
  shell env > `./.env` > `~/.openharness/.env` > defaults.
- `oh hooks list` / `oh hooks describe <name>` — list / inspect
  framework-built-in hooks; with `--enable-plugin-hooks`, also
  includes plugin sources.

### Quality bars

- **1274 tests passing** on CI (Python 3.10/3.11), 11 integration-
  gated (`@pytest.mark.integration`: MCP smoke + real-LLM), 8 skipif-
  gated (Docker / gVisor / real-API).
- **95.33% coverage** on Python 3.11 / **95.24%** on Python 3.10
  (gate: ≥95% global).
- **mypy --strict** clean throughout (188 source files).
- **ruff** check + format clean.
- **CI**: Python 3.10 and 3.11 on GitHub Actions.
- **Pre-commit**: ruff + hygiene hooks.

### Not included (deferred to Phase 8+)

See [`decisions/23-phase-7-final-boundary.md`](./decisions/23-phase-7-final-boundary.md) §6 for the full deferred list with rationale per item. Highlights:

- Anthropic native client (only OpenAI-compatible client ships in 0.1.0)
- LLM auto-compaction Layer 3 (turn summarization for long sessions)
- Memory system (YAML-frontmatter persistent recall)
- Keyring auth + multi-profile API key management
- `oh mcp add/list` + `oh skill run` subcommands (other 2 of the
  SPEC §2 5 missing series — deferred)
- REPL polish: `/save`, `/load`, multi-line input, mid-session `/mode`
- Firecracker microVM substrate
- Background tasks + cron

---

[Unreleased]: https://github.com/maisieyang/build-my-own-harness/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/maisieyang/build-my-own-harness/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/maisieyang/build-my-own-harness/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/maisieyang/build-my-own-harness/releases/tag/v0.1.0
