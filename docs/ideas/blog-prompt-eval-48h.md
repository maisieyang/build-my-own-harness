# 48 小时构建一套 production-grade prompt eval 的 6 个 craft 真相

我花了 48 小时,从 0 给 OpenHarness 一个内部 service(`services/focus_state.py`,负责从最近对话推断 `{goal, next_step}`)写一套 production-grade prompt eval。技术栈是 substrate Protocol + 4 个 scorer(含 LLM-judge)+ cassette replay + 8 axes version stamping + `oh eval` CLI 子命令。但回头看,真正改变我对 eval 认知的不是这套 stack,是中间走过的 6 个 stage —— 每一个都让我意识到 eval 在很多人脑子里被 underrated 了一个量级。

我目前的判断是:**eval 不是测代码,是测你自己对产品的判断能力**。这件事如果一开始没想清楚,后面所有工程都是错位的。

---

## 第一个 5/5 是骗子

第一次跑 spike,5/5。

我手写 5 个 case,2 个 scorer:`parse_ok`(JSON 解析成功)+ `goal_keyword_match`(expected keywords substring match)。真打 qwen-plus。8 个 capability 全 pass。

如果按"eval pass = 产品 OK"的逻辑,这里就能 ship 了。

但这套 5/5 是骗子。问题在 scorer:`expected_goal_keywords=['import', 'test_foo']` + substring match。LLM 输出包含这两个词的概率超过 95% —— 因为 input 里就有这两个词。一个不太烂的 prompt 几乎一定通过。所以 5/5 在告诉我的不是 "prompt 强",是 **"我的测量精度太粗,根本无法区分好坏 prompt"**。

**好的 dataset 是 designed to fail 的**。pass rate 100% 应该让你警觉,不是让你放心 —— 它意味着 dataset 或 scorer 已经不再 discriminate。frontier-LM team 的 internal prompt eval 通常每 prompt 50-300 case,pass rate 在 70-90% 之间最有信息量;接近 100% 或接近 0% 都是 "测量仪器该升级了" 的信号。

我跑过的所有 5/5 都不可信。这是第一条 craft lesson,也是大多数人没走到的第一个跳跃。

---

## Substring brittleness 是测量基础设施缺陷,不是 prompt 弱

把 5 个 case 重写成 capability-anchored —— 每个 case 显式 declare 它测 prompt 的哪个具体 capability,跑前 pre-register pass/fail 操作定义,然后再加 LLM-judge,跨 model 跑 N=3。这时数据真信号才出来。

T5 测的是 "prompt 能不能从多步 tool 调用里抓住 user 的高层任务",failure mode 是 LLM 被最后一步 grep 干扰,goal 写成 "找 `_parse_focus_state_response` 的实现"。我的 substring assertion 是 `goal_must_NOT_contain: ['_parse_focus_state_response']`。

跑出来:

- deepseek 一次写:`"fix the failing test by repairing the markdown logic in _parse_focus_state_response"`。goal **是对的**,只是顺便提了相关函数名。substring 给 0;LLM-judge 给 1 + reason: *"treating symbol discovery as a means to fix the test, not the end itself"*。
- qwen 另一次写:`"identify the implementation of _parse_focus_state_response to debug the failing test"`。goal **是错的**,focus 真的在 discovery 上。substring 给 0;LLM-judge 也给 0。

两个 goal 都触发同一个 substring fail,但语义上一个是 false-positive,一个是真 fail。**substring 不能区分 "symbol as task subject" 和 "symbol as contextual reference"** —— 这是程序化 scorer 的语义上限。

这件事真正让我警觉的不是 substring 弱,是 **assertion 设计本身是一门 craft,而大多数人(包括之前的我)把它当成"按几个关键词检查就行"的小事**。

production-grade scorer 要么:

```
程序化 scorer  ─┬──→ structural checks (parse / schema / regex 边界)
               │     这层 substring brittleness 低,deterministic,cheap
               │
LLM-judge ─────┴──→ semantic checks (intent / actionable / fabrication)
                    这层 substring 抓不准,必须 LLM-judge 接管
                    且需要显式 hedge 5 类 bias:
                      verbosity / position / self-preference / format / drift
```

我们最终 ship 的设计是 **selective LLM-judge**:8 个 capability 里只在 4 个 substring 已证明 brittle 的(T4/T5/T6/T7)上挂 rubric;其他 4 个继续用 substring。这不是技术取巧,是 craft —— 知道哪里 measurement infra 该投资,哪里不该。

测量不准的时候,product metric 会被系统性地拖偏。如果 Stage 2 阶段我看到 T5 的 substring score 是 25% 就慌着改 prompt,会引入完全没必要的 prompt 复杂度去 "修" 一个其实不存在的问题 —— **被自己的测量仪器误导**,是 production engineering 最隐蔽的失败模式。

---

## Prompt 的累积成本 —— 一段 instruction 不只占字符,还占 budget

eval 跑通之后,自然要修 prompt。

T7 是一个 5 runs 跨 model 全 fail 的 locked failure:user 说 "done" 后,LLM 永远忠实地把 next_step 写成 "wait for user new task" —— 信了字面 done,不主动想 verification。我在 FOCUS_STATE_SYSTEM_PROMPT 里加了 5 行:

```
For "next_step": if the user signals completion ("done", "ok",
"完成"), do NOT just wait — propose the most important verification
or follow-up action (run tests, check edge cases, verify the
change works end-to-end) that may still be needed before the task
is truly closed.
```

跑 eval。T7 capability 从 0 → 1 ✓。同时 parse_quality 从 5/5 → 4/5。一个 case 直接 empty response。

第一反应是 "deepseek 抽风"。我编了一个 mentor-style 假说:"deepseek 在 tool-only 短上下文上有 quirk"。

跑第二次,4/5 又恢复 5/5。但 T1 的 parse fail 连续两次出现 —— 这次是 col 10 截断(unterminated string),不是 empty。

我才停下来想:**这两个失败模式不同(empty vs unterminated)。会不会其实是同一个根因?**

是。`max_tokens=256`。

prompt 加了 5 行新指令 → LLM "想得" 更多 → reasoning depth 增加 → 256 tokens 不够 → JSON 在 col 10 被截断,或者根本没生成出第一个字符。**我以为只加了 5 行,实际是给 LLM 多塞了一份 "verification 要怎么想" 的认知负载,而这份负载从 output budget 里扣。**

bump `max_tokens` 256 → 512,跑一次,T1/T5/T7 三个 parse fail 同时修复。

这一次 hypothesis test 让我学到的不是 max_tokens 数字,是:

> **prompt 的每段 instruction 都有累积成本,跟数据库 index 同构 —— 单独看每段都"值得加",累积起来挤兑其他维度的资源。**

更值钱的是发现:我之前的 mentor prior 是错的。**"deepseek tool-only quirk" 和 "max_tokens budget" 是 2 个 hypothesis 解释 3 个症状;实际 1 个 hypothesis 就够。Occam's razor + anti-premature-attribution 在 debug 里反复救场 —— 任何想给每个症状单独编 hypothesis 的冲动都要被数据修正。**

production engineering 最容易踩的坑不是 "我不知道",是 "我以为我知道"。

---

## Reproducibility 不是一个 claim,是三个

最后一个 stage 是 result 落盘。每次 run 写一个 JSONL,header 含 hash:`dataset_sha256`, `prompt_sha256`, `rubric_sha256s`。

我当时以为这就够了。直到一个朋友看了 schema 反问:**"6 个月后我读这个 hash,怎么知道 prompt 长啥样?"**

我答不上来。hash 是 identity claim —— 它说 "这是同一个 X"。但**它不是 content claim** —— 你没法从 hash 反推回去读 X 的内容。如果半年后 git 已经 rewrite,prompt 已经被 v3 覆盖,那个 sha256 就是悬空指针。

加一个 200 字 `prompt_excerpt`?半个 claim,够 eyeball,不够 reproduce。

OK 那加全文。`prompt_text` 字段。`rubric_texts` 字段。

还差一件:**state claim**。哪个 commit 跑的?working tree 有没有未提交改动?这是 dataset.yaml 和 `services/focus_state.py` 不会告诉你的 —— scorer 的实现细节、settings 默认值、SDK 版本,都靠 git commit 锚定。

最终落地是 8 axes,3 个 claim 同时在场:

```
identity claim:   prompt_sha256, rubric_sha256s, dataset_sha256
                  ↓ 比对身份(quick diff between runs)
content claim:    prompt_text, rubric_texts
                  ↓ 6 个月后还能读出来,不依赖 git archaeology
state claim:      git_commit, git_dirty
                  ↓ working tree 干净时 → "完全 reproducible from
                    git checkout alone";dirty 时 → 必须以 content
                    claim 字段为 authoritative
```

只有 identity → 半年后是悬空指针。只有 content → 没法验证身份没漂。只有 state → 不知道当时 working tree 干不干净。

production engineering 真正的 reproducibility 承诺,要兑现到这个程度:**任何人 6 个月后读这条 JSONL,不依赖 git archaeology,能完整知道当时跑的是什么 prompt、什么 rubric、什么 commit 的代码,以及那个 commit 是不是当时所有改动**。

我们 ship 的最后一版 `RunMetadata` 包含 8 axes 全 stamp。每个 axis 都对应 "测量当时" 这件事的一个具体维度;少一个,reproducibility 承诺就 half-broken。

---

## 最后

48 小时,从 5/5 syndrome 到 production-grade 6 性质完整 internalize。技术 stack 在 [GitHub repo](https://github.com/maisieyang/build-my-own-harness) 上(commits `766d1ae` + `e87be81`)。

但回头看,真正改变我对 eval 认知的不是 LLM-judge 有多准、cassette mode 有多优雅、CLI 有多 first-class,是这件事:

**measurement 不准时,product metric 会被系统性地拖偏 —— 决策被错误数据牵引,而你自己不知道。eval 真正的价值不是出分,是揭示自己 assertion 设计的盲区。**

我跑过的所有 5/5 都不可信。我跑过的所有 0/8 也不可信。真正可信的是:dataset 跨 model N=3 + capability-anchored + LLM-judge 在 substring 失效维度上接管 + 8 axes 落盘 + cassette 锁住每一次 LLM 响应。然后当我说 "prompt v2 比 v1 强" 时,我有一份 ledger 可以指。

大多数人讨论 LLM eval 时,他们的真实经验是 "我装了 DeepEval 跑了一次"。这个跟 "production-grade eval" 之间的差距,比 "装框架" 和 "写框架" 之间还大。
