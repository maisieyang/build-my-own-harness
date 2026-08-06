# 统一 Permission + Sandbox 计划

> 写于 2026-08-05，实现开始之前。边界和不变量固定在
> `decisions/51-unified-permission-sandbox-boundary.md`；本文对可独立验证的能力进行排序。

## 目标结果

OpenHarness 拥有一个 session 级 permission profile，并将其编译成 verified runtime
boundary。所有模型可控 local effect 要么在该边界内执行，要么获得一个精确、一次性的
overlay 授权，否则 park。Goal 仍然只是 session runtime 的消费者，永远不拥有权限。

## 实施状态 — 2026-08-05

- S0 已完成：过早实现的 permission resolver/reviewer/park 实验已移除；最终参数重新授权
  和 context-cwd 规范化仍然保留。
- S1 已完成：canonical profile、确定性 fingerprint、verified-boundary 合同、显式工具
  execution domains、coverage report 和真实反映现状的 `/permissions` 状态已经实现。
- S2 仍是 command-only compatibility backend。它已具备 read-only rootfs、移除
  capabilities、no-new-privileges、非 root UID/GID 支持、tmpfs、protected workspace
  mounts、显式 unsupported-feature 报告，并且没有 host fallback。Image digest pinning、
  独立 daemon/runtime doctor 与真实 runc 进程树验收仍未完成；系统不会把它描述成
  session-wide boundary。
- S3 已完成 macOS production posture：Seatbelt compiler 执行真实 startup probe，报告
  verified boundary，默认断网，并在启用 sandbox 时由 production CLI 选择。Profile
  安装失败会中止 sandbox posture，不会回退到 host execution。
- S4 已完成：Bash、Read、Write、Edit 和 Grep 共享同一个 verified `SandboxSession`；
  文件操作进入 sandbox worker，Grep 和 Bash 使用同一个 command boundary，SpawnAgent
  继承同一个 runtime。Production CLI 同时持有 active profile 与 verified boundary，
  `/permissions` 分开显示 configured intent 和 installed facts。
- S5 已完成：启用网络后，访问经过 loopback proxy，由 public-domain allow/deny policy
  判定；private、loopback、link-local 和未声明 Unix socket 均 fail closed。Sandbox
  使用最小且过滤 credential 的环境、non-login shell、有界输出与 timeout，并清理完整
  process group。只有 proxy 记录的确定性 policy denial 才转成 typed violation；DNS、
  upstream 和普通命令失败仍保持普通失败。
- S6 已完成：MCP、Web、Browser 和 Computer Use 是相互独立的 external policy surface，
  不继承 local sandbox trust。MCP adapter 被分类为 external effect；可选 stdio MCP
  sandboxing fail closed；hook 修改后的最终参数会在进入 external policy path 前重新
  授权。`/permissions` 明确列出 local sandbox 未覆盖的已注册 external surface。
- S7 已完成：Permission request 绑定最终参数、profile 与 boundary fingerprint；禁用工具
  的 reviewer 只能授予一次精确 overlay。Hard deny、reviewer failure、不支持的 overlay
  与重复 violation 均 fail closed 或 park。Typed park/approve/deny/resume 状态持久化到
  snapshot；resume 拒绝 boundary drift；多工具 turn 在 park 时仍保存完整 tool-result
  配对；Goal 在 judge 前暂停且不消耗 automatic turn。
- 2026-08-05 的 S3-S7 验证结果：`2732 passed, 11 deselected`，总 coverage
  `95.04%`，strict mypy、Ruff 与 format check 全部通过。

## S0 — 正确性底线和实验清理

能力：

- 最终验证后的参数，必须是在执行前立即被授权的参数，包括 PreToolUse 修改后的参数；
- 所有 permission path 都相对于 `ToolExecutionContext.cwd` 解析；
- 移除不完整的 resolver/boundary/reviewer/park 实验，同时保留上述两项正确性修复；
- 现有 git commit/push 人工交接红线保持不变。

验收：

- 定向 regression tests 通过；
- 没有任何 `AUTO` 路径宣称 Docker 覆盖非 Bash 工具；
- 其他 production behavior 与实验前的 HEAD 保持一致。

## S1 — Canonical profile 和 boundary contracts

能力：

- typed filesystem、network、environment、process 和 external-tool policy；
- 确定性的 profile normalization 和 fingerprinting；
- `SandboxBackend` preflight/open 合同和 `EnforcedBoundary` 结果；
- 显式 tool execution domains 和 registry coverage report；
- `/permissions` 分开显示 configured intent 和 installed facts。

验收：

- 语义相同的 profiles 具有相同 fingerprint；
- 相互矛盾或无法表示的 rules 在 validation 阶段失败；
- 注册未声明 execution domain 的 model-callable tool 时 fail closed；
- 此阶段不改变 backend 或 tool execution behavior。

## S2 — Docker command backend

能力：

- 现有 Docker 实现作为 command-only backend 暴露；
- preflight 报告 daemon/runtime/image/policy 支持情况；
- container hardening 和 protected workspace paths；
- 确定性 cleanup，以及显式 unavailable/unsupported results；
- `/permissions` 和 doctor 展示精确 coverage 与 limitations。

验收：

- 缺少 Docker/runtime 支持时绝不回退到 host；
- 命令无法写入 protected paths，也无法看到 declared mounts 以外的 host paths；
- 默认 network denial 和 resource limits 持续有效；
- timeout 会终止整棵 command process tree；
- integration tests 覆盖 runc；gVisor 继续单独 gating。

## S3 — macOS Seatbelt backend

能力：

- 把 canonical filesystem rules 编译成 Seatbelt profile；
- 使用宿主工具链执行，并让子进程继承边界；
- 保护 writable roots 下的 nested paths；
- 默认 network denial 和 backend diagnostics；
- `sandbox doctor` 检查可用性并执行最小 isolation probe。

验收：

- workspace 写入成功，outside writes 和 protected writes 失败；
- direct process 或 child process 都不能读取 deny-read files；
- network denial 得到强制实施；
- 不支持的 rules 或缺少 `/usr/bin/sandbox-exec` 时 fail closed；
- 没有命令静默通过 `HostExecution` 执行。

## S4 — 统一本地 data plane

能力：

- Read、Write 和 Edit 使用 sandbox worker operations；
- Grep 通过 sandbox command runner 执行；
- Bash 和 filesystem worker 共享同一个 active `SandboxSession`；
- SpawnAgent 继承同一个 verified runtime；
- structured execution results 到达 tool 和 controller layers。

验收：

- 在 sandboxed posture 下，核心 local tools 不存在 ambient-host execution path；
- Bash 和 file tools 遵守相同的 filesystem denies；
- symlink、parent traversal、non-existent path 和 replacement-race tests 均安全失败；
- coverage matrix 证明所有核心 local effects 都被覆盖。

## S5 — 网络、环境和进程边界

能力：

- 通过 proxy 强制实施 public-domain allow/deny policy；
- local/private/link-local 和 Unix-socket 保护；
- 最小环境构造和 credential exclusions；
- non-login shell 和受控进程生命周期；
- backend 能证明原因时产生确定性的 boundary violations。

验收：

- 被允许的公共域名可访问，未被允许的域名失败；
- local/private targets 和未列出的 sockets 失败；
- 默认情况下 credential variables 不会进入模型控制的进程；
- 子进程无法逃离父进程边界；
- 普通网络/工具失败不会被错误标记为 permission violation。

## S6 — External effect policy

能力：

- stdio MCP process sandbox 选项和 environment policy；
- 独立于 local sandbox coverage 的 MCP/app side-effect 分类；
- WebSearch/WebFetch network policy；
- hooks/plugins 的显式 trusted-control-plane 声明；
- status 输出列出每一个未被 local sandbox 覆盖的 external surface。

验收：

- local sandbox status 永远不暗示 remote MCP/Web 是安全的；
- untrusted 或 mutating MCP calls 进入 external approval path；
- trusted hooks 仍能执行 policy enforcement，其修改后的参数会再次授权。

## S7 — Async permission 生命周期

能力：

- 精确的 `PermissionDeltaRequest` 和确定性的 `BoundaryViolation` events；
- boundary-only reviewer 和最小 one-retry overlays；
- 保留 hard deny，并增加 denial circuit breaker；
- 持久化的 typed park/approve/deny/resume；
- Goal 在 judge 之前暂停，park 期间不消耗 auto-turn；
- snapshots 持久化 profile/backend/request fingerprints，并检测 resume drift。

验收：

- contained actions 不调用 reviewer；
- reviewer input 标识精确的最终参数和 requested delta；
- 参数变化会使 one-shot approval 失效；
- reviewer failure/defer 会 park，而不是扩大访问权限；
- 在不同 effective boundary 下 resume 时给出警告或拒绝继续；
- 完整 non-integration tests、strict mypy、Ruff check 和 format check 全部通过。
