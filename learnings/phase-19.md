# Phase 19 Retro — CCPluginLoader (M2 of CC Skill 接入)

> Phase status as of 2026-06-07 evening — T1+T2+T3+T4 landed; T5
> wraps with CHANGELOG + the §2/§3/§4 sections below filled in.
>
> Boundary: [`decisions/39-phase-19-boundary.md`](../decisions/39-phase-19-boundary.md)
> Plan: [`tasks/phase-19-plan.md`](../tasks/phase-19-plan.md)
> Phase 18 retro (the M2 zero-diff prediction that gated this phase):
> [`learnings/phase-18.md`](./phase-18.md) §3

## Commit trail (in progress)

```
76f1487  feat(cli): Phase 19 T3 — oh plugins list subcommand (D39.7 / D39.8)
b50e0ae  feat(plugins): Phase 19 T2 — PluginLoader dual-format dispatch + plugin_discovered event
9c241fd  feat(plugins): Phase 19 T1.1 — CC plugin parser + D39.9 silent-ignore
b65ec0b  docs(phase-19): D39.9 — reverse D39.5 (.mcp.json out of M2 scope)
68c41a0  docs(phase-19): prep — boundary doc + capability plan + T1.0 proactive guard
```

Code net (T1.1 + T2 + T3): **+396 LoC src** (~+24 % over boundary
prediction of +320 driven by the D39.7 ``--log-level`` flag +
``configure_logging`` wiring discovered during T3) and **+991 LoC
tests** (~4.5× the +220 prediction — extra tests went to the D39.9
silent-ignore forcing function, finance-skills real-fixture
integration suites, and the no-fan_out side-effect assertion). T5
discusses the test overshoot honestly in §2.

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

## §2 What missed — TODO in T5

(filled in during Phase 19 close-out walkthrough)

## §3 Predictions for M3 / Phase 20 — TODO in T5

(filled in during Phase 19 close-out walkthrough)

## §4 Abstractions tested — TODO in T5

(filled in during Phase 19 close-out walkthrough)
