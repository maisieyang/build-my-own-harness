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

_Nothing yet._

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

[Unreleased]: https://github.com/maisieyang/build-my-own-harness/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/maisieyang/build-my-own-harness/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/maisieyang/build-my-own-harness/releases/tag/v0.1.0
