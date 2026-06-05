# Memory 系统设计：一份 first-principles 推导

> 写于 2026-06-05 · 中文版
>
> 配套读物：
> - 项目方法论：[CLAUDE.md](../../CLAUDE.md)
> - Phase 16 boundary doc：[`decisions/36-phase-16-memory-pivot-boundary.md`](../../decisions/36-phase-16-memory-pivot-boundary.md)
> - 同 family 的方向调研：[`docs/ideas/eval-first-principles.md`](./eval-first-principles.md)
> - 既有 memory 实现：[`src/openharness/memory/`](../../src/openharness/memory/) (Phase 10) + [`src/openharness/services/extract.py`](../../src/openharness/services/extract.py) (Phase 11 extraction)
>
> 这篇不是 boundary doc 也不是 plan —— 是动手开 Phase 16 boundary 之前
> 跟自己对齐的**方向推导**。把 Claude Code memory 系统从第一性原理推
> 一遍，**不预设 CC 设计是对的**，只把每个 fork point 和它的 trade-off
> 摆出来。最后一节记录是怎么从"我现在设计不合理"走到"1-6 都完全 follow
> cc"的判断过程。

---

## 〇、为什么这件事现在需要想清楚

Phase 16 准备阶段，用户对 memory 设计经历了一次完整的判断翻转：

1. **起点**：用户认为"`oh` 现有 memory 设计不合理，CC 是合理的，我很认同 CC"
2. **中间**：assistant（这个 Claude Code session）跑了两次 spike——cold-start 看起来 qwen3.7-max 能 hold 住，warm-start 直接暴露 3/5 FAIL，包括一次 MEMORY.md 灾难性 overwrite
3. **冲突**：assistant 基于 spike 数据推"synthesis 选项 = 砍掉 MEMORY.md 用 glob discovery 替代"，理由是数据看起来 contract 太脆弱
4. **校准**：用户摆出底层哲学："模型会越来越强，harness 是底层，模型不行就换更强的模型，**前提必须在**"
5. **重新看**：用户在前提下要求 first-principles 解释 CC 设计，理解每个 fork point 后说"1-6 都完全 follow cc"

**这篇文档的存在意义** = 把第 5 步的推理过程固化下来，让以后看的人（包括未来的我自己）知道每个 fork point 为什么这么选，而不是 "因为 CC 这么做了"。**理解 contract 比 import contract 更重要**。

---

## 一、根本问题 + 根本约束

### 1.1 根本问题：LLM 无状态

每个 LLM session 从零开始，**零记忆**。你今天教过的偏好、纠正过的错误、解释过的项目背景，明天一个新 session 完全不知道。

这不是产品 bug，是 LLM 的物理性质——transformer 不持久化任何东西。

### 1.2 根本约束：context window 有限且昂贵

LLM 的 context window 是有限的（典型 200K token，frontier 1M），并且**每 token 都按账算钱**。不能把"用户所有历史"全注入每次开 session 的 prompt 里——会爆 context、burn token、让模型在大量无关信息里挑细节。

### 1.3 根本张力

**累积的知识（想保留全部）vs 每次能注入的知识（只能少量）**。

这一条张力是 memory 系统**所有**设计选择的源头。

---

## 二、张力的解 = 存储 ≠ 加载

把"存了什么"和"这次会读什么"**分开**。

- **存储层**：累积无界，写入慢、读取多，每条 memory 独立持久化
- **加载层**：单次注入受 context window 约束，要选少而精

这两层有**不同的优化目标**：存储优化"可累积 + 可独立更新"，加载优化"上下文相关 + token 节俭"。

这个分离一旦做出，立刻开了至少 **6 个 fork point**，每个都要单独决定。

---

## 三、Fork point 1：存储形态 — 单文件 vs 多文件

| 选项 | 优点 | 缺点 |
|------|------|------|
| (A) 单大文件（一个 JSONL / 一个大 markdown 塞全部 body） | 简单，一次 Read 拿到全部 | 任何一条更新都要重写全文；并发改容易冲突；无法独立 link 单条 |
| (B) 多小文件 + 一个索引 | 每条 memory 是 atomic 单元；改一条不影响别的；可以 link 互相 | 实现复杂；需要索引机制 |

**CC 选 B**：`<memory_dir>/*.md` 是 body，`MEMORY.md` 是索引。

**第一性 reasoning**：memory 是"写入慢、读取多、单条 ROI 高、生命周期独立"的数据。多文件让"一条 memory = 一个 atomic 实体"这个语义成立——可以被独立写、独立 link、独立 deprecate。同时也让"frontmatter + body" 的结构化数据自然栖息（每条 memory 自描述）。

---

## 四、Fork point 2：索引是数据库 vs 上下文注入点

这是 CC 设计**最不直觉**的选择，也是理解 CC memory 的钥匙。

`MEMORY.md` **不是数据库索引**，是**上下文注入点**。

- 它被设计成"小到能在每个 session 启动时被注入 system prompt 里"
- 让 LLM **知道自己有什么 memory**（标题 + 一句话 hook）
- 具体内容 LLM 按需 Read

CC 系统 prompt 里的原文：

> *"MEMORY.md is always loaded into your conversation context — lines after 200 will be truncated, so keep the index concise"*

也就是说：
- **每 session 注入** MEMORY.md 全文
- **only first-200-line truncation** 作为唯一的"ranking"——按物理顺序，不按相关性
- LLM 扫完索引，自己决定 Read 哪个 body
- **harness 侧零 ranking 介入**

为什么 MEMORY.md 每行 ≤ 150 字符的纪律？**因为它是 token budget，不是 readability 偏好**。如果 MEMORY.md 长成 10KB，就不能在每个 session 启动注入了。

**第一性 reasoning**：LLM 不会主动"查询数据库"。它只能基于上下文里看到的东西决定要不要进一步 Read。所以"它知道什么 memory 存在"必须在它的视野里。**索引 = LLM 视野**。

### 4.1 这一条直接回答"为什么不用 glob discovery 替代"

`glob("*.md")` 是 harness 视角能拿到所有 memory；但**LLM 视角拿不到 glob 结果**——除非 harness 把 glob 结果塞进 system prompt（那就是 MEMORY.md 的功能）。

要让 LLM 自决何时 Read memory，**必须有 LLM-visible 的索引层**。

`oh` 的 [`FilesystemMemoryStore.discover()`](../../src/openharness/memory/store.py) 是 harness 内部 API——它对 LLM 不可见。Phase 16 要做的不是替换它，是**在它之上加一个 LLM-visible 索引层**。

---

## 五、Fork point 3：写入触发 — 何时决定写 memory

| 选项 | 谁决定 | 触发频率 | 失败模式 |
|------|--------|----------|---------|
| (A) Rule-based | harness | 每 N turn / 每次结束 | 错过细微信号；写太多噪声 |
| (B) Secondary LLM pass | 独立 LLM 用专门 prompt | 机械每 turn | 副 LLM 判断可能跟主 LLM 不一致；产生固定 per-turn 成本 |
| (C) Inline 主 LLM 自决 | 主 LLM | 当它觉得该写 | 高度依赖主 LLM 判断质量 |

`oh` Phase 11 extraction 选了 **B**。CC 选了 **C**。

**第一性 reasoning（CC 选 C 的原因）**：判断"什么值得记"是**高度上下文敏感**的——同一句话在不同 conversation 中 memorable 程度不一样。只有**主对话 LLM 当下**有完整上下文。把判断交给 rule 或副 LLM = 信息折损。

代价：**完全押注主 LLM 的判断质量**。

### 5.1 这一条的押注属性

当主 LLM 弱（qwen3.7-max on warm-start spike）→ 漂。当主 LLM 强（Claude Opus / Sonnet）→ 几乎不漂。

按用户的底层哲学（["design-for-strong-model"](#)），这个押注是**有意接受的**——harness 设计的能力上限跟随主 LLM 上限。

`oh` Phase 11 extraction 是 B 的实例，是当时对 qwen-plus 弱 main LLM 的工作 around。Phase 16 deprecate 它不是因为它错，是因为前提变了（哲学声明 + 模型升级）。

---

## 六、Fork point 4：读取触发 — 何时载入 memory

CC 双层结构：

- **索引（MEMORY.md）**：每个 session 启动**总是注入**到 system prompt
- **body**：LLM **按需** Read（看到索引里某条相关，就 Read 那个文件）

**第一性 reasoning**：索引的 token 成本是**固定且小**的（~几百 token）；body 的 token 成本是**变量且按需**。这样**冷成本恒定 + 热成本按相关性付**。

跟"全部预加载"对比（A 选项）：那种方式 token 成本随 memory 增长**线性增加**，不可持续。

### 6.1 这一条解释了 relevance.py 在 CC 模型下为什么不存在

`oh` [`memory/relevance.py`](../../src/openharness/memory/relevance.py) 的 `select_relevant_memories` 是 **harness 侧做相关性 ranking**，挑 top-K 注入。

CC 模型下：**LLM 自己看索引，自己决定 Read 哪个**。harness 不参与 ranking。**relevance ≡ LLM 的判断**。

所以 Phase 16 deprecate relevance.py 不是放弃相关性选择，是**把相关性判断权从 harness 移给 LLM**。

---

## 七、Fork point 5：Linking 形态

CC 用 **`[[slug]]`** 引用——一个 memory 可以指向另一个 memory，slug 解析回 `<slug>.md` 文件名。

**第一性 reasoning**：memory 之间有依赖关系（这条 feedback 是基于那条 project context 的）。但**显式数据库式查询**（"WHERE related_to = X"）对 LLM 不友好。

`[[name]]` 是 LLM 友好的——它就是在 markdown 里写一个 link，LLM 看到就可以决定要不要 Read。**人类 markdown 习惯 = LLM 原生友好的语法**。

`[[name]]` 即使指向尚未存在的 memory 也无害——只是个"未来要写"的标记，不是错误。这个特性让 memory 网络可以**渐进生长**而不是一次性架构。

---

## 八、Fork point 6：Scope（per-project vs user-global）

CC 选了 **per-project**：`~/.claude/projects/<sha1-of-project-dir>/memory/`

**第一性 reasoning**：用户在不同项目里有不同偏好、不同上下文。把全部 memory 塞一个全局池，**每个项目都会被无关 memory 污染**。Per-project 让每个项目的 memory 池保持高相关性。

代价：跨项目共享的偏好（"我喜欢简洁回复"）在每个项目都得重写一遍。CC 接受这个代价。

`oh` Phase 10 已经选了 per-project（同 sha1 hash 方案），这一条**已经一致**。

---

## 九、跨模型的存储/使用不对称

CC 设计有一个深刻的不对称特性：

| 层 | 设计目标 | 表现 |
|----|----------|------|
| **存储层** | **Model-agnostic** | markdown + frontmatter，任何 LLM 都能 read/write |
| **使用层** | **押注强 LLM** | 写入判断 / 读取相关性 / link 解析全靠主 LLM 强 instruction-following |

这是个**有意的**不对称：
- **存储**面向未来兼容性（任何模型都能读历史 memory）
- **使用**面向当下能力上限（用今天最强模型）

含义：**模型升级 → memory 直接复用，不需要数据迁移**。

这一条本身就是"模型会越来越强 → 不要让数据被模型 lock-in"的体现。

---

## 十、对照 `oh` 现状 + 决策记录

按 6 个 fork point 对照 `oh` 现状：

| Fork | CC | `oh` Phase 11 现状 | Phase 16 目标 |
|------|-----|-------------------|--------------|
| 1 存储形态 | 多文件 + 索引 | 多文件**无索引** | 加 MEMORY.md 索引 |
| 2 索引性质 | context 注入点 | 没有 | 加 session-start 注入 |
| 3 写入触发 | inline 主 LLM | secondary LLM pass | inline 主 LLM（extraction deprecated） |
| 4 读取触发 | 索引启动注入 + 按需 Read body | glob discover + harness ranking | 同 CC（relevance.py deprecated） |
| 5 Linking | `[[slug]]` | 暂无 | 加 `[[slug]]` 解析 |
| 6 Scope | per-project sha1 | per-project sha1 | 已一致 |

### 10.1 这次推导带出的方法论 lesson

讨论过程中 assistant 推了两次"为了贴合当前 qwen3.7-max 能力而砍 contract" 的方案：

1. "Synthesis option" — drop MEMORY.md，用 glob 替代（warm-start FAIL 后）
2. "Option C" — qwen-plus + isolated memory pass（早期 warm-start 之前）

两次都被用户哲学声明 push back：**"模型会越来越强，harness 是底层。模型不行就换更好的，不要降 contract."**

记录到 [[feedback-design-for-strong-model]] 作为持久 methodology discipline——以后再讨论 harness 架构变更时，默认"为强模型设计 contract，不为当前弱模型留 fallback"。

### 10.2 押注的检验路径

如果"qwen3.7-max 哪一天能 hold 住 CC contract"是真问题，验证路径是：

1. **Phase 16 boundary doc 的 gating eval**（[`decisions/35-eval-coverage-map.md`](../../decisions/35-eval-coverage-map.md) D35.5 P0）：把 spike fidelity 修复（注入 MEMORY.md 内容到 system prompt 模拟 CC 真实运行形态）后再跑 qwen3.7-max
2. **Pass bar = warm-start ≥ 4/5**（参考 CC 在 Opus 上估 4-5/5）
3. **不过的 fallback** = 用 Claude Sonnet/Opus 跑同一个 eval，验证 contract 在强模型上 hold——证明是 model gap 不是 contract 错
4. 都不过 → contract 真有问题，回到这份 first-principles 重新推

---

## 十一、这份文档不回答的问题

显式标出来给后续 phase / 后续讨论用：

1. **Memory 之间冲突如何解决？** 两条 feedback memory 互相矛盾（用户先说 "X 应该 always"，后说"X 不应该 always"）—— CC 现在靠 LLM 在 Read 时取后写的；没有显式冲突解决机制。Phase 16 不引入，等真正冲突出现再说。
2. **Memory GC / 老化？** CC 不做。`oh` Phase 11 boundary doc 也明确"待 Phase 12+"。Phase 16 同样不引入。
3. **Team-scope memory（多个开发者共享）？** `oh` Phase 11 D29.10 已经引入 `team/` 子目录的概念。CC 没有这个概念（CC 是单用户工具）。Phase 16 是否**保留**这个分歧是开放的——boundary doc 里要 explicit ratify。
4. **多 session 并发写**：如果用户在两个 terminal 同时跑 `oh chat`，两个 session 都写 memory 会不会冲突？Phase 16 假设单 session（CC 也假设单 session），并发延后。

这些都不影响 Phase 16 的 6 个 fork point 选择，但**未来重新评估时要记得它们是 deferred 而非 resolved**。

---

## 引用

- [CLAUDE.md](../../CLAUDE.md) - 项目方法论（"框架者高度 / 不变量 / trade-off"）
- [`decisions/35-eval-coverage-map.md`](../../decisions/35-eval-coverage-map.md) - eval 决策面 map（memory pivot 属 P0 #4 inline 决策）
- [`decisions/36-phase-16-memory-pivot-boundary.md`](../../decisions/36-phase-16-memory-pivot-boundary.md) - 本文档对应的 Phase 16 boundary doc（落实这里的推导为不变量）
- [`scripts/spike_memory_capability.py`](../../scripts/spike_memory_capability.py) - cold + warm spike 实现
- [`src/openharness/memory/`](../../src/openharness/memory/) - Phase 10 既有 memory 实现
- [`src/openharness/services/extract.py`](../../src/openharness/services/extract.py) - Phase 11 extraction（Phase 16 deprecate 目标）
