# OpenHarness Tutorial

A 30-minute walkthrough from "I just installed `oh`" to "I can
compose a custom read-only inspection mode with audit logging."

Three progressive scenarios:

1. [Your first query (5 min)](#1-your-first-query)
2. [Authoring a slash command (10 min)](#2-authoring-a-slash-command)
3. [Read-only mode via a ModeBundle (15 min)](#3-read-only-mode-via-a-modebundle)

Optional follow-on:

4. [Adding a filesystem plugin hook (10 min)](#4-adding-a-filesystem-plugin-hook)

Before you start: install + 2 env vars per the
[README Quickstart](../README.md#quickstart). Verify by running
`oh --version` — should show `openharness 0.1.0`.

---

## 1. Your first query

```bash
oh ask "list 5 git commands and explain each in one line"
```

What you'll see (output abbreviated):

```
Here are 5 commonly used git commands:

1. `git status` — Show the working tree status...
2. `git add <file>` — Stage changes for the next commit...
3. `git commit -m "msg"` — Record staged changes...
4. `git push` — Upload local commits to a remote...
5. `git pull` — Fetch + merge from a remote.
```

**What just happened**: the LLM didn't need any tools for this
question — it answered from its own knowledge. `run_query()`
called the API once, got `stop_reason="end_turn"`, exited.

### Try one that *does* use tools

```bash
oh ask "find any TODO comments in this project's src/openharness/ directory"
```

You'll see tool-dispatch lines mixed into the output:

```
[Grep] pattern='TODO' path='src/openharness'
[Grep] → src/openharness/.../...py:42:# TODO: ...
        src/openharness/.../...py:67:# TODO: ...
Found 2 TODO comments in src/openharness/...
```

The lines wrapped in `[...]` are tool dispatch events rendered by
the harness — Grep was the tool the LLM picked. The plain text
between them is the LLM's response.

### Watch the full trace

```bash
OPENHARNESS_LOG_FORMAT=json oh ask "find TODO comments in src/" 2>trace.jsonl
cat trace.jsonl | jq -r 'select(.event == "tool_complete") | "\(.tool) (\(.duration_ms)ms) → \(.output_len) chars"'
```

You'll see each tool dispatch with timing + output size. This is
the harness's structured trace; consumers (LangSmith, Datadog,
your own dashboards) can pipe this stream and reconstruct what the
agent did.

### Dry-run for inspection

```bash
oh ask --dry-run "summarize what's in README.md"
```

`--dry-run` lists every tool call the LLM would make **without
executing them**. Useful for "what would this do?" before letting
the agent loose.

---

## 2. Authoring a slash command

Slash commands encode repeated prompt templates. Instead of typing
the whole prompt every time, you say `oh ask "/review <args>"` and
the harness expands the template before the LLM ever sees it.

### Install the example

```bash
mkdir -p ~/.openharness/commands
cp examples/commands/review.md ~/.openharness/commands/
```

`review.md` is a copy-pastable template (see
[`examples/commands/review.md`](../examples/commands/review.md)):

```markdown
---
name: review
description: Code-review prompt template — focus on correctness, readability, security
---
Please review the following code change for correctness, readability,
and security implications:

{args}

Focus on:
- Edge cases not covered by the change
- ...
```

The frontmatter `name:` is what you invoke; the body is the
template. `{args}` is the substitution point for whatever you
pass after the slash.

### Invoke

```bash
oh ask "/review the change in src/openharness/cli.py from the last commit"
```

Equivalent to typing the entire `Please review the following code
change...` template manually with the args substituted in. The
LLM then proceeds normally (reads the file via Grep/Read, produces
findings).

### See exactly what the LLM received

```bash
OPENHARNESS_LOG_FORMAT=json oh ask "/review last commit" 2>&1 \
    | jq -r 'select(.event == "turn_start") | .request.messages[0].content[0].text' \
    | head -20
```

The user message the LLM saw is the **substituted body**, not your
`/review last commit` string. The framework consumed the slash;
the LLM never saw it.

### Discovery convention

The framework looks in two layers:

| Path | Use |
|---|---|
| `~/.openharness/commands/<name>.md` | Global — applies in any shell |
| `<cwd>/.openharness/commands/<name>.md` | Project-local — overrides global on same name |

Drop a `commands/review.md` in a specific project to override the
global with project-specific guidance.

### Quick "what's available" check

`oh commands list` doesn't ship in 0.1.0 (Phase 8 candidate). For
now, `ls ~/.openharness/commands/ <cwd>/.openharness/commands/`.

---

## 3. Read-only mode via a ModeBundle

A **ModeBundle** composes four overrides into one named "mode" a
slash command can reference:

- A custom system prompt
- A tool whitelist (filter what the LLM can call)
- Extra `deny_paths` (filesystem patterns blocked at permission
  layer)
- Named hooks to register (e.g., `audit_log`, `deny_writes`)

The example: a `read-only` bundle that lets the LLM Read + Grep
but blocks any write tool, with full audit logging.

### Install the bundle + the command that triggers it

```bash
mkdir -p ~/.openharness/{bundles,commands}
cp examples/bundles/read-only.md ~/.openharness/bundles/
cp examples/commands/code-review.md ~/.openharness/commands/
```

The slash command (`code-review`) references the bundle (`read-only`)
via its `mode:` frontmatter field:

```markdown
---
name: code-review
description: Read-only code review using the `read-only` bundle
mode: read-only
---
...
```

The bundle (`read-only.md`) ships with `tools.whitelist`,
`deny_paths`, and `hooks` set. See
[`examples/bundles/read-only.md`](../examples/bundles/read-only.md).

### Try it: a read that succeeds

```bash
oh ask "/code-review identify likely bug in src/openharness/cli.py"
```

The LLM will use Read + Grep, produce findings. Tool dispatch
proceeds normally for read-only tools.

### Try it: a write that gets blocked

```bash
oh ask "/code-review now create a fix in src/openharness/cli.py for the issue you found"
```

The LLM will try to call `Edit` or `Write`. Two layers of defense
fire:

1. **`tools.whitelist`** — the LLM doesn't see Write/Edit/Bash in
   its tool catalog (the `WhitelistRegistry` filters
   `to_api_schema()` to Read + Grep only). It may still hallucinate
   a call to Write…
2. **`deny_writes` hook** — if a non-whitelisted tool name reaches
   dispatch anyway, the PreToolUse hook denies it on
   `is_read_only=False`. Belt and braces.

You'll see the dispatch fail message:

```
[Write error] ✗ deny_writes: tool 'Write' is not read-only
```

### See the audit trail

The `audit_log` hook fires on every PostToolUse:

```bash
OPENHARNESS_LOG_FORMAT=json oh ask "/code-review explain the engine layer" 2>&1 \
    | jq -r 'select(.event == "audit_tool_complete") |
             "[\(.tool_name)] use=\(.tool_use_id) err=\(.is_error) out=\(.output_len)b"'
```

You'll see one line per tool dispatch. Pipe this stream to your
compliance system and you have a tamper-evident record of every
file the agent touched in this session.

### What the bundle did (recap)

```
Slash command         /code-review args
  ↓ frontmatter says mode: read-only
ModeBundle            read-only
  ↓ resolves at CLI bootstrap
QueryContext mods:
  • system_prompt:    "You are a code reviewer in strict read-only mode..."
  • tool_registry:    WhitelistRegistry(base, {Read, Grep})
  • settings:         deny_paths += (secrets/**, *.env, **/credentials.*)
  • hook_registry:    base + audit_log + deny_writes
  ↓
Engine runs the query exactly as it always does — no engine code
knows about Bundle, Skill, Command, or Plugin. They're all
pre-LLM composition.
```

This is the cross-layer composition pattern. Phase 5d's retro
([`learnings/phase-5d.md`](../learnings/phase-5d.md)) is the
framework-builder's deep-dive if you want to see why it landed
this way.

---

## 4. Adding a filesystem plugin hook

**Optional**. Phase 5f lets you drop a `.py` file at
`~/.openharness/hooks/` to register custom hook callbacks without
publishing a Python package.

### Install the example

```bash
mkdir -p ~/.openharness/hooks
cp examples/hooks/turn_counter.py ~/.openharness/hooks/
```

The file (see [`examples/hooks/turn_counter.py`](../examples/hooks/turn_counter.py))
exports a `count_turns` function decorated with `@hook_spec("PreApiCall")`
that logs the turn count before each LLM call:

```python
from openharness.bundles import hook_spec
from openharness.observability.logging import get_logger

_logger = get_logger("plugin.turn_counter")

@hook_spec("PreApiCall")
async def count_turns(context):
    turn = getattr(context, "turn", None)
    if turn is not None and turn >= 5:
        _logger.warning("turn_counter_high", turn=turn)
    return None  # passthrough
```

### Verify discovery

Plugins are **opt-in** (default OFF):

```bash
oh hooks list                            # only built-ins
oh hooks list --enable-plugin-hooks      # also shows count_turns
oh hooks describe count_turns --enable-plugin-hooks
```

The flag exists because plugin hooks can deny/modify any tool call —
too much blast radius for default ON.

### Run a query with plugins enabled

```bash
OPENHARNESS_LOG_FORMAT=json oh ask --enable-plugin-hooks \
    "list 5 git commands" 2>&1 \
    | jq -r 'select(.event | startswith("turn_counter"))'
```

You'll see `turn_counter` log entries for each API turn. On a
multi-turn run (e.g., the LLM uses tools then re-prompts), you'll
see one per turn.

### Reference a plugin from a bundle

Bundles can register plugin hooks just like built-ins:

```yaml
---
name: my-budget-aware-mode
description: ...
hooks:
  - audit_log         # built-in
  - count_turns       # plugin (must be discoverable + flag on)
---
```

Without `--enable-plugin-hooks`, a bundle that references
`count_turns` exits with `Unknown hook: count_turns` (the
discovery never ran). With the flag, it resolves.

---

## Where to go next

- **More features** in [`README.md`](../README.md#key-features) —
  MCP servers, skills, sub-agents, Docker / gVisor sandbox
- **Framework-builder retrospectives** in [`learnings/`](../learnings/) —
  each phase's design decisions + Python patterns + what would be
  done differently
- **Per-decision trade-off records** in [`decisions/`](../decisions/) —
  the architectural rationale, written at decision-time
- **Development log** in [`docs/development-log.md`](./development-log.md) —
  full per-phase feature shipping narrative (preserved verbatim
  from the README's growth period)
