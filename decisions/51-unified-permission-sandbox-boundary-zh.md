# 统一 Permission + Sandbox 边界

> 状态：2026-08-05，在实现前锁定。
>
> 范围：不再假设 `SandboxExecution` 是覆盖整个 session 的安全边界，改为使用一份
> canonical runtime permission profile，并把它编译成经过验证的平台边界。Permission
> resolution 只能依赖已经验证的边界，绝不能只依赖配置所表达的意图。

## 触发原因

`/goal` 已经可以接管 turn 的延续和完成判定，但 permission request 仍然要求人在循环
中途处理。第一次 async permission 设计尝试认为，`AUTO + shell_contained` 足以自动
解决一个具有修改能力的 Bash `ASK`。

代码审查证明这个前提不成立：

- `SandboxExecution` 是 `ExecutionEnvironment.run_command` 的 Docker 实现；只有 Bash
  使用它；
- Read、Write、Edit、Grep、MCP、Web 工具和 hooks 仍通过拥有宿主权限的路径执行；
- Docker backend 将整个 workspace 以读写方式挂载，网络只有 `none` / `bridge` 两档；
- runtime 没有经过验证的描述，无法说明哪些模型可控副作用真正被覆盖；
- 原始命令失败无法可靠地区分普通错误与文件系统、网络或 sandbox policy 越界。

因此，当前 Docker backend 仍然有用，但它本身不能成为 async permission 的可信基础。

## 安全承诺

对于每一个模型可控副作用，必须且只能落入以下三种结果之一：

1. 已验证 active runtime boundary 能强制约束该副作用；
2. 一个精确的 permission delta 获得一次重试授权；
3. 该副作用被拒绝，或 park 后等待人处理。

不能存在未经分类、却以 harness 进程 ambient authority 执行的第四条路径。

形式化表达：

```text
for every effect in ModelControlledEffects:
    Enforced(effect, EnforcedBoundary)
    OR ApprovedOnce(effect, exact_delta)
    OR DeniedOrParked(effect)
```

需要保护的资产包括：

- 声明的 roots 以外的宿主文件；
- workspace 内的 protected paths，例如 `.git`、`.codex` 和 `.agents`；
- 项目和用户凭证，包括 workspace 内被 deny-read 的文件；
- 出站数据以及本地/私网服务；
- 持久化宿主配置和跨项目状态；
- 宿主进程、内存、CPU、PID、socket 和设备能力。

可信 control plane 的范围更窄：harness 代码、明确安装的 hooks/plugins、LLM API
transport、snapshots、Goal judge 调用，以及固定路径的内部 bookkeeping。模型编写的参数
和模型选择的 tool call 都属于 data plane，即使实际 dispatch 它们的是可信 harness
组件。

## 单一事实来源

用户侧配置只有一份 canonical source：

```text
RuntimePermissionProfile
├── FilesystemPolicy
├── NetworkPolicy
├── EnvironmentPolicy
├── ProcessPolicy
└── ExternalToolPolicy
```

`PermissionPolicy` 和 `SandboxPolicy` 不是两份相互独立的用户配置。Backend compiler
把 canonical profile 降低成平台特定的强制策略，并返回一个经过验证的事实：

```text
RuntimePermissionProfile
        │ compile + preflight
        ▼
EnforcedBoundary
├── profile_fingerprint
├── backend + backend_version
├── covered_effects
├── installed filesystem rules
├── installed network rules
├── installed environment/process rules
├── unsupported features
└── verification result
```

Permission resolution 消费 `EnforcedBoundary`；它不能根据配置中的 profile、backend
class name，或 `shell_contained` 这样的布尔值推断安全性。

## Policy 维度

### 文件系统

- read、write 和 deny 访问；
- 多个 workspace roots 和 writable roots；
- 在较宽 writable root 下设置更窄的 protected/denied paths；
- temp 和 cache roots；
- deny-read secret patterns；
- 规范化路径处理，以及明确的 symlink/non-existent-path 行为。

### 网络

- 默认关闭；
- 精确的公共域名 allow 规则，deny 优先；
- 默认拒绝 local、private、link-local 和 loopback 目标；
- 除非明确允许，否则拒绝 Unix sockets；
- 当 hostname policy 无法直接强制执行时，通过 proxy 实施。

### 环境和进程

- 最小化环境继承，并支持 include/exclude/set 规则；
- 默认排除具有 credential 形态的变量；
- 默认使用 non-login shell；
- UID/GID、capability、no-new-privileges、资源、超时和进程树清理策略；
- 子进程继承相同边界。

### 外部工具

本地文件系统 sandbox 的承诺不延伸到 MCP、Web、connectors、browser 或 Computer Use。
这些 surface 需要独立的 effect declaration 和 approval policy。本地 stdio MCP server
自身可以在 sandbox 中启动，但它的远端副作用仍属于 external-tool concern。

## Backend 合同

```python
class SandboxBackend(Protocol):
    def preflight(self, profile: RuntimePermissionProfile) -> BackendSupport: ...
    async def open(self, profile: RuntimePermissionProfile) -> SandboxSession: ...

class SandboxSession(Protocol):
    @property
    def boundary(self) -> EnforcedBoundary: ...
    async def execute(self, operation: DataPlaneOperation) -> ExecutionResult: ...
```

`open()` 要么安装请求的 policy 并返回经过验证的 boundary，要么 fail closed。当请求的
profile 无法强制实施时，任何 backend 都不得静默回退到 `HostExecution`。

`ExecutionResult` 至少区分：

```text
ProcessCompleted
BoundaryViolation       # 仅在 backend 能可靠识别时产生
SandboxUnavailable
TimedOut
ExecutionFailed         # 普通或无法分类的工具失败
```

原始 stderr 解析不是 authorization oracle。Escalation 只有两个合法来源：

- 一个主动的 `PermissionDeltaRequest`，其中明确所需资源；
- 一个带有确定性证据、由 backend 生成的 `BoundaryViolation`。

## 工具覆盖合同

每一个注册工具都声明一个 execution domain。缺失声明时 fail closed，或要求审批。

| Surface | 当前路径 | 目标 domain |
|---|---|---|
| Bash | host 或 Docker command backend | 本地 sandbox data plane |
| Grep | 原始宿主 `rg` 子进程 | 本地 sandbox data plane |
| Read | 宿主 `Path` 调用 | 本地 sandbox filesystem worker |
| Write/Edit | 宿主 `Path` 调用 | 本地 sandbox filesystem worker |
| SpawnAgent | 继承部分 query context | 继承同一个 verified runtime |
| stdio MCP process | 宿主进程、父进程环境 | 可配置的 sandboxed service process |
| MCP call | 远端/server-defined effect | external-tool policy |
| WebSearch/WebFetch | 宿主/provider 网络 | external network-tool policy |
| hooks/plugins | 宿主 Python | 明确信任的 control plane |
| LLM API/snapshot/Goal judge | 宿主 harness | 固定用途的 control plane |

在 Bash、Grep、Read、Write 和 Edit 全部由同一个 verified local boundary 覆盖之前，
不得发布任何 async permission posture。

## Backend 选择

### Docker command backend

保留现有 `SandboxExecution`，但将它重新定位为 command backend。它最初的 coverage
严格等于 `{command}`，不得宣称覆盖整个 session。

在它可以被视为可信 command backend 之前，还需要：preflight、fail-closed startup、
protected paths、在兼容情况下使用 read-only rootfs、非 root 执行、capability drop、
no-new-privileges、显式 temp storage、完整的进程树清理，以及可复现的镜像选择。
`network=none`、资源限制和可选 gVisor 仍然有价值。

Docker 是显式选择的 CI/dev-container/强隔离 backend，不是 native local execution 的
静默 fallback。

### macOS Seatbelt backend

macOS 是第一个 native backend，因为它是主要开发平台。它使用宿主工具链，同时对
进程及其后代强制实施 filesystem 和 network 规则。不支持的 policy 或 profile 加载
失败时 fail closed。Sandbox doctor 和负向 integration suite 属于 backend 合同的一部分。

Linux bubblewrap + seccomp 作为独立 backend phase 随后实现。不伪造平台能力对齐：
不支持的维度必须在 preflight 中报告。

## 本地 data-plane 设计

模型控制的文件系统操作不在拥有 ambient authority 的 harness 进程中直接执行。
Read、Write、Edit 和 Grep 通过 active native sandbox 下启动的小型 worker；Bash 使用
同一个 sandbox session。

```text
Read  ┐
Write │
Edit  ├── sandbox worker / command runner ── verified OS boundary
Grep  │
Bash  ┘
```

这样既不需要在每个工具里重复 path classifier，也能避免 Bash 绕过文件工具限制，或
文件工具绕过 Bash 隔离。任何 PreToolUse 参数修改后都必须重新执行 authorization。

## Permission 和 async 生命周期

只有在 local boundary coverage gate 达标后，`ASK` 才能成为 async boundary workflow：

```text
profile → compile + verify → boundary-contained execution
                            ↓ boundary delta required
PermissionDeltaRequest / deterministic BoundaryViolation
                            ↓
boundary-only auto-review
                            ↓
exact one-retry overlay
                            ↓ unresolved
typed park → Goal pauses without judge call or auto-turn consumption
```

Auto-review 是针对一个精确 boundary request 的 reviewer swap。它不会持久改变基础
profile。Reviewer 输入包括：用户授权、最终验证后的工具参数及其 fingerprint、active
profile 和 backend fingerprints、最小 requested delta、数据来源/目的地，以及 backend
能否强制实施一次性 overlay。

Denial 会要求 worker 不得通过等价 workaround 继续追求相同结果。重复 denial 有 circuit
breaker。人工审批必须精确且受 retry 次数限制。

## Session、Goal 和持久化

- runtime profile 属于 session，而不属于 Goal；
- Goal 在 session 的 verified runtime 中继续 turn；
- snapshot state 记录 active profile id、effective fingerprint、backend identity 和
  parked request；
- 当 effective boundary 发生变化时，resume 拒绝继续或显示警告；
- permission park 发生在 Goal judge 之前，不消耗 auto-turn。

## 实施阶段和 gate

### S0 — 移除错误前提

保留最终参数重新授权和基于 context cwd 的路径规范化。移除或隔离实验性的四字段
boundary、`SHELL` auto-allow、reviewer、typed events、grants 和 `/approve` 实现，直到
它们的数据合同基于 canonical profile 重新构建。

### S1 — Canonical profile + coverage model

落地 policy models、backend protocols、effective-boundary fingerprints、execution-domain
声明、coverage inspection 和 `/permissions` 状态。工具行为不改变，也不自动 escalation。

### S2 — 真实且加固的 Docker command backend

重新定位现有实现，增加 preflight/fail-closed 行为，加固容器，并发布精确 coverage。

### S3 — macOS Seatbelt backend

为宿主工具链命令编译 filesystem/network/process 规则，增加 doctor 输出，并通过
integration tests 证明负向隔离属性。

### S4 — 统一本地 data plane

把 Read/Write/Edit/Grep 移入 sandbox worker，让 Bash 使用同一个 runtime，并证明不存在
host-authority bypass。这是开始 async permission 工作的 gate。

### S5 — 网络、环境和进程 policy

增加域名 proxy、private/local/socket 控制、环境过滤、login-shell policy、进程树生命周期
以及确定性的 typed violation。

### S6 — 外部工具

显式建模 MCP、Web 和 trusted hooks，不夸大 local sandbox boundary。

### S7 — Async permission

重新构建精确 request、boundary-only auto-review、一次重试 overlay、denial circuit
breaker、typed park/resume、snapshot 持久化和 Goal 集成。

## 验收不变量

只有满足以下全部条件，项目才能暴露 async/Auto permission posture：

- 每个模型可控 local effect 都有 execution-domain declaration；
- 所有核心 local data-plane 工具共享同一个 verified boundary；
- backend 对 unsupported policy 的路径 fail closed；
- protected paths、deny-read secrets、network denial、子进程继承、环境过滤、symlink
  paths 和 timeout cleanup 都有负向 integration tests；
- `/permissions` 报告 active profile、backend、installed boundary、coverage、未覆盖的
  external surfaces 和 policy fingerprint；
- reviewer approval 精确、最小、受 retry 限制，并且不能覆盖 hard deny rules；
- parked permission 在 Goal judge 调用之前暂停 Goal。

## 参考资料

- [Codex Permissions](https://learn.chatgpt.com/docs/permissions)
- [Codex Sandbox](https://learn.chatgpt.com/docs/sandboxing)
- [Codex Agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [Codex Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)
- [Codex core sandbox support matrix](https://github.com/openai/codex/blob/main/codex-rs/core/README.md)
- [Codex Linux sandbox](https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md)
