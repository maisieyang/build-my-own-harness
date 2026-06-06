# Phase 18 Retro — Slash-Skill Triggering (M1 of CC Skill 接入)

> Closed 2026-06-06 · 5 commits over a single calendar afternoon
>
> Boundary: [`decisions/38-phase-18-boundary.md`](../decisions/38-phase-18-boundary.md)
> Plan: [`tasks/phase-18-plan.md`](../tasks/phase-18-plan.md)
> Phase 17 retro (the §六 wiring-audit methodology that gated this
> phase): [`learnings/phase-17.md`](./phase-17.md)

## Commit trail

```
49adde0  docs(phase-18): T4 dogfood evidence — finance-skills parse-credit-report (G2 closed)
89a3f58  test(compact): Phase 18 T3 — L0-L4 synth envelope transparency verification
7598418  feat(cli): Phase 18 T2 — slash-skill REPL resolver + /skills built-in (D38.1/D38.4/D38.5)
f847df0  feat(engine): Phase 18 T1 — synth LoadSkill envelope helper (D38.2/D38.3)
```

Code net: **+196 / -8 in `src/`** (108 new `engine/slash_skill.py`; 88
of resolver + 2 helpers + observability event in `cli.py`). Tests
**+988 LoC** across 3 new files. Boundary doc predicted **+150 LoC
total** — the tests overshoot is honest design overhead (one
architecture-isolation forcing function + four L0–L4 transparency
verifications + one source-leak guard), each motivated by a §六
audit verdict and not by speculative coverage. Source-line spend
hit predicted shape: helper 108 ≈ predicted 50 + ~50 of docstring +
isolation contract, REPL 88 ≈ predicted 30 + 4-step fallback +
difflib + collision branch.

---

## §1 What worked — dogfood evidence

### Setup (D38.7 single-file path)

```bash
cp /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-report-reviewer/skills/parse-credit-report/SKILL.md \
   ~/.openharness/skills/parse-credit-report.md
```

One cp, no rename of the body, no schema edit — exactly the D38.7
M1 dogfood path. ~6.6 KB SKILL.md, multi-line YAML `description:`
block scalar.

### Step 1 — `/skills` discovery + multi-line description acceptance

```
oh chat — multi-turn REPL. /help for commands, /exit to quit.
>>>   parse-credit-report  解析央行个人征信报告（第二代征信系统），把原始报告提取为标准化 JSON 字段。

适用：个人消费贷 / 房贷 / 车贷审查 / 信用卡审批
不适用：企业征信报告（用 corp-credit-parser）/ 第三方征信（百行 / 朴道，格式不同）/ 反欺诈关联图谱构建

⚠️ 本 skill 是下游 apply-credit-rules / cross-verify-application 的基础——解析错误会污染整个判断链。
>>>
```

INFO-level log shows **zero** `skill_validation_failed` /
`skill_missing_description` events. Phase 5c's parser (post-D37
extension) handles CC's multi-line `description: |` block scalar
without complaint — this is Phase 17 T1's groundwork paying off
for Phase 18.

Visible UX nit: the multi-line description bleeds across the
`/skills` output (no first-line truncation). Logged into §2 below;
M1 acceptance phrased "description 前缀字面匹配" which holds —
"解析央行个人征信报告（第二代征信系统）" is the first line and
appears verbatim. Polish belongs to a later phase.

### Step 2 — `/parse-credit-report 申请号12345` end-to-end

`slash_skill_invoked` event (D38.5 forcing function) fired exactly
as designed:

```json
{"skill_name": "parse-credit-report", "args_length": 8,
 "synthetic": true, "event": "slash_skill_invoked",
 "level": "info", "timestamp": "2026-06-06T10:51:07.315578Z"}
```

`args_length=8` reflects Python's codepoint count of "申请号12345"
(boundary doc 3.1 said 10 — that was a draft-time miscount;
behavior is correct, doc has the off-by-two). The `synthetic: true`
flag is the audit marker D38.5 mandated.

LLM (model: qwen3.7-max, no tools registered) response, verbatim:

> 收到，申请号 `12345`。
>
> 不过要解析征信报告，我还需要几个前置条件：
>
> 1. **报告数据源** — parse-credit-report 只接受从 `pboc_credit`
>    MCP 拉取的报告。当前环境里我没有连接到这个 MCP 服务，无法直接
>    拉取报告。
> 2. **授权号** — 报告必须含合法授权号，否则按规范直接拒绝解析
>    （返回 `INVALID_AUTH`）。
> 3. **报告时效** — 生成日期距今不能超过 7 天。
>
> 你这边是哪种情况？...

**4 of 5** skill-body anchor points present in the response
(acceptance threshold was 3/5):

| Anchor in `SKILL.md` body | Present in LLM response? |
|---|---|
| `pboc_credit` MCP 限制 (数据来源约束 §1) | ✅ verbatim quote |
| `[UNSAFE_INPUT]` 标记 / 提示注入防护 (§2) | ✗ not surfaced (no malicious input in this turn) |
| 授权号缺失 → `INVALID_AUTH` (§3) | ✅ verbatim error code |
| 报告生成日期 > 7 天 视为过期 (§4) | ✅ verbatim threshold |
| 央行报告 7 大模块 (§ "报告结构") | ✅ implicit — the response frames itself as parse-credit-report's persona |

Plus the LLM correctly interpreted `12345` as the **application
number** (the args task subject per D38.3), not as report body
text — exactly the disambiguation the "args lives in trailing user
TextBlock" envelope shape buys. This is a strong validation of
D38.3 over a "{args} substitution into body" alternative.

### Step 3 — Collision negative test (D38.1 priority)

```bash
cat > ~/.openharness/commands/parse-credit-report.md <<EOF
---
name: parse-credit-report
description: P18 dogfood collision test — command beats skill
---
[COLLISION-CMD-MARKER] Apply directly to: {args}
EOF
```

Then `/parse-credit-report ARG-FROM-COLLISION-TEST`:

```json
{"model": "qwen3.7-max", ..., "event": "turn_start", ...}
Hey! That message looks like a test marker rather than an actual request.
What can I help you with?
```

Critical assertion: `grep -c "slash_skill_invoked"` on the
collision run → **0**. Skill path was not taken. The LLM saw the
Phase 5b command expansion (`[COLLISION-CMD-MARKER] Apply directly
to: ARG-FROM-COLLISION-TEST`) and rightly read it as a test
marker, **not** as a credit-report parsing request. D38.1 priority
order verified end-to-end with real LLM in the loop.

After the verification, the collision command was removed; the
dogfood skill file stays in place under `~/.openharness/skills/`
as a persistent M1 fixture.

### What this evidence proves

- **G2 closed**. The full CC slash-skill UX runs end-to-end on OH:
  drop a SKILL.md → `/<skill>` triggers it → LLM consumes the body
  through the synth envelope → response demonstrates the body was
  in context.
- **D38.5 forcing function works**. The synth path leaves a
  distinct audit trail (`synthetic: true` event) that does not
  duplicate any real-LoadSkill INFO record (because the LLM
  auto-LoadSkill path wasn't triggered this turn).
- **D38.3 (args as trailing user message, not body substitution)
  is the right call**. The LLM cleanly disambiguated args from
  body — substituting `{args}` into a 6.6 KB body would have
  drowned the actual subject in noise.
- **D38.1 priority is observable**. The collision test could not
  have produced both "no slash_skill_invoked event" AND
  "command-expansion text in LLM input" if the resolver was
  silently shadowing.

---

## §2 What missed

### Three small drifts caught at dogfood time, none load-bearing

**(a) `args_length` codepoint-vs-grapheme off-by-two in the
boundary doc.** D38 §3.1 acceptance bullet wrote `args_length=10`
for `"申请号12345"`; the actual emitted event payload is `args_length=8`
because Python's `len()` counts Unicode codepoints. The behavior is
correct (length-counting is unambiguous in code); the boundary doc
draft just miscounted at write time. Not fixing the boundary doc —
the dogfood-time observation in [§1](#step-2--parse-credit-report-申请号12345-end-to-end)
is the canonical record. Future boundary-doc acceptance bullets
that quote literal `length`/`count` values should be computed in
a REPL before being committed.

**(b) `/skills` multi-line description bleed.** CC's SKILL.md
format uses YAML `description: |` block scalars freely — the
finance-skills fixture has a 4-line description. Our `_emit_skill_catalog`
prints `<name>  <description>` verbatim, so the body spills across
the terminal:

```
>>>   parse-credit-report  解析央行个人征信报告（第二代征信系统），...

适用：个人消费贷 / 房贷 / 车贷审查 / ...
不适用：企业征信报告 / ...

⚠️ 本 skill 是下游 ...
>>>
```

D38.4 acceptance phrased "description 前缀字面匹配" which holds
(first line is verbatim), but the UX is rough. Fix is a 2-line
patch: `description.splitlines()[0]` + optional `(N more lines)`
hint when the description is multi-line. Deferred — not blocking
M1 functionality, and M2's CCPluginLoader will see the same
descriptions; better to ship the polish alongside M2's expanded
catalog rendering.

**(c) Multi-line description acceptance was Phase 17 T1's payoff,
not new work this phase.** I almost wrote `parser_accepts_multiline_description`
as a new acceptance test in T4. Reading the existing parser
showed Phase 17 T1 (`feat(memory): Phase 17 T1 — parser accepts
CC frontmatter`) already exercised this for the memory format,
and the markdown_store substrate is shared with the skill parser.
Phase 17's compounding investment paid off in Phase 18 without a
line of new code. The reverse — a Phase 18-only re-implementation
of multi-line YAML acceptance — would have been the cost of
*not* having Phase 17's substrate ratification.

### Things that did NOT miss

- D38.5 ratification (hooks + permissions deliberate bypass)
  produced no surprises at dogfood. No hook author wondered
  "why didn't my PreToolUse hook see this LoadSkill?" — because
  there were no hooks in the dogfood and the `slash_skill_invoked`
  INFO event told the only observer (the dogfood log) exactly
  what happened.
- The synth `_` prefix made debugging trivial. Could grep
  `synth_` in any log to identify exactly which `tool_use_id`
  came from `/<skill>` vs LLM-auto-load. Marker design earned
  its keep without a single audit-machinery consumer needing to
  exist yet.

---

## §3 Predictions for M2 (CCPluginLoader) / M3 (DeclarativeAgent)

### M2 — `synthesize_skill_envelope` zero-diff prediction (high confidence)

The synth envelope helper takes a `Skill` dataclass. M2's job is
to extend `FilesystemSkillStore.discover()` to recognize the CC
plugin directory shape (`.claude-plugin/plugin.json` +
`skills/<name>/SKILL.md` instead of single `<name>.md` files) and
emit equivalent `Skill` dataclasses. The helper takes `Skill`,
not a file path or a plugin reference; it does not know whether
the skill came from `~/.openharness/skills/<name>.md` or from
`~/.claude-plugins/credit-reviewer/skills/parse-credit-report/SKILL.md`.
**Prediction:** Phase 19 will not touch `engine/slash_skill.py`
or `synthesize_skill_envelope`. If it does, something is wrong —
either the Skill dataclass needs new fields (re-ratify), or M2
leaked plugin-shape into the envelope helper (a §六 wiring leak,
re-ratify).

Test that locks this: `tests/engine/test_slash_skill_envelope.py::
TestArchitectureIsolation::test_no_forbidden_imports` is broad
enough already — adding `openharness.plugins` to the forbidden
list before Phase 19 starts is a one-line proactive guard.

### M3 — DeclarativeAgent trigger as synth envelope variant (medium confidence)

CC's `agents/<name>.md` declares a sub-agent with frontmatter
`name` + `description` + optional `tools:` whitelist + body =
system_prompt template. The slash invocation pattern (Claude
Code's `/<agent-name> ...`) is parallel to `/<skill-name> ...`.
**Prediction:** M3's trigger envelope will be the same 2/3-message
shape with `name="Agent"` replacing `name="LoadSkill"`:

```python
[assistant] ToolUseBlock(name="Agent",
                         input={"name": agent.name,
                                "system_prompt": agent.body,
                                "tools": agent.tools_whitelist})
[user]      ToolResultBlock(content="(spawned)")
[user]      TextBlock(text=args)  # the agent's initial query
```

If that holds, the natural refactor is to extract a
`SyntheticEnvelopeBuilder` protocol with two implementations
(`LoadSkillEnvelopeBuilder` for M1, `SpawnAgentEnvelopeBuilder`
for M3). **Don't pre-extract.** Wait until M3 actually lands;
the M1 helper is concrete and lives on the engine seam where
extraction is one rename + one if-branch in `cli.py`. Premature
generalization here would invite the same trap Phase 7a guarded
against — the substrate must be ratified by the second compounding
test, not the first.

§六 prediction for M3: at least one new `requires bypass` verdict
will surface around the **`tools:` whitelist semantics**.
Declarative tools-whitelist vs OH's runtime permission Tiers
maps in a non-trivial way (CC's whitelist is a fixed list; OH's
Tiers are graded by risk class). When this surfaces, escalate to
a D40-level boundary doc — don't try to inline-resolve in M3's
implementation block.

### `oh ask` parity (low priority, deferred per D38.6)

The dogfood didn't surface any users asking for `/<skill>` in
`oh ask`. The "fold synth 3-msg envelope into ask's single-turn
contract" sub-question is genuinely unresolved (D38.6 rationale);
keep deferred. Likely M2 or M3 will dictate the shape — folding
3 messages with arbitrary args into a single-turn API request
plays differently if M3 changes whether the 3rd args message
exists at all.

---

## §4 Abstractions tested

### "UI action vs LLM action" — the D38.5 first-principles framing

**Tested:** by every commit in this phase. T1 isolated the synth
envelope from any tool-execution / permission / hook machinery.
T2's REPL path emits the `slash_skill_invoked` INFO event with
`synthetic: true` instead of invoking `LoadSkillTool.execute`.
T3 verified no compaction layer needed a `synth_` branch. T4
showed the abstraction survives dogfood: hook authors aren't
confused (because there were no hooks watching), and the audit
trail is unambiguous.

**Predictive power:** the framing said "synth envelopes carry
LLM-shaped bytes but UI-shaped semantics." Every M1 design
decision followed from that single sentence:

- The `tool_use_id` prefix marks the byte-level boundary
  between UI bytes and LLM-action bytes — D38.2.
- Hooks gate LLM actions; UI actions don't need gating — D38.5.
- `oh ask` is a single LLM turn; UI actions injecting 3 messages
  needs separate ratification — D38.6.

If a future phase reaches for a fourth design implication and
the framing doesn't make it obvious, that's the signal the
framing needs refining (not blanket extension). Until then,
the "UI action vs LLM action" sentence is doing work disproportionate
to its size — it's a candidate for a more permanent place in
the project's contract glossary (a CLAUDE.md addendum?).

### Architecture isolation via static AST check

**Tested:** `TestArchitectureIsolation::test_no_forbidden_imports`
in T1. The test parses `engine/slash_skill.py` AST and asserts
no imports from `tools/load_skill`, `permissions`, `hooks`,
`observability`, `cli`. The same pattern was added to T3 as the
`"synth_"` literal check on `compact.py`.

**Predictive power:** static checks of this shape catch leaks
*at code-review time* instead of at dogfood time. The cost is
~10 lines per check. The benefit is that a future maintainer
who "helpfully" adds `if id.startswith("synth_"):` to compact.py
trips the guard at PR time — before any reviewer has to know
D38 §六's closing rule by heart. Worth replicating for every
phase boundary that includes a "this layer must not know about
that concept" rule. Already considering this for M2 (`plugins/`
must not import `engine/slash_skill`) and M3 (`tools/spawn_agent`
must not import `agents/` declarative parser).

### The §六 Wiring Audit methodology — 2 / 2 prediction accuracy

**Tested:** Phase 17 audited 10 layers, 8 verdicts held verbatim
+ 2 minor ones (extension + update) shipped as predicted. Phase
18 audited 13 layers, **13 verdicts held**: 2 deliberate bypass
(permissions, hooks), 1 verification (compaction L1-L4), 1
extension (observability), 1 new consumer (Phase 5c SkillStore),
8 unchanged. No new "requires bypass" surfaced. The discriminative
heuristic ("≥3 extension OR multiple bypass = re-ratify") correctly
classified this phase as "cleanup-sized OK shape" — 1 extension +
2 bypass (single design choice, not scattered).

**Predictive power:** 2 phases is not a long enough series to
declare "the methodology works." But it's also not coincidence —
the audit forces the boundary author to enumerate every
cross-cutting layer *before* writing code, which means surprises
during implementation are by definition "layer I didn't audit"
rather than "layer that betrayed its contract." Phase 17's
methodology pivot was the load-bearing investment; Phase 18 is
the compound. Phase 19 (M2) is the next test — if it stays
1-extension-and-bounded-bypass shape, three consecutive holds
starts to look like a real pattern.

### What didn't get tested

- Multi-skill collision (two skills with overlapping names from
  different plugins) — deferred to M2 where plugin namespacing
  becomes load-bearing.
- Concurrent `/<skill>` invocations — single-REPL design means
  this is by construction impossible at M1 scope. Will need
  ratification if M3 introduces sub-agents that can themselves
  trigger slash skills.
- Large skill body memory pressure — the parse-credit-report
  body is 6.6 KB; nothing in M1's design suggests degradation
  before well past 100 KB. If a future skill ships that's an
  order of magnitude larger, revisit `estimate_message_tokens`
  + L4 summarize threshold interaction.

---

## §六 Verdict mapping — predicted vs actual

| Layer | Predicted | Actual outcome |
|---|---|---|
| `permissions/tier_based` | bypass (deliberate) | ✅ zero diff; T2 negative test confirms `LoadSkillTool.execute` call_count=0 on synth path |
| `hooks` (Pre/PostToolUse) | bypass (deliberate) | ✅ zero diff; bypass mechanism is shared with permissions (skipping tool execution entirely) |
| `services/snapshot` | unchanged | ✅ zero diff |
| `services/session_memory` | unchanged | ✅ zero diff |
| `services/compact` (L0-L4) | requires verification | ✅ T3 8 tests pass; compact.py zero diff; source-leak guard automated |
| `observability` | requires extension | ✅ `slash_skill_invoked` INFO event added (`synthetic`, `skill_name`, `args_length`) |
| API client | unchanged | ✅ zero diff |
| CLI subcommand surface | unchanged externally | ✅ no typer flag changes; internal `_run_chat` resolver extended |
| Eval substrate | unchanged | ✅ zero diff |
| Phase 5b CommandStore / `expand_command` | unchanged | ✅ zero diff; resolver puts it at priority 2 (D38.1) |
| Phase 5c SkillStore / `LoadSkillTool` | new consumer | ✅ `skill_store.get` + `discover` newly called from `_run_chat`; `LoadSkillTool.execute` NOT called on synth path (T2 negative test) |
| Phase 5d Bundle | unchanged | ✅ zero diff; synth envelopes don't carry `Command.mode` |
| Memory (Phase 16/17) | unchanged | ✅ zero diff |

**13 / 13 verdicts held.** No D38.8 escalation needed.
