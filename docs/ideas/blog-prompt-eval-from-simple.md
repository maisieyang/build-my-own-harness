# 从最简单开始：设计一套 prompt eval 的逐 stage 推演

刚开始想给 `services/focus_state.py` 装 eval 时,我列了一份 production-grade eval 应该有的清单:dataset、scorer、LLM-judge、cassette、version stamping、CLI 子命令。然后我把这份清单扔了。

我决定从最简单的形式开始,只在每一步**强迫**我升级时才升级 —— 如果我不知道下一层抽象解决的具体问题是什么,我就不应该建那一层。

我目前的判断是:**好的 measurement 基础设施不是先设计后建的,是被你"没法回答的问题"一层层逼出来的**。每一层抽象都应该对应"上一层没法看清楚的一件事"。下面是这套 eval 6 个停顿点的设计推演 —— 每一个停顿点都是一个具体的问题,逼出下一层。

---

## 第一版:5 个 case + 2 行 scorer

我手写了 5 个 case,每个是一段对话 + 一组期望的关键词。两个 scorer:

```python
def score_parse_ok(result):
    return 1.0 if result.goal is not None else 0.0

def score_goal_keyword_match(result, expected_keywords):
    return 1.0 if any(kw in result.goal.lower() for kw in expected_keywords) else 0.0
```

这是我能想到的"最小可用 eval"。3 行 scorer + 5 个 case + 一个 Python script + 真打 LLM。

跑出来:5/5。

如果按 "eval pass = 产品 OK" 的逻辑,工程已经做完。但我盯着那个 5/5 看了一会:**我没有任何办法判断,这 5/5 是因为我的 prompt 强,还是因为我的 scorer 太松**。

`expected_keywords` 设的是 input 里出现过的词。LLM 输出包含这些词的概率,可能比 "产品质量" 高得多。

这是第一个停顿点 ——

**上一层没法看清楚的问题**:5/5 这个数字本身没有 discrimination power。我没法判断它是 measurement 失真,还是 prompt 真的好。

---

## case 不是 example,是 pre-registered 假设

我把 5 个 case 全部重写。每个 case 加两个字段:`capability`(显式声明这个 case 在测 prompt 的哪个具体能力)+ `capability_assertions`(跑前 pre-register 的 pass/fail 操作定义)。

```yaml
- case_id: T7-edit-done-followup-required
  capability: T7
  shape: adversarial
  messages:
    - role: user
      content: [...]   # 用户 Edit 文件后说 "done"
  capability_assertions:
    next_step_must_contain_any_of: [test, 测试, run, verify]
    next_step_must_NOT_contain: [wait for, 等用户, new task]
```

这看上去只是结构改动。但概念上是一个 reframe:**case 从"例子"变成"falsifiable 假设"** —— 跑前我已经显式 declare 了什么 output 算 pass、什么算 fail。

为什么这一步必须做?因为只有 case 变成假设之后,**数据才能 falsify 你的设计**。上一版的 5/5 是 confirmation;现在如果 T7 score 0,我可以指着 rubric 说 "next_step 没出现 verify 类词,prompt 在 '用户说 done 时主动想 follow-up' 这个 capability 维度结构性缺失"。

跑出来:8 个 capability 里 3 个 fail。其中 T5 / T6 触发了一个我没料到的失败模式。

T5 的 `goal_must_NOT_contain: ['_parse_focus_state_response']` 想抓的是 "LLM 把 symbol 当 task 本身" 的 anti-pattern。但实际跑出来:

- deepseek 写:`"fix the failing test by repairing the markdown logic in _parse_focus_state_response"` —— goal **是对的**,只是顺便提了相关函数名。substring 给 0。
- qwen 写:`"identify the implementation of _parse_focus_state_response to debug the failing test"` —— goal **是错的**。substring 也给 0。

两个 goal 都 fail 同一个 substring 规则,但语义上一个是 false-positive,一个是真 fail。

第二个停顿点 ——

**上一层没法看清楚的问题**:substring 不能区分 "symbol as task subject" 和 "symbol as contextual reference"。这是 substring 作为 scorer 形式的语义上限。它已经不是 "我的规则没写好",是规则**本身没法表达**这个判断。

---

## 加 LLM-judge,但只在 substring 失败的维度上挂

LLM-judge 是显然的解法 —— 用 LLM 判 LLM 的 semantic 维度。但我不想全 8 个 capability 都上 LLM-judge。原因:

- T1 / T2 / T3 / T8 上 substring 已经 100% 准,加 judge 是 over-engineering
- LLM-judge 不 free —— 每个 case 多一次 LLM call,有 cost
- LLM-judge stochastic —— 不是 deterministic 测量,得引入 reproducibility 麻烦
- LLM-judge 自带 5 类 bias(verbosity、position、self-preference、format、calibration drift),要在 rubric 里 hedge

所以我做的是 **selective LLM-judge**:只在 T4 / T5 / T6 / T7 四个 substring 已证明 brittle 的 capability 上挂 rubric。其他 4 个继续走 substring。

每个 rubric 设计遵循 5 条原则:binary(1/0 不五分制)、chain-of-thought(reason 字段先于 score)、PASS / FAIL example 各两个跨语言、prompt < 500 字、强制结构化 JSON 输出。

跑出来,disagreement 信号开始 surface:

```
qwen run, T6:
  goal: "read the content of EXTRACTION_SYSTEM_PROMPT in services/extract.py"
  substring score:  0  (FAIL goal_must_NOT_contain: found 'read ')
  LLM-judge score:  1  (reason: "read is used as English verb describing
                        the user's intent to view the content, not as a
                        reference to the Read tool")

⭐ SUBSTRING BRITTLENESS EXPOSED
```

LLM-judge 接管的正是 substring 失效的语义维度,**disagreement 本身是 calibration 信号** —— substring 0 + judge 1 不再是 noise,是一个明确的信号说 "substring 在这里有上限,judge 是 ground truth"。

第三个停顿点 ——

**上一层没法看清楚的问题**:LLM-judge 是 stochastic 的。同一个 input 跑两次,score 可能不一样。这跟程序化 scorer 不一样了 —— 我开始需要 "reproducibility" 的概念。

---

## Reproducibility 不是一个 claim,是三个

我开始做版本管理时,第一版是只 stamp hash:`prompt_sha256`, `rubric_sha256s`, `dataset_sha256`。

直到一个朋友看了 schema 反问:**"6 个月后我读这个 hash,怎么知道 prompt 长啥样?"**

我答不上来。hash 是 identity claim —— "这是同一个 X"。但**它不是 content claim** —— 你没法从 hash 反推 X 的内容。如果半年后 git 已经 rewrite,prompt 已经被 v3 覆盖,那个 sha256 就是悬空指针。

加 200 字 `prompt_excerpt`?够 eyeball,不够 reproduce。

OK 那加全文 `prompt_text` + `rubric_texts`。

还差一件:**state claim**。哪个 commit 跑的?working tree 有没有未提交改动?这是 dataset.yaml 跟 `focus_state.py` 不会告诉你的 —— scorer 实现细节、settings 默认值、SDK 版本,都得靠 git commit 锚定。

最终落地是 8 axes,3 个 claim 同时在场:

```
identity claim:   prompt_sha256, rubric_sha256s, dataset_sha256
                  ↓ 比对身份,quick diff between runs
content claim:    prompt_text, rubric_texts (全文)
                  ↓ 6 个月后还能读出来,不依赖 git archaeology
state claim:      git_commit, git_dirty
                  ↓ working tree 干净时 → 完全 reproducible from git checkout
                    dirty 时 → 必须以 content claim 字段为 authoritative
```

只有 identity → 半年后悬空指针。只有 content → 没法验证身份。只有 state → 不知道当时是不是脏 working tree。

3 个 claim 同时在场,**reproducibility 承诺才完整**。

第四个停顿点 ——

**上一层没法看清楚的问题**:metadata 足够 reconstruct 跑的是什么,但每次 LLM 调用的响应没有 freeze。我没法让别人 "免费 + deterministic" 复跑同一组数据。

---

## Cassette + results JSONL —— iteration 的两条 ledger

最后一层抽象是把每次跑的 LLM 响应录下来(cassette),每次跑的 score + 8 axes metadata 落盘(results JSONL)。

Cassette 让 replay 0 cost + deterministic。改 prompt 一次,可以 `--mode record` 录一组新 baseline,然后 `--mode replay` infinite 比对。LLM 不再被调一次,replay 从文件读上次录的响应。

Results JSONL 让每次 run 留 trace。半年后想知道 "T7 capability score 从 0.5 到 0.2 是什么时候发生的",可以 grep `results/` 目录,按 timestamp 排,找出哪次 commit 改 prompt 引入了 regression。

```bash
# 典型 iteration 工作流
vim src/openharness/services/focus_state.py   # 改 prompt
oh eval focus_state --mode record             # 录新 baseline
oh eval focus_state --mode replay             # 0 cost 反复 verify
```

这一层不是 "加更多 feature",是把前 5 层产生的所有数据**变成可 trace 的 ledger**。

到这里 stack 终于 stable。8 个 capability,4 个 scorer dim,3 mode cassette,8 axes version stamping,1 个 `oh eval` CLI 子命令。

---

## 不是 designed,是 forced

回头看这条路径产生的 stack,跟我一开始列的那份 production-grade 清单**几乎一模一样**。但拼装顺序不是设计出来的,是被实验**逼**出来的:

```
Stage 0 (5 case spike)        → blind: 5/5 没 discrimination power
Stage 0c (capability anchor)  → blind: substring 有语义上限
Stage 3 (LLM-judge)           → blind: judge stochastic,怎么 reproduce?
Stage 4 (cassette)            → blind: hash 是 identity 不是 content
Stage 5 (results + git stamp) → blind: 6 个月后这条 record 谁能信?
```

每一层都是上一层的盲区。如果我一开始 top-down 设计,可能会把 cassette 和 LLM-judge 当成 "独立的 feature",而不是看到它们其实在解决一组层层递进的具体问题 —— cassette 不是 "省钱的优化",是 LLM-judge 引入 stochasticity 之后的必然回应;version stamping 不是 "工程礼仪",是 cassette + judge 让 score 来源变复杂之后的诚实需求。

我的看法是:**上一层没法看见的东西,正好是下一层应该测量的东西**。这是 measurement engineering 的递归原则,也是为什么我从最简单的开始 —— 你没法 design 一个抽象去解决你还没遇见的问题。

最简单的版本不是 "过渡阶段",是让你看见自己 measurement 盲区的工具。如果你跳过它直接到终态,你会有一套完整的 stack —— 但你不会真的理解,每层抽象为什么是它现在这个样子。

---

substrate code 在 [GitHub repo](https://github.com/maisieyang/build-my-own-harness) 的 `src/openharness/eval/`;每层抽象的设计决策记录在 `decisions/31-34-eval-*.md`,30+ ratified design decisions。
