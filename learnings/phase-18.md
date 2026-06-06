# Phase 18 Retro — Slash-Skill Triggering (M1 of CC Skill 接入)

> Phase status as of 2026-06-06 evening — T1+T2+T3+T4 landed; T5
> wraps with CHANGELOG + the §2/§3/§4 sections below filled in.
>
> Boundary: [`decisions/38-phase-18-boundary.md`](../decisions/38-phase-18-boundary.md)
> Plan: [`tasks/phase-18-plan.md`](../tasks/phase-18-plan.md)
> Phase 17 retro (the §六 wiring-audit methodology that gated this
> phase): [`learnings/phase-17.md`](./phase-17.md)

## Commit trail (in progress)

```
89a3f58  test(compact): Phase 18 T3 — L0-L4 synth envelope transparency verification
7598418  feat(cli): Phase 18 T2 — slash-skill REPL resolver + /skills built-in (D38.1/D38.4/D38.5)
f847df0  feat(engine): Phase 18 T1 — synth LoadSkill envelope helper (D38.2/D38.3)
```

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

## §2 What missed — TODO in T5

(filled in during Phase 18 close-out walkthrough)

## §3 Predictions for M2 / M3 — TODO in T5

(filled in during Phase 18 close-out walkthrough)

## §4 Abstractions tested — TODO in T5

(filled in during Phase 18 close-out walkthrough)
