# memory_read Eval Dataset Card

> C4 / 决策面 #4(inline 决策)另一半 · 2026-07-23 · four-declaration header

## Declarations(四声明头)

**1. Capability claim**:测**生产** prompt 组装(`build_system_prompt` 的
`## Memory` = 写规则 [prompts/memory.py] + MEMORY.md 索引 [memory_inject.py])
加默认 `Read` 工具,在决策面 **#4 / C4**(memory READ 自选)上的行为:模型扫
索引后,该 recall 时读对的 memory、该克制时不读。Phase 16(D36.7)后 harness
不再代排相关性(relevance.py 退役),读的决策整个交给模型——本 eval 就是量它。

**镜像 skill_trigger(C1)**:同是"看目录/索引 → 自决调不调工具、调哪个",
单步、tool-call 存在性硬 oracle。

**不为之设计**:
- **模糊相关性**判断("seems relevant" 的软区)——只有 MR6 一个探针,见留白
- memory **写**(C3,已 evals/memory_decision 覆盖)
- Read 之后对 body 的**消费**(多轮 L2 轨迹,非单步)
- relevance.py 的退役算法(已有 tests/memory/test_relevance.py,且已 deprecated)
- 跨 model 强弱比较(D35.8 前置未满足)

**2. Input spec**:共享 fixture = 合成 MEMORY.md 索引(4 条 memory:deploy
prefs / Aurora status / staging DB / review tone)+ slug 清单(供 scorer 区分
memory 读 vs 任务读)。N=6,镜像 skill_trigger 的 trigger/restraint/selection
平衡:3 must-read(recall/check/remember 明确触发词,含 4 选 1 选择正确性)+
3 restraint(ignore 指令 / 无匹配 / near-miss 邻近但非 recall)。扩量走 D41.6。

**3. Judgment spec**:两维,never collapsed,均 tool-call 存在性硬 oracle:
- `read_decision` — must-read → 必读到某 memory(路径命中 slug);restraint →
  必**不**读任何 memory(读任务文件等非 memory 路径**允许**,只 memory 读受测)。
- `memory_selection` — must-read → 读的必须是**对**的那条(点名读错哪个);
  restraint → 无可选,vacuous pass。

**4. Reference policy**:参照模型 **qwen-max**(跨 eval 可比)。参照 ≠ 生产/
benchmark 的 qwen3.7-max——测的是"参照系上 prompt/契约没坏",非"今日部署模型
好坏"(D41.5)。他模型 run 是 information 非 gate(spike 打印提示)。

## Pass bar(ratify 2026-07-23)

- **Gate:qwen-max 上 `cases all-dims-pass = 6/6`**(全稳定绿)。
- 依据(N=4 画像):6,6,6,6 /6——四轮零方差全绿。must-read 3 条契约触发词全
  遵守且选对,restraint 3 条全克制。含预判可能 flaky 的 near-miss(MR6),
  qwen-max 4/4 稳定克制。bar 满格有画像支撑。

## 已知留白(6/6 满分意味着什么)

**6/6 读作"契约清晰场景下读的决策正确",不读作"memory-read 完美"。** must-read
用明确触发词(recall/check/remember)= 契约 MUST,restraint 用清晰信号(显式
ignore / 完全无关)。真正软的是"**seems relevant**"判断区(话题邻近、隐含相关
但用户没明说 recall)——那里"对"本身有争议。**当前只有 MR6 一个 near-miss 探针
进这个区**,它守住了(qwen-max 没被 deploy 话题钓去读旧偏好),但一个探针不是
整个模糊空间。

**MR6 的信息量**:它验证了模型**不 trigger-happy**——话题邻近不等于自动读记忆。
这是 restraint 里最有价值的一条。但更广的模糊相关性(该读却因措辞含蓄没读 =
fail-open 静默漏;不该读却读 = 噪声)留飞轮:待 dogfood 出现真实的读-决策分歧
再扩,不凭空想象软区的失败模式(D41.6)。

## 决策面 #4 收口

C3(memory WRITE,evals/memory_decision)+ C4(memory READ,本 eval)= 面 #4
inline 决策两半齐。

## Cassettes & results

- `cassettes/qwen-max/infer/` — 6 case 回放基线(record 6/6;回放一致已验证)
- `results/qwen-max-run{1..4}.txt` — N=4 画像原始输出
- 复跑:`OPENHARNESS_EVAL_MODE=replay uv run python scripts/spike_memory_read_eval.py`
