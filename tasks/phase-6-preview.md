# Phase 6 Preview — Sandbox (Execution Substrate Abstraction)

> **Status**: PREVIEW（不是正式 boundary doc）。Phase 6 入口时基于此文档做正式
> Three-Axis 讨论，产出 `decisions/<NN>-phase-6-boundary.md` 拍板。
>
> 写于 2026-05-15。Phase 5 MCP boundary 锁定的同一天浮现的 Phase 6 设计预演。
> 沉淀这份文档的目的：**避免 Phase 6 入口再想一遍**——boundary doc 入口时直接
> 基于此推进，能节省 2-3 小时设计时间。
>
> 关联：[`decisions/11-phase-5-boundary.md`](../decisions/11-phase-5-boundary.md)
> Cross-cutting invariant / [`learnings/phase-3-framing.md`](../learnings/phase-3-framing.md)
> §4-7 / [`ARCHITECTURE.md`](../ARCHITECTURE.md) §4 Phase 6 候选

---

## Phase 6 Essence

> **第一次把"tool 在哪儿跑"作为框架一等概念**——把 Phase 1-3 隐含的"host
> process 执行"假设显式化成可注入的依赖。Docker 只是这个抽象的第一个具体
> 实现。

**Deliverable**：

1. `BaseTool` 和 OS 之间引入 `ExecutionEnvironment` 抽象层
2. `HostExecution` 包装当前行为（Phase 1-3 零行为变化）
3. `SandboxExecution` 提供 Docker substrate
4. `oh ask --sandbox` 让 Bash 在容器里跑；permission/hook/observability 三层
   **完全零改动**
5. 端到端 smoke 验证：`Bash("cat /etc/passwd")` 在 sandbox 下返回 `No such
   file`；`curl evil.com` 网络不通；fork bomb 被 cgroup 截

**关键 invariant**：跟 Phase 5 MCP 同一条——**不增加新的 dispatch path**。
`permissions/checker.py` / `hooks/executor.py` / `engine/query.py` 必须零 diff。
这是 P3-T4 hook 实施隐藏 acceptance 在 Phase 6 的第二次兑现（Phase 5 是第一次）。

---

## 两层结构（这点非常重要）

Sandbox 不是一个**功能**，是**两件事**叠加：

```
┌─────────────────────────────────────┐
│ A. 抽象层：ExecutionEnvironment      │  ← 框架级新概念，一次性建立
│  - "tool 在哪儿跑" 显式化             │  ← 跟 Phase 5 MCP 共享同一个抽象
│  - 注入点：QueryContext.execution_env│
│  - 接口：async run_command(...)     │
└─────────────────────────────────────┘
                ↑ 多个实现
                │
┌───────────────┼───────────────┬───────────────┐
│ HostExecution │ McpExecution  │ SandboxExec.  │
│ (Phase 1-3,   │ (Phase 5)     │ (Phase 6) ⭐  │
│  default)     │               │               │
└───────────────┴───────────────┴───────────────┘

B. Docker substrate（Phase 6 具体实现）
   - 容器生命周期（per-query spawn/teardown）
   - bind mount cwd 到 /workspace
   - path translation（LLM 看 host path）
   - network=none default
   - cgroup resource limits
```

**A 是契约决策**（影响 BaseTool 接口），**B 是工程实现**（一种 substrate，不
动框架）。两者可以分开 ship：

- **Phase 6a**：只做 A（HostExecution 包装现状，零行为变化）+ BashTool 改用
  `ctx.execution_env`——纯重构，准备好抽象
- **Phase 6b**：做 B（SandboxExecution + Docker lifecycle + Settings/CLI）

---

## L4 架构定位（在 harness 代码的哪一层）

```
┌─────────────────────────────────────────────────────────┐
│  cli.py                                                  │
│    构造 QueryContext (含 execution_env: HostOrSandbox)   │
└─────────────────────────────┬───────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────┐
│  engine/query.py + hooks/ + permissions/ + observability/│ ← 零改动
│         （Phase 1-5 已建立的横切基础设施）                  │
└─────────────────────────────┬───────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────┐
│  tools/bash.py                                           │
│    BashTool.call() → ctx.execution_env.run_command(...)  │ ← 一行改动
└─────────────────────────────┬───────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────┐
│  execution/  ← Phase 6 新增的抽象层 ⭐                    │
│    ├── base.py  (ExecutionEnvironment Protocol)          │
│    ├── host.py  (HostExecution，现状包装)                 │
│    └── sandbox.py (SandboxExecution，Docker substrate)   │
└─────────────────────────────┬───────────────────────────┘
                              ↓
                    OS (kernel namespace 已经隔离)
```

**否决的候选层**：

| 候选 | 否决理由 |
|---|---|
| engine `_dispatch_one` 里加 sandbox 分支 | engine 不应懂 "Bash 需 sandbox / Read 不需要" 这种 tool 知识 |
| Permission checker 里加 sandbox | 把 sandbox 当 permission——前面已分析（input policy vs runtime confinement 是两个 enforcement 范式） |
| BashTool 内硬编码 docker | sandbox 焊进 tool；Phase 7 换 gVisor 要改 tool 代码 |
| 整个 harness 进容器（Codex 风格） | 牺牲 dev 体验；macOS 嵌套 VM；失去 differential sandboxing 粒度 |

---

## S1 — Sandbox Runtime

选哪个 substrate 作为 Phase 6 MVP：

| 选项 | 含义 | trade-off |
|---|---|---|
| **A. Docker** | docker daemon + docker CLI/SDK | 最易获取，docs 最全，dev 体验最熟 |
| **B. Podman** | rootless friendly, 无 daemon | 比 Docker 更 niche；macOS 支持稍弱 |
| **C. runc/crun 直接调** | 跳过 docker 包装直接走 OCI runtime | 工程量大；学习项目不值得 |
| **D. gVisor** | userspace kernel re-impl，更深隔离 | Linux only，性能损失 ~10% |
| **E. Firecracker** | microVM | OpenAI Code Interpreter 选型；overkill for coding agent |
| **F. macOS sandbox-exec** | Mac 原生 Seatbelt sandbox | 不跨平台；功能比 Docker 弱 |

**Tentative: A（Docker）**——学习项目最佳。其它 substrate 留 Phase 7+ polish。
未来扩展遵循 ExecutionEnvironment 接口，是 plug-in 不是重写。

---

## S2 — 哪些 Tool 进 Sandbox ⭐（核心决策）

这是 Phase 6 入口要真正 Three-Axis 的一题：

| 选项 | 含义 | trade-off |
|---|---|---|
| **A. 全部 tool 进 sandbox** | Read/Write/Edit/Grep/Bash 都跑容器里 | 一致性最好；Python tool 要重写为 container 内 shell 命令 |
| **B. 只 Bash 进 sandbox** | Bash 在容器跑；其它 tool 用 host fs | Bash 是 arbitrary code execution 入口，威胁集中点 |
| **C. Harness 整个跑容器里** | host CLI 是 thin client；agent loop 跑容器内 | Codex 做法；最干净但 dev 体验差 |

**Tentative: B**——80/20 切点。

理由：

- Read/Write/Edit/Grep 是 path-pure tool，Phase 3 Tier 1/2/3 permission 已经
  罩死（Tier 1 hardcoded ~/.ssh、Tier 2 glob deny_paths、Tier 3 mode-based）
- Bash 才是 shell expansion / subprocess spawn / arbitrary syscall 的入口；
  permission 在 Bash 上是不完备的（前面 §1 的 `cat $(echo /etc/passwd)` 例子）
- A 的工程成本不成比例（重写 Python tool 为 container shell）
- C 的部署形态留 Phase 7+ 作为可选 mode（不强制，但抽象支持）

Phase 6 入口拍板前要再验证：MCP tool（Phase 5 落地后）是否也消费
`ctx.execution_env`？——倾向**否**，因为 MCP 已经在远程 server 进程里跑，
sandbox 是对**本地 tool 行为**的约束。

---

## S3 — Sandbox Lifecycle

| 选项 | 行为 | trade-off |
|---|---|---|
| **A. per-tool-call** | 每次 Bash 都 spawn 新容器 | 100ms-1s/次开销；交互式 UX 灾难 |
| **B. per-query** | `oh ask` 一次用一个容器，结束 tear down | 摊薄到 ~1s/query；和 Phase 5 MCP server 同形态 |
| **C. per-session** | 容器跨 `oh ask` 复用 | UX 最快；state pollution 风险，lifecycle 复杂 |

**Tentative: B**——甜蜜点。跟 P5 McpClientPool 同生命周期模型，
框架级一致。

---

## S4 — Path Mapping（LLM 看什么 path）

| 选项 | LLM 看到 | trade-off |
|---|---|---|
| **A. 永远 host path** | `/Users/me/proj/foo.txt` | LLM 跨 sandbox/host 心智一致；harness 内部翻译 |
| **B. 永远 container path** | `/workspace/foo.txt` | sandbox 内一致但 LLM 上下文奇怪（path 看起来像 server） |
| **C. 透明不固定** | 混杂 | LLM 困惑，调用错 |

**Tentative: A**——和 Phase 5 `Server.Tool` namespace 同思路：**user-facing
抽象保持稳定**，translation 在 dispatch 边界完成。

实现：`SandboxExecution.run_command` 接收 host path 形态，内部 `docker exec`
时改写 cwd 为 container 内的 `/workspace`（cwd bind mount 的对端）。

---

## S5 — Bind Mount 策略

| 选项 | 挂什么 | trade-off |
|---|---|---|
| **A. cwd RW** | 只挂当前项目目录，可读写 | 最小授权；Bash 看不到其它 |
| **B. cwd RO + tmp RW** | 项目目录只读 + 容器内 /tmp 可写 | 强力但 LLM 改文件失败 |
| **C. 显式白名单** | Settings 配置 mount list | 灵活但 friction 大 |

**Tentative: A**——cwd 单挂载，可读写。`/etc`, `/home`, `~/.ssh` 等**结构性
不存在**于容器视角；defense in depth 由 namespace 保证。

---

## S6 — Network 策略

| 选项 | 含义 | trade-off |
|---|---|---|
| **A. none（默认）** | 容器无任何网络 | 最强默认；阻断 exfiltration |
| **B. bridge full egress** | 默认 docker bridge，全网出 | Docker default；但失去 sandbox 一半意义 |
| **C. egress allow-list** | 用户配置允许的 host | 灵活但工程复杂 |

**Tentative: A 默认 + `--sandbox-network=bridge` opt-in**。

理由：

- 默认 `none` 阻断「LLM 把 ~/.ssh/id_rsa POST 到外部」这类经典 prompt
  injection 攻击
- 真要装包、拉 git，用户显式 `--sandbox-network=bridge`（一次性 escape hatch）
- C 留 Phase 7+ 真有 enterprise 需求时再加

---

## S7 — Resource Limits（默认值）

cgroup-controlled defaults：

| 资源 | 默认 | env override |
|---|---|---|
| memory | 1GB | `OPENHARNESS_SANDBOX_MEMORY=512m` |
| CPU | 1 核 | `OPENHARNESS_SANDBOX_CPUS=2` |
| pid count | 256 | `OPENHARNESS_SANDBOX_PIDS=128` |
| Bash 单调用 timeout | 60s | `OPENHARNESS_SANDBOX_TIMEOUT=120` |

足够 normal coding workflow；fork bomb / 内存溢出会被 kernel 截。

---

## S8 — Image 策略

| 选项 | 含义 | trade-off |
|---|---|---|
| **A. 单一 base image** | `openharness/sandbox:latest`，包含 bash/coreutils/git/curl/python3/node | 简单；体积大 |
| **B. per-tool image** | Bash 用一个 image，将来其它 tool 各自 image | 灵活但维护负担 |
| **C. 用户可换** | Settings 指定 image name | 简单 + 可定制 |

**Tentative: A + C**——maintain 一个官方 image，Settings 字段允许换。社区
可以 fork base image 加工具。

---

## Cross-cutting Invariant — Phase 6 复用 Phase 3+5 基础设施

⭐ **Phase 6 sandbox 必须不增加新的 dispatch path**——和 Phase 5 boundary
锁的同一条 invariant。

具体 dispatch 链路（sandbox on/off 字节级一致）：

```
LLM → tool_use
  ↓
engine/query.py:_dispatch_one()              ← 零改动
  ↓
PermissionChecker.evaluate()                  ← 零改动
  ↓
HookExecutor.run(PreToolUse)                  ← 零改动
  ↓
tool = registry.get(tool_name)                ← 零改动
  ↓
result = await tool.call(input, ctx)          ← BashTool 改一行
  ↓ tool.call 内部：
  ctx.execution_env.run_command(...)          ← 新分歧点
    ├── HostExecution: subprocess
    └── SandboxExecution: docker exec
  ↓
HookExecutor.run(PostToolUse)                 ← 零改动
  ↓
log tool_complete (+execution_env 字段)        ← 加 1 字段
```

**三条零改动**：

1. `permissions/checker.py` 不增加 `if isinstance(SandboxTool)` 分支——
   permission 依然 dispatch 前的 tool input 静态检查
2. `hooks/executor.py` 不知道 tool 在哪儿跑——PostToolUse 看到的是
   `ToolResult`，不区分 stdout 来自 host 还是 container
3. `engine/query.py` 不增加 sandbox-aware 路径——dispatch loop 永远调
   `tool.call()`

**如果落地时必须改这三层**——说明 ExecutionEnvironment 抽象做错了或 BaseTool
接口形态不对，回头修。这条 invariant 是 Phase 5 invariant 在新 substrate
上的二次兑现。

---

## 跨平台现实（Phase 6 必须直面）

`docker` 在 Linux 是原生（直接用 host kernel namespace），但：

- **macOS**：Docker Desktop 内部跑了一个 LinuxKit VM；容器在那个 VM 里。
  dev 机本质 sandbox 是嵌套 VM
- **Windows**：类似（WSL2 / Hyper-V）
- **Linux CI**：原生

影响：

| 维度 | macOS dev | Linux CI |
|---|---|---|
| 首次启动延迟 | 慢（唤醒 LinuxKit VM, 5-10s） | 快（< 1s） |
| 文件 I/O bind mount | 跨 VM 边界，慢一个量级 | 原生快 |
| 测试稳定性 | smoke 可能不稳 | 稳定 |

**测试策略**：

- unit + integration（mocked Docker）：跨平台都跑
- end-to-end smoke（真 docker run）：**只在 Linux CI gate**；macOS 本地手动验证
- 跟 Phase 1 的 `@pytest.mark.integration` 同模式

---

## Tentative Recommendations Summary

| ID | 决策 | Tentative |
|---|---|---|
| **S1** Sandbox runtime | Docker（其它 substrate 留 Phase 7+） |
| **S2** ⭐ 哪些 tool 进 sandbox | 只 Bash（80/20） |
| **S3** Lifecycle | per-query（spawn/teardown 同 `oh ask`） |
| **S4** Path mapping | LLM 始终看 host path，harness 边界翻译 |
| **S5** Bind mount | cwd RW 单挂载 |
| **S6** Network | 默认 none，opt-in `--sandbox-network=bridge` |
| **S7** Resource limits | memory 1GB / CPU 1 核 / pid 256 / Bash timeout 60s |
| **S8** Image | 单一 base image `openharness/sandbox:latest`，用户可换 |

---

## Phase 6 落地路径（capability sketch）

```
P6-T1  ExecutionEnvironment 抽象 + HostExecution 包装现状
       零行为变化，纯重构；BaseTool 接口微调

P6-T2  BashTool.call() 改用 ctx.execution_env
       Phase 1-3 行为保持，invariant 第一次试金石

P6-T3  sandbox/ 包：SandboxExecution + Docker lifecycle
       container spawn/teardown + bind mount + path translation

P6-T4  Settings + CLI flag
       --sandbox / --sandbox-network / --sandbox-memory ...

P6-T5  端到端 smoke：
       - Bash("cat /etc/passwd") sandbox 下 No such file
       - Bash("curl evil.com") sandbox 下网络不通
       - Bash("fork bomb") cgroup 截
       - permission/hook/observability 三层 zero diff verification

P6-T6  Coverage 95%+ + learnings/phase-6.md retro + README
```

预算：1-2 周，6 capability。比 Phase 5 略大（Docker 跨平台坑），但抽象一旦
做对后续延伸（gVisor / Firecracker / 远程 worker）是 plug-in 成本。

---

## Phase 6 Entry Three-Axis 时要重新走的题

到 Phase 6 入口时，**不是抄此文档拍板**——要重新走 Three-Axis 流程，因为：

1. **Phase 5 MCP 落地后** ExecutionEnvironment 抽象的真实形态可能跟现在不
   同——MCP 的 lifecycle 实战经验可能改 substrate 接口
2. **`BaseTool.call(input, ctx)` 接口可能微调**——Phase 5 引入
   `QueryContext.execution_env` 字段后会显现是否够用
3. **Docker 生态在变**——2026 年中 Podman 5.x / nerdctl 是否成熟可重评 S1

但**基础 framework 应该稳定**：两层结构（抽象 + substrate）、L4 架构定位、
三条零改动 invariant——这几条不变。

**Three-Axis 入口要问的 5 个问题**（不是答案，是问题）：

1. ExecutionEnvironment 接口 5 个 method 够不够？需不需要加
   `read_file` / `write_file`（如果 S2 决定 Read/Write 也进 sandbox）？
2. Per-query lifecycle 在真 `oh ask` 上的延迟可接受吗？macOS dev 体验如何？
3. Path translation 在 LLM 真消费时有没有 corner case（symlink / absolute
   path inside response）？
4. cgroup default 是否过松/过严？fork bomb 防得住吗？真实编程任务（npm
   install）会不会被资源限制误杀？
5. observability `execution_env` 字段够不够？要不要加
   `container_id` / `sandbox_image` 等？

---

## Pointers

- ARCHITECTURE.md §4 Phase 6 候选位置
- `decisions/11-phase-5-boundary.md` — Cross-cutting invariant 模板
- `decisions/08-phase-3-boundary.md` — D13.1 hook / D13.2 permission /
  D13.6 observability（Phase 6 不动的三层）
- `learnings/phase-3-framing.md` §4-7 五条统一原则
- `learnings/phase-1-and-2.md` §6 LLM-as-RPC-client（execution substrate
  是 RPC server 的本地变体）

---

## 一句话

> **Phase 6 = 把"tool 在哪儿跑"从隐含假设变成显式可注入的依赖**——也是
> ExecutionEnvironment 抽象诞生的时刻。
>
> Sandbox 本身只是这个抽象的一种实现（Docker substrate）。这层抽象做对了，
> Phase 7+ 的 gVisor / Firecracker / 远程 worker pool 都是 plug-in。
>
> 唯一隐藏 invariant：**Phase 6 不增加 dispatch path**——MCP（Phase 5）和
> Sandbox（Phase 6）都是 `ExecutionEnvironment` Protocol 的不同实现，
> permission / hook / observability 三层一视同仁。这条 invariant 在 Phase
> 5 第一次兑现，在 Phase 6 第二次兑现——证明 Phase 3 hook/permission 抽象
> 是稳定的契约，不是过度设计。
