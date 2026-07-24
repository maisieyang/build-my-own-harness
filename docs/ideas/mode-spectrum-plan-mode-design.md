# 素材+设计 — 四点光谱:从默认对话到走开,plan 模式在 OH 的落地形状

> 2026-07-24,从三轮对话 + 一次官方文档调研(CC v2.1.200+ docs)沉淀。
> 上承 [goal-mode-acceptance-stack.md](./goal-mode-acceptance-stack.md)(验收栈)
> 与 [dialogue-2026-07-23-goal-mode.md](./dialogue-2026-07-23-goal-mode.md)(对话原液)。
> 双重身份:喂 Ch17(接触面:REPL 与 headless 的分工);同时是将来
> plan-mode 模块的设计前置(该模块不在 REFERENCE.md §5 拆分内,动手前
> 需回认知地图定位插点)。

## 〇、这份文档怎么来的(修正史,防止只留结论丢掉论证)

三轮推演,两次被证伪,各留一条判据:

1. **"plan 和默认模式的区别是 prompt"(作者初判)→ 证伪**。plan 模式
   的可信不靠 prompt(模型自律),靠权限硬闸 + 审批状态机(harness 拦截)。
   Boris"计划批准前不写一行代码"能当纪律引用,因为它是机制保证不是承诺。
   → 落进"两类模型说了不算"框架:只有 prompt 的 plan mode 是第一类都算
   不上的"模型自律";真 plan mode 是"LLM 提议 + harness 拦截"。
2. **"OH 的 plan 出口 = goal card 入队 autopilot"(Claude 提案)→ 作者
   否决,证伪成立**。该设计强迫每个计划付**上下文断绝税**:执行者若是
   另起炉灶的进程,计划必须自包含(陌生 agent 凭它能干活),这正是验收栈
   困难 1 的全部重量。而 plan 便宜的秘密恰恰是**计划可以欠上下文的债**
   ——只需人一屏能审,同 session 执行时对话里的全部理解还活在上下文窗口
   里。把中间站抬到和终点站一样贵,中间站就没有存在意义。
3. **"CC 的 /goal = 走开出口、上下文断绝"(Claude 光谱表 v1)→ 调研
   证伪**。CC 的 /goal 是同 session 跨 turn 条件循环,上下文连续;
   断绝的是 autopilot/routines 那一档。见 §二。

## 一、四点光谱(修正后定稿)

| 姿态 | 机制 | 出口工件 | 上下文 | 人的位置 |
|---|---|---|---|---|
| 默认对话 | 无 | 无(理解在人脑里) | 连续 | 循环内 |
| plan → 批准执行 | 权限钳 + 审批硬闸 | 计划(一屏、**依赖上下文**) | 连续(同 session 执行) | 批准一屏计划,松散在场 |
| /goal 条件循环 | 完成条件 + 独立小模型判官,跨 turn 自续 | 完成条件(判官读**本 session** transcript) | **连续**(同 session) | 走远但没走开,通知拉回 |
| autopilot / routines | 自包含卡入队,新进程执行 | goal card(**独立于上下文成立**) | **断绝** | 真走开,读判决 |

两条判据切出四段:

- **有没有可批准工件**:分开默认对话和 plan。讨论/探索/建品味的场合,
  强制产出计划工件是噪音——默认对话不被淘汰,是三态的"地面"。
- **工件是否须独立于上下文成立**:分开 2/3 点和第 4 点。plan 的计划和
  /goal 的完成条件都便宜,同一个原因——它们欠着上下文的债(计划靠消息
  历史里的理解补全;完成条件靠判官读本 session transcript 补全)。goal
  card 贵,因为它必须写到"陌生 agent 凭它独立干活"的标准——贵,换来的
  是人可以不在场。
- 这条判据是"跨 turn 边界"判据(2026-07-02 心智模型)的深层原因的
  **修正版**:跨 turn 边界切的是 2|3 之间,自包含切的是 3|4 之间,
  两条判据正交,共同定出四点。

## 二、CC 现状调研(v2.1.200+ 官方文档,2026-07-24)

行业锚点的机制事实,设计 OH plan 模式前必须知道的:

1. **进入 plan**:Shift+Tab 循环 / `/plan` 前缀 / `--permission-mode
   plan` 启动。文档口径:**纯权限模式切换,无 prompt 注入**——只读
   探索放行,编辑拦截。("三层解剖"在当前版本只剩两层:权限钳制 +
   审批状态机。)
2. **退出 plan:没有 `/execute` 命令,没有"执行模式"**。Claude 呈交
   计划时 harness 渲染审批菜单:"Yes, and use auto mode / Yes, manually
   approve edits / No, refine with Ultraplan / No, keep planning";
   Ctrl+G 可把计划丢进编辑器改完再批。**ExitPlanMode 工具已不存在**
   ——审批不走"模型调工具提议退出",是 harness 直接接管的 UI 硬闸,
   模型连提议退出的工具资格都没有("两类说了不算"第二类)。
3. **执行不是模式,是批准后落进某个权限模式的状态**:批准选项即落地
   模式选择(auto / default 手动批 / bypassPermissions)。自然语言
   "现在可以执行了"不能翻转模式——它只是促使模型收敛呈交计划,翻转
   仍只发生在菜单那一下。
4. **权限模式全集**:default / acceptEdits / plan / auto / dontAsk /
   bypassPermissions。
5. **/goal(2.1.139,2026-05-12)与 plan 正交**:session 级完成条件,
   每 turn 结束由小模型(Haiku)判断是否达成,未达成自动开下一 turn。
   同 session、上下文连续、可与 plan 组合(先 plan 批准,再
   `/goal all tests pass` 跑到绿)。

Sources:
- https://code.claude.com/docs/en/permission-modes.md
- https://code.claude.com/docs/en/goal.md

## 三、OH 落地设计(plan-mode 模块,v1)

### 现状盘点(2026-07-24 源码核对)

- 光谱两端已建成:REPL(`oh chat`,斜杠命令系统)= 第 1 点;
  `--verify`(L3)+ `--goal-condition`(L3′)+ L4 修复循环 ≈ CC /goal
  = 第 3 点;autopilot 队列 = 第 4 点。**缺的只有第 2 点(plan)**。
- 权限层机制齐备:规则引擎 deny > ask > allow,`accept_edits_preset()`
  已存在(`permissions/rules.py`),且已有设计立场"新姿态收编为规则
  预设而非新 PermissionMode 枚举值"——plan 模式在 OH 架构里的形状
  被这条立场直接决定。
- 真正的空白:**REPL 无模式状态机**——没有 mode 概念、没有审批闸、
  没有"批准→翻转→同 session 继续"的状态转移。

### 状态机(v1)

```
            /plan                     /execute [--accept-edits]
  默认  ─────────▶  plan 模式  ─────────────▶  执行(同 session)
   ▲                   │                            │
   │     /default      │                            │ turn 结束回落
   └───────────────────┘◀───────────────────────────┘
```

- **进入 `/plan`**:叠加 `plan_mode_preset()` deny 规则
  (`Edit(*)` / `Write(*)` / `Bash(*)`);只读工具照常;状态栏亮
  `mode=plan`(`format_status_bar` 现成)。
- **批准 `/execute`**:人敲命令即审批闸——不给模型 ExitPlanMode 类
  工具(CC 自己也演化到了这个形状,OH 的 REPL 里人敲的斜杠命令天然
  就是 harness-owned gate,连菜单 UI 都省了)。撤 deny 预设,按参数
  选落地模式:裸 `/execute` → DEFAULT(手动批);`--accept-edits` →
  `accept_edits_preset()`(Edit/Write 放行,Bash 仍拦)。计划不需注入
  ——它就在消息历史里,这是上下文连续的红利。
- **放弃 `/default`**:撤姿态回地面,计划文本留在对话里当理解沉淀。

### 锁定的旋钮

| 旋钮 | 决定 | 理由 |
|---|---|---|
| 批准由谁发起 | 人敲 `/execute`,模型无退出工具 | 权力划分最干净;CC 已收敛到同形状 |
| 执行完权限落到哪 | turn 结束回落 DEFAULT | 批准的授权范围 = 这份计划;授权不跨事延伸 |
| 落地模式选择 | `/execute` 的参数 | 抄 CC 审批菜单"选项即落地模式"的细节 |
| plan 出口默认去向 | 同 session 执行;goal card 导出仅作可选出口 | §〇 修正 2:不强迫计划付上下文断绝税 |

### 留给 v2 的已知简化

- **Bash 整拒**:plan 模式里探索靠 Read/Grep;"只读 Bash 命令分类
  放行"(CC 的 `useAutoModeDuringPlan`)是 CC 花大力气的地方,v1 不啃。
- **planning prompt 注入**:CC 文档口径无此层,但 v1 可注一段轻姿态
  prompt(收敛快)——注意它是姿态不是契约,契约只在 deny 预设。
- **Ctrl+G 计划外置编辑**:好细节,v1 不做。

### 依赖插点(按 module loop,动手前回 REFERENCE.md 定位)

依赖 permissions(已完成)+ REPL 模式状态(**新**)+ 斜杠命令系统
(已完成)。上游 OpenHarness 无此模块——属于 §5 之外的新增,需先在
认知地图上补位再动手。

## 四、论点候选(一句话 thesis)

- 执行不是一个模式,是批准后落进某个权限模式的状态——"批准选项即
  落地模式"是审批闸和权限梯子的接头。
- plan 和 /goal 便宜、goal card 贵,是同一条判据的两面:工件欠不欠
  上下文的债。敢让工件欠债,人就得留在附近;要人走开,就得先替工件
  还清债。
- 审批闸的演化方向是权力越收越紧:从"模型调 ExitPlanMode 提议退出"
  到"模型连提议工具都没有,harness 直接渲染菜单"——接触面越简单,
  权力划分越严格。
