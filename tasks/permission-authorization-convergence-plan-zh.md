# Permission 授权语义收敛计划

> 状态：计划中，2026-08-07。
>
> 前置基础：`decisions/51-unified-permission-sandbox-boundary.md` 与
> `tasks/unified-permission-sandbox-plan.md` 已完成统一 local data plane、verified
> boundary、exact one-shot overlay、external-effect policy 与 durable park/resume。
> 本计划不重做这些能力，而是删除它们上方仍然残留的第二套授权语义。

## 目标结果

OpenHarness 只有一条授权链：

```text
human authorization
        │
        ▼
session RuntimePermissionProfile + non-overridable deny policy
        │
        ▼ compile + verify
EnforcedBoundary
        │
final validated model action
        ├── local + contained ────────────────→ execute
        ├── local + exact boundary delta ─────→ exact review
        ├── external effect ──────────────────→ proactive exact review
        └── hard deny / unrepresentable ──────→ deny or park
```

`ASK` 不再是 engine 内部的一种授权裁决，`AUTO` 也不再把不确定动作解释为
`ALLOW`。自动与人工的差别只在 reviewer 身份：两者审核同一个 exact request，
产生同一种 approve-once / deny / defer 结果。

## 核心不变量

1. 每个 model-controlled effect 只能落入三种安全终态：verified boundary 内执行、
   一次精确批准后执行、或者不执行并 deny/park。
2. 任何 allow 都必须能追溯到 session profile 或一个尚未消费的 exact grant；
   deny-only policy 可以收窄授权，但不能扩大授权。
3. `--auto` 只选择 auto-reviewer，不改变 policy、boundary 或 request 的含义。
4. 没有 verified boundary 的运行不得进入 autonomous local-data-plane posture；读取也
   可能把 host secret 带入 model context，不能因“只读”获得豁免。不允许
   从 sandbox startup/preflight failure 静默回退 host execution。
5. local sandbox trust 不传播到 MCP、Web、Browser、Computer Use 等 external
   surfaces；这些动作必须继续使用 proactive exact approval。
6. deny-only policy 在 hook 前先挡住已知禁止调用；hooks 修改参数后，最终参数必须重新
   validation，并再次经过 deny-only policy 与 boundary/external resolution。Reviewer
   永远只看将要执行的最终参数。
7. Goal 不拥有权限。它只消费 session runtime；permission park 必须发生在 goal
   judge 之前，并且不消耗 auto-turn。
8. `DRY_RUN` 是执行姿态，不是 permission mode；它不执行、不调用 reviewer，也不
   生成可消费 grant。

## 当前问题

当前 production path 同时存在：

- `TierBasedPermissionChecker`：Bash 灾难模式、git commit/push 红线、Tier 1
  敏感路径、`deny_paths`、`permissions.allow/deny/ask`、headless fail-closed、
  cwd 外 ASK 与通用 Bash ASK；
- `RuntimePermissionProfile + EnforcedBoundary`：session 级可执行能力和实际安装事实；
- `PermissionRuntime`：boundary/external exact review、one-shot grant、park/resume；
- `PermissionMode`：把 DEFAULT/AUTO reviewer 姿态与 DRY_RUN 执行姿态混在一个 enum。

最危险的重叠是 `Decision.ASK` 在 `PermissionMode.AUTO` 下直接通过。这个通过不是
exact grant，也没有说明缺少哪项能力。启用 verified sandbox 时，后续 boundary 还能
限制实际后果；未启用 sandbox 时，它会直接使用 legacy host authority。因此旧层可以
作为迁移期兼容保护存在，但不能继续成为 async/auto 架构的授权依据。

## 可靠性 Review 结论

2026-08-07 对当前 production wiring、tests、snapshot 与 unified runtime 做逐路径 review
后，原计划需要以下六项修正；未修正前不进入代码实施：

1. **不能先删 `AUTO → ASK` 再迁移 checker 职责。** 当前每条 interactive Bash 都会
   产生 legacy ASK；若先改变 AUTO 语义，verified sandbox 尚未接管 dispatch 时，正常
   Bash 会全部被旧 checker 拦住。必须先并行安装 deny-only policy、plan capability
   shaping 与 verified local cutover，再拆除混合 `PermissionMode`。
2. **External exact approval 不能依赖 local sandbox boundary。** 当前
   `PermissionDeltaRequest.create` 和 `PermissionRuntime` 强制要求
   `EnforcedBoundary`，导致未启用 local sandbox 时 external request 无法 durable park。
   External request 应绑定 canonical profile 与一份 typed external policy evidence；只有
   local overlay request 才要求 backend/boundary fingerprints。
3. **Reviewer posture 不能从 snapshot 恢复授权。** AUTO/MANUAL 与 DRY_RUN 是本次启动
   的显式选择，不应由历史 transcript 或 snapshot 静默恢复。Snapshot 只持久化 exact
   permission state、profile/boundary evidence 与 parked transition；新 session 重新选择
   reviewer，并用当前事实验证旧 state。
4. **Deny-only policy 必须保留 hook 前后两次检查。** 只检查 hook 修改后的参数会让本应
   立即拒绝的调用先进入 hook。现有“authz before hook + modified arguments reauthorized”
   不变量应保留，只把结果域收窄为 deny-or-no-match。
5. **Opaque Bash violation 不保证能生成 typed delta。** File worker 与 managed network
   proxy 能产生 deterministic violation；Seatbelt 下任意 Bash filesystem denial 通常只是
   non-zero command result，禁止解析 stderr 猜授权。此类动作要么事前声明 typed
   capability，要么在 base boundary 内失败；不能承诺自动 overlay。
6. **Semantic command guard 不是不可绕过的 hard boundary。** git/Bash 字符串检测只是
   defense-in-depth tripwire。真正的 `.git`、credential、network 与 process 约束必须由
   profile/backend 执行；`/permissions` 不得把语义 guard 报告成 installed fact。
7. **Delegated runtime 必须计入 capability 与 coverage。** `SpawnAgent` 不是
   `LOCAL_DATA`，而是 `DELEGATED_RUNTIME`，但它继承完整 registry/runtime 后可以产生
   local effects。Plan mode 的只读 view 与 autonomous startup gate 都必须显式处理它，
   不能只按 Edit/Write/Bash 名字过滤。

修订后的顺序是：先加替代物但不切流，再让 verified path 切到新语义，最后拆旧 API。
任何阶段结束时 production 都必须至少保留上一阶段的安全能力，不能出现“旧防线已删、
新防线尚未接管”的窗口。

## 目标概念模型

### 1. 分开三条正交轴

```text
ExecutionPosture = EXECUTE | DRY_RUN
ReviewPosture    = MANUAL | AUTO
RuntimePosture   = VERIFIED | LEGACY_HOST
```

- `ExecutionPosture` 决定是否产生副作用；
- `ReviewPosture` 决定 exact request 交给谁；
- `RuntimePosture` 是事实，不由 CLI 自称：只有 backend 返回匹配 profile 的 verified
  boundary 才是 `VERIFIED`。

实现不强制使用以上枚举名，但不得再次把三个轴压进一个 `PermissionMode`。

### 2. 将执行前策略收窄为 deny-only

新的执行前策略不拥有正向裁决，只返回：

```text
None
Deny(reason)
```

它负责 framework hard denies、session mode clamp 和无法由 OS 完整表达的语义红线，
不提供 `ALLOW`，也不提供 `ASK`。`None` 只表示“本层没有匹配的拒绝理由”，不代表
动作已经获得执行授权。

### 3. 统一 exact approval

需要额外授权时，controller 必须先形成完整 request：

- final validated arguments；
- authorization context；
- execution domain 与 effect kind；
- active profile 与 execution-domain-specific enforcement evidence；
- 最小 delta 与 data flow；
- hard-deny/representability facts；
- request、grant 与 operation fingerprints。

UI 可以把这个状态显示为“需要批准”，但 engine 不再传递无结构的 `ASK`。

Enforcement evidence 是 closed union：

- local request：verified boundary、backend identity 与 operation fingerprint；
- external request：surface、effect kind、trust source、server/tool identity 与 active
  external policy facts。

External request 不伪造 local boundary，也不因为缺少 Seatbelt/Docker 而失去持久化与
人工审批能力。

## 旧职责迁移表

| 旧职责 | 目标位置 | 迁移原则 |
|---|---|---|
| Tier 1 敏感路径 | `RuntimePermissionProfile.filesystem` / backend hard deny | 能由 OS 表达的必须进入 boundary |
| cwd 内/外判断 | profile + boundary violation | 删除预测式 Tier 3 ASK |
| `deny_paths` 显式根路径 | filesystem profile | 规范化成 cwd/absolute path rule |
| `deny_paths` glob | deny-only compatibility guard 或 unsupported | 不得伪装成 OS 已强制；strict posture 对不可表示规则 fail closed |
| `permissions.allow` path rule | filesystem/network/external profile | 只有可编译能力才能迁移成 allow |
| `permissions.ask` | 删除 | 未预授权 effect 自然产生 exact request |
| Bash allow prefix | 不自动迁移 | 命令前缀不是 containment，启动时警告或拒绝 strict posture |
| Bash 灾难模式 | deny-only policy + OS/resource boundary | 明确字符串检测只是 tripwire，不宣称完整 |
| git commit/push | deny-only semantic policy + `.git`/network boundary | policy 保留人类落章语义，boundary 提供实际兜底 |
| plan-mode deny preset | capability-shaped registry + dispatch defense | 不再构造临时 `PermissionRules` |
| headless fail-closed | verified-runtime startup gate | 不再依赖“缺 allow rule就 deny” |
| `AUTO → ASK allow` | 删除 | AUTO 仅安装 independent reviewer |
| `DRY_RUN` | 独立 execution posture | permission pipeline 不参与 |

迁移不承诺旧规则 1:1 自动转换。无法可靠映射的规则必须被明确列为 deprecated、
unsupported 或 compatibility-only；不能为了兼容而扩大实际授权。

## 分阶段实施

每个阶段的行为变更严格遵循 TDD：先写或更新测试并确认因目标缺口 RED，再修改生产
代码到 GREEN。不得通过弱化现有安全断言制造绿色。

### S0 — 锁定新边界与当前行为画像

目标：在改代码前固定哪些语义要保留、哪些明确删除。

工作：

- 新增 append-only architecture decision，锁定本计划的八条不变量；
- 为当前 `ASK + AUTO → ALLOW` 增加 characterization test，测试名明确标注 legacy；
- 把后续目标行为写成具名 test matrix，但不把长期 RED 提交到 S0：AUTO 不得改变授权
  结果、无 verified boundary 的 autonomous local-data-plane session 启动失败、DRY_RUN
  不调用 reviewer；这些测试在对应 S1–S6 阶段开始时先 RED、同阶段转 GREEN；
- 枚举 production registry 中所有 tool 的 execution domain、effect kind 与 sandbox
  coverage，作为删除 checker 前的 coverage gate；
- 冻结现有 permission settings 与 snapshot schema 的兼容矩阵。

验收：

- legacy characterization 与 coverage/schema baseline 全部 GREEN；
- 后续每条目标行为都有明确落点阶段与预期 RED 原因；
- coverage report 证明 Bash/Read/Write/Edit/Grep/SpawnAgent 的 local effects 都有
  verified runtime 路径；
- decision 明确 legacy host posture 不属于 async security claim。

### S1 — 并行安装 deny-only action policy

目标：先复制旧 checker 中真正必要的拒绝职责，不改变 production dispatch。

工作：

- 新建最小 `ActionDenyPolicy` protocol；返回 `DenyResult | None`，没有 ALLOW/ASK；
- 迁入 framework protected-action rules、git commit/push handoff 与灾难命令 tripwire；
- policy 在 validated original arguments 上先运行一次；PreToolUse 修改参数后重新
  validation，并对 final arguments 再运行一次；
- 明确记录每条 guard 是否同时被 OS boundary 覆盖；无法由 boundary 完整表达的规则在
  `/permissions` 中标为 semantic guard，而不是 installed sandbox fact；
- 本阶段旧 checker 仍保留 production authority，新 policy 先以 shadow/characterization
  方式证明拒绝集合没有缩小。

RED/GREEN 覆盖：

- action policy API 无 ALLOW/ASK constructor；
- hook 在原始调用已被 deny 时不执行；
- hook 改写后的 git commit/push 仍被拒绝；
- profile allow 不能覆盖 deny result；
- no-match 后仍必须进入旧 checker（本阶段）与后续 execution path；
- policy logs 不泄露完整 command、credentials 或外部 payload。

验收：

- 所有准备从 `TierBasedPermissionChecker` 移出的 deny-only 行为都有等强测试；
- 新 policy 无法创建、消费或模拟 exact grant；
- production 行为保持 GREEN，尚未删除旧防线。

### S2 — Plan mode 改为 capability shaping

目标：plan mode 不再依赖 `PermissionRules` 临时 deny overlay。

工作：

- 根据 tool execution metadata 创建只读 registry view：移除所有 `is_read_only=False`
  工具，并显式移除 `DELEGATED_RUNTIME`；不能只按 Edit/Write/Bash 名字过滤；
- system prompt 与 API request 只暴露当前 registry 的 tool schemas；
- dispatch 通过 S1 deny-only policy 保留 mode-scoped defense，拒绝模型伪造或缓存的
  mutation call；
- plan approval 只恢复 default capability view，不生成 grant、不自动执行；
- 新路径 GREEN 后删除 `overlay_plan_permissions` 与 `plan_mode_preset` 的 production
  wiring，旧 helper 可暂留兼容测试到 S7。

RED/GREEN 覆盖：

- plan turn 的 API request 不包含 mutation tool schemas；
- 伪造任意 mutating 或 delegated-runtime tool call 仍被拒绝；
- SpawnAgent 不出现在 plan tool schema 中，不能借子 agent 绕过 plan clamp；
- approve 后下一 turn 恢复工具，但不执行之前计划；
- Goal 与 plan 组合仍不在 plan posture 触发 judge。

模型行为验证：

- 该阶段修改 production tool catalog，必须按
  `evals/tool_choice/dataset_card.md` 的 reference policy 运行 replay；
- replay 只验证 scorer/cassette 接线，随后使用 qwen-max live 重新 ratify 9/9 pass bar；
- 若新增 plan-specific tool-choice cases，先更新 dataset card 的 capability/input/judgment
  declarations，再录制 cassette。

### S3 — Exact request evidence 按 execution domain 解耦

目标：external approval 不再借用 local sandbox facts，先把统一 lifecycle 的数据合同
做正确。

工作：

- 将 exact request 的 enforcement evidence 改为 closed union：local boundary evidence
  与 external policy evidence；
- local evidence 保留 profile/boundary/backend/operation fingerprints；
- external evidence 绑定 canonical profile、surface、effect kind、trust source、tool/server
  identity 与 active external policy facts，不要求 `EnforcedBoundary`；
- 将 grant ledger、denial circuit、park/approve/deny/resume 从 local overlay 安装职责中
  解耦，使 authorization runtime 在无 local sandbox 时也能持久化 external request；
- local overlay resolver 仍要求 verified boundary 与同 backend one-shot replacement；
- snapshot 持久化 typed request/evidence；旧 local-only state 使用显式 schema migration。

RED/GREEN 覆盖：

- no-sandbox external mutating call 能生成 exact parked request，而不是字符串错误；
- external request 不携带伪造的 backend/boundary fingerprint；
- local request 缺 boundary evidence 时构造失败；
- manual 与 auto reviewer 看到 byte-equivalent request envelope；
- surface/tool/trust/effect/arguments 任一变化都使旧 grant 无效；
- resume 在当前 external policy facts 漂移时 fail closed。

验收：

- external authorization 的安全性和持久化能力不依赖 Seatbelt/Docker 是否启用；
- local overlay 仍只消费 verified local request。

### S4 — Verified local path 切换到新语义

目标：sandboxed local dispatch 不再使用 legacy PermissionChecker grant/ASK 语义。

工作：

- final local operation 经过 S1 policy 后直接交给 active `SandboxSession`；
- contained operation 零 reviewer；只有 deterministic `BoundaryViolation` 生成最小 delta；
- approved local delta 只通过同 backend 的 verified one-shot overlay 重试一次；
- unrepresentable violation、unsupported overlay、boundary/backend/profile drift 一律
  deny 或 park；禁止解析 stderr 猜测 permission delta；
- structured Read/Write/Edit/Grep 使用 operation 自身的 path/effect；
- 为 opaque Bash 设计可选、typed、与 command 同 fingerprint 的 capability declaration
  （filesystem access / network domains）。Declaration 只产生 request，绝不授予能力；
  未声明或无法确定的 Bash violation 保持普通失败；
- SpawnAgent 继承相同 profile、sandbox session、deny policy 与 authorization runtime。

RED/GREEN 覆盖：

- workspace 内 Read/Write/Edit/Grep/Bash 均遵守同一 boundary；
- cwd 外结构化 file operation 不再产生 legacy ASK，而是 deterministic violation；
- Bash 绕过字符串 path 识别仍无法越过 OS boundary；
- Bash capability declaration 与不同 command/arguments 不能共享 grant；
- contained local actions 不调用 reviewer；批准变化后的 arguments 不消费旧 grant；
- sandboxed production context 不调用 `TierBasedPermissionChecker`，legacy host context
  暂时仍调用。

模型行为验证：

- 若 Bash schema 增加 capability declaration，按 tool-choice dataset card 更新 input spec
  与 cases，并在 qwen-max live re-ratify；
- 不把“模型是否总能正确声明”作为安全条件，遗漏声明必须由 base boundary fail closed。

验收：

- verified local effect 的合法执行来源只有 base boundary 或 consumed exact overlay；
- `AUTO → legacy ASK allow` 在 verified local path 已不可达，但 legacy API 尚未删除。

### S5 — 拆分 execution/review posture，并建立 autonomous gate

目标：在 verified local path 已接管后，再消除 `PermissionMode` 的概念混合。

工作：

- 将 DEFAULT/AUTO 改为本次启动的 reviewer 选择；DRY_RUN 拆成独立 execution posture；
- CLI `--auto` 只决定是否构造 `LlmPermissionReviewer`；
- CLI `--dry-run` 只决定是否跳过 effect dispatch，不调用 reviewer、不生成 grant；
- reviewer/execution posture 不写入新 snapshot，也不从旧 snapshot 恢复 authority；旧
  `permission_mode` 只用于 schema migration/诊断，resume 使用当前 CLI 明确选择；
- SpawnAgent 继承当前 runtime objects 与 dry-run execution fact，不从 snapshot 推断
  reviewer；
- 为 autonomous posture 建立 gate：只要 registry 暴露 local data-plane capability 或
  delegated runtime，就必须存在覆盖其 read/write/search/command effects 的 verified
  boundary，并证明 delegated runtime 继承同一 runtime；
- sandbox startup/preflight/coverage failure 不得回退 host。

Autonomous posture 至少包括：

- `--auto`；
- active `/goal` 自动续 turn；
- headless 中暴露任何 model-controlled local data-plane tool 的运行。

无 sandbox 的 autonomous read-only local run 也不豁免：Read/Grep 可能读取 host secret，
随后经受信 LLM transport 进入 model context。只有 registry 不包含任何 local data-plane
或 delegated-runtime tool 的纯 control-plane/external-only run，才可以不要求 local
boundary；external exact policy 仍独立生效。Bash 永远属于 local
command/general-compute capability。

RED/GREEN 覆盖：

- `--auto` 不改变 exact request 或 policy 结果，只改变 reviewer；
- no-sandbox + AUTO 在模型调用前失败；
- no-sandbox session 设置暴露 local data-plane tools 的 `/goal` 时立即失败；
- DRY_RUN 在任何 reviewer/runtime posture 下都不产生副作用或 grant；
- resume 读取旧 snapshot，但不会静默恢复 AUTO 或 DRY_RUN；
- reviewer failure/defer durable park，Goal 在 judge 前暂停且不消耗 auto-turn。

验收：

- engine 内不存在 reviewer posture → ALLOW 分支；
- runtime verification 是 backend fact，不是 CLI enum value。

### S6 — 建立 canonical profile 的唯一配置入口

目标：让 user-facing allow intent 只有一个 source of truth，并移除 sandbox config 中的
授权旁路。

工作：

- 为完整 `RuntimePermissionProfile` 建立正式 settings 入口，包含 filesystem、network、
  environment、process 与 external tools；
- backend selection/image/runtime 等保留为 backend config，不再承载 permission intent；
- `_sandbox_profile` 不再从分散的 `sandbox_network_*`、external policy 与硬编码规则临时
  拼装授权；
- 建立 legacy config translator：只转换语义等价且 backend 可表示的规则；
- 对 command prefix allow、ASK rule、无法强制的 glob allow 等拒绝自动迁移；
- `/permissions` 同时显示 canonical intent、translation warnings、semantic guards、
  unsupported features、external evidence 与 installed local facts。

RED/GREEN 覆盖：

- 语义相同的配置产生相同 fingerprint；
- 冲突或不可表示的 strict profile 在 backend open 前失败；
- legacy 显式 path rules 可确定性迁移；
- unsafe/unrepresentable legacy allows 不会扩大 profile；
- external policy 不再从 sandbox config 旁路进入 QueryContext；
- permission reviewer、snapshot 与 `/permissions` 看到同一个 profile fingerprint。

验收：

- production session 只接受一个 canonical profile；
- backend config 只选择实施机制，不能扩大授权。

### S7 — 删除 legacy production layer

目标：完成 API、配置、测试与文档收尾。

工作：

- 从 `QueryContext`、snapshot writer/restore、SpawnAgent 中移除
  `permission_checker` 与旧 `permission_mode`；
- 删除生产 wiring 后，再删除 `Decision`、`DecisionResult`、`PermissionChecker`、
  `DenyListChecker`、`TierBasedPermissionChecker` 与不再使用的 rule matcher；
- 删除或迁移 `Settings.deny_paths`、`Settings.permissions.allow/deny/ask`；
- 对 legacy env vars 提供一个有截止版本的 startup migration error，而不是静默忽略；
- 更新 README、中文 README、`.env.example`、CLI help、module docstrings 与 status output；
- 删除只验证旧架构形状的测试，保留并迁移其行为不变量测试。

验收：

- `rg` 确认 production `src/` 无 `Decision.ASK`、`AUTO → ALLOW`、
  `TierBasedPermissionChecker`、`permission_checker`；
- public import surface 不再暴露两套授权 API；
- legacy config 被明确拒绝或迁移，不会被无声忽略；
- fresh session 与 snapshot resume 使用同一 canonical profile/boundary/reviewer semantics。

### S8 — Dogfood、eval 与最终删除门

目标：证明收敛后的架构不仅机制正确，也支持真实无人工作。

机制验证：

```bash
uv run pytest -m "not integration" -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
```

必须额外运行平台负向 integration：

- outside/protected/deny-read filesystem；
- child-process boundary inheritance；
- network deny/domain overlay/private/loopback；
- environment credential filtering；
- exact overlay one-shot consumption；
- parked snapshot resume 与 boundary drift；
- no-sandbox autonomous startup refusal。

模型行为验证：

- tool catalog 或 system prompt 变化：按 `evals/tool_choice/dataset_card.md` live
  re-ratification；
- 若修改 goal judge prompt/input：按 `evals/verify_judge/dataset_card.md` 使用 qwen-max
  live 重跑 8/8；replay 不代替 live；
- 若修改 permission reviewer prompt/envelope：先建立独立 permission-review dataset
  card，覆盖 approve-once、deny、defer、hard-deny exclusion、prompt injection、argument
  drift 与 data-exfiltration cases，再 ratify pass bar。

Dogfood 场景：

1. `/goal` 在 workspace 内编辑并运行测试，全程零 permission review；
2. `/goal` 需要 PyPI，产生 domain delta，auto-review 后只开放一次；
3. reviewer defer，Goal 在 judge 前 park；人工 approve + resume 后精确重试；
4. 相同意图但参数变化，旧 grant 无效；
5. git commit/push 被保留为人工落章；
6. no-sandbox 下尝试 autonomous local read 或 mutation，均在执行任何工具前失败；
7. mutating external tool 即使 surface allow 仍进入 exact review。

最终删除门：

- 上述 dogfood 与全部 gates 通过；
- `/permissions` 能独立回答“配置意图、安装事实、未覆盖 surface、parked request”；
- 一次正常 goal 的 reviewer call 数远小于 tool call 数；
- 不存在依赖 legacy checker 才能守住的 production safety invariant。

## 可执行 `/goal` 切分

上面的 S0–S8 描述架构迁移顺序，但仍不适合作为单次 `/goal` 的边界。实际执行拆成
下面 12 个串行 goal。一个 goal 只跨一个主要架构接缝；前一个 goal 的完成证据是后一个
goal 的输入，不能并行切流。

### 所有 goal 共用的执行合同

1. 只以当前 production code、tests、README 与相应 dataset card 判断当前事实；本计划和
   decision 只说明目标与设计原因。
2. 行为变更在同一个 goal 内完成 RED → GREEN；不得把长期 RED 留给下一个 goal，也不得
   弱化断言。每个 goal 结束时仓库必须全绿。
3. 每个 goal 至少运行相关定向测试，以及：

   ```bash
   uv run pytest -m "not integration" -q
   uv run mypy --strict src/
   uv run ruff check
   uv run ruff format --check
   ```

4. 只有改变 tool catalog、prompt、review envelope 或 judge 输入的 goal 才触发对应 live
   eval；一旦触发，replay 不能替代 dataset card 要求的 live re-ratification。
5. 不在同一个 goal 中同时“安装替代物”和“删除被替代物”。除 G10 外，legacy public API
   可以保留；除明确写出的 production path 外，不提前切流。
6. 如果发现前置不变量不成立，先把 request durable park 或记录为当前 goal 的 blocker；
   不得用扩大 profile、恢复 `AUTO → ASK allow`、解析 stderr、跳过 verified boundary 等
   方式绕过。
7. 每个 goal 的交付说明必须列出：RED 证据、GREEN 证据、完整验证结果、仍在使用的 legacy
   path、下一 goal 的真实前置条件。除非用户另行要求，不创建 commit 或进入下一 goal。

### 依赖图

```text
G0 baseline
  → G1 deny-only shadow
  → G2 plan capability shaping
  → G3 typed exact-request evidence
  → G4 external authorization runtime
  → G5 structured local cutover
  → G6 Bash local cutover
  → G7 delegated-runtime inheritance
  → G8 posture + snapshot split
  → G9 autonomous startup gate
  → G10 canonical profile
  → G11 legacy removal + final ratification
```

### G0 — 固定事实基线与删除门

**对应阶段：** S0。

**执行证据：** `tasks/permission-authorization-convergence-g0-baseline-zh.md` 汇总当前行为、
registry coverage、settings/snapshot compatibility 与后续目标测试矩阵；其中具名测试是
机器删除门，文档本身不替代 production code/tests。

**目标结果：** 在不改变 production 行为的前提下，用 GREEN 的 characterization tests 和
机器可检查的矩阵固定 legacy 行为、tool domain/effect/coverage、settings 与 snapshot
schema；复核 decision 52 足以约束后续迁移。

**边界：** 不新增长期 RED，不实现 deny policy，不切 dispatch。已有
`ASK + AUTO → allow` 只能作为明确标注 legacy 的当前行为画像，不能写成目标语义。

**完成证据：** coverage matrix 包含 Read/Grep/Write/Edit/Bash/SpawnAgent 及全部 production
tools；兼容矩阵指出旧 snapshot/config 的读取与新 schema 写入策略；全套质量门 GREEN。

**可直接执行：**

```text
/goal 执行 tasks/permission-authorization-convergence-plan-zh.md 的 G0。只固定当前事实基线与
删除门，不改变 production 授权行为。按共同执行合同完成 characterization、registry
coverage 和 settings/snapshot compatibility matrix；结束时报告 RED/GREEN 不适用或证据、
完整验证结果、仍存 legacy path，然后停止。
```

### G1 — 安装 deny-only policy，但只做 shadow

**对应阶段：** S1。

**依赖：** G0 coverage 与兼容矩阵 GREEN。

**目标结果：** 建立只能返回 `DenyResult | None` 的 action policy，迁入 protected action、
git handoff 与灾难命令 tripwire，并在 hook 前和 hook 改写后的 final arguments 上保持两次
检查；production 正向授权仍由旧 checker 负责。

**边界：** policy 不能产生 ALLOW、ASK、grant 或 overlay；不删除 checker，不改变 AUTO，
不把 semantic guard 报告成 installed boundary fact。

**完成证据：** shadow comparison 证明计划迁出的拒绝集合未缩小；original deny 不进入 hook；
hook 改写无法绕过；no-match 不被误解释为 allow；全套质量门 GREEN。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G1：以 TDD 安装 deny-only ActionDenyPolicy 并保持 shadow，
保留 legacy checker 的 production authority。严格遵守 hook 前后复查、不授予能力、不切流；
跑定向测试和共同质量门，交付证据后停止。
```

### G2 — Plan mode capability shaping

**对应阶段：** S2。

**依赖：** G1 policy 可在 dispatch 处提供 mode-scoped deny defense。

**目标结果：** plan turn 的 registry、prompt 和 API schema 不再暴露 mutating 或
`DELEGATED_RUNTIME` 工具；伪造/cached tool call 仍被 dispatch policy 拒绝；批准 plan 只恢复
默认 capability view。

**边界：** 不生成 permission grant，不执行被批准的旧 plan，不修改通用 exact approval；
新路径 GREEN 后才移除 plan overlay 的 production wiring。

**完成证据：** SpawnAgent 与所有 `is_read_only=False` 工具从 plan schema 消失；伪造调用被
拒绝；tool-choice replay 接线正确且 qwen-max live 达到 dataset card 的 9/9 pass bar。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G2：把 plan mode 改成 metadata-driven capability-shaped
registry，并保留 dispatch deny defense。使用 TDD，按 tool_choice dataset card 完成 replay
和 qwen-max live re-ratification；全绿并提交行为证据后停止。
```

### G3 — 建立 execution-domain-specific exact evidence 合同

**对应阶段：** S3 的数据合同部分。

**依赖：** G2 全绿；G0 已枚举所有 execution domains。

**目标结果：** exact request 使用 closed evidence union：local request 必须携带 verified
boundary/backend/operation facts，external request 必须携带 surface/effect/trust/tool-server/
policy facts；fingerprint、序列化和旧 schema migration 均能区分两类 evidence。

**边界：** 本 goal 只建立类型、fingerprint、serialization 与 validation 合同，不把 external
runtime 从 local boundary 上切开，不改变 reviewer 或 dispatch 行为。

**完成证据：** local 缺 boundary 构造失败；external 不含伪造 local fingerprints；evidence
任一安全相关字段漂移都会使 request/grant 失效；新旧 snapshot round-trip GREEN。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G3：以 TDD 建立 local/external exact-request evidence 的
closed union、fingerprint、serialization 和 schema migration。只改数据合同，不切运行时；
全套质量门 GREEN 后报告兼容性证据并停止。
```

### G4 — External authorization runtime 与 local sandbox 解耦

**对应阶段：** S3 的运行时部分。

**依赖：** G3 typed evidence 已稳定并 GREEN。

**目标结果：** 无 local sandbox 时，external effect 仍能形成同一 exact envelope，进入
manual/auto reviewer，approve-once、deny 或 durable defer/park/resume；local overlay resolver
仍严格要求 verified boundary。

**边界：** 不放宽 external proactive review，不允许 external grant 安装 local overlay，
不切 structured local dispatch，不改变 AUTO 的 legacy local 语义。

**完成证据：** no-sandbox external mutation 能 park/resume；manual/auto reviewer 收到
byte-equivalent request；policy/tool/trust/arguments drift fail closed；local request 无 boundary
仍失败。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G4：把 external exact approval ledger、review、park/resume
从 EnforcedBoundary 生命周期解耦，同时保持 local overlay 必须 verified。按 TDD 验证
no-sandbox external 全生命周期与 drift fail-closed；全绿后停止。
```

### G5 — 结构化本地工具切到 verified dispatch

**对应阶段：** S4 的 Read/Grep/Write/Edit 部分。

**依赖：** G1 deny policy 与 G3/G4 authorization runtime GREEN。

**目标结果：** Read、Grep、Write、Edit 的 final validated operation 在 deny policy 后直接
交给 active SandboxSession；contained 零 review，deterministic violation 才形成最小 local
delta，一次批准只重试一次。

**边界：** Bash 和 SpawnAgent 暂不切流；legacy host interactive path 仍保留 checker；禁止
预测 cwd 内外授权或从文本错误推导 delta。

**完成证据：** verified structured path 不调用 legacy checker；读与写都受 boundary；参数、
profile、backend 或 operation 漂移不能消费旧 grant；unsupported overlay deny/park；平台负向
filesystem integration GREEN。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G5：仅将 Read/Grep/Write/Edit 切到 deny-policy + verified
SandboxSession + exact one-shot overlay。不要切 Bash/SpawnAgent，也不要删除 legacy API；以
TDD 和 filesystem 负向 integration 证明 contained/violation/drift 行为，全绿后停止。
```

### G6 — Bash 切到 verified base boundary

**对应阶段：** S4 的 Bash 部分。

**依赖：** G5 证明 structured local cutover 模式可靠。

**目标结果：** verified posture 的 Bash 不再通过 legacy ASK 获得正向授权，所有 child
process 继承 base boundary；managed network 等 deterministic violation 可以形成 exact delta，
opaque filesystem denial 保持普通 command failure。

**边界：** 绝不解析 stderr；不把模型声明当授权。typed Bash capability declaration 只有在
有独立 schema、最小性与 fingerprint 测试且确有产品需求时才加入；否则记录为明确不支持，
不阻塞本 goal。

**完成证据：** Bash 与 child process 无法越界；contained command 零 review；可确定 network
violation 的 one-shot overlay 正确；不同 command/args 不能共享 grant；若改 schema，完成
tool-choice live re-ratification。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G6：将 verified Bash 切到 base boundary，保留 deterministic
violation 的 exact lifecycle，对 opaque denial fail closed，禁止 stderr 推断。把 typed
capability declaration 视为需证据的可选项；按 TDD、child-process/network integration 及
必要的 tool-choice live eval 验证，全绿后停止。
```

### G7 — 收紧 delegated-runtime 继承与 coverage

**对应阶段：** S4/S5 的 SpawnAgent 接缝。

**依赖：** G5/G6 已覆盖所有直接 local data-plane effects。

**目标结果：** SpawnAgent 及未来 `DELEGATED_RUNTIME` tool 必须继承同一 canonical profile、
SandboxSession、deny policy、authorization runtime 与 execution facts；coverage gate 能追踪
delegate 最终可达的 local/external effects。

**边界：** delegate 不复制 grant、不扩大 profile、不自行选择 reviewer；不在本 goal 改
snapshot authority 或 CLI posture。

**完成证据：** child 无法取得 parent 未授权能力；one-shot grant 不能被复制或重复消费；
park 状态与 denial circuit 一致；缺继承事实时 SpawnAgent 在 dispatch 前 fail closed。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G7：以 TDD 固定 SpawnAgent/DELEGATED_RUNTIME 对 profile、
sandbox、deny policy、authorization runtime 和 one-shot ledger 的安全继承；任何 coverage
缺口在 dispatch 前 fail closed。不要修改 CLI posture；全绿后停止。
```

### G8 — 拆分 execution/review posture 与 snapshot authority

**对应阶段：** S5 的状态模型部分。

**依赖：** G4 exact lifecycle 与 G7 runtime inheritance GREEN。

**目标结果：** AUTO/MANUAL 只选择当前 reviewer，DRY_RUN 只控制本次执行；新 snapshot 不
持久化 reviewer/execution authority，旧 `permission_mode` 只作为 schema migration 输入。

**边界：** 本 goal 不建立最终 autonomous coverage gate，不删除 legacy checker 类型；不得
让 resume 静默恢复 AUTO/DRY_RUN，也不得让 DRY_RUN 调 reviewer 或生成 grant。

**完成证据：** manual/auto 生成相同 request；AUTO 不改变 policy 结果；DRY_RUN 零副作用、
零 reviewer、零 grant；旧 snapshot 可读但当前 CLI/config 始终胜出。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G8：拆开 review posture、execution posture 与 verified
runtime fact，并迁移 snapshot 使其不恢复 reviewer/DRY_RUN authority。以 TDD 覆盖 AUTO
同请求、DRY_RUN 零 effect、旧 snapshot migration；保留 legacy API，全部 GREEN 后停止。
```

### G9 — 建立 autonomous startup/continuation gate

**对应阶段：** S5 的 gate 部分。

**依赖：** G7 coverage 可证明 delegate 继承；G8 posture 已拆分。

**目标结果：** `--auto`、active `/goal` continuation、headless local/delegated runtime 在任何
模型调用前验证 boundary coverage；sandbox open/preflight/coverage failure 直接失败，不
回退 host。Goal 遇 defer 必须在 judge 前 park 且不消耗 auto-turn。

**边界：** 只有 registry 完全不含 local data-plane/delegated-runtime 的纯 control-plane 或
external-only run 可无 local boundary；这不豁免 external policy。只读 local tool 也不豁免。

**完成证据：** no-sandbox AUTO、Goal、headless local run 均在模型调用前失败；pure external
run 可启动但 mutation 仍 exact review；defer/approve/resume 的 Goal turn accounting 正确；
startup 失败无静默 fallback。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G9：实现所有 autonomous posture 的 pre-model verified
coverage gate，包含 --auto、/goal、headless 与 delegated runtime；只允许纯 external/control
plane 例外，external policy 仍独立。以 TDD 和 startup/Goal park-resume integration 验证，
全绿后停止。
```

### G10 — 建立 canonical profile 唯一配置入口

**对应阶段：** S6。

**依赖：** G9 已用 runtime facts 执行 gate，G3 fingerprint 合同稳定。

**目标结果：** filesystem/network/environment/process/external intent 只来自一个
RuntimePermissionProfile settings source；backend config 只选择实施机制。可等价 legacy
配置确定性翻译，不可表示 allow 明确拒绝或收窄。

**边界：** 先完成双读单写与 warnings，不在本 goal 删除 legacy public types/config parser；
不自动迁移 command-prefix allow、ASK 或不可强制 glob allow。

**完成证据：** 等价配置得到同 fingerprint；strict profile 在 backend open 前拒绝冲突/
不可表示项；snapshot/reviewer/runtime/status 使用同 profile；`/permissions` 区分 intent、
installed fact、semantic guard、unsupported 与 parked request。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G10：建立 canonical RuntimePermissionProfile 的唯一用户
配置入口和安全 legacy translator，backend config 不得携带授权意图。完成双读单写、状态
可观测性与 fail-closed tests；保留 legacy parser/public types，全部 GREEN 后停止。
```

### G11 — 删除 legacy 层并完成最终 ratification

**对应阶段：** S7 + S8。

**依赖：** G0–G10 全部完成，coverage、migration 与 dogfood 前置场景可证明无需旧 checker。

**目标结果：** 删除 production `PermissionChecker`/`Decision.ASK`/混合 `PermissionMode`、旧
settings 与 snapshot wiring；完成明确 migration error、README/CLI/status 更新、全部平台
负向 integration、所触发的 live eval 与七个 dogfood 场景。

**边界：** 删除前先用 `rg`、coverage tests 与负向 integration 证明没有 safety invariant
依赖旧层；若任一 invariant 仍依赖它，本 goal 不删除并报告 blocker，不能用兼容分支静默
放行。

**完成证据：** `src/` 无旧生产符号和 `AUTO → ASK allow`；新旧 snapshot/config 行为有明确
结果；permission-review envelope 若改变则已有 dataset card 与 live pass；完整质量门、平台
integration、tool-choice/goal-judge（若触发）和 dogfood 全部通过。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G11：只在 G0–G10 的删除门全部满足后删除 legacy
permission production layer，并完成 migration errors、文档、平台负向 integration、必要 live
eval 和七个 dogfood。发现仍依赖旧层的 safety invariant 时 durable 记录 blocker，不得删后
放宽。交付最终 rg/测试/eval/dogfood 证据后停止。
```

### 执行节奏

- 默认一次只启动一个 goal；完成后先人工检查交付证据，再启动下一个。
- G3→G4、G5→G6、G8→G9、G10→G11 是明确的“先建合同/替代物，再切流/删除”边界，
  不合并执行。
- G6 的 typed Bash capability declaration 是条件项，不是完成统一授权链的必要条件；没有
  足够证据时，选择 base-boundary fail-closed 比新增模型自述协议更可靠。
- G11 是唯一允许批量删除 legacy surface 的 goal；在它之前出现的“unused”旧代码也先保留，
  除非删除与安全切流完全无关且有独立证明。

## 兼容与发布策略

建议分两个 release，而不是一个 commit 内强删：

### Release A — 双读、单写

- 读取 canonical profile；
- 对可安全转换的 legacy config 显示迁移结果与 deprecation warning；
- snapshot 读取旧 schema、只写新 schema；
- verified async path 已不调用 legacy checker；
- legacy host interactive path 暂时保留 deny-only compatibility guard。

### Release B — 删除旧授权面

- 删除 `permissions.allow/deny/ask`、`deny_paths` 与 `PermissionMode`；
- legacy env 启动时报带替代配置示例的明确错误；
- 若继续保留 no-sandbox interactive mode，必须命名为 `legacy-host`，并明确它是
  synchronous/best-effort，不属于 async security claim。

不建议长期维持双写或让同一条规则同时进入 checker 与 profile；这会重新制造两个
fingerprint、两个 precedence 和两个用户心智模型。

## 风险与控制

### 风险 1：删除 checker 后放开原本被隐式阻止的动作

控制：先建立 coverage matrix 和 deny-only policy，再切 local dispatch；S1/S2 必须先于
S4，S4 必须 GREEN 后才能在 S5 拆除 `AUTO → ASK`，并以负向 integration 为门。

### 风险 2：legacy rules 无法等价迁移

控制：只迁移 backend 可表示的显式能力；不确定时拒绝 strict posture并给出具体替代
配置。安全迁移允许收窄，绝不允许静默扩大。

### 风险 3：Plan mode 只隐藏工具但挡不住伪造调用

控制：registry shaping 与 dispatch deny defense 同时存在；前者减少模型选择，后者守住
执行边界。

### 风险 4：过度依赖 sandbox，忽略边界内破坏

控制：worktree/checkpoint 提供可逆性，protected paths 与 hard policy 保护不可逆动作，
测试和 Goal judge 管正确性；不把这些问题重新塞回 per-call ASK。

### 风险 5：AUTO 无 sandbox 后用户体验变差

控制：startup 时立即解释缺少的 backend/coverage 与修复命令，不让任务运行到中途才
失败。安全承诺优先于静默兼容。

### 风险 6：External request 继续被 local boundary 生命周期绑架

控制：S3 在 local cutover 前先拆 typed evidence 与 grant ledger；测试 no-sandbox
external park/resume。External policy drift 与 local backend drift 分开验证，禁止用空值或
伪 fingerprint 兼容旧 schema。

### 风险 7：Snapshot 静默恢复 AUTO 权限

控制：snapshot 只保存事实与未决状态，不保存下一次运行的 reviewer authority。Resume
必须使用当前 CLI/config 显式选择 reviewer，并针对当前 profile/evidence 重新验证 parked
request 与 grant state。

### 风险 8：为 Bash capability declaration 误建自授权通道

控制：declaration 只能构造 exact request，必须绑定完整 command、final arguments 与
operation fingerprint；遗漏 declaration 由 base boundary fail closed，过宽 declaration
由 hard policy/reviewer deny 或 defer。模型声明永远不直接修改 profile 或 overlay。

## 建议的提交边界

每个提交保持一个可回滚语义：

1. decision + characterization/RED tests；
2. deny-only action policy（shadow，旧防线仍在）；
3. plan capability shaping + tool-choice re-ratification；
4. execution-domain evidence union + external authorization runtime；
5. verified local dispatch cutover + optional Bash typed declarations；
6. split postures + autonomous startup gate + snapshot migration；
7. canonical profile settings + legacy translator；
8. remove legacy APIs/config；
9. docs + permission-review eval + dogfood evidence。

任一提交不得同时删除旧防线并引入其替代物；替代物必须先以测试证明生效，下一提交
才能删除旧路径。

## 完成定义

本计划完成时，下面这句话必须可以从代码和测试中直接证明：

> OpenHarness 不再通过预测一个工具调用“看起来是否安全”来授予执行权。用户先授权
> session 能力，backend 把它编译成 verified boundary；边界内动作直接执行，边界外或
> external effect 只接受一次精确授权，无法决定时持久化 park。Auto 只替换 reviewer，
> 从不替换授权语义。
