# Training Stack Framing — Pretrain / Post-training / Fine-tune / Reasoning 的认知地图

> 写于 2026-05-12。Phase 2 close-out 之后第二份 framing 文档。
>
> 起源：构建 harness（应用栈）的同时识别到 FDE 角色需要补齐另一块版图——
> 模型本身怎么生产出来的。本文档不是要成为训练专家的教材，是 FDE 跟客户
> 对话时需要的「**懂得对话**」深度。
>
> 跟 [`phase-3-framing.md`](./phase-3-framing.md) 是配套：那份覆盖
> LLM 应用栈上半段（harness / 应用工程化），本份覆盖下半段（模型生产）。
> 两份合起来是 framing §8 「跨语言/跨栈/跨技术周期的迁移性资产」在
> 当前技术周期的完整覆盖。

---

## 1. 触发问题 + 战略定位

### 1.1 为什么补这块知识

FDE 跟客户聊时撞到的场景：

| 客户痛点 | 解决方案在哪一层？ |
|---|---|
| "模型输出格式不稳定" | prompt / SFT 二选一 |
| "模型对我们行业术语理解差" | RAG / fine-tune 二选一 |
| "模型推理太慢" | quantization / 小模型 / caching |
| "我们想要 your own ChatGPT 的 brand voice" | RLHF / DPO 必须 |
| "为什么 Claude 比 GPT-4 更 cautious" | post-training 哲学差异 |

→ 没有训练栈认知就只能默认推 "用 prompt 解决"——可能错。**FDE 的核心 leverage 在边界判断**：哪一层解决哪类问题。

### 1.2 T-Shaped 知识结构

```
LLM 应用栈
   ┌─────────────────────────────────────────────┐
   │ 应用层  harness / agent / workflow / RAG    │ ← 你的深度
   │ 接入层  API call / prompt / fine-tune       │ ← 你已熟
   │ 模型层  pretrain / SFT / RLHF / DPO         │ ← 本文档覆盖
   │ 基础设施 GPU / serving / quantization       │ ← 不打算开
   └─────────────────────────────────────────────┘
```

- **竖**（应用层）：FDE 真正的 leverage 在这
- **横**（模型层）：FDE 的"对话面"——撑住客户问什么都能答到位

### 1.3 「懂得对话」的深度门槛

不需要：精通训练算法 / reproduce paper / SOTA 优化
需要：

- 能讲清完整训练链路（pretrain → SFT → RLHF/DPO）
- 知道每段在解什么痛点
- 客户问"该用哪一层解"时 5 秒能给出推荐
- 客户的训练问题超出你深度时，能精准识别"这要找 ML 专家"

→ **Stop at level "能跟 ML 团队对话"，不追 level "自己实现"**。

### 1.4 节奏建议

跟 harness 主战场 parallel 进行：

| Phase | 资源 | 时长 | 产出 |
|---|---|---|---|
| **A. 起手** | Karpathy nanoGPT | 3-5 天 | Transformer 训练循环体感 |
| **B. 系统化** | Sebastian Raschka《Build a Large Language Model From Scratch》 | 2-3 周 | 完整训练栈 explicit framework |
| **C. Post-training** | HuggingFace TRL 库 + 小 SFT/DPO 实验 | 1-2 周 | "SFT/DPO/RLHF 区别和取舍"体感 |

总投入 ~6-8 周，每天 1-2h。不影响 harness 主战场（Phase 3-7 估 10-12 周）。

---

## 2. LLM 应用栈分层 — 4 层结构

```
应用层 (Application)
├── Harness / Agent / Workflow / RAG
├── 你的核心 leverage
└── 解决: 业务场景的具体需求

接入层 (Interface)
├── API call / Prompt engineering / Tool schema design
├── 90% 客户痛点的解决面
└── 解决: 怎么跟模型沟通

模型层 (Model)
├── Pretrain / Post-training (SFT / RLHF / DPO)
├── LLM 公司的核心 IP 所在
└── 解决: 模型本身的能力和性格

基础设施 (Infrastructure)
├── GPU / Serving / Quantization / KV cache
├── 大公司专精
└── 解决: 让模型跑得动、跑得快、跑得便宜
```

**关键原则**：每层都有自己的 judgment framework。**FDE 的工作就是判断「客户痛点该在哪一层解」**。

---

## 3. Pretrain — 「学语言 + 读完所有书」

### 3.1 最朴素定义

让神经网络读海量文本，学会一件事：**预测下一个 token**。

```
输入: "The capital of France is"
模型: 输出每个可能 next token 的概率
正确答案: " Paris"
loss: 让模型的概率分布跟真实分布的差距最小 (cross-entropy)
```

就这一件事。**所有 pretrain 都在做这一件事**。

### 3.2 涌现 — 为什么这件事这么强大

要把 "预测下一个 token" 做到极致，模型必须涌现地建立：

- 语法（不学 Paris 是名词预测不对）
- 世界知识（不知道法国首都预测不对）
- 推理能力（"如果 A 然后 B"序列）
- 风格（学术 vs 口语 vs 代码）
- 多语言互译

→ 单一目标（next-token prediction）训出一个"理解世界"的东西。**涌现**是 pretrain 的核心魔法。

### 3.3 产物：Base Model

| 例 | 状态 |
|---|---|
| GPT-3 base / GPT-4 base | OpenAI 闭源 |
| LLaMA 2/3 base | Meta 开源 |
| Qwen base | Alibaba 开源 |
| DeepSeek V3 base | DeepSeek 开源 |

Base model 特点：
- 什么都懂
- **但不知道怎么跟人对话**
- 你问 "What is 2+2?" 它可能续写 "What is 2+2? asked the math teacher..."（因为续写在训练数据里比直接答多）

### 3.4 规模

| 维度 | 量级 |
|---|---|
| 数据 | TB 级（互联网 + Wikipedia + 书 + 代码） |
| 算力 | 数十万 GPU 小时 |
| 时间 | 数月 |
| 谁能做 | 只有 OpenAI / Anthropic / Google / Meta / Alibaba 等大公司 |

### 3.5 一句话

> Pretrain = next-token prediction at scale → 涌现出「世界知识 + 语言能力 + 推理潜力」的 base model。**单一目标，全部能力**。

---

## 4. Post-training — 「学职业 + 学礼貌 + 学边界」

### 4.1 解决 base model 的根本局限

Base model **会续写但不会"对话"**。Post-training = **塑造 base model 的行为**：让它学会"被问就答"、"按指令做事"、"不说危险的话"。

### 4.2 三个主要环节

| 阶段 | 名字 | 数据形态 | 学到什么 |
|---|---|---|---|
| **4.2.1 SFT** | Supervised Fine-Tuning | (prompt, ideal_response) 配对 | "对话"格式 + 指令跟随 |
| **4.2.2 RLHF/DPO** | 偏好对齐 | (prompt, response_A, response_B, 谁更好) | 风格 + 价值观 + 拒答边界 |
| **4.2.3 Constitutional AI / RLAIF** | 用 AI 替代人类标注 | 同上但 AI 当评委 | 同上 + scale 加速 |

### 4.3 具体例子

```
Base model 看到: "Write me a poem about loneliness"
回复: "Write me a poem about loneliness, said John to the AI..."  ← 续写

SFT 后:
回复: "In silence wraps the empty room..."                        ← 对话

RLHF/DPO 后:
回复同样但更符合"人类偏好"——结构工整、情感精准、不会突然说脏话
```

### 4.4 产物：Chat Model

| Base | 对应 Chat |
|---|---|
| LLaMA 3 base | LLaMA 3 Instruct |
| Qwen base | Qwen Chat |
| Claude base | Claude 3.5 Sonnet |

### 4.5 规模（远小于 pretrain）

| 维度 | 量级 |
|---|---|
| 数据 | GB 级（精挑、人工标注、贵） |
| 算力 | 数百-数千 GPU 小时 |
| 时间 | 天-周 |
| 谁能做 | 中等公司 / 甚至个人（用 LoRA） |

### 4.6 一句话

> Post-training = 从 base model 到 chat model 的转化 → 塑造**行为 + 格式 + 安全 + 指令跟随**。**Base model 是原矿，post-training 是雕刻**。

---

## 5. Fine-tune 是 Umbrella Term，不是某个象限

这是最容易搞混的术语。

### 5.1 字面定义

**Fine-tune** = **继续训练一个已有模型**（vs. from-scratch 训练）。

→ 它**底下有一堆完全不同的事情**——所以单说"我要 fine-tune"没意义。

### 5.2 完整 mental model

```
"训练 LLM" 这件事
│
├── from scratch (0 → base)              ← pretrain (不是 fine-tune)
│
└── 继续训练已有 model                    ← fine-tune (umbrella)
    │
    ├── 改 "知识"
    │   └── Continued Pretrain            ← 用更多文本继续 next-token training
    │
    └── 改 "行为" (= post-training)
        ├── SFT (= Instruction Tuning)    ← (prompt, response) 配对
        └── Alignment
            ├── RLHF                       ← 偏好对 + Reward Model + PPO
            ├── DPO                        ← 偏好对,直接监督
            └── RLAIF / Constitutional AI  ← 用 AI 标注偏好
```

### 5.3 Fine-tune 的 4 个独立维度

| 维度 | 含义 | 典型选择 |
|---|---|---|
| **1. 目标** | 改什么 | 知识 / 行为格式 / 风格&价值观 |
| **2. 数据形态** | 输入什么 | 纯文本 / (prompt, response) / 偏好对 |
| **3. 方法** | 怎么改权重 | full fine-tune / LoRA / QLoRA |
| **4. 起点** | 从什么 model 开始 | base / chat / 自己之前 fine-tune 过的 |

**单说"fine-tune"无意义**——必须 disambiguate 这 4 维。

### 5.4 同一个词，5 种不同的事

| 目标 | 数据形态 | 方法 | 实质 |
|---|---|---|---|
| 学医疗知识 | 1000 本医学书纯文本 | full | **Continued Pretrain** |
| 学医疗对话 | 10K (患者问, 医生答) 配对 | LoRA | **SFT** |
| 学回答更人性化 | 5K 偏好对 | LoRA | **DPO** |
| 让 base 学 chat 格式 | OpenAssistant 数据集 | full | **Instruction Tuning** |
| 在 Llama-Chat 上叠新风格 | 自己公司风格偏好对 | QLoRA | **DPO on chat model** |

### 5.5 FDE 听到「fine-tune」的追问 Framework

```
客户: "我们想 fine-tune"
   ↓
你: "目标是什么?"
   ├── "让模型学我们行业术语" → continued pretrain 或 SFT
   ├── "让模型按我们格式输出" → SFT
   ├── "让模型更像我们 brand voice" → DPO / RLHF
   └── "让模型拒答竞品问题" → DPO + system prompt 联动

你: "你们手上的数据是什么形态?"
   ├── 一堆 PDF / 文档 → 纯文本 → continued pretrain
   ├── 客服对话记录 → (prompt, response) 配对 → SFT
   └── A/B 测试评分 → 偏好对 → DPO

你: "预算 / 算力?"
   ├── 消费级 GPU → QLoRA + SFT
   ├── 一两张 A100 → LoRA + SFT 或 DPO
   └── 大集群 → full fine-tune 或 RLHF

你: "从什么 model 开始?"
   ├── 开源 base → 先 SFT 再 alignment
   └── 开源 chat → 跳过 SFT,直接 DPO 加偏好
```

### 5.6 谁说「fine-tune」通常意味着什么

| 谁说 | 默认指 | 暗含数据 |
|---|---|---|
| 普通客户 | SFT | (prompt, response) |
| ML engineer | SFT，会问 full vs LoRA | 同上 |
| Alignment team | RLHF / DPO | 偏好对 |
| Domain expert | continued pretrain 或 SFT | 行业文本 |
| 个人玩家 | QLoRA + SFT | 小规模配对 |

→ FDE 必须**先翻译到 4 维度**再判断方案。否则就是"客户说什么我做什么"。

### 5.7 一句话

> Fine-tune 不是 RLHF——RLHF 是 fine-tune 的一种特殊形式。Fine-tune ⊃ post-training；continued pretrain 也是 fine-tune 但不是 post-training。**单说 fine-tune 无意义，必须 disambiguate 4 维度**。

---

## 6. RLHF / DPO — Alignment 的核心

### 6.1 为什么需要 RLHF — SFT 教不了「taste」

SFT 的根本局限：**需要 "ideal response"**——人写一个理想答案，模型学着写一样的。

但很多事情：

| 场景 | 你能写出理想答案吗？ |
|---|---|
| "解释光合作用" | ❌ 不同人写不同风格，都可以是"对" |
| "写一首关于孤独的诗" | ❌ 没有唯一"对"的答案 |
| "回答 + 简洁 + 不要 hallucinate" | ❌ "简洁" 本身就主观 |

**人类的真相**：「我说不出怎么写最好，但我能告诉你 A 比 B 更好」。

→ RLHF 起点：**偏好（preference）比"理想答案"更容易表达**。

### 6.2 RLHF 三步法

#### Step 1: 训 Reward Model (RM)

数据：`(prompt, response_A, response_B, A>B 还是 B>A)`

人类标注一堆这种偏好对（典型 100K-1M 条）。训一个 reward model：

```python
RM(prompt, response) → scalar  # 分数越高表示越接近"人类偏好"
loss = -log(σ(RM(prompt, chosen) - RM(prompt, rejected)))
```

**本质**：把"人类偏好"这件抽象的、说不清的事，**compress 成一个 scalar function**。

#### Step 2: 用 RL 优化 model 最大化 reward

把 SFT model 当 RL agent：

| RL 概念 | 在这里对应 |
|---|---|
| state | prompt + 已生成的 tokens |
| action | 选下一个 token |
| reward | `RM(prompt, full_response)` |

用 PPO（Proximal Policy Optimization）等 RL 算法更新 model 权重。

**关键防护：KL penalty**——RL 训练时 model 容易 "hack reward model"（产 RM 喜欢但人类觉得奇怪的输出）。KL penalty 强制新 model 不能离 SFT model 太远。

#### Step 3: 迭代

跑完一轮 → 新 model → 标更多偏好对 → 训新 RM → 再 PPO。

### 6.3 First-principles：RLHF = LLM 输出的 A/B test 自动化

跳出 RL 术语：

- **痛点**：SFT 教不了 "taste"——能学语法、格式、事实，但学不了"让人觉得舒服的回答方式"
- **抽象**：把 "taste" compress 成 reward function；让 RL 自动找最大化的策略

类比 production system 的 A/B test → 自动优化。

### 6.4 RLHF 的工程痛点 → DPO 出现

RLHF 优雅但**4 个工程痛点**：

| 痛点 | 含义 |
|---|---|
| **3 个 model 同时存在** | base + RM + policy（被训的） |
| **RL 训练不稳定** | PPO 超参难调 |
| **Reward hacking** | model 学会"骗"RM |
| **算力贵** | RM 一份 + PPO 一份 |

→ **DPO (Direct Preference Optimization, 2023)**：

- 数学推导：RLHF 的最优解可以写成偏好对的 closed-form
- **不需要 RM**，**不需要 RL 算法**
- 变成**监督学习问题**

```python
# DPO 训练循环（简化）
loss = -log(σ(β * (log_p_new(chosen) - log_p_old(chosen)
                  - log_p_new(rejected) + log_p_old(rejected))))
```

| 维度 | RLHF | DPO |
|---|---|---|
| 模型数量 | 3 | 1 |
| 训练范式 | RL (PPO) | 监督学习 |
| 稳定性 | 不稳定 | 稳定 |
| 算力 | 贵 | 便宜 |
| 灵活度 | 高 | 低 |

**2024 之后大部分新模型用 DPO**（LLaMA 3 / Qwen 2 / Mistral）。但 SOTA 大厂（OpenAI / Anthropic / Google）仍坚持 RLHF（灵活度 + 可加 Constitutional AI 等变体）。

### 6.5 RLHF 是 LLM 公司的核心 IP

LLM 公司之间最大差异**不在 base model**，**在 post-training 哲学**：

| 公司 | post-training 哲学 |
|---|---|
| OpenAI | helpful + balanced |
| Anthropic | cautious + thoughtful（Constitutional AI） |
| Meta | open + neutral（让用户自己 post-train） |
| Mistral | minimal alignment |
| Qwen / 国内 | helpful + 中文优先 + 监管合规 |

→ **一家 LLM 公司的核心 IP = 「我们怎么定义 helpful / harmless / honest」**。这就是 RLHF dataset + RM。
→ Anthropic 写 Constitutional AI paper，OpenAI 闭源 RLHF 流程——**RLHF dataset 是最值钱资产**。

### 6.6 一句话

> SFT 教对话格式，RLHF/DPO 塑造**性格 / 价值观 / taste**。RLHF 是 LLM 时代的「性格塑造」——同一个 base，不同 post-training 训出来的 chat model 性格完全不同。

---

## 7. Reasoning Model — 新的 Scaling 维度

### 7.1 看清 reasoning 到底是什么

**普通 LLM**：
```
Q: 一个苹果 3 元,7 个苹果 + 5 个梨,梨 2 元一个,共多少钱?
A: 31 元                          ← 直接答,可能错
```

**Reasoning LLM (o1 / R1 / Claude Thinking)**：
```
Q: 一个苹果 3 元...
<thinking>                        ← thinking tokens
苹果: 3 × 7 = 21
梨: 2 × 5 = 10
总: 31
验证: 7+5=12 件水果...对
</thinking>
A: 31 元
```

**本质**：让 model 在产生答案前**消耗大量 token 做 multi-step reasoning + self-verification**。

### 7.2 新 Scaling 维度

```
传统 scaling law: 能力 = f(model size × training data × training compute)

新 scaling law:   能力 = f(model size × training data × training compute × inference-time compute)
                                                                       ↑
                                                                  全新维度
```

→ **让 model 在推理时多想一会（多 token），能力直接上升**。

具体证据：

| 任务 | GPT-4o 直接答 | GPT-4o + CoT | o1 (训出的 reasoning) |
|---|---|---|---|
| AIME 数学 | ~15% | ~50% | ~85% |
| Codeforces | ~10% | ~30% | ~90% |
| GPQA 科学 | ~50% | ~60% | ~78% |

**同一个 base 能力（同 model size），让它多花 inference 时间 → capability 跳一档**。

### 7.3 怎么训出来的

完整链路：

```
Step 1: Base model
   ↓
Step 2: Optional SFT (教基本对话格式)
   ↓
Step 3: ★ RL with verifier-based reward     ← 关键创新
   - 给一堆 reasoning 任务 (数学 / coding / 科学)
   - 让 model 生成多个 candidate solutions
   - 跑 verifier 判答案对错 → reward
   - 用 PPO / GRPO 更新 model
   ↓
Step 4: 二次 alignment (普通 RLHF for helpful)
   ↓
Reasoning Model
```

### 7.4 关键创新：Outcome-based RL

```
传统 RLHF:    [model 产生 chain] → [人标 chain 好不好]    ← 评过程, 慢
Reasoning RL: [model 产生 chain + 答案] → [verifier 跑]   ← 评结果, 自动 scale
```

注意：
- 不评估 reasoning chain "看起来对不对"
- 只评估**最终答案**对不对
- model **自己 explore** 出有效的 reasoning 模式

这是 RL 的核心 power——**给 reward 不给 path**。

### 7.5 DeepSeek-R1-Zero 的震撼

2024 末 DeepSeek 论文：**不用 SFT，直接 base + RL + verifier，model 涌现出 reasoning 行为**。

观察到的 emergent behavior：

| Behavior | 例 |
|---|---|
| Self-reflection | "Let me think again..." |
| Backtracking | "Wait, I made a mistake..." |
| Alternative approaches | "Let me try this from another angle..." |
| Verification | "Let me verify by..." |
| **Aha moment** | model 在某 training step 突然解锁长 reasoning |

**没人教这些**——纯 RL 给 outcome reward，model 自己长出来。

→ **Reasoning 是 emergent，不是 inject**。Base model 里早就潜伏着 reasoning 能力，只是默认情况下 model 不愿意"花时间"——RL 训练教会 model "什么时候值得花时间想"。

→ **Reasoning 不是教出来的，是 RL「打捞」出来的**。

### 7.6 GRPO — DeepSeek 的算法创新

| 算法 | 谁用 | 复杂度 | 算力 |
|---|---|---|---|
| **PPO** | OpenAI | 复杂（需 value model） | 贵 |
| **GRPO** | DeepSeek | **简单**（candidate 相对比较） | **便宜** |

GRPO 想法：
- 不训 value model
- 同 prompt 生成 K 个 candidates
- candidate 之间相对比较（谁 reward 高）
- 用 relative reward 直接训

→ DeepSeek-R1 用 GRPO 训出跟 o1 同档次的 model，**算力远低于 OpenAI**。

**产业含义**：**算法 > 算力**——frontier model 不再是大公司专利。

### 7.7 用户「切换 thinking model」的本质

不是切换 model——是**同一 reasoning model 的两种 inference mode**：

| 维度 | Fast mode | Thinking mode |
|---|---|---|
| 响应速度 | 秒级 | 数秒-数十秒 |
| 价格 | 便宜 | 贵 5-10× |
| 中间过程 | 不可见 | 可见 thinking 段 |
| 适合 | 闲聊、简单 QA | 数学、代码、planning |

**不同厂商的产品包装**：

| 厂商 | 形态 |
|---|---|
| OpenAI | 拆两个 model（GPT-4o vs o1/o3） |
| Anthropic | 同 model + thinking toggle |
| Google | Gemini Deep Think mode |
| DeepSeek | R1 vs V3 双产品线 |

技术本质：**同一 reasoning model + inference compute budget 控制**。产品包装是商业决策。

### 7.8 改变了什么

| 改变 | 含义 |
|---|---|
| **新 scaling 维度** | model size 可能不需要继续暴涨；推理算力需求大幅上升 |
| **Domain transfer** | reasoning RL 训出来的能力泛化到非 coding/math 领域 |
| **Agentic capability 起飞** | reasoning model 跑 agent 任务自然就有 planning / 自我纠错 |

### 7.9 一句话

> Reasoning model 不是新 model，是 **「教会 base model 在推理时多想」的训练范式**。技术核心是 outcome-based RL + deterministic verifier——让 model 自己 explore reasoning。**Reasoning 是 emergent**——不是教出来的，是 RL「打捞」出来的。

---

## 8. Coding 是长上下文训练的核心方向

这是 2026 最深的产业洞察之一。

### 8.1 三个独立洞察叠加

```
Coding 天然是长上下文 (洞察 1)
        +
Coding 有 deterministic verifier (洞察 2)
        +
Coding 训出来的能力高度通用 (洞察 3)
        =
唯一一个能 scale "long context + agentic reasoning" 的 RL training ground
```

### 8.2 洞察 1：长上下文训练数据的稀缺性

互联网上的"长文本"主要类型：

| 类型 | 体量 | 是否真"长上下文" | 训练效果 |
|---|---|---|---|
| 书籍 / 长文 | 多 | ❌ 弱 | 章节间关联弱 |
| 学术论文 | 多 | 部分 | 单篇 20-30 页 |
| 网页 / Wikipedia | 海量 | ❌ | 短而碎片 |
| 法律文档 | 中 | 部分 | 量太少 |
| **代码仓库** | **海量** | **✅ 强** | **跨文件 deep semantic 关联** |

→ **代码是唯一同时满足"量大"+"真长上下文"的语料**。

为什么代码"真长"：
- 改一个 React 组件要看：组件 + hooks + utils + props + styles + tests
- 修 bug 要看：stack trace + 相关文件 + git blame
- 写 feature 要看：项目结构 + similar pattern + type 一致性

这些**自发产生的长上下文任务**——不是合成数据。

### 8.3 洞察 2：Coding 有 Deterministic Verifier

`Verifier` = 验证器（一个程序，输入"答案"，输出"对/错"）。
`Deterministic` = 确定性（同输入永远同输出，不依赖人）。

合起来：**deterministic verifier = 一段确定性程序，能自动判断 model 输出对不对**。

| 任务 | Verifier | Deterministic? |
|---|---|---|
| **Coding** | 跑 unit test / 编译 | ✅ 完全客观 |
| **Math** | 比较最终答案 | ✅ 数值相等 |
| 棋类 | 规则 | ✅ 确定 |
| Reasoning | 人评估 | ❌ 主观 |
| Writing | 人评分 | ❌ 主观 |

→ Coding + Math 是**verifier-friendly** 领域，RL 可以**无限 scale**。其他领域 verifier 是瓶颈。

**这就是 deterministic verifier 是 RL 的"圣杯"的原因**——RL 的 reward signal 不再受人类标注速度限制。

### 8.4 洞察 3：Coding 训的是 5 件通用能力

Coding 训练**表面在训 syntax**，**实质在训 5 件 general capabilities**：

| 能力 | Coding 训练怎么训到 | 泛化到 |
|---|---|---|
| **Long-form planning** | "先规划架构再写代码" | 任何复杂任务的 planning |
| **Multi-step reasoning** | "bug 修不好继续 debug" | 数学 / 科研 / 商业 |
| **Tool use** | 看代码 / 改代码 / 跑测试 | 任何 agentic 任务 |
| **Self-correction** | 编译失败 → 看 error → 修 | 任何 feedback loop |
| **Long context utilization** | 在大量代码里 retrieve relevant | RAG / 长文档 / agent 长任务 |

→ "Coding 是 long context + agentic 的训练 sandbox"——**真正的训练目标是这 5 件能力**，coding 只是它们最易 scale 的载体。

**已验证的泛化**：
- DeepSeek-R1：纯 coding/math RL 训，general reasoning（GPQA / MMLU）也 SOTA
- Claude 3.5 Sonnet：Claude Code 训练显著提升整体 agentic 能力
- o1/o3：reasoning RL 在 coding 突破后迁移到 science / law

### 8.5 业界一致下注

| 公司 | 动作 |
|---|---|
| Anthropic | Claude Code（产品 + training data engine） |
| OpenAI | SWE-bench / 内部 coding agent 训 o3 |
| DeepSeek | R1 公开 GRPO on coding/math |
| Google | Jules / Gemini Code Assist |
| Meta | Code Llama series |
| Mistral | Codestral |
| Qwen | Qwen Coder |

→ **所有 frontier lab 都在重押 coding training**——因为这是当前**唯一能 scale "long context + reasoning" 的 RL training ground**。

### 8.6 Dogfood Flywheel — LLM Lab 独有的飞轮

**普通 SaaS dogfooding**：用自己产品 → 发现 bug → 改产品 → 用户更满意。
**LLM lab dogfooding**：多一层——**trace 变 training data**。

```
工程师用 Claude Code 写 Claude Code
         ↓
   产生 (task, trajectory, outcome) 三元组
         ↓
   高质量 training data
         ↓
   训下一代 Claude (更会写代码)
         ↓
   工程师再 dogfood, 更顺
         ↓
   循环
```

→ **Anthropic 给 Claude Code 投这么大资源**：不只是产品业务，**也是训练数据 engine**。OpenAI / Google 也在做。

### 8.7 一句话

> Coding 在 2026 是「长上下文训练的核心方向」，因为它独占三件事：**天然长上下文 + deterministic verifier + 训出的能力高度通用**。这三件事叠加让 coding 成为唯一能 scale "long context + agentic reasoning" 的 RL training ground——所有 frontier lab 都在重押。

---

## 9. 2026 产业地形

### 9.1 三大事实

| 事实 | 状态 | 说明 |
|---|---|---|
| **Pretrain 已 commoditized** | ✅ | 数据 / 架构 / 算力都公开 |
| **Post-training 是主战场** | ✅ | 差异化空间只剩这里 |
| **1M context 是入场券** | ✅ | 必备非充分 |

### 9.2 Pretrain 为什么对齐

三个约束都已 commoditize：

| 约束 | 2022 | 2026 |
|---|---|---|
| 数据 | 闭源、爬法是秘密 | 几乎公开 |
| 架构 | Transformer + trick 闭源 | 全开（LLaMA / Qwen 代码可复现） |
| 算力 | H100 买不到 | 谁都能买；Scaling Law 公开 |

**实证**：2025-2026 开源 base model（DeepSeek V3 / Qwen 2.5 / LLaMA 3.1 405B）**几乎跟闭源 base 旗鼓相当**。

### 9.3 Post-training 为什么是主战场

2024-2026 明星模型，**每一个都是 post-training 创新**：

| 模型 | 火的原因 |
|---|---|
| Claude 3.5 Sonnet | Constitutional AI + 细腻 RLHF |
| GPT-4o | multimodal + post-training tuning |
| DeepSeek-R1 | GRPO + reasoning RL |
| o1/o3 | reasoning RL training |
| Qwen 2.5 | 中文 + 多语言 SFT/DPO 投入 |

### 9.4 1M Context 是入场券

**能力质变临界点**：

| context | 能容下 | 能做什么 |
|---|---|---|
| 8K | 几页文档 | chatbot |
| 32K | 一篇论文 | 文档分析 |
| 128K | 一本中等书 | 长文档总结 |
| **1M** | **整个代码仓库 / 多文档集** | **agent 自己看完所有代码再写** |

**入场券的精确含义**：

```
2026 牌桌入场标准:
├── 1M context (必备)
├── tool use & function calling (必备)
├── multimodal vision (必备)
└── ~7B-70B 的 chat model (必备)

牌桌上真正的胜负 (差异化):
├── reasoning 深度 (o1 / R1 / Claude Thinking)
├── agentic 长任务能力
├── domain post-training (Coder / Med / Legal)
└── post-training 哲学 (helpful vs cautious vs minimal)
```

→ **「坐在牌桌上」≠「能赢牌」**。1M context 是入场费，赢牌靠 4 件事。

### 9.5 串成飞轮

```
Coding 有 ground truth + deterministic verifier
              ↓
RL 训练可无限 scale (不被人类标注瓶颈)
              ↓
Frontier lab 重押 coding training (R1 / o1 / Claude Code 都这条路)
              ↓
Frontier lab dogfood agentic coding tool
              ↓
Dogfood flywheel: 产品 → trace → training data → 下一代 model
```

### 9.6 一句话

> 2026 产业三大事实：**base model 已 commoditized，post-training 是差异化主战场，1M context 是入场券非赢牌**。入场券之上的真竞争在 reasoning / agentic / domain post-training / post-training 哲学——**全部是 post-training + inference-time 工程**。

---

## 10. Training Stack vs Harness — 本质区别

### 10.1 7 维度对比

| 维度 | post-training | harness |
|---|---|---|
| **发生时间** | train-time（部署前） | inference-time（每次推理） |
| **改变对象** | 模型权重（永久） | 模型输入 + 输出处理（runtime） |
| **可逆性** | 不可逆（要重新训） | 完全可逆（改 config） |
| **生效速度** | 小时-天 | 微秒-秒 |
| **粒度** | 粗（整个模型） | 细（每个 tool / args / mode） |
| **成本** | 高（GPU + 数据 + 时间） | 低（代码 + config） |
| **谁负责** | ML engineer | application engineer / FDE |

### 10.2 Mental Model 类比

| 类比 | post-training | harness |
|---|---|---|
| 计算机系统 | 改硬件 / CPU 微码 | OS / 应用层 |
| 公司 | 招聘 + 长期价值观塑造 | 流程 / 规章 / 权限 / 审批 |
| 餐厅 | 培训厨师的烹饪习惯 | 菜单 + 出菜流程 + 食材采购 |

→ post-training 塑造**默认行为**，harness 塑造**约束环境**。

### 10.3 决策 Framework — 哪个客户痛点用哪个

| 客户痛点 | post-training | harness | 谁主导 |
|---|---|---|---|
| 不知道我们公司术语 | SFT | RAG / system prompt | **harness 优先** |
| 输出格式不稳定 | SFT | prompt engineering | **harness 先试** |
| 调错 tool | SFT 加 tool 样本 | system prompt + tool description | **harness 主导** |
| **调对 tool 但 args 危险** | **不可靠** | **permission 必须** | **只能 harness** |
| Hallucinate | RLHF 减少 | RAG + verification hook | **两边都要** |
| Bias 输出 | RLHF / Constitutional 调 | 难做 | **只能 post-training** |
| 长期一致 brand voice | RLHF / DPO 必须 | system prompt 撑不住 | **只能 post-training** |

### 10.4 三层嵌套关系

```
Pretrain (会什么)               ← base model
   ↓
Post-training (愿意做什么)       ← chat model
   ↓
Harness (在具体场景做什么)        ← production app
```

→ **三者层次嵌套**，不是平行三选一。production LLM 应用**必须三者并存**。

### 10.5 你的工作位置

| 层 | 你的状态 | FDE 工作占比 |
|---|---|---|
| harness（应用） | 深 | ~80% 时间 |
| post-training（模型层） | 懂得对话 | ~10% 时间 |
| pretrain | 知道有 | ~5% 时间 |
| infrastructure | 知道有 | ~5% 时间 |

→ **你深做 harness 是对的**。客户 80% 需求 harness 能解决（便宜 + 快 + 可逆）。**post-training 是稀缺技能但低频用**——客户只在 harness 撑不住时才请你做 fine-tune 方案。

**懂 post-training 是为了「边界判断」**——知道哪些客户痛点 harness 撑不住，必须建议 fine-tune。这条边界判断能力是 FDE 的核心 leverage。

---

## 11. Training Stack 跟 Harness 的 RPC 同构性

回到 [`phase-3-framing.md`](./phase-3-framing.md) §2 那 5 件 production RPC 配套——它们在训练栈**完全成立**。

| RPC 配套 | harness | LLM 训练栈 |
|---|---|---|
| **Retry** | api/retry.py 指数退避 | data loader batch retry / checkpoint reload / FSDP fault tolerance |
| **Middleware** | hook lifecycle (D13.1) | callbacks（loss logger / eval hook / lr scheduler / EMA） |
| **AuthZ** | PermissionChecker (D13.2) | data filter / safety classifier / RLHF reward model |
| **Observability** | structlog (D13.6) | wandb / tensorboard / loss curve / activation histograms |
| **Error Taxonomy** | error rename (D13.4) | training failure modes（OOM / NaN / loss spike / divergence） |

业界 LLM 训练平台（HuggingFace / Mosaic / Anthropic 内部 / Meta llama factory）演化出**一模一样的 5 件配套**——因为底层都在解 [`phase-3-framing.md §2.7`](./phase-3-framing.md) 那个统一痛点：

> **有没有一类痛点会随用户/调用增多反复出现，且不抽象就只能 inline 重复？**

→ **你不是在学一个新领域，是在用同一套 judgment framework 走过 LLM 应用栈的另一段**。

这是 [`phase-3-framing.md §8`](./phase-3-framing.md) 「language as substrate, judgment as substance」在 LLM 应用栈下半段的具体兑现。

---

## 12. 关键术语词典

| 术语 | 一句话定义 |
|---|---|
| **Base model** | Pretrain 后产物，会续写但不会对话 |
| **Chat model** | Post-training 后产物，会对话、听指令、有边界 |
| **Pretrain** | 大规模 next-token prediction，目标是涌现世界知识 |
| **Post-training** | 从 base → chat 的转化，含 SFT + RLHF/DPO |
| **SFT** | Supervised Fine-Tuning，用 (prompt, response) 配对教对话格式 |
| **RLHF** | Reinforcement Learning from Human Feedback，用偏好对 + reward model + PPO 训 |
| **DPO** | Direct Preference Optimization，跳过 reward model 直接监督学习 |
| **Constitutional AI / RLAIF** | 用 AI 替代人类标注偏好，scale 加速 |
| **GRPO** | DeepSeek 创新的 RL 算法，不需 value model，相对比较 |
| **Fine-tune** | Umbrella term，指"继续训练已有模型"——包含 continued pretrain + post-training |
| **Continued pretrain** | 用新文本继续 next-token training，改的是"知识"层 |
| **LoRA / QLoRA** | 低秩矩阵 fine-tune 方法，省算力，消费级 GPU 也能玩 |
| **Instruction Tuning** | = SFT（同义词） |
| **Alignment** | 让 model 行为符合人类意图，主要靠 RLHF/DPO |
| **CoT (Chain of Thought)** | "Let's think step by step" prompt trick，让 model 多说几步 |
| **Reasoning model** | 经过 outcome-based RL 训练，会自动 thinking 的 model |
| **Outcome-based reward** | 评估最终答案对错（不是评估过程），需 deterministic verifier |
| **Inference-time compute scaling** | 让 model 在推理时多想 → 能力上升（新 scaling 维度） |
| **Ground truth** | 已知的正确答案，loss 计算的基准 |
| **Deterministic verifier** | 能自动判断对错的程序（同输入永远同输出） |
| **Frontier lab** | 训当前 AI 能力上限模型的 lab（OpenAI / Anthropic / Google / DeepSeek 等） |
| **Frontier model** | 当前能力前沿的 model（GPT-4 级别及以上） |
| **Dogfooding** | 自己用自己的产品；LLM lab 的 dogfooding 多一层 trace → training data |
| **Reward hacking** | model 学会"骗" reward model 但人类觉得变差 |
| **KL penalty** | RLHF 训练时强制新 model 不能离 SFT model 太远的约束 |
| **Emergent capability** | 训练中"自然涌现"的能力（不是直接被教的） |
| **Aha moment** | DeepSeek-R1-Zero 训练中观察到的现象——model 在某 step 突然解锁长 reasoning |

---

## 13. FDE 决策 Framework — 客户场景 × 用哪层

### 13.1 客户对话 cheatsheet

| 客户说 | 你的判断 |
|---|---|
| "我们希望 LLM 调对 tool" | **harness**（system prompt + tool design） |
| "我们希望 LLM 输出格式稳定" | **harness 先试**（prompt + parsing），不行再 SFT |
| "我们希望 LLM 拒答竞品相关问题" | 短期 system prompt，长期 RLHF |
| "我们要 your own ChatGPT, with our brand voice" | **RLHF / DPO** — harness 撑不住 |
| "调工具前要查权限" | **harness** — permission 必须 last-line defense |
| "我们希望 LLM 更懂我们行业" | **RAG**（知识）+ **SFT**（术语/格式） |
| "我们要 fine-tune" | **追问 4 维度**：目标 / 数据 / 方法 / 起点 |
| "我们要 reasoning 能力" | reasoning model 或 reasoning RL on own data（看预算） |
| "我们的客户场景在长 context" | 1M context base model + harness 加 long-context 优化 |
| "我们希望降低推理成本" | quantization / smaller model / caching / harness 减 token |

### 13.2 边界判断的判断

```
痛点分类:
├── 行为可预测、规则明确 (rule-based)
│   → harness (permission / hooks / prompt)
│
├── 行为偏好、风格、价值观 (taste-based)
│   → post-training (SFT / RLHF / DPO)
│
├── 知识缺失 (knowledge-based)
│   → RAG (harness 侧) 或 SFT (看 update 频率)
│
├── 安全 / 不可越线 (safety-critical)
│   → harness 必须 (权限 last-line defense)
│   → post-training 可加 (让 model 自己拒答)
│
├── 推理深度不够 (reasoning depth)
│   → reasoning model (o1 / R1 / Claude Thinking)
│
└── 长 context (long context)
    → 1M base model + harness 工程化
```

### 13.3 FDE 的双重 leverage

| 能力 | 占比 | 价值 |
|---|---|---|
| **Harness 深度** | 80% | 解决大多数客户痛点 |
| **Training 栈对话** | 20% | 边界判断 — 知道何时必须切换到训练方案 |

→ FDE 不是 ML engineer，**FDE 是「边界判断 + 工程化交付」的工程师**。

---

## 14. Inference-time Capability Amplification — Thinking + Harness 的元同构

> Phase 2 close-out 第二轮深聊（2026-05-12）后浮现的元洞察：
>
> > 「**模型还是那个模型，但实际的效果是唤醒了模型的更大的能力**」
>
> 这条洞察把 §7 (reasoning) 和你整个 harness 项目压在同一个抽象上——
> 是本文档**最深的一节**，也是 LLM 应用栈整个 framing 的元层 punch line。

### 14.1 洞察核心 — 两件事的元同构

| 维度 | Thinking (reasoning) | Harness |
|---|---|---|
| **改 model 权重？** | ❌ 不改 | ❌ 不改 |
| **怎么"提升能力"？** | 让 model 自己花 token reason | 在 model 周围加 dispatch / tool / loop |
| **多花什么** | inference token | inference turns + tool calls |
| **释放的能力来自** | model 里**早就潜伏**的 reasoning | model 里**早就潜伏**的 agentic ability |
| **本质** | inference-time capability amplification | inference-time capability amplification |

→ **两者都不是教 model 新东西——是「调出 model 已有但默认不发挥的潜力」**。

这是 §7.5 DeepSeek-R1-Zero "**reasoning 不是教出来的，是 RL 打捞出来的**" 在
更大尺度的 generalization：

> **不只 reasoning 是 emergent —— agentic capability 也是 emergent。Thinking
> 和 harness 是两种「打捞机制」**。

### 14.2 业界 framework 名字：Test-time Compute Scaling

这条洞察 2024-2025 学术界 + 产业界正在 build framework，叫
**"test-time compute scaling"** 或 **"inference-time compute scaling"**
（Karpathy 称之为 "the new scaling axis"）。

里面包含**三种 inference-time strategy**：

| Strategy | 怎么花 inference compute | 例 |
|---|---|---|
| **Sequential** | 让 model 自己产生更长 reasoning chain | o1 / R1 / Claude Thinking |
| **Parallel** | 同时生成 N 个 candidate，verifier 选最好 | Best-of-N / majority vote / self-consistency |
| **Agentic** | 让 model 多 turn 跟外部世界交互 | Claude Code / Devin / **你的 harness** |

→ **三种都是 "同 model + 不同 inference strategy" 的 capability amplification**。

参考论文：
- "Scaling LLM Test-Time Compute Optimally Can Be More Effective Than Scaling Model Parameters" (Snell et al., 2024)
- 业界共识：**test-time compute 是新的 scaling 维度，跟 model size / training compute 同等重要**

### 14.3 跟 [framing §8 substrate/substance](./phase-3-framing.md) 的同构

这是 framing §8「language as substrate, judgment as substance」**在另一个 layer 的具体化**：

| Layer | Substrate（不变） | Substance（操作策略） |
|---|---|---|
| 跨语言能力（framing §8） | 编程语言 | judgment framework |
| 同 model 的能力发挥（本节） | model weights | inference-time strategy |

→ **同一个抽象在两个 layer 上具体化**：底层永远是 substrate，真正的力量在如何
operate substrate。

### 14.4 跟 RPC core/shell 演化的同构

回到 [phase-3-framing.md §2](./phase-3-framing.md)：

| RPC 演化 | LLM 应用栈 |
|---|---|
| **Core 慢演进**（server handler 实现） | model weights（pretrain + post-training） |
| **Shell 快演进**（middleware / observability / interceptor） | inference-time strategy（thinking + harness） |

→ Production system 都遵循 **"core 稳定 + shell 灵活"** 的演化哲学。
你今天看清的 thinking + harness 同构，**就是 LLM 应用栈版本的 "shell 演进 > core 演进"**。

具体含义：

| 改什么 | 成本 | 速度 | 可逆性 | ROI |
|---|---|---|---|---|
| **改 model weights** (post-training) | 高 | 慢（小时-天） | 低 | 中 |
| **改 inference strategy** (thinking + harness) | 低 | 快（秒-分钟） | 高 | **高** |

→ **后者通常 ROI 更高**——这是为什么 inference-time scaling 在 2024-2026 成为
产业焦点。改 inference strategy 比改 model 划算一个量级。

### 14.5 FDE 决策 Cheatsheet 的核心补充

当客户说 "**我们的 model 能力不够**"——之前你可能直接想 "post-training"。
这条洞察补一条**先问**：

```
"先问: inference-time strategy 用得对吗?"
   ├── Thinking 开了吗? (reasoning model 切换 / extended thinking)
   ├── Multi-turn dispatch 有吗? (agentic loop / harness)
   ├── Best-of-N + verifier 试过吗? (parallel sampling)
   └── 三种都试了仍不够 → 才考虑 post-training
```

**优先 inference-time strategy 而非 post-training**——便宜、快、可逆。这跟
你 harness 工作的判断完全一致：**先 harness，撑不住再 fine-tune**。

更新 §13.2 决策 framework：

```
痛点分类:
├── 行为可预测、规则明确 (rule-based)
│   → harness (permission / hooks / prompt)
│
├── 行为偏好、风格、价值观 (taste-based)
│   → post-training (SFT / RLHF / DPO)
│
├── 能力不够 (capability-bound)
│   → ★ 先试 inference-time scaling (thinking + harness + best-of-N)
│   → 撑不住 → reasoning model 切换
│   → 仍不够 → post-training (reasoning RL on own data)
│
├── 知识缺失 (knowledge-based)
│   → RAG / SFT
│
└── 安全 / 不可越线 (safety-critical)
    → harness 必须 + post-training 可加
```

### 14.6 三种 strategy 在 harness 里的具体落点

你的 harness 项目其实**已经在落实 inference-time scaling 的 agentic 那一支**：

| Strategy | Harness 里的对应 | Phase |
|---|---|---|
| **Sequential** (thinking) | 接 reasoning model 时支持 thinking stream / render / cost | Phase 5 / 6 |
| **Parallel** (best-of-N) | 多 candidate + verifier hook (D13.1 hook 扩展) | Phase 5+ |
| **Agentic** (multi-turn dispatch) | **你的核心 — run_query 循环** | ✅ Phase 2 done |

→ 你 Phase 2 已经 ship 的 agentic loop 就是 **test-time compute scaling 的 agentic strategy 的工程化基础设施**。这不是事后追认——是 framing §6 你 harness 在产业生态位置的具体兑现。

### 14.7 一句话

> **Thinking 和 harness 是 inference-time capability amplification 的两种形态——model 还是那个 model，但通过不同的"操作策略"调出了 model 潜伏的能力**。
>
> 业界叫这件事 **test-time compute scaling**——三种 strategy（sequential / parallel / agentic）都用 inference compute 换 capability。
>
> 这是 LLM 应用栈版本的 **「core 慢演进，shell 快演进」**——改 model 贵且慢，
> 改 inference strategy 便宜且快。**后者 ROI 通常更高**。
>
> 你的 harness 是 agentic strategy 的工程化基础设施。**这不是"应用层小事"——是
> 产业级 scaling 新维度的具体形态**。

---

## 一句话沉淀

> **LLM 应用栈分四层**：应用（harness）/ 接入（prompt）/ 模型（pretrain + post-training）/ 基础设施。FDE 的深度在应用层，对话面在模型层。
>
> **Pretrain 训世界知识，post-training 训行为对齐，harness 在运行时套 control surface**。三者层次嵌套——pretrain "会什么" → post-training "愿意做什么" → harness "在场景下能做什么"。
>
> **2026 三大产业事实**：base 已 commoditized / post-training 是主战场 / 1M context 是入场券。入场券之上真竞争在 reasoning / agentic / domain post-training / post-training 哲学——全部是 post-training + inference-time 工程。
>
> **Coding 是当下最关键的训练 sandbox**——独占三件事（天然长上下文 + deterministic verifier + 训出能力通用）。所有 frontier lab 都在重押。你的 harness 在这条产业飞轮上**双重身份**：既是 dogfood 的 product，也是 trace 的 source。
>
> **Reasoning 是 emergent，不是 inject**——RL 加 outcome reward + deterministic verifier 把 base model 里潜伏的 reasoning 能力**打捞**出来。DeepSeek-R1-Zero 证明这件事不需要 SFT。
>
> **Fine-tune 是 umbrella term，不是某个象限**——必须 disambiguate 4 维度（目标 / 数据 / 方法 / 起点）才有意义。FDE 听到客户说 fine-tune 第一动作是追问。
>
> ⭐ **元洞察（§14）**：**Thinking 和 harness 都是 inference-time capability amplification——model 还是那个 model，"操作策略"调出了潜伏能力**。这是产业级 scaling 新维度（test-time compute scaling），也是 LLM 应用栈版本的 "core 慢演进 + shell 快演进"。你 harness 项目是 **agentic strategy 的工程化基础设施**——不只是应用层工件，是新 scaling 维度的具体形态。
>
> 这套训练栈认知**跟 harness 同构**——5 件 RPC 配套在训练栈完全成立。**同一套 judgment framework 走过 LLM 应用栈的两段**——这是 [`phase-3-framing.md §8`](./phase-3-framing.md)「language as substrate, judgment as substance」在 LLM 应用栈下半段的具体兑现。

---

## 写完后的 checklist

- [x] 各章节都有"一句话"小结
- [x] 跟 phase-3-framing.md 双向 link
- [x] 表格密度跟 framing doc 同形态
- [x] FDE 视角的决策 framework 显式化
- [x] 关键术语词典覆盖今天聊过的全部概念
- [x] coding-as-training-ground 洞察沉淀
- [x] reasoning model 训练范式沉淀
- [x] dogfood flywheel 沉淀

**人看一遍后做的事**：

- [ ] §13.1 客户对话 cheatsheet 可以再补——你跟客户聊时撞到的新场景加进去
- [ ] §12 术语词典——读 Raschka 书 / 跑 nanoGPT 时遇到新术语加进去
- [ ] 完成 nanoGPT / Raschka / TRL 三段式后回来写 §15「实战体感」
