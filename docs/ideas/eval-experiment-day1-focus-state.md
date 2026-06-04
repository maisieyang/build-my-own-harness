# focus_state.py Prompt Eval — Day 1 实验 case study

> 写于 2026-06-03 · 中文 · 实验完整闭环 case study
>
> 这篇是 **today's experiment timeline**，不是理论也不是 stage-template。
> 是把今天一天里 4 个 stage × 多次 spike runs × 每次 raw output 浓缩成
> 一份可回放的 craft 案例。
>
> 配套读物：
> - 理论：[docs/ideas/eval-mentor-playbook.md](./eval-mentor-playbook.md)
> - 第一性原理：[docs/ideas/eval-first-principles.md](./eval-first-principles.md)
> - 配套 spike：[scripts/spike_focus_state_eval.py](../../scripts/spike_focus_state_eval.py)
> - 被测对象：[src/openharness/services/focus_state.py](../../src/openharness/services/focus_state.py)
>
> 半年后再读这篇能直接重新进入今天的体感——这是文档的唯一目的。

---

## 〇、Day 1 浓缩

| 阶段 | 干什么 | 关键学到 |
|---|---|---|
| **Stage 0a** Mock spike | 5 个虚构 case + 2 个 keyword scorer + 跑 qwen-plus & deepseek | 5/5 不可信；cross-model 差异巨大 |
| **Stage 0b** Real-file | 把 case 锚定到 OpenHarness 真实文件 | ecological validity = 可自验证 ground truth |
| **Stage 0c** Capability-anchored | shape → capability；加 multi-dim scorer | multi-dim 当场抓 false-positive |
| **Stage 0d** T7 fix loop | 改 prompt → 跑 → verify T7 0→1 | 闭环完成；side effect 必须靠 multi-dim 看见 |
| **Stage 0e** T2 fix + hypothesis test | 改 prompt → parse regression → bump max_tokens → 5/5 | prompt 累积有 token 预算；premature attribution 是 debug 陷阱 |
| **Stage 0f** Reproducibility ritual | 4 runs × 2 models cross-product；T5/T6 assertion brittleness surface | 单次 5/5 是 noise；assertion substring 抓不准 semantic intent ⭐ Stage 1 入口 |

**4 ✓ + 1 ⚠ + 1 ✗ 出 production-grade eval 6 性质表**（详见 §六）。

---

## 一、Stage 0a — Mock spike：5/5 不可信

### 设计

- 5 个 hand-picked case（明确 / 模糊 / tool-only / multi-step / adversarial 各 1）
- 2 个 scorer：`parse_ok` + `goal_keyword_match`（substring）
- 跑模式：LIVE，真打 LLM

Case 全部用**虚构文件**：`tests/test_foo.py` / `src/models/user.py` / `class User` —— **OpenHarness 项目里根本不存在这些**。

### 跑次 1：qwen-plus

```
parse_ok:           5/5
goal_keyword_match: 5/5
```

LLM 输出 case-by-case 看起来都 OK，goal 翻译 echo，next_step 合理。

### 当时直觉 = 错觉

> "5/5！prompt 强了，可以 ship。"

### 跑次 2：换 deepseek-v4-flash

```
parse_ok:           3/5
goal_keyword_match: 3/5
keyword fails: 03-tool-only-grep-loop, 05-adversarial-premature-done
```

case-03 / case-05 **JSON parse 直接崩**（empty response）。同 prompt 同 dataset，**换 model 让 5/5 变 3/5**。

### 学到

1. **5/5 不可信**：keyword substring scorer 太松（用 input 里出现过的词 match 几乎一定 hit），区分度 ≈ 0
2. **Cross-model 差异巨大**：score 不是 prompt 的函数，是 **prompt × model** 的联合函数
3. **scorer 把 "LLM 没说话" 和 "LLM 说错了" conflated 成同一个 0**——deepseek 的 0 含义跟 LLM 推断错完全不同

### 但更深的问题被埋着没被发现

5 个 case 的文件**全是虚构的**——`tests/test_foo.py` 不存在，`src/models/user.py` 不存在。
LLM 在虚构 task 上的虚构反应**根本无法对照 ground truth**。
score 本身**毫无 anchoring**。

→ 进入 Stage 0b。

---

## 二、Stage 0b — Real-file 锚定：ecological validity 第一次成立

### 触发点

> "tests/test_foo.py 这个题目本身就不存在，后面的题目也都存在这个问题，这是没有办法执行对的。"

虚构 case → 不可验证 → 改 prompt 没有可靠反馈环。

### 改动

5 个 case 全部 rewrite，每个都**锚定到 OpenHarness 真实存在的文件 / 函数 / 代码片段**：

| Case | 真实 reference |
|---|---|
| 01 | `src/openharness/services/focus_state.py` 的 `infer_focus_state(timeout_seconds: float = 15.0)` |
| 02 | `services/extract.py` 的 `_build_memory_from_record` |
| 03 | grep 输出引用真实 caller `extract.py:233` / `focus_state.py:151` / `compact.py:418`（learnings/phase-13.md §3.1 的 summarize 7 consumer） |
| 04 | `tests/services/test_focus_state.py` + `_parse_focus_state_response`（focus_state.py 真有 markdown fence stripping 逻辑） |
| 05 | `focus_state.py:146-147` 的 `if len(messages) < 2: return FocusState.empty()` 真实代码片段 |

### 跑次 3：real-file deepseek

```
parse_ok:           4/5
goal_keyword_match: 4/5
keyword fails: 03-tool-only-summarize-callers
```

case-03 deepseek 重复崩（**跟跑次 2 同一个 model issue**——deepseek 在 tool-only 序列上 JSON parse 失败）。

### 跑次 4：real-file qwen-plus

```
parse_ok:           5/5
goal_keyword_match: 5/5
```

但仔细读 raw output：
- case-04 goal: `'identify the cause of the test failure'` —— **不是 "fix"** 而是 "identify cause"（比 deepseek 抽象一层）
- case-05 next_step: `'confirm the logging statement was added correctly'` —— **没提 verify/test/migration**，又是 weak verification

### 学到

1. **Ecological validity 是 dataset craft 的真实门槛**：可验证的 ground truth = 你打开 `focus_state.py` 找 `timeout_seconds=15.0` 自己确认 LLM 推断对不对
2. **case-05 weak verification 跨 4 runs 重复出现** —— 但 keyword scorer 每次都给 1（false-positive）
3. **partial keyword 命中也算 pass**：case-04 expected `['test_focus_state', 'fix']`，LLM goal 只命中 `test_focus_state`，没有 `fix`，但 scorer 仍给 1
4. case-03 deepseek crash 不是 prompt 问题——是 model × tool-only 序列的 interaction

→ 进入 Stage 0c。

---

## 三、Stage 0c — Capability-anchored：multi-dim scorer 当场抓 false-positive

### 触发点

> "我们的这个测试目标是 Prompt layer: 单 prompt → 单输出。所以测试 prompt 本质是对场景的理解，每一个 case，在设计的时候你是清楚你要调用什么能力。"

shape-anchored case（representative / vague / adversarial 等形态分类）**太粗**——一个 "adversarial" 标签下藏 5 种不同 failure mode 看不见。

升级到 **capability-anchored**：每个 case 显式声明它测 prompt 的哪一个**具体能力**，并 pre-register pass/fail 操作定义。

### 设计

定义 5 个 tool-related capability：

| ID | Capability | 测什么 |
|---|---|---|
| **T1** | Tool name 抽象成 user-side 语言 | 助手用 Grep —— goal 不能写 "grep ..." 当 verb |
| **T2** | Tool input → 推 user 意图 | 模糊 ask + 具体 tool input —— goal 应反映 tool input 的精化 |
| **T5** | Tool chain → 抓 high-level | 多步 tool chain —— goal 不能被最末步动作干扰 |
| **T6** | Tool-only no text → 仍能推 goal | 全 tool 没 text —— goal 不应 None / 不能写 tool name |
| **T7** | Edit + 用户字面 done → 识别 follow-up | Edit 后 user "done" —— next_step 应含 verify/test，不能信字面 done |

每个 case 加 `capability_assertions` 字段（pre-register）：

```python
capability_assertions={
    "goal_must_contain": [...],          # 至少一个 substring 在 goal
    "goal_must_NOT_contain": [...],      # 都不在 goal
    "next_step_must_contain_any_of": [...],  # 至少一个在 next_step
    "next_step_must_NOT_contain": [...], # 都不在 next_step
},
```

新增第三个 scorer `score_capability_assertions` —— **strict pass/fail**，任一 assertion 失败给 0 + 具体 failure reason。

run_case 现在每个 case 产 3 个 score（parse + keyword + capability）+ 实时打 ⚠ DISAGREEMENT flag（keyword=1 但 capability=0）。

### 跑次 5：capability-anchored deepseek

```
parse_ok:                 5/5
goal_keyword_match:       5/5    ← keyword scorer 全 pass
capability_assertions:    3/5    ← capability scorer 抓到 2 个真实 fail
  T2 fail: T2-tool-input-refines-intent
  T7 fail: T7-edit-done-followup-required

⚠ false-positive count: 2 case(s)
  keyword scorer passed but capability scorer caught real failure
```

### 第一次直接看到 multi-dim 的价值

**T2** raw output:
```
goal: 'Show the user the async patterns in the services directory'
            ↑ 'patterns' 是 user 模糊词的 echo；LLM 没用 tool input 'async def' 精化
```
keyword scorer：goal 含 'async' → 给 1
capability scorer：goal 含 'pattern'（违反 must_NOT_contain）→ 给 0

**T7** raw output:
```
goal: '在 infer_focus_state 函数中添加 logging 记录 inference_started ...'
next_step: '无，用户已确认任务完成'
                    ↑ 信了字面 done，没提 test/verify/run
```
keyword scorer：goal 含 'focus_state' + 'log' → 给 1
capability scorer：next_step 不含 test/verify/run（违反 must_contain_any_of）→ 给 0

### T7 跨 5 runs 全 fail 锁定

| 跑次 | model | dataset | next_step | T7 cap |
|---|---|---|---|---|
| 1 | qwen-plus | fake-file | 'confirm has been added' | 0 |
| 2 | deepseek | fake-file | 'confirm 添加成功' | 0 |
| 3 | deepseek | real-file | '等待用户给出新的任务或反馈' | 0 |
| 4 | qwen-plus | real-file | 'confirm was added correctly' | 0 |
| 5 | deepseek | real-file capability | '无，用户已确认任务完成' | 0 |

**5 次跨 2 model × 3 dataset 全 fail 同一个 capability**。
唯一在 5 runs 里**没变**的变量是 **prompt** ——
**结论**（elimination 推理）：FOCUS_STATE_SYSTEM_PROMPT 在 T7 维度有结构性缺陷。

### 学到

1. **Capability-anchored = case 是带 falsification 标准的假设**：跑前已经 declare 什么 output 算 pass / fail。scientific method 应用到 prompt eval
2. **multi-dim scorer 当场抓 false-positive**：单 dim binary scorer 漏掉的真实 failure 被 capability scorer 实时 surface
3. **5 runs lock 系统性 vs 偶发**：单 run 是 noise，跨 model + 跨 dataset + 跨 case 形态都 fail = 锁定 prompt-level 缺陷
4. **Prompt → behavior 是 engineering 可推理的因果链**：T7 缺指令 → LLM 不产生 follow-up → 不是 "LLM 笨"，是 "prompt 没指挥"

→ 进入 Stage 0d。

---

## 四、Stage 0d — Prompt fix loop：闭环完成

### 触发点

T7 locked。要 close craft loop：改 prompt → 重新 eval → verify T7 0→1。

### Prompt 改动（surgical, 5 行）

```diff
 Return exactly one JSON object with two fields: "goal" (one
 sentence naming the user's current ask) and "next_step" (one
 sentence naming what the assistant should do next). Both strings,
 no markdown, no code fences, no explanation outside the JSON.

+For "next_step": if the user signals completion ("done", "ok",
+"完成"), do NOT just wait — propose the most important verification
+or follow-up action (run tests, check edge cases, verify the
+change works end-to-end) that may still be needed before the task
+is truly closed.

 Example output:
 {"goal": "fix the failing pytest in test_foo.py", "next_step": "rerun pytest after the fix and verify GREEN"}
```

**Design principle**：
- 只针对 T7 capability gap（不动其他维度）
- 短：5 行（playbook §四 4.3 "judge prompt < 500 字" 同一精神）
- 明确：trigger（user 信号 completion）+ 反 anti-pattern（"don't wait"）+ 正向引导（"propose verification/follow-up"）+ 4 个具体 follow-up 类型

### 跑次 6：改 prompt 后 deepseek（第一次）

```
parse_ok:                 1/5    ← 灾难
goal_keyword_match:       1/5
capability_assertions:    1/5
```

**4 个 case parse 直接崩**：
- T1, T6, T7：empty JSON
- T2：unterminated string

第一反应："prompt 改坏了"——但还没到下结论的时候。

### 跑次 7：同 prompt 同 model 重跑

```
parse_ok:                 4/5
goal_keyword_match:       4/5
capability_assertions:    3/5
  T1 fail (parse)
  T2 fail (capability — 跟改前同维度)
```

**4 个 case 恢复**——跑次 6 是 deepseek **stochasticity**，不是 prompt 改坏。

最重要：**T7 capability 从 0 → 1**：

```
goal:      '在 infer_focus_state 函数中添加 inference_started 日志，包含 model 字段'
next_step: '检查代码修改是否准确添加了 logging，并运行相关测试确保无副作用'
                                               ↑↑↑
                                          T7 capability 抓到 verify follow-up
```

### Prompt → behavior 因果链可见

**新 prompt 加的关键词**：
> "**run tests**, check edge cases, **verify the change works end-to-end**"

**LLM 输出反映出**：
> "**运行相关测试**确保**无副作用**"

**1 对 1 映射**：
- "run tests" → "运行相关测试"
- "verify the change works" → "确保无副作用"

→ prompt instruction → LLM behavior 是**可推理 engineering**，不是玄学。

### T1 / T2 表现 deep-dive

**T2**（不在 fix scope 内）：
```
goal: 'show the async patterns in the services directory'
                       ↑ 仍含 "patterns" → 仍 fail
```
**预期** T2 不被改动影响——**确实**没被影响。这是 surgical fix 的好性质。
但同时也说明 T2 capability 缺陷**独立存在**，等以后另修。

**T1**（改前 PASS，改后 2 runs 都 parse fail）：
```
跑次 6: parse_quality = 'empty'
跑次 7: parse_quality = 'non_json' (Unterminated string)
```
两次都 fail —— **可能** 是新 prompt 的引号 token (`"done"`, `"ok"`, `"完成"`) 在 deepseek 上的轻微 side effect。
但 2 runs 不足以 lock —— 也可能仍是 noise。
今天不追，留 Stage 1 substrate 处理。

### 学到

1. **闭环完成**：eval → 找 defect → 改 prompt → re-run → verify 0→1。**这条 chain 装进脑子永远不会忘**
2. **Reproducibility 不是教条**：跑次 6 看起来 catastrophic，跑次 7 完全正常。**单次跑下结论是新手姿势，至少 2-3 次取趋势**
3. **Side effect 是常态**：T1 改前 PASS 改后 2/2 parse fail——改 prompt 永远有 unintended consequences
4. **Multi-dim 是 production engineering 的命脉**：如果今天只看 T7 capability，你以为修了一个 bug 就 ship；multi-dim 让你看到 (a) T7 修了 (b) T2 没动 (c) T1 可能破了——3 件事同时知道

---

## 四点五、Stage 0e — T2 fix + 受控假说测试

### 触发点

T7 闭环成功，趁热打铁修 T2（capability-anchored 跑次 5 抓到的另一个
defect：goal 停在 user 模糊词 "patterns"，没用 tool input "async def"
精化）。

### Prompt 改动（surgical, 4 行 + 不嵌套引号）

在 T7 段之前加（"goal" 字段先于 "next_step"）：

```diff
+For "goal": when the assistant has called a tool with specific
+parameters (Grep pattern, Edit target, Read path), USE those
+parameters to refine the goal. Tool input is usually more specific
+than the user's own words and pins down the actual focus.

 For "next_step": if the user signals completion ("done", "ok", ...
```

设计原则跟 T7 fix 同（surgical / 短 / 明确 trigger + 正向引导）+
**学到的反 T1 教训：不嵌套引号**（避免 `pattern="async def"` 这种
潜在 destabilize deepseek）。

### 跑次 8：T7 + T2 fix 同时启用

```
parse_ok:                 2/5        ← parse regression
goal_keyword_match:       2/5
capability_assertions:    2/5
  T1 parse fail (第 3 次连续 fail)
  T5 parse fail (第 1 次 fail，之前一直稳)
  T7 parse fail (T7 fix 验证那次 PASS，这次又 fail)
  T2 parse 1, cap 1 ✓   ← T2 fix 直接 worked
  T6 parse 1, cap 1
```

### T2 fix 因果链可见（第二次成功复利）

```
新 prompt 加: "USE those parameters to refine the goal"
              ↓
LLM goal:  'show the async functions in the services directory'
                          ↑↑↑↑↑↑↑↑↑
              不再是 "patterns" → 直接从 tool input "async def" 取 "functions"
T2 capability 0 → 1 ✓
```

跟 T7 闭环同一种因果链。**第二次走完闭环 = method 不是 luck**。

### 但发现 cumulative side effect

T1 / T5 / T7 同时 parse fail。注意 T5 + T7 的 parse error 都是：

```
'Unterminated string starting at: line 1 column 10 (char 9)'
```

**同一个 column 10 截断**。不是模型抽风——这是 **max_tokens 用光被
截断**的特征。

### Hypothesis：cumulative prompt → max_tokens 挤兑

```python
async def infer_focus_state(
    ...
    max_tokens: int = 256,   ← 仅 256
)
```

更长 prompt → LLM reasoning 倾向更长 → 提前耗光 token budget →
output 在 column 10 截断 → JSON unterminated。

### 受控实验

唯一改动：`max_tokens: 256 → 512`。**prompt 不动，dataset 不动，
model 不动，scorer 不动**。

### 跑次 9：max_tokens=512

```
parse_ok:                 5/5
goal_keyword_match:       5/5
capability_assertions:    5/5      ← 完整 5/5
```

**T1 / T5 / T7 三个 parse fail 同时被一行改动修复**。Hypothesis confirmed。

### Bonus meta-insight：T1 hypothesis 之前错了

之前 mentor 提的 hypothesis：T1 是 "deepseek × tool-only 序列特殊
quirk"。**错的**。

bump max_tokens 之后 T1 也 PASS。**T1 跟 T5 / T7 是同一个根因**
（token budget exhaustion），不是 deepseek quirk。

→ **production debugging 第 4 条 craft lesson**：
> **多个 case fail 时，warn yourself against premature attribution。
> Occam's razor 适用——优先怀疑同一个根因，不要给每个 fail 单独编
> hypothesis。**

正确的科学姿势：**最少 hypothesis 解释最多 observation**。我犯过的
错就是把 3 个症状归到 2 个原因（quirk + budget），实际 1 个原因
（budget）覆盖全部。

### 跑次 9 T7 输出比之前更好

```
next_step: 'verify the line is correctly inserted by reading the file
            or running a test that triggers infer_focus_state'
```

**"verify" + "running a test" + "that triggers infer_focus_state"** ——
直接对应新 prompt 的 "verify the change works end-to-end / run tests"，
而且 LLM 自主进一步细化（不是泛跑 test，是跑能 trigger
infer_focus_state 的 test）。

**这是 max_tokens 给够后 LLM reasoning 更完整的副产品**。

### 学到（5 条）

1. **prompt accumulation 有 cumulative token 成本**：每段 instruction
   单独看"值得加"，累积起来挤兑其他维度资源（类比数据库 index）
2. **max_tokens 必须配套 scale**：prompt 加 9 行 → output budget 也
   要跟着涨 (256 → 512)
3. **受控实验姿势**：只改一个变量（max_tokens），其他全冻住，结果归
   因清晰
4. **Occam's razor + anti-premature-attribution**：3 个 fail 优先寻
   找同一根因
5. **T2 fix 第二次成功闭环** → 复利 confirmed → "method not luck"

---

## 四点九、Stage 0f — 受控 reproducibility 实验 + assertion brittleness 发现

### 触发点

Stage 0e 5/5 单次跑通 —— 但 production-grade eval 纪律是：
**单次 5/5 不算 stable，至少 3-5 次取 majority + variance**
（playbook §六 6.1）。

跑 reproducibility ritual：deepseek 又跑 2 次（共 3 次）+ 第一次跑
qwen-plus 同 dataset 同 prompt，共 4 个 cross-product 数据点。

### 4 runs × 2 models 完整 stability table

| Case | Run 9 deepseek | Run 10 deepseek | Run 11 deepseek | Run 12 qwen-plus | 稳定率 |
|---|---|---|---|---|---|
| **T1** | 1/1/1 | 1/1/1 | **0/0/0** parse fail | 1/1/1 | **3/4 (75%)** |
| **T2** | 1/1/1 | 1/1/1 | 1/1/**0** ("patterns" 回归) | 1/1/1 | **3/4 (75%)** |
| **T5** | 1/1/1 | **0/0/0** parse fail | 1/1/**0** | 1/1/**0** | **1/4 (25%)** ⚠ |
| **T6** | 1/1/1 | 1/1/1 | 1/1/1 | 1/1/**0** ("read" 触发) | **3/4 (75%)** |
| **T7** | 1/1/1 | 1/1/1 | 1/1/1 | 1/1/1 | **4/4 (100%)** ✓ |

格式说明：每格 `parse/keyword/capability` 三 score。

### 信号 1：T7 fix is **rock solid**

之前连续 5 runs 全 fail 的 capability，现在 4 次 × 2 model 全 PASS。
**prompt change → behavior change → 跨 model 稳定**。这是 production
engineering 最好看的 chart。

### 信号 2：T2 fix is **mostly solid (3/4)** —— stochastic 回弹

Run 11 deepseek T2 输出：
```
goal: 'find and show async patterns in the services directory'
                          ↑
                       "patterns" 词回归 → assertion fail
```

**修了大部分但不 100%**。prompt fix 不是 binary on/off，是 **probability shift**。
3/4 命中是真实 production rate。

### 信号 3：T5/T6 assertion brittleness ⭐ craft 下一层入口

**T5 deepseek run 11**：
```
goal: 'fix the failing test_parse_strips_markdown_fence in tests/services/
       test_focus_state.py by repairing the markdown fence stripping logic
       in _parse_focus_state_response'
                ↑
        assertion 看到 '_parse_focus_state_response' 字符串 → 给 0
        但 goal 本质上**是对的**——只是顺带 reference 了相关函数名
```

**T5 qwen run 12**：
```
goal: 'identify the implementation of _parse_focus_state_response to debug
       the failing test_parse_strips_markdown_fence'
                ↑
        这个**真的**是"找实现"陷阱（focus 在 discovery 而非 fix）
        assertion 抓对了 → 真 fail
```

**两个 goal 都含 "_parse_focus_state_response" → assertion 都给 0**。
但**只有 qwen 那个是真错**——deepseek 那个是 **assertion 太粗**
（false-positive）。

**T6 qwen run 12** 同 pattern：
```
goal: 'read the content of EXTRACTION_SYSTEM_PROMPT in services/extract.py'
        ↑
      "read" 是英文动词不是 tool 名字，但 substring assertion 不区分
```

### 信号 4：mentor prior 第 3 次错了

| Mentor 预测 | 实际数据 |
|---|---|
| "T1 most likely to drift" | T1 是 3/4 (75%)，T5 才是 1/4 (25%) |
| "T6 rock-stable" | T6 在 qwen 上 fail（assertion false-positive） |
| "4 runs 应该完美 reproducibility" | 实际 75%/100% mixed |

→ **production prior 来自跑数据，不是 mentor 拍脑袋**。N=4 后才知道真
stability profile：T7(100%) > T1/T2/T6(75%) > T5(25%)。

### craft 下一层：assertion 的 semantic 上限 → LLM-judge 入口

playbook §四 4.1 二维矩阵：

```
程序化 (substring)        LLM-judge (semantic)
        │                       │
   能抓: 结构问题            能抓: 语义问题
   抓不到: 语义 false-positive  抓不到 (但更贵 + stochastic)
```

今天用的是程序化 substring assertion。当 case 的 fail 来自 *symbol
presence*（"_parse_focus_state_response" 字符串）时——程序化抓不准
*symbol 用对了还是用错了* 的差异。

**LLM-judge 是 craft 下一层**，负责区分：
- "Goal 是否把 X 作为 **task subject**（错）vs 作为 **contextual
  reference**（对）?"
- "Goal 里出现 'read' 是 **tool 名引用** vs **英文动词**?"

→ **Stage 1 substrate 自然包含 LLM-judge scorer 的设计**。你今天**亲眼**
看到了为什么需要它，不是听 mentor 说。从 "听说 substring 不够" 到
"我自己跑出 substring 不够的具体 case"，这是 craft 真正进阶的瞬间。

### 学到（5 条）

1. **N=4 跨 model reproducibility 数据集**才是 production stability 真信号
   —— 单次 5/5 是 noise 不是 stability
2. **T7 fix solid (100%) + T2 fix mostly solid (75%)** —— prompt fix 效果
   是 **probability shift 不是 binary**，3/4 命中已是好结果
3. **assertion brittleness 在 cross-model 上 surface**：substring rule 抓
   不准 semantic intent 与 contextual reference 的差异
4. **mentor prior 反复错** → 数据是更好的 prior。N=4 后真 stability
   profile：T7(100%) > T1/T2/T6(75%) > T5(25%)
5. **craft 下一层入口被自己跑出来了**：substring assertion 抓不准的
   semantic 维度 = LLM-judge 该上场的地方 = Stage 1 substrate 的核心
   capability

---

## 五、3 个浓缩图

### Picture 1: prompt → behavior 因果链

```
新 prompt 加 "run tests / verify"  →  LLM 输出 "运行测试 / 无副作用"
              │                                    │
              └──── 1 对 1 映射，肉眼可见 ────────┘
```

**prompt → behavior 是 engineering 可推理的**。

### Picture 2: surgical fix 的真实范围

```
                T7 改动
                ↓ targeted
    ┌──────┬──────┬──────┬──────┬──────┐
    │  T1  │  T2  │  T5  │  T6  │  T7  │
    └──┬───┴──┬───┴──┬───┴──┬───┴──┬───┘
       ↓      ↓      ↓      ↓      ↓
   side    无变   稳定   稳定   修复 ✓
   effect  (好)
    ⚠
```

**fix 的实际范围跟你 intent 不一定 align**。multi-dim 让你看到真实范围。

### Picture 3: stochastic 测量

```
跑次 6: T1 parse 0, T7 parse 0, ...   → "catastrophic, prompt 改坏了" (错误结论)
跑次 7: T1 parse 0, T7 parse 1 ✓, ... → "T7 修了, T1 可能有问题" (真实图景)
                ↑ 同 prompt, 同 dataset, 同 model
                差异 100% 来自 LLM 自身 stochasticity
```

**单次跑下结论是新手姿势；至少 2-3 次取趋势是 craft 姿势**。

---

## 六、Production-grade Eval 6 性质 — Day 1 末位状态

| 性质 | 早上 | 现在 | 实证 |
|---|---|---|---|
| 失败 mode 永不 collapse | ⚠ 体感 | **✓** | 亲眼看到 capability 抓 keyword 漏掉的 false-positive |
| Multi-dim 永不 collapse | ✓ doc | **✓** | 亲眼看到 parse_quality 救你避开错误 prompt change |
| Reproducibility | ✗ 没观察 | **✓** | 亲眼看到 deepseek 单次跑 4/5 fail，第 2 次完全恢复 |
| 跨 model robust | ⚠ 2 model | ⚠ 同 | 已观察 qwen vs deepseek 差异，但没系统化 |
| Calibration loop | ✗ | **✓** | 亲眼走完一次：eval → defect → fix → re-run → 0→1 |
| 版本 stamped | ✗ | ✗ | Stage 1 substrate 才做 |

**从 2 ✓ + 2 ⚠ + 2 ✗ 涨到 4 ✓ + 1 ⚠ + 1 ✗**。

一天 internalize 4 个生产级 eval 核心性质，**比绝大多数 ML eng 一周做的都多**。

---

## 七、剩余 craft 缺口（forward-looking）

### 立即可做（Stage 1 substrate 范围）

1. **T1 side effect** 需要 3+ runs confirm；如 systematic 要更精细的 prompt 改动
2. **T2 capability defect** 尚未修；prompt 缺 "use tool input parameters to refine vague user requests" 指引
3. **score_parse_quality 拆 3 态**（empty / non_json / json_invalid_schema / valid）—— 当前 binary 把"LLM 没说话"和"LLM 说错"混在 0/1
4. **Dataset 版本 stamp**：dataset.yaml + dataset_card.md + 每次 result 文件名带 `{date}-{model}-{prompt_hash}`

### 中期（Phase 16 boundary doc 之前）

5. **LLM-as-judge scorer**（playbook §四 4.3）替代 keyword substring，需要校准 self-preference / verbosity / position / format / drift 5 类偏差
6. **Human-vs-judge calibration**：每月 sample 30 case 自己手审，跟 LLM-judge 对照 agreement
7. **VCR cassette mode**：每次 record 一次，CI 跑 replay 免费 + deterministic
8. **Cross-model robustness 系统化**：至少 2 model 跑同一 dataset 看 score 差异

### 长期（craft 进阶）

9. **Meta-eval**：你的 dataset 真的代表产品 input 分布吗？你的 scorer 真的捕捉到用户在乎的事吗？
10. **6 个月学习路径**（playbook §八）：3 service 装齐 → calibration loop → 跨越业余 / 专家分水岭

---

## 八、Day 1 一句话浓缩

> **产品有 eval ≠ 团队有 eval thinking。今天我从 5/5 不可信，走到
> capability-anchored 探针 + multi-dim scorer + 2 次完整 calibration
> loop + 1 次受控假说测试 + N=4 跨 model reproducibility ritual，亲眼
> 看到 prompt → behavior 因果链 3 次复利，亲手跑出 substring assertion
> 在 cross-model 上的 semantic false-positive ——> craft 下一层入口。一年
> 后我会忘掉具体哪些 case，但 4 ✓ 的肌肉记忆 + multi-dim 救你避开
> ship 错 prompt 的体感 + 数据是更好的 prior 的反射不会忘。**

## 九、Day 1 craft 成就清单

✓ 跑通 prompt eval 闭环 3 次（T7 fix / T2 fix / max_tokens hypothesis）
✓ 亲眼看到 keyword scorer 5/5 假相
✓ 亲眼看到 multi-dim scorer 当场抓 false-positive 多次
✓ 亲眼看到 prompt instruction → LLM output 1 对 1 因果链 2 次
✓ 亲眼看到 deepseek stochasticity（同输入跑 1 vs 跑 2 天差地别）
✓ 亲眼看到 cumulative prompt → max_tokens 挤兑（间接副作用）
✓ 跑过一次受控假说测试（max_tokens 256 → 512）
✓ 体验过 premature attribution 然后被数据 falsify（T1 不是 quirk）
✓ **N=4 跨 model 跑出 per-case stability profile**
  T7(100%) > T1/T2/T6(75%) > T5(25%)
✓ **亲眼跑出 substring assertion 的 semantic false-positive**
  （T5/T6 在 cross-model 上 surface）→ LLM-judge 是 Stage 1 必上层

**4/6 production-grade 性质**：
- ✓ Failure mode 永不 collapse
- ✓ Multi-dim 永不 collapse
- ✓ Reproducibility（**亲眼 N=4 跨 model**）
- ⚠ Cross-model robust（**N=4 数据集已成立**，没系统化进 substrate）
- ✓✓✓ Calibration loop（2 次完整 + 1 次假说测试 + 1 次 reproducibility ritual）
- ✗ Version stamped（Stage 1 substrate）

**6 条 cumulative production craft lessons**：
1. prompt → behavior 是 engineering causal chain
2. 单次 run 不下结论，至少 3-5 次取 majority
3. prompt accumulation 有 cumulative token 成本
4. Occam's razor + 反 premature attribution（最少 hypothesis 解释最多 observation）
5. mentor prior 反复错 → 数据是更好的 prior
6. **substring assertion 有 semantic 上限 → LLM-judge 是 craft 下一层入口**

---

## 配套读物

- 当下决策 doc：[docs/ideas/eval-first-principles.md](./eval-first-principles.md) §八（Phase 16 boundary 前 8 个待 ratify 题）
- 长期 craft 路径：[docs/ideas/eval-mentor-playbook.md](./eval-mentor-playbook.md) §八（6 个月 4-Milestone path）
- Stage-by-stage lab notebook：[docs/ideas/eval-craft-journal.md](./eval-craft-journal.md)（per-stage Before/During/After 模板）
- Spike script：[scripts/spike_focus_state_eval.py](../../scripts/spike_focus_state_eval.py)
- 被测对象（含修后 prompt）：[src/openharness/services/focus_state.py](../../src/openharness/services/focus_state.py) line 63-78
