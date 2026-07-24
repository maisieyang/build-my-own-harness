# 素材 — 从"点 yes 的焦虑"到"走开的底气":goal 模式与验收栈

> 2026-07-23,从一场"用户先说体感、再对照行业动向"的对话沉淀。喂
> Ch17(接触面:REPL 与 headless 的分工)、Ch9/Ch10(eval/judge:
> 走开的地基)、Ch19(前沿)。成文归作者;本文件只存一手案例、
> 数据、论点 bullet。
> 待补的一手数据:B3 dogfood 五实验(tasks/dogfood-b3-goal-condition.md)
> 的判官/人工金标分歧记录——落地后本包从观点升级为实证。

## 〇、开篇钩子(用户原话,可直接当章首)

> "当我没有用 /goal、默认还是 REPL 的方式的时候,AI 生成的速度远远
> 超过了我阅读的速度,实际就是我只是傻傻的在等着点 yes……我会点出
> 焦虑。这个过程是 AI 在领导,我在追随,也追不上。"

命名:**同步监督范式的死亡信号**。REPL 隐含前提 = 人的阅读速度是流水
线时钟;生成超过阅读后,"监督"既不产生理解也不产生控制,只剩仪式性
把关。焦虑是体感先于话语探到范式边界。
X-thread 钩子版:"我用 AI 编程点 yes 点出了焦虑,后来发现焦虑是对的,
错的是那个按钮。"

## 一、瓶颈迁移史(一张表)

| 代际 | 交互单位 | 人的位置 |
|---|---|---|
| Copilot | token/行 | 作者(AI 是输入法) |
| Chat/REPL | 轮次 | 循环内审查者(焦虑发生地) |
| Plan mode | 计划 | 前置对齐者 |
| /goal | 目标+验收条件 | 边界上的人:定义与验收 |

- 行业定名:Karpathy 2026 宣布 vibe coding 对专业场景过时,改提
  **Agentic Engineering**;spec-driven 阵营论点:瓶颈从"写代码"迁移到
  "信任你没读过的代码"
- 解法不是读得更快,是**换审查对象**:事前审 spec/plan(几百字),
  事后审判决+证据(一屏),永远不审生成流(无界)
- 判据一句话:**你审查的工件必须是人类带宽尺寸的**。盯 token 滚屏
  = 该任务欠一个 plan 或欠一个 gate;盯梢是设计缺陷的症状,不是敬业

## 二、CC 长程设计的三根柱子(人退出循环后,边界凭什么站得住)

1. **可逆**(checkpoints/rewind):信任不靠"它不会错",靠"错了很便宜"
2. **可审**(plan mode):把人的审查从产出(代码,无界)挪到意图
   (计划,一屏);Boris 纪律:"计划批准前不写一行代码"
3. **可验**(/goal 独立 checker + hooks):完成条件是可执行工件,
   checker 跑真实命令读输出;机器盯机器,人只读判决

辅助机制:并行(subagents/background/routines)、渐进信任(权限模式
阶梯:default → acceptEdits → 全自动,信任是旋钮不是开关)。

## 三、收敛证据(书的 thesis 弹药:harness 设计的必然性)

- CC `/goal` 2.1.139(2026-05-12)上线:"描述可验证终态而非指令清单,
  独立 checker 模型确认"——**独立 checker、不信模型自评 = OH 的
  "门不信自评"设计决策**(L3/L3′,loop-runtime plan 2026-06)
- 两边独立收敛到同一形状:验证闸的二元性(确定性 exit code vs LLM
  裁判)在 OH 里是 `--verify` vs `--goal-condition`,在 CC 里是
  hooks/tests vs goal checker
- 叙事弧线(简历/面试同款):体感焦虑 → 亲手建三把椅子(L3/L3′/L4/
  autopilot)→ 行业同月收敛(CC /goal)。体感 → 构建 → 印证

## 四、从业者实录(2026-07 检索)

- **Boris Cherny**(CC 作者):终端 5 个并行实例(5 个 git checkout,
  标签编号)+ 浏览器 5-10 个会话;系统通知告知何时需要人;CLAUDE.md
  当制度记忆;/commit-push-pr 日跑几十次;Opus + extended thinking,
  "正确性优先于速度"
- 关键读法:**并行的第一作用不是吞吐,是戒断盯梢的强制机制**——5 路
  并行物理上不可能盯任何一路;控制流反转:不是人轮询 agent,是 agent
  中断人
- **Codex 团队**:新人 onboarding buddy(装机/讲库/首日出活);非工程
  团队做内部应用;AGENTS.md 同模式;周活 500 万+
- **Anthropic agent 团队制 CR**:多 AI 审查者并行审 PR,人读发现清单
  不读 diff——验收三件套里的 CR 也在"人读判决不读流"化

## 五、四个搬家的困难(逆风面——章的下半场,防啦啦队化)

1. **验收标准的编写是新的编程**。"可验证终态"最重的词是"清楚":合取
   条件做一半判什么(E2)、无事实基准的条件怎么判(E3)。委派的
   阿姆达尔定律:不可委派残余(定义+验收)成为新瓶颈,且比看上去难
2. **验证不对称性**。测试/类型/lint 一秒判;架构/安全/品味比写还贵。
   goal 模式的可用边界 = 便宜验证的边界。**eval 是把贵验证批量转化为
   便宜验证的机器**——七个决策面每建一面,敢走开的领域多一块
3. **理解债**。不读代码,写 goal 和验收的品味会折旧;解法不是回去盯流,
   是**把理解当预算分配**:在复利处深读(用户实例:harness 产出不逐行
   读,eval 链逐行读了三个下午),其余读判决。人的理解从计划和验收两端
   进入,不从生成流进入
4. **责任不可委派 → 信任本身需要被验证**。信任栈:人类金标(小而贵)
   → 元评估校准判官(verify_judge,2026-07-23,8/8+抗注入)→ 判官守门
   → agent 干活。人退到栈顶,接触面缩小,但每层被下层显式校准。行业
   大谈 checker,少有人谈 **checker 的 eval**——OH 在这层领先于话语
   (commit 6820085 为证)

## 六、论点候选(一句话 thesis)

- "敢不敢走开"最终是个 eval 问题
- coding 让给 AI 之后,人剩下的两件事恰好是最难自动化的两件事:把话
  说清楚(定义),和知道什么算好(验收)
- REPL 没死,它退守为理解模式:探索、品味域、以对话为产品的场景;
  写代码归 goal 模式,建立理解归对话模式——四层语法框架的运行时版本
- 理解不该从看 diff 来,该从对话、dogfood 和读判决来

## 七、Sources

- https://explainx.ai/blog/claude-code-goal-command-long-running-agents-2026
- https://www.anthropic.com/news/enabling-claude-code-to-work-more-autonomously
- https://mindwiredai.com/2026/04/14/claude-code-creator-workflow-boris-cherny/
- https://explainx.ai/blog/boris-cherny-steps-ai-adoption-claude-code-july-2026
- https://openai.com/index/codex-for-every-role-tool-workflow/
- https://newsletter.eng-leadership.com/p/how-openais-codex-team-works-and
- https://www.augmentcode.com/guides/vibe-coding-vs-spec-driven-development
- https://www.infoq.com/news/2026/04/claude-code-review/
- https://www.mindstudio.ai/blog/claude-code-auto-mode-goal-routines-autonomous-agents-2
