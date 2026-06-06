# Eval case study：memory_decision M3 走一遍

> 写于 2026-06-06 · 中文
>
> 配套读物：
> - 数据集：[`evals/memory_decision/dataset.yaml`](../../evals/memory_decision/dataset.yaml) + [`dataset_card.md`](../../evals/memory_decision/dataset_card.md)
> - Scorer 实现：[`src/openharness/eval/memory_decision_scorers.py`](../../src/openharness/eval/memory_decision_scorers.py)
> - 契约来源：[`decisions/36-phase-16-memory-pivot-boundary.md`](../../decisions/36-phase-16-memory-pivot-boundary.md) + [`docs/ideas/memory-first-principles.md`](./memory-first-principles.md)
> - 决策面 map：[`decisions/35-eval-coverage-map.md`](../../decisions/35-eval-coverage-map.md) §D35.5（本 eval = 决策面 #4 inline 决策 P0）
>
> 这篇不是 boundary doc、不是 plan、也不是 retro——是一次**教学性 case study**。
> 起源：2026-06-06 一次跟 Claude Code 的对话里，用户回头考古
> "为什么当时同意了 eval-first 的策略但其实没真理解"，结果牵出了一整套
> eval 数据设计的纪律。用户把这次对话标为 case study 并要求完整记录。
> 这篇文档是那次记录。

---

## 〇、起点：为什么是 M3

Phase 16 的 gating eval `memory_decision` 总共 6 个 case（[`dataset.yaml`](../../evals/memory_decision/dataset.yaml)）。
6 个里**最值得拿来教学走一遍**的是 **M3 (warm-correction)**，原因：

1. **它落在 warm-start**——dataset 里真正干活的 3 个 warm 样本之一
2. **它会触发全部 5 个 scorer 的全部分支**——cold 路径 ④ 永远 NA，trivial 路径 ②③④⑤ 全 NA，只有 warm 能把 5 个 scorer 的关系展开完整
3. **它的 user_msg 不在 taxonomy 重叠区**（correction = feedback 是唯一答案）——能让 ⑤ TypeJudge 的行为干净可读，不被歧义干扰

所以 M3 是 5 个 scorer 关系的"完整露面舞台"。

---

## 一、跑前布置（fixture）

测试目录 `/tmp/test_memory/` 里**预先**放好：

**MEMORY.md**（3 条 seed）：

```
# Memory index

- [Code style 80 chars](code-style-line-width.md) — wrap code at 80 chars max
- [Test runner command](test-runner-command.md) — tests run via `uv run pytest -q`
- [Build dir Linear project](linear-build-tracking.md) — infra bugs in Linear project BUILD
```

3 个对应的 `.md` body 文件也都存在（`code-style-line-width.md` / `test-runner-command.md` / `linear-build-tracking.md`，每个都有合法 frontmatter）。

**user_msg**（这次会话的唯一一句用户输入）：

> "Actually we use Tavily for web search, not SerpAPI. SerpAPI is too expensive at our scale."

**契约期望**：

- 写一条新 feedback memory（slug 自由，比如 `web-search-tavily.md`）
- **Edit** MEMORY.md 追加一行——3 条 seed 不能丢
- frontmatter `type: feedback`（correction 类对应 feedback）

---

## 二、5 种可能的模型输出 × 5 个 scorer

下面 5 个场景覆盖了模型可能走的所有有意思的分支。每个场景给出 tool_use 序列 + 5 个 scorer 的具体判决。

### 场景 A — 理想路径（Edit MEMORY.md）

模型 emit 两个 tool_use：

```
[1] Write
    file_path=/tmp/test_memory/web-search-tavily.md
    content="---
            name: web-search-tavily
            description: Use Tavily for web search, not SerpAPI
            metadata:
              type: feedback
            ---
            Use Tavily (not SerpAPI) for web search.
            Why: SerpAPI cost is prohibitive at our scale.
            How to apply: when web search is needed, use Tavily."

[2] Edit
    file_path=/tmp/test_memory/MEMORY.md
    old_string="- [Build dir Linear project](linear-build-tracking.md) — infra bugs in Linear project BUILD"
    new_string="- [Build dir Linear project](linear-build-tracking.md) — infra bugs in Linear project BUILD
                - [Web search Tavily](web-search-tavily.md) — use Tavily not SerpAPI"
```

| Scorer | 判 | 为什么 |
|---|---|---|
| ① Judgment | **PASS** | `expect_write=true` ✓ Write 出现了 |
| ② Frontmatter | **PASS** | `FRONTMATTER_RE` 匹配，`name` + `metadata.type` 字段齐全 |
| ③ IndexUpdate | **PASS** (1.0) | Edit on MEMORY.md = canonical 形态 |
| ④ NoOverwrite | **NA** | 没用 Write 在 MEMORY.md 上——没什么可检 |
| ⑤ TypeJudge | **PASS** | feedback 在 `M-judge-correction` rubric 的 defensible 答案集里 |

**总判：PASS**（4 通过 + 1 NA）

### 场景 B — 索引漂（drift）

模型只 emit 一个 tool_use：

```
[1] Write
    file_path=/tmp/test_memory/web-search-tavily.md
    content="--- name: web-search-tavily ... ---"
```

`.md` 写了，MEMORY.md 没碰。

| Scorer | 判 |
|---|---|
| ① Judgment | **PASS** |
| ② Frontmatter | **PASS** |
| ③ IndexUpdate | **FAIL** (0.0) ← drift |
| ④ NoOverwrite | **NA** |
| ⑤ TypeJudge | **PASS** |

**总判：FAIL**——5 个数里 ③ 单独跳出来，**一眼定位失败模式**：drift。
**修法**：在 system prompt 加一句"写完 `.md` 之后一定要 Edit MEMORY.md"。

### 场景 C — 灾难（destructive overwrite）

模型 emit 两个 tool_use，但第二个用了 **Write** 不是 Edit：

```
[1] Write
    file_path=/tmp/test_memory/web-search-tavily.md
    content="..."

[2] Write    ← 注意是 Write 不是 Edit
    file_path=/tmp/test_memory/MEMORY.md
    content="# Memory index

            - [Web search Tavily](web-search-tavily.md) — use Tavily not SerpAPI"
```

`Write` 是覆盖语义——MEMORY.md 整个被新内容替换，3 条 seed **全消失**。

| Scorer | 判 | 关键观察 |
|---|---|---|
| ① Judgment | **PASS** | Write 出现了，方向算对 |
| ② Frontmatter | **PASS** | `.md` 自己没问题 |
| ③ IndexUpdate | **PARTIAL** (0.5) | 碰了索引但用了 Write 不是 Edit——**单看 ③ 不知道是真坏事还是写得聪明** |
| ④ NoOverwrite | **FAIL** (0.0) | 在 MEMORY.md 内容里找 anchor 串（`code-style-line-width` / `test-runner-command` / `linear-build-tracking`）——**一个都没找到**，0% 保留率 |
| ⑤ TypeJudge | **PASS** | 类型对 |

**总判：FAIL**——③ + ④ 配合给出极清晰的信号：**"用了 Write 不是 Edit (③=0.5)，且这个 Write 把 seed 砍光了 (④=0.0)"**。
**修法**：在 prompt 里更硬地约束 `"never Write MEMORY.md, only Edit"`；或在 harness 层加 guard 不让 LLM 对 MEMORY.md 用 Write 工具。

### 场景 D — 抢救（Write-with-preservation）

模型还是用了 Write 在 MEMORY.md 上，**但先读出原内容 + 拼上新行 + 整体 Write 回去**：

```
[1] Write
    file_path=/tmp/test_memory/web-search-tavily.md
    content="..."

[2] Write    ← 还是 Write
    file_path=/tmp/test_memory/MEMORY.md
    content="# Memory index

            - [Code style 80 chars](code-style-line-width.md) — wrap code at 80 chars max
            - [Test runner command](test-runner-command.md) — tests run via `uv run pytest -q`
            - [Build dir Linear project](linear-build-tracking.md) — infra bugs in Linear project BUILD
            - [Web search Tavily](web-search-tavily.md) — use Tavily not SerpAPI"
```

| Scorer | 判 | 关键观察 |
|---|---|---|
| ① Judgment | **PASS** | |
| ② Frontmatter | **PASS** | |
| ③ IndexUpdate | **PARTIAL** (0.5) | 跟场景 C **一模一样**——单看 ③ 看不出区别 |
| ④ NoOverwrite | **PASS** (1.0) | 3 个 anchor 全找到，100% 保留率 |
| ⑤ TypeJudge | **PASS** | |

**总判：PASS**（按 dataset_card composite rule："warm-start Write path 0.5 + NoOverwrite 不 FAIL = 抢救为 PASS"）

**这是 ③/④ 搭档最优雅的一面**：单独的 ③ 在场景 C 和 D 输出完全相同（PARTIAL），**只有 ③ + ④ 一起读才有定论**：
- ③ 说"结构不理想"
- ④ 说"但内容没受损"
- 合判：**容忍**——契约接受 Write-with-preservation，因为它没造成真损害，只是没用最优工具

任何一个单独看都给不出这个判决。**这就是为什么 ③ 不被设计成自己去读内容判完**——拆开成 ③（结构）+ ④（内容）让两个 scorer 都简单、可独立改、可独立判错。

### 场景 E — 完全没写

模型只回复一句话，不 emit 任何 tool_use：

> "Got it — I'll use Tavily from now on."

| Scorer | 判 |
|---|---|
| ① Judgment | **FAIL** (0.0) ← `expect_write=true` 但没 Write/Edit |
| ② Frontmatter | **NA** |
| ③ IndexUpdate | **NA** |
| ④ NoOverwrite | **NA** |
| ⑤ TypeJudge | **NA** |

**总判：FAIL**——① 一个数定胜负，其余全 NA。

这是 **M6 trivial-skip 的镜像反面**：
- M6 是"该 NA 满屏的时候 NA 满屏 = PASS"（克制）
- 场景 E 是"**不该** NA 满屏的时候 NA 满屏 = FAIL"（漏写）

**修法**：prompt 里 nudge 模型在 correction 信号上主动写 memory，不要只口头确认。

---

## 三、5 个场景的指纹对比

```
                  ①Judg   ②Front   ③Index    ④NoOver   ⑤Type    总判
─────────────────────────────────────────────────────────────────────
A 理想 Edit       PASS    PASS     PASS      NA        PASS     PASS
B drift           PASS    PASS     FAIL      NA        PASS     FAIL
C 灾难 Write      PASS    PASS     PARTIAL   FAIL      PASS     FAIL
D 抢救 Write      PASS    PASS     PARTIAL   PASS      PASS     PASS
E 完全没写        FAIL    NA       NA        NA        NA       FAIL
```

每一行都是一个 **5 位指纹**——PASS / FAIL / PARTIAL / NA 的不同组合。这张表能直接读出几件事：

1. **B 和 E 都 FAIL，但修法完全不一样**——B 改 prompt 索引 nudge，E 改 prompt 主动写 nudge。
2. **C 和 D 在 ③ 上完全相同**（都 PARTIAL），但 ④ 让结局相反——D 抢救成 PASS，C 确认成 FAIL。
3. 5 个 scorer 在 5 个场景里给出 5 种不同的指纹模式——**信息量没塌缩**。

把这 5 个指纹压成单分（比如加权平均成 0–1）：

```
A 1.0   B 0.6   C 0.4   D 0.9   E 0.0
```

光看 0.4 vs 0.6 你**区分不了 destructive overwrite 和 drift**——但这两个失败的修法**完全不一样**。这是"评分颗粒度要跟可采取的行动颗粒度匹配"原则的具体兑现：5 位指纹保住了 4 种修法的区分能力，单分把它压没了。

---

## 四、5 个 scorer 的关系树

把上面 5 个场景揭示的关系画出来：

```
                  ① Judgment（写了还是没写？方向对吗？）
                              │
                              │ ← 永远开火，不会 NA
                              │
              ┌───────────────┴───────────────┐
              │                               │
         "没写" 路径                     "写了" 路径
              │                               │
   (M6 trivial-skip 走这条)                   │
   ②③④⑤ 全部 NA                              │
   看 ① 一个数定胜负                          │
                              ┌───────────────┼──────────────┐
                              │               │              │
                       ② Frontmatter    ③ IndexUpdate   ⑤ TypeJudge
                       (.md 格式合规?)  (索引碰了吗?)   (类型选对了吗?)
                                              │
                          ┌───────────────────┼───────────────┐
                          │                   │               │
                       没碰索引            Edit MEMORY.md   Write MEMORY.md
                          │                   │               │
                        0.0                 1.0              cold → 1.0
                       (索引漂)           (理想)             warm → 0.5 (待裁定)
                                                                    │
                                                                    │
                                                            ④ NoOverwrite 开火
                                                            （只在这一种情况下）
                                                                    │
                                                          ┌─────────┴─────────┐
                                                          │                   │
                                                       保留了 seed         砍了 seed
                                                          │                   │
                                                     抢救：等价 1.0       0.0
                                                                          (destructive
                                                                          overwrite 灾难)
```

### 4.1 ③ + ④ 搭档的设计要点

整张树里最值得反复看的是 **③ + ④ 这对搭档**：

- **③ 的代码很简单**——只看 tool_name + file_path，不读 MEMORY.md 内容
- **④ 的代码很专注**——只在 warm + Write MEMORY.md 这一种情况下开火，专门读内容看 anchor 保留率
- **它们各自可以独立改、独立判错**

如果把这两件事压进一个 scorer："警告 IndexUpdate 自己去读内容判优劣"——它会很臃肿，责任也乱（既管"碰没碰"又管"碰得好不好"）。拆开之后，两个 scorer 都是单一职责。

这是 **drift 和 destructive overwrite 是两种不同失败模式 → 它们的检测代码也分开** 在评分层的具体兑现。

### 4.2 ① Judgment 的特殊性

① 是**唯一永远开火**的 scorer。其他 4 个都会在某些情况下返回 NA：

| Scorer | NA 条件 |
|---|---|
| ② Frontmatter | 没写 .md |
| ③ IndexUpdate | 没写 |
| ④ NoOverwrite | cold 或 warm-但-用-Edit（最挑剔的一个） |
| ⑤ TypeJudge | 没写 |

所以 trivial-skip 样本（M6）跑下来只有 ① 给一个数，其余 4 个 NA。**M6 不需要其他 4 个 scorer，因为它要测的就是"不开火"本身**——一个 NA 满屏的样本反而证明了克制能力。

---

## 五、用户从这个 case study 得到的两条认知

case study 跑完后，用户明确说这次走读让他从两个角度对 eval 有了更深的理解。这两条**用户自己说的**总结，是这篇 doc 的目的地：

### 5.1 通感角度：eval 测试用例 ≈ 软件测试用例

设计 eval case 这件事，**结构上跟给软件写测试用例是同一件事**：

| 软件测试 | Eval 设计 |
|---|---|
| Fixture（测前布置环境） | 预填 MEMORY.md + 3 条 seed |
| 控制变量 | M1 ↔ M5 同 shape 不同 fixture，让"warm 这层"是唯一变量 |
| 失败模式驱动 case | 先列 canonical failure mode（drift / overwrite）再列 case |
| 多 assertion 不 collapse | 5 个 scorer 各报各的，不压成单分 |
| NA / N/A skip 条件 | ② / ③ / ④ / ⑤ 在没写的样本上返回 NA |
| 最少必要覆盖 | 6 个不是 60 个 |

**这一通感打开了**：eval 设计不是新工程门类，是软件测试纪律在概率系统上的迁移——所有软件测试的最佳实践都能搬过来用。

### 5.2 特殊性角度：为什么需要引入 LLM-as-judge

eval 跟传统软件测试不完全一样的那部分，**集中在 ⑤ TypeJudge 这种 scorer**。

什么时候需要 LLM-as-judge？回答**不是**"答案难算" 或 "需要语义理解"——而是：

> **契约本身留下了 defensible 歧义的地方，需要一个能容忍多个正确答案的裁判**。

具体到 ⑤：CC 自己定义的 type 里，"preference" 同时落在 `user` 和 `feedback` 的定义里——契约本身留了重叠。程序化 scorer 只能写 "type 必须是 X"，那就在扣**契约自己的歧义**而不是扣模型。LLM-judge + 一个允许多个 defensible 答案的 rubric，是这种 contract-level 歧义的**专属工具**。

**反过来这一条也给"什么时候不该用 LLM-judge"的判据**：

- 失败模式有**程序可检的明确特征**（drift = 工具调用列表没碰 MEMORY.md；overwrite = 内容 anchor 保留率 < 50%）→ 用程序化 scorer，不要请 LLM-judge
- 失败模式落在**契约自己留下的语义重叠区** → LLM-judge 是唯一合法工具

这条判据让 LLM-judge 从"高级评分手段"还原成"为特定 contract 形态服务的专门工具"。

---

## 六、Cross-refs（为什么把这条放最后）

这篇 doc 是一次教学性 case study，不是规范文档。它**引用**下面几份规范文档，但不替代它们：

- 数据集的 **3-claim contract**：[`evals/memory_decision/dataset_card.md`](../../evals/memory_decision/dataset_card.md)
- Scorer 实现：[`src/openharness/eval/memory_decision_scorers.py`](../../src/openharness/eval/memory_decision_scorers.py)
- 契约推导：[`docs/ideas/memory-first-principles.md`](./memory-first-principles.md)
- Phase 16 boundary：[`decisions/36-phase-16-memory-pivot-boundary.md`](../../decisions/36-phase-16-memory-pivot-boundary.md)
- 决策面 map（本 eval 在 #4）：[`decisions/35-eval-coverage-map.md`](../../decisions/35-eval-coverage-map.md)
- 同 family 的方法论：[`docs/ideas/eval-mentor-playbook.md`](./eval-mentor-playbook.md)
- 哲学锚点：[[feedback-design-for-strong-model]]
