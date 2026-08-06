# 审批不该消失，而应该从循环中间搬到两端

> 从 `/goal`、Claude Code 与 Codex 出发，重新设计无人阶段的权限系统。

## 我以为 `/goal` 已经让 agent 异步了

我给自己的 harness 实现 `/goal` 时，解决的是一个非常具体的问题：过去每一轮
assistant 回复结束后，都要由人来决定下一步。

模型说“我已经改完了”，我得看一眼；模型说“接下来应该跑测试”，我得再发一句
“开始测试”。即使真正的工作都由模型完成，**turn 与 turn 之间的接力棒仍然握在
人手里**。

`/goal` 把这件事机器化了。用户不再逐轮下指令，而是先写一个完成条件：

```text
/goal 完成认证模块升级；相关测试通过；不修改无关测试；最多运行 20 turns
```

主模型每轮结束后，一个独立判官根据 transcript 判断条件是否已经成立：

```text
未成立 → 把理由反馈给主模型 → 自动开始下一 turn
已成立 → 清除 goal → 响铃并交还控制权
```

这一步同时替掉了人原来承担的两个动作：**接下一轮**，以及**判断任务是否已经
完成**。我第一次 dogfood 时，以为这就意味着 agent 可以独立工作了。

然后它停在了一次 permission 上。

不是模型不知道怎么做，也不是判官不会判断，而是模型想运行一条会改变系统状态的
Bash 命令。权限层给出 `ASK`，但此时我已经不在电脑前。一个以“人会当场回答”为前提
设计的状态，进入无人循环以后只剩两个坏结局：要么阻塞等人，要么退化成拒绝。

我这才意识到：`/goal` 只拿走了一根接力棒。另一根还在我手里。

## 真正的 async 要移走两个人工接力点

在同步交互里，人至少持续承担两种职责：

| 人工接力点 | 人每一轮在做什么 | 机器需要的替代物 |
|---|---|---|
| Turn 接力 | 决定是否继续、下一轮追什么 | Goal condition + 独立判官 |
| Permission 接力 | 决定某个动作能不能执行 | 事前批准的权限范围 + 运行时强制 + 超出范围时的处理 |

这里的“权限范围”，是用户在 goal 开始前一次性划定的活动边界：哪些目录可以写、
是否允许联网、可以访问哪些域名，以及哪些动作始终不允许自动执行。后文把这份与
goal 绑定、结束后自动失效的权限范围称为 permission profile。

第一种判断是“工作是否完成”，第二种判断是“动作是否被授权”。它们经常同时出现在
一个 UI 里，所以很容易被混成“agent 还不够自动”。但机制上，它们是两套独立的
控制环。

Claude Code 的官方文档也明确把二者拆开：`/goal` 用一个新模型决定是否开始下一
turn；Auto mode 则负责减少每个 turn 内的工具审批。`/goal` 本身不会改变 permissions，
两者是互补关系。[Claude Code: Goals](https://code.claude.com/docs/en/goal)

这给了我一个更准确的完成定义：

> 一个 agent 只有在不需要人逐轮接 turn、也不需要人逐次接 permission 时，才真正从
> sync 进入了人的注意力意义上的 async。

问题随之变成：permission 要怎样异步化？

## 为什么 `ASK` 在无人场景必然失效

`ASK` 不是一种安全边界，而是一项协议：系统暂时不做决定，把决定权移交给一个此刻
在线的人。

它有三个隐含前提：

1. 人在场；
2. 人理解即将发生的副作用；
3. 等待人的成本可以接受。

同步 REPL 满足这些前提。异步 goal loop 一个也不满足。于是最直觉的修复通常是：

```text
无人模式下，把 ASK 当成 ALLOW。
```

我回头读代码确认过：`oh ask` 和 `oh chat` 都已经实现了 `--auto`。CLI 会把它转换成
`PermissionMode.AUTO`；引擎遇到 `ASK` 时不再返回错误，而是直接继续执行。没有带
`--auto` 时，目前也不会真的弹出审批框，而是返回一条“需要确认，请使用 --auto”的
permission error。对应实现见 [`cli.py`](../../src/openharness/cli.py) 和
[`engine/query.py`](../../src/openharness/engine/query.py)。

所以，`--auto` 不是尚未实现的设想，但它也不是一个会逐次分析动作的自动审批器。
它的实际语义是：用户在启动时预先信任所有最终落到 `ASK` 的动作。它消除了这类动作
对当前运行的阻断，却没有让授权判断变得更精确，只是把“谁来逐次批准”改成了“整类
动作事前放行”。

Write 之类的结构化工具还勉强能这么做，因为参数直接暴露目标路径：系统知道它准备
写哪里。Bash 不同。它是一条通用计算通道：变量、重定向、子进程、包装器和解释器
都能把真实副作用藏在字符串后面。

因此，permission 的困难不只是“规则还不够多”，而是**有些副作用根本无法从调用
参数中可靠地事前还原**。静态预测 Bash 永远会在两个坏结果之间摇摆：保守到频繁
打断，或者宽松到留下敞口。

异步化 permission 不能只问“怎么少弹窗”，而要回到风险本身。

## Claude Code 与 Codex 在优化不同的风险变量

可以把 agent 的预期风险粗略拆成两个变量：

```text
预期风险 ≈ 危险动作被错误放行的概率 × 错误动作能够造成的影响范围
```

第一项是判断质量，第二项是 blast radius。Claude Code 与 Codex 今天都使用规则、
分类器和隔离，但如果看它们各自的主路径，优先级不同。

### Claude Code：先提高判断质量

Claude Code 的 Auto mode 会让一个独立 classifier 在动作执行前判断：这个动作是否
超出用户请求、是否指向未识别的基础设施、是否可能受恶意内容驱动。显式 deny/ask/
allow 规则仍在 classifier 前生效；未被规则和内置安全分类解决的动作才进入模型判断。
连续阻断达到阈值后，Auto mode 会回退，而不是无限重试。

它主要回答：

> 这个动作是否符合用户已经表达的意图？

这种方案能做超出命令前缀匹配的语义判断：一次 push 是否把秘密带到公开仓库、一个
部署目标是否属于生产环境、删除的是本轮创建的临时产物还是既有数据。代价是它仍是
概率判断，官方也明确说明 Auto mode 不构成安全保证。
[Claude Code: Permission modes](https://code.claude.com/docs/en/permission-modes)

如果没有 sandbox 等强制边界，classifier 一次错误放行后，命令会直接使用 harness
进程原本拥有的文件、凭据和网络权限运行。Classifier 可以减少误判，却不会自动缩小
误判发生后的影响范围。换句话说，它主要在降低**危险动作被错误放行的概率**。

### Codex：先限制错误后的影响范围

Codex 的主路径先确定一个 OS 强制的执行边界：哪些文件可读、哪些目录可写、网络能否
访问。workspace 内的常规操作不需要逐个审批；命令只有在请求越过边界时才产生
越界请求。macOS 使用 Seatbelt，Linux 路径使用 bubblewrap/seccomp 等机制，平台
无法执行所选策略时会拒绝，而不是静默退回无隔离执行。

它首先回答：

> 无论模型判断对错，这个进程在物理上最多能碰到什么？

当前 Codex 同样有模型审批：Auto-review 可以把本来要交给人的越界审批请求转给一个
独立 reviewer。但官方对它的定义很关键——这是 sandbox 边界上的 **reviewer swap**，
不是取消 sandbox，也不是让 reviewer 永久扩大边界。边界内动作不触发 reviewer；只有
出界请求才触发。[Codex: Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)

所以，更准确的对照不是“Claude Code 有 LLM 审批官、Codex 没有”，而是：

| | Claude Code Auto 的主问题 | Codex sandbox + Auto-review 的主问题 |
|---|---|---|
| 分类器站位 | 大量未预批准动作的执行前 | 已经撞到强制边界的越界请求上 |
| 首要信任 | 对用户意图的概率理解 | OS 执行边界 |
| 判断错后的默认后果 | 取决于宿主机实际权限 | 通常先被限制在已批准边界内 |
| 降低的主要变量 | 危险动作被放行的概率 | 错误动作的影响范围 |
| 模型成本 | 未被确定性规则解决的动作 | 只有边界例外 |

Claude Code 也能启用 sandbox，Codex 也使用 reviewer。两者全部打开以后会逐渐趋同。
这里比较的是两条设计路线的重心，而不是给产品贴一个永远不变的标签。

## 我的选择：先把最坏后果限制住

对我的 harness 来说，选择 Codex 路线不是因为 classifier 没用，而是因为我的问题发生
在无人阶段。

人在场时，误放一次 permission 还能在下一秒按 Ctrl+C。人离开后，错误可能继续沿着
后续 turns 放大。此时首先需要的不是一个更自信的 yes/no，而是让任何 yes/no 的最坏
后果都有上限。

因此我选择的结构是：

```text
goal-scoped permission profile
        + SandboxExecution
        + boundary-only auto-review
        + typed park/resume
```

四部分分别回答四个问题：

1. **Profile**：这个 goal 事前获得了哪些能力？
2. **SandboxExecution**：这些能力怎样成为不可绕过的运行时事实？
3. **Auto-review**：偶发的必要越界由谁处理？
4. **Park/resume**：机器无法处理时，怎样把同步弹窗改成异步交接？

## 先把每个动作压缩成三种结果

这四个组件最终要共同守住一条简单的不变量：**每个模型可控副作用只能落入三类——
边界内执行、精确扩大一次边界，或者不执行。**

完整状态机是：

```text
模型提出动作
    ↓
在已验证 boundary 内？
    ├── 是 → 直接执行，零 reviewer
    └── 否 → 能否描述最小、精确、一次性的 permission delta？
              ├── 能 → reviewer / 人审核
              │         ├── 批准 → 精确重试一次
              │         ├── 拒绝 → 不执行
              │         └── 无法决定 → park
              └── 不能 → 拒绝或 park
```

这里尤其不能存在一条未经分类的第四路径，例如 sandbox 不可用时静默退回宿主执行，
或者 reviewer 说“看起来安全”就绕过运行时边界。边界内动作不需要 reviewer；只有已经
撞到边界、而且能够表达成最小精确增量的例外，才有资格被审核。

这条不变量不是为了保证模型永远不犯错。那是不现实的。它真正保护的是：**模型犯错
时，任何实际副作用仍然有一个明确且不可绕过的授权来源。**

允许执行的理由只能是两种：运行时边界已经约束住它，或者它获得了一个精确且一次性
的例外授权。否则就不执行。

这也是 permission 从 sync 走向 async 的关键：不是让机器更大胆地替人批准，而是让
无人阶段的每个副作用都处在可以机械验证的边界内；只有真正需要改变边界时，才把
控制权异步交还给人。

## Profile：把授权从每个动作搬到 goal 开始前

一个 goal 不应该继承无限期、全局的 `Bash(*)`。它应该在设立时绑定一份有生命周期的
permission profile，例如：

```yaml
goal: upgrade-auth-module

filesystem:
  read:
    - workspace
    - minimal-runtime
  write:
    - workspace
    - goal-temp-dir
  deny:
    - workspace/.git
    - workspace/.codex
    - "**/.env"
    - ~/.ssh

network:
  enabled: true
  allow:
    - pypi.org
    - files.pythonhosted.org

hard_deny:
  - git commit
  - git push
  - destructive git reset
  - production deployment

lifetime:
  until_goal_ends: true
```

模型可以根据 goal 提议这份包络，但不能让它自行生效。合法流程是：模型提议，harness
收窄，用户在同步阶段确认一次。授权的对象不是一串模型猜出来的命令，而是一组具体
能力：可写目录、网络目的地、socket、受保护路径和不可覆盖红线。

这与 goal condition 是同构的：

| 用户事前声明 | 无人阶段的机器依据 |
|---|---|
| 什么算完成 | Goal condition + judge |
| 什么算有权执行 | Permission profile + runtime enforcement |

两者都把原本散落在循环中间的人类判断，前置到启动阶段。

## SandboxExecution：让 Profile 不只是愿望

如果 profile 说“只能写 workspace”，Bash 却仍以宿主机用户权限运行，这份 profile
只是提示词。`SandboxExecution` 必须把它翻译成真实执行环境：

- workspace 挂载为可写；
- `.git`、策略目录和 secrets 挂载为只读或不可见；
- 其他宿主路径不可写；
- 网络默认关闭，或经过域名 allowlist；
- 不把 harness 自身的 API key 无条件传给子进程；
- 测试、包管理器和它们创建的子进程继承同一边界。

这样即使模型调用：

```bash
python -c 'open("/outside/workspace", "w").write("x")'
```

系统也不需要理解 Python 字符串。运行时边界会让它失败。

这也是为什么我不能一边说“选择 Codex 路线”，一边坚持“不引入 sandbox”。没有运行时
强制，只剩 profile 和 reviewer，实质仍是预测式授权。Codex 路线的核心不是界面上的
“Approve for me”，而是 review 发生之前已经存在一堵墙。

## Boundary-only Auto-review：审批的是例外，不是日常工作

一个画得好的 profile 应该覆盖绝大多数正常开发动作。编辑 workspace、运行现有测试、
读取源码都不需要额外模型调用。只有执行层报告缺少某项能力时，才生成结构化
`ApprovalRequest`：

```json
{
  "goal_id": "goal-17",
  "tool": "Bash",
  "command": "uv sync",
  "boundary": "network",
  "requested_capability": {
    "domains": ["pypi.org", "files.pythonhosted.org"],
    "scope": "single-command"
  },
  "reason": "the locked dependencies are not installed"
}
```

Reviewer 判断的是一个窄问题：为了当前 goal，是否允许这条精确命令临时访问这两个
域名？它不应拥有永久写入 allowlist 的权力。理想输出只有三类：

```text
approve_once   精确动作获得一次临时能力，重试后失效
deny           把理由交给主模型，允许寻找明显更安全的方案
needs_user     无法替用户决定，转入 park
```

硬红线不进入 reviewer。`git commit`、`git push` 等如果被定义为事后人工落章，就不该
因为 reviewer 写了一段漂亮理由而变得可自动执行。Reviewer 是例外解释器，不是新的
权限所有者。

## Typed park/resume：把同步弹窗变成异步交接

到这里仍然缺最后一块。如果 reviewer 拒绝或超时，而 goal loop 继续把它当普通工具
错误，判官会说“目标尚未达成”，下一轮再试相同动作。两个自动控制环会一起烧 token，
却没有任何新增能力。

所以 permission failure 不能只是字符串：

```text
permission denied: network access required
```

它必须是 typed blocker：

```python
PermissionBlocker(
    kind="reviewer_unavailable",
    request_id="req-42",
    resumable=True,
    suggested_actions=(
        "approve once",
        "edit goal permission profile",
        "clear goal",
    ),
)
```

Goal 状态机也要从 `active / met / cleared` 扩成：

```text
ACTIVE
PAUSED_PERMISSION
PAUSED_BUDGET
MET
CLEARED
```

执行顺序必须是：

```text
完成一个 turn
→ 是否存在 unresolved permission blocker？
   → 有：持久化 pause，停止自动续 turn
   → 无：再调用 goal judge
```

能力缺失的优先级高于完成判定。Park 不是失败退出，也不是一个正在后台阻塞的 prompt；
它是把当前 goal、profile、审批请求、reviewer 理由和恢复方式一起写入 snapshot，然后
释放人的注意力与运行资源。

人回来后可以批准这一次、修改 profile、人工完成不可自动化的动作，或者清除 goal。
Resume 时也不应直接重放旧 tool call，而应把批准信息注入对话，让主模型在最新上下文
里重新提出动作。命令参数一旦变化，就重新过边界检查。

## 三个动作怎样流经新系统

### 一：运行测试

```text
uv run pytest -q
```

它只读取运行时、读写 workspace 临时文件，不需要网络：

```text
Profile 内 → SandboxExecution 直接运行 → reviewer 调用 0 次
→ 测试证据进入 transcript → goal judge 判断
```

这是正常路径。一个好系统不是让 reviewer 快速批准所有动作，而是让 95% 的动作根本
不需要 reviewer。

### 二：安装依赖

```text
uv sync
```

如果 profile 默认断网，执行层检测到需要访问 PyPI：

```text
BoundaryViolation(network)
→ ApprovalRequest
→ reviewer 检查 goal、lockfile、目的域名和命令
→ approve_once
→ 只为本次命令开放两个域名
→ 命令结束，临时 grant 失效
```

如果 reviewer 无法确认域名或数据流，goal 进入 `PAUSED_PERMISSION`，而不是弹窗等人，
也不是继续空转。

### 三：git commit / push

```text
git commit -am "finish"
```

如果设计规定 commit/push 留给事后 review，它会同时撞上两层：AuthZ 的不可覆盖红线，
以及 sandbox 中只读的 `.git` 或关闭的网络。请求不会进入 auto-review，而是明确告诉
goal：“这一步不属于无人阶段的能力包络。”

更好的体验是在 goal 创建时就发现冲突：如果完成条件要求自动 commit，而 profile 又
禁止 commit，系统应要求用户修改条件，而不是跑到第十轮才发现目标物理上不可达。

## 成本：最便宜的 review 是根本不调用 review

加入 goal judge 和 permission reviewer 后，一个任务的模型成本不再只有主模型：

```text
总成本 = 主模型 turns
       + goal judge calls
       + boundary review calls
       + compact / summarize calls
```

因此成本优化不能只做“把 reviewer 换成更小模型”。第一优先级是让 permission profile
覆盖常规路径，让 reviewer calls 远小于 tool calls。第二优先级是 permission blocker
早于 goal judge 触发 park，避免在缺能力时继续生成无意义 turns。第三优先级才是用 eval
决定 judge 和 reviewer 是否可以降档。

我计划把主模型默认切到 `qwen-plus`；goal judge 与 permission reviewer 初版也先使用
足够可靠的模型和很小的结构化输出上限，再分别测量：

- 每个 goal 的 review 次数；
- approve / deny / timeout 比例；
- reviewer token 与延迟；
- 重复越界请求的 fingerprint；
- 因 early park 节省的 auto-turn；
- 完全落在 sandbox 内的动作占比。

如果普通测试、编辑、构建频繁触发 reviewer，首先说明 profile 画错了，而不是 reviewer
还不够便宜。

## 安全承诺，以及明确做不到的事

这套方案能承诺：

- 无人阶段的大多数动作在强制边界内执行；
- profile 外的能力不会因为 goal 活跃而自动获得；
- auto-review 只处理精确、低频、可审计的边界例外；
- 无法安全决定时，任务会持久化并 park；
- hard deny 不可被主模型或 reviewer 覆盖；
- goal 结束后临时权限失效；
- commit/push 可以保留为事后人类落章。

它不能承诺：

- sandbox 内的所有代码修改都正确；
- reviewer 永远不会误判；
- 允许访问的网络目的地一定可信；
- 用户批准的边界本身一定合理；
- 一个没有 OS 强制能力的平台，也能获得同等保证。

Sandbox 降低 blast radius，不能替代代码 review；reviewer 降低错误批准越界请求的概率，
不能替代 sandbox；goal judge 判断完成条件，不能证明实现没有条件之外的缺陷。三个模型
和三层机制各自只对自己的问题负责。

## 从人在循环中，到人在循环两端

回头看，sync 与 async 的差别不是审批数量从十次变成零次，而是**审批发生在哪里**。

同步交互把人放在循环中间：

```text
模型行动 → 人接 turn → 模型行动 → 人批工具 → 模型行动 → 人判断完成
```

异步交互把人移到两端：

```text
事前：定义 goal + 验收条件 + permission profile

中间：sandbox 强制边界
     + goal judge 接 turn
     + auto-review 处理低频例外
     + 无法决定时 park

事后：review diff + 人工落章
```

所以审批不应该消失。真正需要消失的是那些建立在“人此刻正盯着屏幕”上的中间态。

`/goal` 让我先拿走了 turn 的接力棒；permission profile、SandboxExecution、
boundary-only auto-review 与 typed park/resume，则要拿走动作授权的接力棒。当这两根
接力棒都不再要求人在每一轮握住，agent 才不是“跑得更久的同步聊天”，而是一段人可以
放心离开、稍后回来验收的工作。

这才是我要实现的 async。

## 参考资料

- [Claude Code：Keep Claude working toward a goal](https://code.claude.com/docs/en/goal)
- [Claude Code：Choose a permission mode](https://code.claude.com/docs/en/permission-modes)
- [Claude Code：Configure permissions](https://code.claude.com/docs/en/permissions)
- [Codex：Agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [Codex：Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)
- [Codex：Sandbox](https://learn.chatgpt.com/docs/sandboxing)
- [Decision 44：交互模式 Bash 默认 ASK](../../decisions/44-interactive-bash-ask.md)
- [Decision 48：REPL session 级 `/goal`](../../decisions/48-repl-goal-boundary.md)
- [授权 vs 隔离：权限系统到底在防什么](./ch7-permission-authz-vs-containment.md)
- [Claude Code `/goal` 设计与实现调研](./cc-goal-design-reverse.md)
