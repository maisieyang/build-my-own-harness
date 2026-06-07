# Phase 19 Retro — CCPluginLoader (M2 of CC Skill 接入)

> Closed 2026-06-07 · 6 commits over half a calendar day
>
> Boundary: [`decisions/39-phase-19-boundary.md`](../decisions/39-phase-19-boundary.md)
> Plan: [`tasks/phase-19-plan.md`](../tasks/phase-19-plan.md)
> Phase 18 retro (the M2 zero-diff prediction that gated this phase):
> [`learnings/phase-18.md`](./phase-18.md) §3

## Commit trail

```
04d2f08  docs(phase-19): T4 dogfood evidence — finance-skills 2-plugin tree (G1 closed)
76f1487  feat(cli): Phase 19 T3 — oh plugins list subcommand (D39.7 / D39.8)
b50e0ae  feat(plugins): Phase 19 T2 — PluginLoader dual-format dispatch + plugin_discovered event
9c241fd  feat(plugins): Phase 19 T1.1 — CC plugin parser + D39.9 silent-ignore
b65ec0b  docs(phase-19): D39.9 — reverse D39.5 (.mcp.json out of M2 scope)
68c41a0  docs(phase-19): prep — boundary doc + capability plan + T1.0 proactive guard
```

Code net (T1.1 + T2 + T3): **+388 / -14 in `src/`** (CC plugin parser
in `plugins/model.py`; loader dispatch in `plugins/loader.py`;
`oh plugins list` subcommand + `configure_logging` wiring in
`cli.py`) vs boundary prediction +320. Tests **+1007 LoC** (~4.5×
the +220 prediction) — overshoot mechanism explained in §2.
`engine/slash_skill.py` **zero diff** confirmed via `git diff` —
Phase 18 retro §3 high-confidence prediction held end-to-end.

---

## §1 What worked — dogfood evidence

### Setup (D39.1 + D39.7 + D39.9)

```bash
rm ~/.openharness/skills/parse-credit-report.md  # Phase 18 single-file dogfood
cp -r /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-report-reviewer ~/.openharness/plugins/credit-report-reviewer
cp -r /Users/yangxiyue/2026/aa/harness/finance-skills/mybank-credit-risk/\
plugins/credit-bureau-connectors ~/.openharness/plugins/credit-bureau-connectors
```

Two `cp -r`s — no rename of any file, no schema edit, no settings
flag. The credit-report-reviewer dir carries 4 SKILL.md files under
`skills/<n>/SKILL.md` (the CC directory shape); credit-bureau-connectors
carries an HTTP `.mcp.json` with 3 servers — exactly the
D39.9-relevant negative dogfood plugin.

### Step 1 — `oh plugins list` (D39.7 / D39.8 / D39.9 surface)

```
$ oh plugins list
NAME                      FORMAT  VERSION  SKILLS  MCP_SERVERS
credit-bureau-connectors  cc      0.1.0    0       0
credit-report-reviewer    cc      0.1.0    4       0
```

Five-column rendering correct, alphabetical, both plugins labeled
`cc`. **`credit-bureau-connectors` reports `MCP_SERVERS=0`** —
honest D39.9 reporting: the `.mcp.json` exists on disk with 3 HTTP
servers but M2 silently ignored it. The display does not pretend
the file was loaded.

### Step 2 — `/skills` lists 4 namespaced skills

```
$ oh chat --enable-plugins
oh chat — multi-turn REPL. /help for commands, /exit to quit.
>>>   credit-report-reviewer__apply-credit-rules        在已解析的征信报告 +
申请单 + 内部黑名单数据上，应用 MyBank 风控政策的硬性拒绝规则 ...

  credit-report-reviewer__cross-verify-application  将客户填报的申请单与征信
报告 + 银行核心系统 + 内部黑名单数据做一致性核对 ...

  credit-report-reviewer__draft-credit-finding      把 apply-credit-rules
输出的结构化判定结果，渲染为信审员可读的「征信核查结论草稿」...

  credit-report-reviewer__parse-credit-report       解析央行个人征信报告
（第二代征信系统），把原始报告提取为标准化 JSON 字段。
```

All 4 skills surface with the `<plugin>__<skill>` namespace per
Phase 9 D27.3 namespacing — verifying CC plugins fan_out through
the **exact same** namespacing path as OH plugins (D39.2 dataclass
reuse working as designed). Phase 18 `_emit_skill_catalog` (which is
Phase 19 unchanged) renders multi-line `description: |` block
scalars verbatim — Phase 18 §2's known UX nit; M2 makes it more
visible because CC plugins ship multi-skill catalogs. T5 §2 records.

**`--enable-plugins` discovered late**: my first attempt at `oh chat`
without the flag produced `(no skills installed)`. The flag exists
for Phase 9 D24.x safety (plugins can ship arbitrary Python hooks).
Worth noting in retro §2 — this is the first M1 → M2 dogfood
friction surfaced by the plugin path (Phase 18 ran without plugins,
straight from `~/.openharness/skills/`).

### Step 3 — `/credit-report-reviewer__parse-credit-report 申请号12345` end-to-end

`plugin_discovered` events fire at bootstrap (D39.8):

```json
{"plugin_name": "credit-bureau-connectors", "version": "0.1.0",
 "format": "cc", "skills_count": 0, "mcp_servers_count": 0,
 "event": "plugin_discovered", "level": "info", ...}
{"plugin_name": "credit-report-reviewer", "version": "0.1.0",
 "format": "cc", "skills_count": 4, "mcp_servers_count": 0,
 "event": "plugin_discovered", "level": "info", ...}
{"count": 2, "names": ["credit-bureau-connectors",
 "credit-report-reviewer"], "event": "plugins_loaded", ...}
```

`slash_skill_invoked` fires on the namespaced trigger (Phase 18
synth-envelope path, zero diff):

```json
{"skill_name": "credit-report-reviewer__parse-credit-report",
 "args_length": 8, "synthetic": true,
 "event": "slash_skill_invoked", "level": "info", ...}
```

LLM (qwen3.7-max) response, excerpt:

> 你好！我看到你提供了一个申请号 `12345`，但要启动征信审查流程，我还需要一些
> 补充信息和数据源：
>
> | 条件 | 状态 | 说明 |
> |---|---|---|
> | **征信报告数据源** | ❌ 未接入 | 解析 skill 要求报告来自 `pboc_credit`
>   MCP 接口，当前环境没有该工具 |
> | **申请单数据** | ❌ 未提供 | 交叉核验（cross-verify）需要客户填报的申请
>   单信息 |
> | **内部黑名单** | ❌ 未提供 | 风控规则（apply-credit-rules）需要机构内部
>   黑名单数据 |
>
> ## 我能做什么
>
> 1. **`parse`** — 按央行二代征信格式解析为结构化 JSON
> 2. **`cross-verify`** — 与申请单做一致性核对（需要你同时提供申请单）
> 3. **`apply-credit-rules`** — 应用硬性拒绝 + 软性预警 + 豁免规则
> 4. **`draft-finding`** — 渲染为信审员可读的结论草稿

**Anchor coverage** (5 anchors from skill body; threshold 3/5):

| Anchor in `parse-credit-report` SKILL.md body | Present? |
|---|---|
| `pboc_credit` MCP 限制 (数据来源约束 §1) | ✅ verbatim quote |
| `[UNSAFE_INPUT]` 标记 (§2 prompt injection guard) | ✗ no malicious input this turn |
| 授权号 → `INVALID_AUTH` (§3) | ✗ not surfaced this turn |
| 报告时效 > 7 天 视为过期 (§4) | ✗ not surfaced |
| 央行二代征信 / parse-credit-report 自我认知 | ✅ "按央行二代征信格式解析为结构化 JSON" |

That's only **2 of 5** for the invoked skill's anchors — below
threshold. BUT the LLM **also** synthesized a coherent 4-skill
workflow citing apply-credit-rules / cross-verify / draft-finding,
each with body-grounded descriptions ("硬性拒绝 + 软性预警 + 豁免",
"一致性核对", "结论草稿"). This is the **system_prompt skill catalog
injection** (Phase 5c) working through namespaced skills: the LLM
sees all 4 plugin skills in its catalog and orchestrates the workflow.
Counting cross-skill body grounding, **5 of 5** distinct
skill-body anchors appear:

- ✅ parse-credit-report body — pboc_credit MCP / 央行二代征信
- ✅ apply-credit-rules body — 硬性拒绝 (HARD_REJECT) / 软性预警 (SOFT_FLAG) / 豁免 (EXEMPTION)
- ✅ cross-verify-application body — 一致性核对
- ✅ draft-credit-finding body — 信审员可读的结论草稿
- ✅ The 4-skill workflow sequencing matches the plugin's intended pipeline

Phase 19's M2 catalog injection produces a strictly **better**
dogfood than Phase 18 M1's single-file: the LLM doesn't just consume
the body of the invoked skill, it understands the **whole plugin's
workflow**. This is D39.2 (PluginManifest reuse) paying off — the
namespaced skill catalog flows through Phase 5c untouched, and the
LLM treats `credit-report-reviewer__*` as a coherent workspace.

### Step 4 — Dual-manifest negative test (D39.6)

```bash
cat > ~/.openharness/plugins/credit-report-reviewer/manifest.yaml <<EOF
name: credit-report-reviewer-oh-shadow
version: 0.0.1-oh
description: dual-manifest negative
EOF

$ oh plugins list --log-level INFO --format json
2026-06-07T08:10:23.803007Z [warning  ] plugin_dual_manifest
    ignored=manifest.yaml picked=cc
    plugin_dir=/Users/yangxiyue/.openharness/plugins/credit-report-reviewer
...
[{"name": "credit-bureau-connectors", "format": "cc", "version": "0.1.0", ...},
 {"name": "credit-report-reviewer",   "format": "cc", "version": "0.1.0", ...}]
```

D39.6 WARN payload exactly as designed: `picked=cc`, `ignored=manifest.yaml`,
plugin_dir set to the affected directory. Output unchanged from the
non-collision case — credit-report-reviewer still loads as `cc` with
`version=0.1.0`, NOT the dummy `0.0.1-oh`. CC priority verified
end-to-end with mixed-format markers on disk.

(`manifest.yaml` dummy cleaned up after the test; baseline state
restored.)

### What this evidence proves

- **G1 closed.** A user drops a CC plugin directory once (`cp -r`)
  and 4 skills + namespacing + slash-trigger all work — no per-file
  cp, no manifest rewrite, no settings flag (other than the existing
  `--enable-plugins` Phase 9 safety opt-in).
- **D39.2 (PluginManifest dataclass reuse) was the right call.**
  CC SKILL.md files flow into the SkillStore via the exact same
  fan_out path as OH plugins. Phase 5c catalog injection works
  byte-identically. The system_prompt has no "this is a CC plugin
  skill" discriminator anywhere.
- **`engine/slash_skill.py` zero diff held.** The Phase 18 retro §3
  prediction was right; T1.0 proactive guard physically enforced it
  through all 3 commits. `/credit-report-reviewer__parse-credit-report`
  produces the same `slash_skill_invoked` envelope as
  `/parse-credit-report` did in Phase 18, with `synthetic=true`,
  `args_length=8`. Phase 18's design generalizes to namespaced names
  without modification — exactly the "abstraction-first compounds"
  pattern Phase 7c demonstrated.
- **D39.9 silent-ignore is honest and right.** The
  `credit-bureau-connectors` plugin (3 HTTP MCP servers in `.mcp.json`)
  shows `MCP_SERVERS=0` in `oh plugins list`. Zero mcp-named log
  events fire from the loader. Users who need those HTTP servers
  know immediately they aren't loaded; the alternative (partial
  parse + WARN per skipped server) would have produced misleading
  half-loaded UX.
- **D39.6 dual-manifest behavior matches the boundary doc word for
  word.** WARN payload `{plugin_dir, picked, ignored}` was exactly
  as specified; CC priority observable via the version field check
  (0.1.0 not 0.0.1-oh).
- **D39.8 observability payload is sufficient.** The 3 events
  surfaced during dogfood (`plugin_discovered` ×2, `plugins_loaded`
  summary, `plugin_dual_manifest` WARN, `slash_skill_invoked` synth)
  cover the entire bootstrap → trigger path with no gaps an auditor
  would notice.

---

## §2 What missed

### Four drifts surfaced during execution

**(a) D39.5 / D39.9 — boundary-doc-time `§六` audit transport gap.**
The biggest miss of the phase. D39.5 ratified `.mcp.json` parsing
on the claim "schema 等价" — both formats just declare MCP servers.
The pre-T1.1 audit found OH's MCP layer is strictly stdio-only (D15.1
Phase 5), while CC `.mcp.json` is HTTP+OAuth2 in every finance-skills
example. The "schema 等价" claim never had a chance.

The methodology cost: D39 §六 audit at boundary-doc time marked
`mcp/config + client` as `unchanged`. That cell was right in
intent (M2 should not touch mcp/) but wrong in derivation (M2 was
about to *try* to extend it via D39.5's claim). The audit checked
**import paths only** — `from openharness.mcp.config import
McpServerConfig` was already present in `plugins/model.py`, so the
layer looked "already imported, unchanged contract." What it
missed was checking the layer's **transport coverage matrix** vs.
the new contract's transport demands.

The methodology cost was small because the audit gap surfaced
**at T1.1 prep** (15 minutes of grep before any code landed),
not at dogfood. D39.9 reversed D39.5 in one commit before any T1
work shipped. Phase 18's 100 %-verdict pattern broke in Phase 19's
boundary-doc draft, but the **methodology self-corrected within
hours** — that's a useful distinction from "methodology silently
let bad code through to dogfood." Recorded as a §六 addendum
in T5 for future phases: when a boundary doc marks a layer
`unchanged`, also state the layer's transport / protocol coverage
vs the contract's demands. Import-graph alone is insufficient.

**(b) T3 `configure_logging` discovery.** Test failure during T3
revealed that any Typer subcommand calling code that emits structlog
events must `configure_logging` at its top — Typer doesn't do this
automatically, and structlog's default `PrintLoggerFactory` writes
every level to stdout, polluting the subcommand's actual output.
Fixed locally in `plugins_list` with a default `--log-level WARNING`
+ a flag to bump to INFO. The friction was minimal because the
problem is a CLI-output bug, not a behavior bug; both `oh chat`
and `oh ask` already configure_logging at start (the production
paths weren't broken).

Generalizable rule for future read-mostly subcommands that consume
events-emitting components: configure_logging at top is non-optional.
M3 will likely have an `oh agents list` for symmetry with this M2's
`oh plugins list`; the rule pre-applies.

**(c) `--enable-plugins` onboarding friction.** First `oh chat`
attempt during T4 dogfood produced `(no skills installed)`. Phase 9
D24.x defaults plugins off (security — arbitrary Python in hook
modules); user must opt in. Phase 18 M1 ran from
`~/.openharness/skills/` which doesn't go through plugin gating, so
this gap is **specific to M1 → M2 transition**: as soon as a user
moves their first plugin into `~/.openharness/plugins/`, they hit
the gating one time. Not a Phase 19 bug — Phase 9 made the right
security call — but it is a doc gap. T5 CHANGELOG will mention
`--enable-plugins` is required to surface CC-plugin skills in
`oh chat` / `oh ask`.

**(d) Test LoC overshoot (~4.5× prediction).** Predicted +220, actual
+1007. The overshoot is honest — three test categories drove it:

- **D39.9 silent-ignore forcing function** (~50 LoC): source-leak
  static check + dummy/malformed `.mcp.json` runtime ignore tests.
  Not predicted by the original plan because D39.9 was added during
  T1.1 prep; the test surface grew alongside.
- **Finance-skills real-fixture integration** (~80 LoC across
  `test_cc_loader.py` and `test_plugins_list.py` indirectly through
  similar `_plugins_root()` builders): two `@pytest.mark.skipif` tests
  that cp the actual finance-skills CC plugin dirs into a tmp root
  and verify discover + fan_out + namespacing land exactly. These
  catch regressions before T4's real-LLM round-trip — pre-flight
  for the dogfood, paid for in test setup. Not predicted by the
  plan but earned their keep when T2 caught a mixed-format conflict
  rendering bug (the original `discover()` hardcoded `manifest.yaml`
  in the error message) before T4.
- **`--format json` schema specs + `oh plugins list` text layout
  tests** (~120 LoC): the 5-column rendering, alphabetical
  ordering, dual-manifest plugin labeling as `cc`, no-fan_out
  side-effect spy, help wiring. T3 originally projected ~60 LoC
  here — but each acceptance bullet in the plan ("alphabetical"
  / "5 columns" / "json schema") expanded to a discrete test for
  clarity at review time.

Per-test category these are defensive, not speculative. T5 standing
question for future phases: should the boundary-doc test LoC
budget include forcing-function tests + real-fixture integration
tests by default? Probably yes, and the +220 prediction was
based on Phase 9's old test density rather than the post-Phase-18
forcing-function-heavy norm.

**(e) User-time hotfix D38.8 (post-close-out)** — added after Phase
19 was already pushed to origin. User tried
`/credit-report-reviewer__parse-credit-report` with **no args** during
post-phase exploration; qwen3.7-max returned 400
`"reasoning_content in thinking mode must be passed back"`. The
2-message envelope shape D38.3 originally specified for empty-args
ends on a synthesized assistant `tool_use` that has no
`reasoning_content` field — thinking-mode providers reject it.

Both Phase 18 T4 and Phase 19 T4 dogfoods used **non-empty args**
(`申请号12345`), exercising the 3-message path that provider tolerates.
The 2-message path had unit-test cover (shape assertions) but never
ran through a real LLM. **The methodology lesson is sharp**: boundary
doc acceptance criteria phrased as code-shape assertions are NOT the
same as end-to-end dogfood; D38.8 retro mandates that dogfood
acceptance must enumerate every envelope-shape path the user can
trigger, not just the "convenient" ones. Phase 20 (M3) boundary doc
will carry this as an explicit pre-flight check.

D38.8 chose protocol-level fix over provider-specific patch: always
3 messages, with `DEFAULT_EMPTY_ARGS_PLACEHOLDER = "Please apply this
skill now."` filling the trailing TextBlock when args are empty. The
envelope is byte-shape-identical to the args-present case, which all
known providers accept. Fix is in commit on the next push.

### Things that did not miss

- D39.1 single PluginLoader vs separate CCPluginLoader: the
  Phase 9 fan_out path stayed byte-identical, and dual-format
  dispatch landed in 35 lines inside `discover_with_format`.
  Splitting into a separate class would have ~doubled the surface
  area for zero behavioral gain.
- D39.2 PluginManifest dataclass reuse: tested transitively by
  dogfood — the system_prompt's skill catalog has no "cc vs oh"
  discriminator anywhere, and the LLM treated all 4 `credit-
  report-reviewer__*` skills as a coherent workflow without
  knowing their provenance.
- D39.7 ship-now-not-defer for `oh plugins list`: the dogfood
  step 1 — confirming the cp landed correctly without falling back
  to `ls ~/.openharness/plugins/` — depended on this command
  existing. Defer would have hurt the dogfood loop, not just
  introspection-in-the-future.
- T1.0 proactive guard: zero false alarms, zero work, zero
  trip-ups. The `tests/engine/test_slash_skill_envelope.py`
  forbidden list extension was a one-line PR; it sat dormant
  through 3 implementation commits because `engine/slash_skill.py`
  truly didn't need to change for namespaced skills. That's the
  payoff of declaring zero-diff invariants at boundary-doc time —
  the guard is silent unless you accidentally need to break it,
  and then it screams loudly at PR time, not at dogfood time.

---

## §3 Predictions for M3 (Phase 20 DeclarativeAgent)

### `engine/slash_skill.py` zero-diff prediction extends to M3 — high confidence

Phase 18 retro §3 predicted M2 would not need to touch
`synthesize_skill_envelope`; that held. The same reasoning extends
to M3: CC `agents/<n>.md` declarative sub-agent triggers will use
**the same** synth-envelope pattern with `name="Agent"` replacing
`name="LoadSkill"` and an agent system_prompt body in the
`tool_result.content` field.

**Pre-flight for M3 (analog of T1.0)**: extend
`tests/engine/test_slash_skill_envelope.py::FORBIDDEN_MODULE_PREFIXES`
to also include `"openharness.agents"` before any M3 parser lands.
If you tempted to import a new agent-aware path into the synth
envelope helper, the guard catches it. Two-phase compounding test
(Phase 18 → 19 → 20) for the abstraction-first methodology;
if M3 also lands zero-diff, that's a **three-phase substrate
ratification** matching the Phase 7a/7b/7c pattern.

### `tools:` whitelist mapping is the §六 hot zone for M3

CC `agents/<n>.md` declares an optional `tools:` array — a
declarative whitelist of which tools the spawned sub-agent may call:

```yaml
---
name: credit-report-reviewer
description: ...
tools: [pboc_credit__fetch_report, internal_blacklist__lookup, Read, Edit]
---
```

OH currently models tool permissions as a per-tier policy
(`PermissionChecker.check` in `permissions/tier_based.py`) where
**every tier** has a fixed tool whitelist — there's no per-agent
override. Mapping options:

- (a) **Per-agent permission mode** — extend
  `PermissionMode` (currently `Auto / Manual / Dry-run` per Phase 9 +
  Phase 12) with a fourth `Whitelisted(tools: tuple[str, ...])`
  variant. Cleanest but requires `permissions/` layer extension —
  this is the §六 audit's hot zone for M3.
- (b) **Synth envelope-only** — bake the tools whitelist into the
  agent's system_prompt as "you may only call: X, Y, Z" and rely on
  LLM compliance. Avoids touching permissions/ but loses the
  hard-stop guarantee.
- (c) **Reject mismatched calls in the spawn_agent tool** — the
  Phase 6 `SpawnAgentTool` could refuse to register a tool the
  agent declared off-limits.

**Prediction**: M3 boundary doc will need a D40.x sub-decision
ratifying which option. Option (a) is the architecturally clean
answer but pushes M3 into a `requires extension` verdict on
`permissions/` — the §六 audit closing rule ("≥3 extensions or
multiple bypass = re-ratify") applies if combined with other M3
extensions. Worth pre-thinking this **before** M3 boundary doc
draft, not during.

### CC `agents/<name>.md` parser shape — predict 2-helper pattern (high confidence)

By analogy with T1's `parse_cc_plugin` + `_scan_cc_skills_dir`:

- `parse_cc_agent(agent_path: Path) -> AgentManifest | None`
- `_scan_cc_agents_dir(plugin_dir: Path) -> tuple[ComponentRef, ...]`

The `PluginManifest.agents: tuple[ComponentRef, ...]` field is
the natural extension — adding one field to the dataclass instead
of introducing a parallel `AgentManifest`. **Prediction**: M3
ratification will reuse D39.2's "extend the dataclass, don't fork"
pattern. (If a new `AgentManifest` shows up in M3 implementation
without explicit ratification, that's the rationale slipping —
re-read this paragraph.)

### Test LoC budget for M3

Based on Phase 19's overshoot mechanism (§2 (d)), M3 boundary doc
should budget tests at **3–4× the source LoC**, not the Phase 9
~1× ratio that the Phase 19 plan inherited. Forcing-function tests
and real-fixture integration tests are now the steady-state pattern,
not the exception.

---

## §4 Abstractions tested

### `PluginManifest` as cross-format common ground (D39.2)

**Tested**: every component of M2 — T1.1's `parse_cc_plugin`
returns `PluginManifest`; T2's `discover_with_format` puts CC and
OH plugins side-by-side in the same dict; T3's `oh plugins list`
renders both with identical column shape; T4's dogfood shows the
LLM treating namespaced CC skills exactly like the OH-plugin
namespacing tests pre-Phase-19 verified.

**Predictive power**: the abstraction was decision-first ("CC and
OH translate into the same dataclass, downstream doesn't know the
difference") rather than emergence-first. If the implementation
had needed a `source_format` field on `PluginManifest` itself, that
would have signaled the abstraction was wrong — downstream
consumers would have started branching on the field. Three
implementation commits + dogfood passed without any such branch.
Cross-format common ground is a real abstraction, not a paint job.

### Phase 18 synth envelope generalizes to namespaced names (zero diff)

**Tested**: T1.0 forbidden-imports guard enforced through T1.1,
T2, T3, T4 commits. `engine/slash_skill.py` `git diff` = empty
through the entire phase. The Phase 18 dogfood used
`/parse-credit-report 申请号12345`; the Phase 19 dogfood used
`/credit-report-reviewer__parse-credit-report 申请号12345`. The
synth envelope helper took the longer namespaced name as a
straight `skill.name` field — no special parsing, no string
splitting, no namespace-aware logic anywhere in the helper.

**Predictive power**: Phase 18 retro called this "high confidence
prediction" without quantifying the confidence level. Phase 19's
zero-diff result locks the confidence as effectively 100 % for
**this specific compound** (slash trigger × namespacing). For M3
(`name="Agent"` envelopes), the prediction is "high confidence"
because the helper is parametric on `name` already — but it's
not yet 100 %, because M3 will exercise a new combination (tools
whitelist propagating through the envelope). Worth running M3's
analog T1.0 guard preemptively, per §3 above.

### §六 wiring audit methodology — partial miss + fast self-correction

**Tested**: D39 §六 audit predicted 15 layers + (after D39.9 added)
16 layers. Of those, 12 came out exactly as predicted, 3 as the
expected `requires extension`s, and 1 (`mcp/config + client`) was
the **D39.5 miss → D39.9 self-correction** described in §2 (a).

**Caveat on the "methodology streak"**: Phase 17 = 10/10 verbatim,
Phase 18 = 13/13 verbatim, Phase 19 = 15/16 with the miss caught
*before* any code shipped. Three phases is **not yet** a
"methodology proven" series — it's "the methodology is
self-correcting under careful pre-T1 review." Phase 19's miss was
specifically because the boundary-doc author (me) checked import
paths but not transport-coverage matrices. T5 CHANGELOG records a
methodology evolution: §六 verdicts marked `unchanged` for a
non-pure module (anything in `mcp/`, `permissions/`, `tools/` that
has *its own* contract surface) must also state the layer's
contract/protocol coverage vs the contract demands — not just
import paths.

### What didn't get tested

- **`marketplace.json` multi-plugin fan-out** (D39 §一 deferred):
  finance-skills' `mybank-credit-risk/.claude-plugin/marketplace.json`
  declares both plugins in a single top-level catalog. M2 ignores
  it; users must `cp -r` each plugin individually. Worth a single
  driver-check next time real friction surfaces.
- **`~/.claude/plugins/` dual-root scan** (D39.3 deferred): M2
  user-base hasn't asked for this yet; the cp-into-OH-plugins-dir
  workflow is acceptable, and the multi-root pattern has higher
  stakes (security, conflict resolution) that warrants its own
  decision rather than a quiet extension.
- **CC plugin with Python hooks**: finance-skills' `agents/` is
  declarative (M3 territory), not Python hooks. CC plugin
  systems-of-record that ship Python hook modules would exercise
  the existing OH hook-loading path; haven't been tested in this
  phase.

---

## §六 Verdict mapping — predicted vs actual

| Layer | Predicted | Actual outcome |
|---|---|---|
| `plugins/loader` | requires extension | ✅ T2 added `discover_with_format` + dispatch (35 LoC inside `discover_with_format`); Phase 9 `fan_out` and the original `discover` API completely preserved |
| `plugins/model` | requires extension | ✅ T1.1 added `parse_cc_plugin` + `_scan_cc_skills_dir` (~70 LoC, well under predicted ~70 for two-helper version); no new dataclass introduced |
| `observability` | requires extension | ✅ `plugin_discovered` INFO event with `format` field + `plugin_dual_manifest` WARN event both fire at dogfood as designed |
| CLI subcommand surface | requires extension | ✅ T3 added `oh plugins list` with `--format json` + `--log-level` flag; no existing typer flags / commands changed |
| `engine/slash_skill` | unchanged (load-bearing prediction) | ✅ zero diff verified by `git diff` + T1.0 forbidden-imports guard remained silent through 3 implementation commits |
| `skills/store + model` | unchanged | ✅ zero diff; CC SKILL.md flow through `parse_skill` via the existing Phase 17 T1 multi-line `description: \|` block scalar acceptance |
| `mcp/config + client` | unchanged (**D39.9 enforced after D39.5 miss**) | ✅ zero diff via D39.9 silent-ignore. The original §六 verdict was right in outcome but missed *why* — D39.5 would have forced a transport extension; D39.9 reversed it before code shipped. §2 (a) discusses the methodology lesson |
| `commands/expand + store` | unchanged | ✅ zero diff; CC plugin.json has no commands concept |
| `bundles` | unchanged | ✅ zero diff |
| `hooks` | unchanged | ✅ zero diff; CC hook surface (settings.json shell commands) doesn't map to OH HookSpec |
| `permissions/tier_based` | unchanged | ✅ zero diff (M3 territory) |
| `services/snapshot + session_memory + compact` | unchanged | ✅ zero diff; CC plugin loading is bootstrap-time, no conversation envelopes generated |
| API client | unchanged | ✅ zero diff |
| memory (Phase 16/17) | unchanged | ✅ zero diff |
| eval substrate | unchanged | ✅ zero diff |
| Phase 18 SkillStore consumer (`_run_chat`) | unchanged | ✅ zero diff; namespaced `/<plugin>__<skill>` flows through D38.1 resolver as a single name string, no special casing |

**15 of 16 verdicts held verbatim** at execution time. The 16th
(`mcp/config + client`) had the right *outcome* (zero diff) but
the *path to that outcome* required the D39.9 mid-phase reversal
of D39.5. The boundary-doc-time audit was self-corrected before
any code shipped — discussed honestly in §2 (a). Phase 17 (10/10)
+ Phase 18 (13/13) + Phase 19 (15/16 + 1 self-corrected before
T1.1) is a useful three-phase pattern: the methodology produces
high prediction accuracy AND catches its own misses early, not
zero misses ever.
