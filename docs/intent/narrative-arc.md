# Intent — 三层弧线叙事(README + PLAYBOOK)

> 由 interview-me 收敛,2026-06-15。承接 [[playbook-rebuild]] 之后的下一步。

## 确认意图(最终版)

把 README + PLAYBOOK 从"我重建了一个 harness"升级成**完整三层弧线**,用**敢于不一样、笃定、有锋芒**的观点声音写。

- **Outcome**:补回缺的两层(`my-skills` 定制方法论/工作流 + `finance-skills` 垂直落地)+ Anthropic 洞见,串成 model→harness→skill→垂直/商业化 的完整弧线。
- **User**:今天 = DeepSeek **eng** 申请读者,但**不为岗位裁剪观点**;本质 = 任何会被"独特洞见 + 真热爱"打动的人。
- **Why now**:今天 DeepSeek eng 要投出去;现有 README/PLAYBOOK 只截了第一层 harness,漏了最值钱的"基座→方法论→垂直"完整思考 + 一手实践。
- **Success**:读者记住的不是"又一个重建 harness 的人",而是"这人对怎么 build、怎么进垂直**有自己的、敢讲的观点,而且真做出来了**"。
- **核心原则(作者立的)**:面试是综合、不可预测的;**60 分(能力/artifact)是地板,地板之上「敢于不一样」是天花板**。别为猜中面试官去优化,把独特的东西笃定放出来。
- **Constraint**:不阉割观点迎合岗位;每个大主张底下有真做出来的东西兜底(60 分地板);今天投得出去。
- **Out of scope**:FDE 专门重定位叙事(见下方 todo,另起);商业化/Anthropic 洞见今天**可进**文档作为真实观点,但不展开成产品论文。

## 分工

- **PLAYBOOK** = 观点中心(它本来就是信念文档)。
- **README** = `How it was built` 之后补一段,把 my-skills + finance-skills 作为"基座之上长出来的两层"链出去(finance 对 eng 框成"架构泛化的工程证据")。

## 叙事脊梁(两根,合一)

**① 方法是分形的(fractal)**——study 最好的参照 → 从零 rebuild → 蒸馏原则,在平台层和垂直层各跑通一遍:

```
harness:   研究 OpenHarness        → 造 build-my-own-harness → PLAYBOOK
finance:   研究 Anthropic 金融服务 → 造 mybank-credit-risk   → finance-skills/PLAYBOOK
```

**② 概念阶梯(centerpiece 暴论)——`tool → skill → plugin`**:
- `tool` = 原子能力(BaseTool,LLM 的 syscall)
- `skill` = tool 的 **lazy load**(按需展开的能力)
- `plugin` = **商业原语** = skill 之上叠 **版本控制 + 权限管理 + 商业化**

这条阶梯 = 三个 repo 的映射(harness/my-skills/finance)= Anthropic `model+harness+plugin` 进高 ACV 垂直理论的拆解。**作者三级都亲手建过,还把 plugin 跑在自己 harness 上(dual-format PluginLoader,phase-19 dogfood,`learnings/phase-19.md`)。** finance 主线 = "用 plugin 验证 Anthropic 进垂直理论";earned 洞见 = "plugin 本质还是 tool,值钱的是它的业务含义"。FDE-不是所爱 = 一句旁白。

核心主张:**"技术原语怎么一级级长成商业原语——大多数人讲不清,我能,因为每一级都造过。"**

## 三个 repo(arc 锚点,链进去)

- 基座 [build-my-own-harness](https://github.com/maisieyang/build-my-own-harness) — 从零重建生产级 harness(对标 OpenHarness)
- 方法论 [my-skills](https://github.com/maisieyang/my-skills) — solo + AI 工作流,fork agent-skills 只编码基石没有的
- 垂直 [finance-skills](https://github.com/maisieyang/finance-skills) — 同一套打法进金融:研究 Anthropic `financial-services` 当参照,从零造 [`mybank-credit-risk`](https://github.com/maisieyang/finance-skills/tree/main/mybank-credit-risk),核心零改动;自身也是完整 study→build→distill

---

## TODO(改天单独做)— FDE 重定位叙事

把这条三层弧线**重新定位**成 FDE / forward-deployed 的能力证明:从"会重建 harness 的工程师"→"理解 model→harness→垂直→商业化 整条链路、且能把平台带进一个垂直客户跑通商业价值的人"。这恰好是 FDE 的核心能力。**另起一份 FDE-facing 叙事**,深挖商业化 / Anthropic B 端商业模式(`anthropic-anaysize.md`),申请 forward-deployed 类岗时用。今天不碰。
