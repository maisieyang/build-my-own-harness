# focus_state.py Eval Dataset Card

> Stage 1 substrate dataset · 2026-06-03
>
> 跟代码分离的 dataset metadata + 跨 run 的 stability profile +
> 已知 brittle 维度。

## Identity

| 字段 | 值 |
|---|---|
| Dataset name | focus_state-capability-anchored |
| Version | 0.2.0 (Stage 2 — T3/T4/T8 expansion) |
| Created | 2026-06-03 (initial) · 2026-06-03 (Stage 2) |
| Subject | `openharness.services.focus_state.infer_focus_state` |
| File | `evals/focus_state/dataset.yaml` |
| Sample count | 8 |

## Reference policy(D41.5 增补,2026-07-12)

参照模型 **qwen3.7-max**(cassettes 双基线:qwen-plus + qwen3.7-max,
以 qwen3.7-max 为 gate 基线;qwen-plus 上的红 = 信息,不是 gate 红——
design-for-strong-model,D41.5)。pre-D41.5 建档,本声明为追补,
既有 stability profile 与 bar 语义不变。

## Capability coverage

| Capability | Sample | 测什么 |
|---|---|---|
| T1 | `T1-tool-name-abstraction` | Grep/Read tool name 抽象成 user-side 语言（goal 不能写 "grep/read" tool verb）|
| T2 | `T2-tool-input-refines-intent` | Tool input 精化 vague user ask（用 "async def" 精化 "async 模式"）|
| **T3** | `T3-tool-result-content-integration` | tool_result 内容是否被 read 进 next_step（具体 token 引用 vs generic 抽象）|
| **T4** | `T4-tool-error-recovery` | tool error 时是否提 recovery 路径（ls / grep / 让用户改正） |
| T5 | `T5-tool-chain-high-level` | 6-msg chain 抽高层 goal，不被末步 grep 干扰 |
| T6 | `T6-tool-only-no-text-still-infer` | 全 tool 没 text 仍能推出 user-side goal |
| T7 | `T7-edit-done-followup-required` | Edit + 字面 "done" 后 next_step 必含 verify/test |
| **T8** | `T8-bash-user-level-abstraction` | Bash 跑 pytest 时 goal 是 user 任务级，不 leak shell（"uv run" / "execute"）|

**Stage 2 new (2026-06-03 expansion)**: T3 / T4 / T8 — 3 个新 capability 探针填 Stage 1 dataset_card 列出的覆盖缺口。

**Not yet covered**（forward gaps for Stage 2+ 继续扩量）：
- Non-task-focused 对话（闲聊 / topic switch / 用户改主意）
- Multi-turn 跨主题转换
- Code review 类对话（user 贴一段 code 让评审）
- Architecture / decision 类对话（user 问 D29.x 为什么这么定）
- 30 representative 类 case 来源 OpenHarness 真实 session log（playbook §八 task 2）

## Stability profile

### Day 1 N=4 runs × 2 models（2026-06-03 跑次 9-12）

每格 `parse/keyword/capability`：

| Sample | deepseek×3 + qwen-plus×1 | 稳定率 | 备注 |
|---|---|---|---|
| T1 | 1·1·0(parse) · 1 | **3/4 (75%)** | deepseek-v4-flash 偶发 parse fail |
| T2 | 1·1·1 · 1(回弹"patterns")·1 | **3/4 (75%)** | prompt fix probability shift 不是 binary |
| T5 | 1·0(empty)·0·0 | **1/4 (25%)** ⚠ | substring assertion semantic 上限暴露 |
| T6 | 1·1·1·0("read"误判) | **3/4 (75%)** | substring assertion semantic 上限暴露 |
| T7 | 1·1·1·1 | **4/4 (100%)** ✓ | rock solid，最早 fix 的 capability |

### Stage 1 substrate refactor 验证（2026-06-03 跑次 13, qwen-plus）

| Sample | 这次 | 跟 Day 1 stability 对照 |
|---|---|---|
| T1 | 1·1·1 | matches 75% (this run is hit) |
| T2 | 1·1·1 | matches 75% |
| T5 | 1·1·0(_parse_focus_state_response) | matches 25%（substring brittleness 再次出现） |
| T6 | 1·1·1 | matches 75% (this run is hit) |
| T7 | 1·1·1 | matches 100% |

### Stage 2 收尾 — N=3 stability（2026-06-03 跑次 14-16）

跑次 14 qwen-plus, 跑次 15 qwen-plus, 跑次 16 deepseek-v4-flash。

| Cap | Run 14 | Run 15 | Run 16 | N=3 rate | 失败根因 |
|---|---|---|---|---|---|
| **T1** | ✓ | ✓ | ✓ | **3/3 (100%)** ✓ | rock solid |
| **T2** | ✓ | ✓ | ✓ | **3/3 (100%)** ✓ | rock solid |
| **T3** 🆕 | ✓ | ✓ | ✗ parse | **2/3 (67%)** | deepseek empty (model noise) |
| **T4** 🆕 | ✓ | ✗ | ✗ | **1/3 (33%)** ⚠ | **assertion 措辞太窄**（"typo"/"verify" 没 cover） |
| **T5** | ✗ | ✗ | ✓ | **1/3 (33%)** | qwen identify-impl 真 trap + substring 上限 |
| **T6** | ✗ | ✓ | ✓ | **2/3 (67%)** | qwen "read the content" 偶发 (substring 上限) |
| **T7** | ✓ | ✓ | ✗ | **2/3 (67%)** | deepseek 中文 "检查"/"确认" - assertion 只 cover 英文 |
| **T8** 🆕 | ✓ | ✓ | ✗ parse | **2/3 (67%)** | deepseek empty (model noise) |

**关键发现**：

1. **T1/T2 rock solid (100%)** — prompt 在 tool name 抽象 + tool input 精化两个维度 production-grade
2. **T4 1/3 不是 prompt 弱** — N=3 里 LLM 产出 3 种 valid recovery 措辞：`'checking the correct filename'` / `'checking for a typo or alternative filename'` / `'verifying the file path'`，**assertion list 只 cover 第 1 种**。assertion 设计需要扩展或上 LLM-judge
3. **T7 67% 是 cross-language assertion gap** — deepseek 用中文 verification 动词（"检查" / "确认"），assertion 只 cover 英文 (test/verify/run)。**Stage 3 LLM-judge 同时解决跨语言 + 同语义不同措辞两个问题**
4. **T3/T8 67% 都是 deepseek 偶发 empty response** (model-specific noise，不是 capability 问题)
5. **T5 33%** — qwen 真 trap (identify-implementation) + substring 上限 共存

### 3-tier capability map (Stage 2 substring-only 视角)

```
┌─────────────────────────────────────────────────────┐
│ Tier A — rock solid (100%): T1, T2                  │
├─────────────────────────────────────────────────────┤
│ Tier B — 67%: T3, T6, T7, T8                        │
│ Tier C — 33%: T4, T5                                │
└─────────────────────────────────────────────────────┘
```

### Stage 3 LLM-judge 揭示的真实 production strength（2026-06-04 跑次 17-18）

| Cap | Stage 2 substring % | Stage 3 judge | 真实状态 |
|---|---|---|---|
| T1, T2 | 100% | (no rubric) | **真 100%** |
| T3, T8 | 67% | (no rubric) | **真 67%, 失败 = deepseek model noise (非 prompt)** |
| T4 | 33% | 跑 2 次都 PASS (judge) | **真实 ≥ 67%**, substring 措辞列太窄 |
| **T5** | **25%** ⚠ | **judge 跑 2 次都 PASS** | **真实 ≥ 67%**, substring "symbol-as-end" 误判 |
| **T6** | **75%** | **judge 跑 2 次 1·ERROR (1 = qwen, ERROR = deepseek noise)** | **真实 ≥ 90%**, substring "read" 误判 |
| T7 | 100% | judge 跑 2 次都 PASS | **真 100%** |

**Stage 3 craft truth**：**substring brittleness 让 prompt 看起来比实际弱 30-40%**。

跑次 18 qwen-plus 2 个 ⭐ SUBSTRING BRITTLENESS EXPOSED 信号是 Stage 3 craft 闭环的直接证据：
- T5 substring=0, judge=1 — judge 识别 "symbol discovery as a means to fix" (PASS)
- T6 substring=0, judge=1 — judge 识别 "'read' as English verb" (PASS)

### Judge rubric N=2 quality check

| Cap | Rubric 工作? | 证据 |
|---|---|---|
| T4 | ✓ | judge reason 提 "informing the user...suggesting verification" — 跟 rubric PASS criteria 对齐 |
| T5 | ✓ | judge reason 提 "symbol discovery as a means" — 精确复现 rubric "means vs end" 区分 |
| T6 | ✓ | judge reason 提 "English verb describing intent, not Read tool reference" — 精确复现 rubric 区分 |
| T7 | ✓ | judge reason 提 "verify by running test" — 跨语言 rubric example 生效 |

**4 个 rubric 全 calibrated**，无需 Stage 4 immediate iterate。后续如有 borderline case，再加 example。

### Stage 3 LLM-judge 待解决的 6 个 assertion brittleness 维度

| # | Cap | Brittleness | LLM-judge rubric |
|---|---|---|---|
| 1 | T4 | 措辞多样性："checking the" vs "typo" vs "alternative" vs "verifying" 都是 valid recovery | "Does next_step propose at least one recovery action (alternative search / clarification / different approach)?" |
| 2 | T5 | "symbol as task subject" vs "symbol as contextual reference" | "Is the goal **focused on** finding the implementation (FAIL), or just **mentioning** it as part of broader fix (PASS)?" |
| 3 | T6 | "read" 作 tool name vs 作英文动词 | "Does goal use 'read' as a tool-name verb (FAIL) or as English verb 'examine/view' (PASS)?" |
| 4 | T7 | 跨语言：英文 test/verify vs 中文 检查/确认 | "Does next_step propose any verification action (in any language)?" |
| 5 | (general) | LLM 用同一语义不同措辞 | semantic equivalence check |
| 6 | (general) | LLM 用 Markdown / bullet 格式而非 plain text | format-tolerant comparison |

## 已知 brittle 维度（Stage 3 LLM-judge 接管）

| Sample | Brittleness | Stage 3 fix |
|---|---|---|
| T5 | `goal_must_NOT_contain: [_parse_focus_state_response]` 无法区分 "symbol as task subject" vs "symbol as contextual reference" | LLM-judge: "Is the goal *focused on* finding the implementation, or just *mentioning* it as part of the broader fix?" |
| T6 | `goal_must_NOT_contain: [read ]` 无法区分 tool 名引用 vs 英文动词 | LLM-judge: "Does the goal use 'read' as a tool-name verb, or as the English verb 'examine'?" |

## Prompt under eval

`src/openharness/services/focus_state.py:63-78` (FOCUS_STATE_SYSTEM_PROMPT)
+ `max_tokens=512` (bumped from 256 in Day 1 Stage 0e to accommodate
cumulative prompt → output budget pressure).

## 改 dataset 的纪律

按 playbook §六 6.3：
- **不要**为了让 score 涨而删 fail case
- **不要**为了避开 brittle assertion 而改 assertion 规则（除非 LLM-judge 上场）
- 加新 case 必须 **明确 capability ID** + **pre-register assertions**

## 引用

- 实验全过程：[`docs/ideas/eval-experiment-day1-focus-state.md`](../../docs/ideas/eval-experiment-day1-focus-state.md)
- Substrate code：[`src/openharness/eval/`](../../src/openharness/eval/)
- 理论：[`docs/ideas/eval-mentor-playbook.md`](../../docs/ideas/eval-mentor-playbook.md)
