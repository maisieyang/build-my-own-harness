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

### 状态机(v1,follow CC:审批 = harness 渲染的菜单,非用户命令)

```
            /plan
  默认  ─────────▶  plan 模式(deny 钳制,只读探索)
   ▲                   │
   │                   │ assistant turn 结束(计划呈交)
   │                   ▼
   │           ┌─ 审批菜单(harness 渲染)─────────────┐
   │           │ [1] 批准,手动批边改(落 DEFAULT)      │──▶ 执行 turn ──┐
   │           │ [2] 批准,acceptEdits                 │──▶ 执行 turn ──┤
   │           │ [3] 继续规划(留在 plan)              │                │
   │           │ [4] 放弃,回默认                      │                │ turn 结束
   │           └──────────────────────────────────────┘                │ 回落
   └───────────────────────────────────────────────────────────────────┘
```

- **进入 `/plan`**:叠加 `plan_mode_preset()` deny 规则
  (`Edit(*)` / `Write(*)` / `Bash(*)`);只读工具照常;状态栏亮
  `mode=plan`(`format_status_bar` 现成)。
- **审批菜单**:plan 模式下每个 assistant turn 结束,harness 渲染
  四选项菜单(交互 prompt 有 D44 interactive-bash-ask 先例)。批准
  选项即落地模式选择(follow CC"选项即落地模式");选批准后 harness
  注入一条 canned 批准消息自动发起执行 turn。模型无任何退出类工具
  (CC 已移除 ExitPlanMode,审批是 harness 直接接管的 UI 硬闸)。
  计划不需注入——它就在消息历史里,上下文连续的红利。
- **v1 相对 CC 的一处声明简化**:CC 在"计划呈交时"弹菜单(需识别
  计划完成);v1 在 plan 模式**每个** turn 结束弹菜单,"继续规划"
  选项兜住模型还在追问/探索的情形——简单、可预测,识别"计划已呈交"
  留给 v2。

### 锁定的旋钮

| 旋钮 | 决定 | 理由 |
|---|---|---|
| 批准由谁发起 | harness 在 turn 结束渲染审批菜单;模型无退出工具 | follow CC(菜单硬闸);模型连提议退出的资格都没有 |
| 落地模式选择 | 菜单批准选项即落地模式(手动批 DEFAULT / acceptEdits) | follow CC"选项即落地模式" |
| 批准后如何启动执行 | harness 注入 canned 批准消息,自动发起执行 turn | 批准即执行;只翻权限干等输入是半吊子批准 |
| 执行完权限落到哪 | turn 结束回落 DEFAULT | 批准的授权范围 = 这份计划;授权不跨事延伸 |
| plan 出口默认去向 | 同 session 执行;goal card 导出仅作可选出口 | §〇 修正 2:不强迫计划付上下文断绝税 |

### 留给 v2 的已知简化

- **Bash 整拒**:plan 模式里探索靠 Read/Grep;"只读 Bash 命令分类
  放行"(CC 的 `useAutoModeDuringPlan`)是 CC 花大力气的地方,v1 不啃。
- **planning prompt 注入**:CC 文档口径无此层,但 v1 可注一段轻姿态
  prompt(收敛快)——注意它是姿态不是契约,契约只在 deny 预设。
- **Ctrl+G 计划外置编辑**:好细节,v1 不做。

### 定位插点(对着 REFERENCE.md 做的盘点,2026-07-24)

**地位声明**(沿 loop-runtime-plan.md 先例):REFERENCE.md §1-§5 整份
冻结,地图身份是逆向上游 v0.1.9——上游无 plan mode,写进 §5 会污染
参照系,所以**不动地图,定位落在本文档**。plan-mode 不是 §5 的第 18
号模块,也不是 ohmo/autopilot 那类"harness 之上的应用"(§5 覆盖核对
的除外先例)——它和 loop-runtime L2 同类:**已建成模块的跨要素扩展**,
capability 级新 epic 的一部分。认知来源:本文档 §一/§二(CC 对标)。

**九要素盘点**(loop-runtime §1 同款表法):

| 九要素 | 现状 | plan-mode 要它干什么 | 差距 |
|---|---|---|---|
| §3.5 安全边界(模块5 ✅) | 规则引擎 deny>ask>allow + `accept_edits_preset()` | plan 进入时的只读钳制;批准后的落地模式 | **扩展**:一个 `plan_mode_preset()` deny 预设——"新姿态收编为规则预设而非新枚举值"立场的第二次应用 |
| §3.9 接触面(模块4 + repl-ux D42/D43 ✅) | `oh chat` REPL + 斜杠命令系统(Phase 5b/18)+ `format_status_bar` + 交互 prompt(D44 先例) | `/plan` 进入;turn 结束渲染审批菜单;状态栏亮 mode | **REPL 模式状态机 + 审批菜单——唯一真正的新面**(mode 状态 + 菜单四选项的转移) |
| §3.1 循环 / §3.3 工具 | ✅ | 同 session 继续执行;计划靠消息历史自然在场 | 零改动(上下文连续的红利:engine 对 plan-mode 无感知) |
| §3.2/3.4/3.6/3.7/3.8 | — | 不触碰 | — |

**依赖**:模块5(permissions)✅ + 模块4/repl-ux ✅ + 斜杠命令 ✅
——无未建成的前置,**当下可建**。

**与 loop-runtime L 系列的关系**:正交且互补——L 系列(L1-L4/autopilot)
是无头侧的三把椅子,plan-mode 是交互侧 REPL 的审批闸;对回 §一光谱:
plan-mode = 第 2 点,L3′/L4 ≈ 第 3 点,autopilot = 第 4 点。可组合
(先 plan 批准,再交给条件循环跑到绿——CC 的 plan+/goal 组合同款)。

## 四、论点候选(一句话 thesis)

- 执行不是一个模式,是批准后落进某个权限模式的状态——"批准选项即
  落地模式"是审批闸和权限梯子的接头。
- plan 和 /goal 便宜、goal card 贵,是同一条判据的两面:工件欠不欠
  上下文的债。敢让工件欠债,人就得留在附近;要人走开,就得先替工件
  还清债。
- 审批闸的演化方向是权力越收越紧:从"模型调 ExitPlanMode 提议退出"
  到"模型连提议工具都没有,harness 直接渲染菜单"——接触面越简单,
  权力划分越严格。

## 五、Dogfood 后追加(2026-07-24,plan-mode v1 落库 + 作者亲手 /goal
跑完一个任务之后的对话增量)

### 5.1 两种 approve:意图门 vs 动作门

作者体感:"approve 的次数少了,可是还是在 approve"——执行中弹一次
yes,不批就卡住,人还是走不开。解剖:那次 approve 和批准计划的 approve
**不是同一个物种**。

- **意图门**:批"要做什么"(plan 菜单 / goal 定义)。不可消除——它就
  是不可委派残余本身(§goal-mode-acceptance-stack 困难 4)。
- **动作门**:批"这个具体动作敢不敢放行"(权限 ASK)。人看到的是一条
  孤零零的命令,没有意图上下文,判断质量低——这是**动作粒度上的
  "仪式性把关"**,旧政权的最后一块飞地。

排干动作门的三件套,全是"让这次人工判断不必再发生":
1. **Allow 规则 = 把这次 approve 冻结**——dogfood→eval 飞轮在权限层
   的同构版本:每次被问 yes,就是一条还没冻结的规则(D44 预留的
   "per-project 持久化——等触发",亲手 dogfood 即触发);
2. **Sandbox = 让 approve 不必要**(爆炸半径封顶;ASK 授权≠隔离);
3. **可逆性 = 让 approve 不值得**(错了很便宜,问的必要性趋零)。

**终点形态:不是零次 approve,是每任务常数两次**——开头批一张意图,
结尾收一次判决。次数从 O(动作数) 降到 O(1),且每次都发生在人类带宽
尺寸的工件上。动作门没排干的地方 = 注意力还没完全赎回的地方。

### 5.2 重喂 vs 续跑:同一个裁判,两种循环,两个产品形态

源码核对(semantic_gate.py / repair.py):OH 的 `--goal-condition` + L4
已完整实现"运动员/裁判分离"——判官是全新调用(tools_disabled,无手脚)、
不同 prompt、读 untrusted transcript(定界符 + 防注入声明:**运动员
不能贿赂裁判**,B3 抗注入 eval 为证)、fail-closed(裁判缺席即判负),
外加行业少有的**裁判的裁判**(verify_judge 元评估)。

但循环形态与 CC /goal 有真实差异:

| | CC `/goal` | OH `--goal-condition` + L4 |
|---|---|---|
| 循环单位 | turn(同 session 续跑,每 turn 判) | attempt(每次新 headless run) |
| 上下文 | 完整延续 | attempt 间断绝,靠 repair prompt 压缩传递 |
| 形态 | **续跑式**(continue) | **重喂式**(re-feed) |

光谱定位自我修正:OH 的 goal-condition 机制实际站在第 3、4 点**之间
偏 4**——第 3 点(session 内条件循环)在 OH 是空位。补位是 D48 尺寸:
plan-mode 已把地基全部打绿(turn 结束挂点 = 审批菜单同位置、
`pending_input` 自动续 turn 机制、`run_semantic_verification` 裁判现成、
`ChatMode` session 态先例)。

### 5.3 /goal 设计初衷的三层(从机制形状反推,非官方口径)

- **表层**:自动续 turn——省掉人敲 continue 的手;
- **中层**:运动员/裁判分离——**没收模型的自评停止权**(Stop hook 的
  实现选择泄露了靶子:不是给模型加马力,是没收它的刹车);它自动化的
  不是"干活的人",是"催活的人"(人一直在充当循环的续接函数);
- **底层**:对人类注意力的再定价——定义和判断是稀缺资产,续接和盯梢
  是可自动化的廉价劳动,产品职责是把前者从后者里剥离。

**结构关系:不是并列的两个原因,是目的/手段**——注意力再分配是目的,
裁判分离是让它安全的手段。没有裁判的走开是盲信;有了被元评估校准过的
裁判,走开才是委派而不是弃权。**用一个信任机制赎回一种注意力配置。**

另两条从内部体会的机制观察(Ch17 素材):
- goal 条件文本在执行侧不只是判据,是**行为的引力场**——全程在场,
  每步向它收敛;条件写得好坏直接塑造执行质量,不只塑造判定质量。
  goal 指向一份 boundary doc(decisions/47)而非散文,是本次 /goal 跑
  得稳的真实原因之一;
- /goal 接口"描述可验证终态而非指令清单"是**对人的强制函数**——写不出
  可验证条件的任务在入口处即暴露;工具的接口在训练它的用户("从持续
  对齐到先想清楚再对齐"的 mindset 迁移不是副作用,是初衷的一半)。
