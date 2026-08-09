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

## 可执行 `/goal` 切分（G0 后修订）

G0 完成后重新 review，原 G1–G11 的粒度过细。它们主要是技术接缝、TDD 阶段与建议提交
边界，不是 11 个独立的业务结果。`/goal` 是可跨 turn 持续执行的工作单元，没有必要在
每次类型变更、单类 tool 切流或 snapshot 调整处停下来等待人继续分配注意力；那反而违背
本项目从 sync 走向 async 的初衷。

剩余工作收敛为 3 个串行大 goal。S1–S8 继续作为 goal 内部的实施顺序与安全 checkpoint，
不再作为用户需要逐个启动的任务。

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
5. 同一个大 goal 可以包含“安装替代物 → 证明 GREEN → 切流 → 删除局部旧 wiring”，但顺序
   不可颠倒。每次切流前必须有替代路径的定向 GREEN 证据；public legacy API/config 的批量
   删除只允许发生在 G3。
6. 如果发现前置不变量不成立，先把 request durable park 或记录为当前 goal 的 blocker；
   不得用扩大 profile、恢复 `AUTO → ASK allow`、解析 stderr、跳过 verified boundary 等
   方式绕过。
7. 每个 goal 的交付说明必须列出：RED 证据、GREEN 证据、完整验证结果、仍在使用的 legacy
   path、下一 goal 的真实前置条件。除非用户另行要求，不创建 commit 或进入下一 goal。

### 为什么是三个，而不是十一个或两个

- **不是十一个：** deny policy、typed evidence、structured/Bash/delegate 分批切流、posture
  与 snapshot 拆分，都是同一业务结果的内部实施步骤。把它们做成独立 `/goal` 会制造九次
  额外的人类验收与续派，不增加安全性。
- **不建议两个：** 若把“建立新授权内核”和“所有 autonomous path 切流”合在一起，一个
  goal 会同时修改 permission models、ledger、engine dispatch、tool catalog、CLI、snapshot、
  Goal continuation 与 SpawnAgent。替代物尚未稳定时就进入切流，回归定位和回滚范围过大。
- **保留三个真实边界：** 新内核完整可表达所有请求之后才能切流；verified async path 全部
  GREEN 之后才能删除旧产品面。这两个先后关系是安全和发布需要，而不是代码组织偏好。

### 依赖图

```text
G0 当前事实基线（已完成）
  → G1 统一授权内核
  → G2 verified async execution 全面切流
  → G3 canonical product surface 与 legacy removal
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

### G1 — 建立统一授权内核

**业务结果：** 系统用一套 exact authorization lifecycle 表达 local boundary delta 与
external effect；无 local sandbox 时 external request 仍可 approve-once、deny、defer、durable
park/resume。执行前 policy 只负责拒绝，不能产生正向授权。

**对应内部阶段：** S1 + S3。先以 shadow 安装 deny-only policy，再建立 typed evidence，最后
把 external ledger/review/park/resume 从 local overlay 安装职责中解耦。

**包含工作：**

- `ActionDenyPolicy: DenyResult | None`，保留 hook 前 original 与 hook 后 final 两次检查；
- local/external closed evidence union、fingerprints、serialization 与旧 snapshot migration；
- grant ledger、denial circuit、review 与 park/resume 独立于 local boundary；
- local overlay resolver 继续强制 verified boundary、same backend 与 exact operation；
- 若 reviewer envelope/prompt 改变，同 goal 建立或更新 permission-review dataset card 并按
  reference policy live ratify，不能把 eval 债务留到 G3。

**本 goal 不做：** 不切 local tool dispatch，不改变 AUTO/DRY_RUN，不修改 plan catalog，不
删除 checker 或 legacy settings。deny-only policy 在 production 只做 shadow/拒绝等价验证。

**完成证据：**

- policy API 无 ALLOW/ASK/grant；shadow deny 集合不缩小，hook 无法绕过；
- local 缺 verified evidence 构造失败，external 不含伪 local fingerprints；
- no-sandbox external mutation 完成 exact review 与 durable park/resume；
- manual/auto reviewer 收到 byte-equivalent request，安全相关 drift 全部 fail closed；
- snapshot 新旧 schema round-trip 与完整质量门 GREEN。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G1“建立统一授权内核”。按 S1→S3 的内部顺序和共同执行
合同，以 TDD 完成 deny-only shadow、local/external typed evidence、独立 exact ledger/review/
park/resume 与 snapshot migration。保持 local dispatch、AUTO/DRY_RUN、plan catalog 和 legacy
public API 不变；若 reviewer envelope 变化，同 goal 完成 permission-review live ratification。
交付 RED/GREEN、完整质量门、仍存 legacy path 后停止。
```

### G2 — 将 verified autonomous execution 全面切到新语义

**业务结果：** `--auto` 与 `/goal` 可以在预授权 verified boundary 内持续工作，不再逐工具
消耗人的注意力；只有真正的 exact boundary/external crossing 才 review 或 park。AUTO 只替换
reviewer，DRY_RUN 只控制执行，没有 sandbox coverage 时 autonomous run 在模型调用前失败。

**对应内部阶段：** S2 + S4 + S5。内部仍按 plan shaping → structured tools → Bash →
delegated runtime → posture/snapshot → autonomous gate 的顺序切流；这些是同一 goal 内的
checkpoints，不再要求用户逐段续派。

**包含工作：**

- plan capability-shaped registry 与 dispatch deny defense，移除 production plan rule overlay；
- Read/Grep/Write/Edit、Bash 与 deterministic violation 的 verified dispatch；
- SpawnAgent/delegated runtime 的 profile/sandbox/policy/ledger 安全继承与 coverage；
- review/execution/runtime 三轴拆分，新 snapshot 不恢复 reviewer/DRY_RUN authority；
- `--auto`、active Goal、headless 的 pre-model verified coverage gate；
- permission defer 在 Goal judge 前 park，不消耗 auto-turn；
- tool catalog/schema 变化按 tool-choice dataset card replay + qwen-max live ratification。

**本 goal 不做：** 不删除 legacy public types/settings/config parser；legacy host interactive
compatibility 可以暂留，但不得出现在 async security claim 中。Bash typed capability
declaration 仍是证据驱动的可选项，绝不为完成 goal 强行加入。

**完成证据：**

- verified path 不调用 legacy checker，contained local action 零 reviewer；
- plan schema 无 mutating/delegated tools，伪造调用仍拒绝，tool-choice live 达标；
- Bash/child process、structured tools 与 delegate 均不能越过或扩大 boundary；
- AUTO/MANUAL request 相同，DRY_RUN 零 effect/reviewer/grant；
- no-sandbox AUTO/Goal/headless local run 在模型调用前失败，无 silent fallback；
- Goal defer/approve/resume 的 judge 顺序与 turn accounting 正确；
- 平台 filesystem/network/child-process 负向 integration 与完整质量门 GREEN。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G2“verified autonomous execution 全面切流”。按
S2→S4→S5 的内部 checkpoints 以 TDD 完成 plan capability shaping、structured/Bash/delegated
verified dispatch、posture/snapshot 拆分和 autonomous pre-model gate。替代路径定向 GREEN 后
才切对应 wiring；保留 legacy public config/types，不为 Bash 强加未经证明的 declaration。
完成必要 tool-choice live eval、平台负向 integration、Goal park/resume 与完整质量门后停止。
```

### G3 — 收敛 canonical product surface 并删除 legacy layer

**业务结果：** 用户只面对一个 canonical RuntimePermissionProfile 和一套授权心智模型；
backend config 只选择实施机制。旧 config/snapshot 要么确定性迁移，要么给出明确错误；代码、
文档与 dogfood 能证明 legacy checker 已无存在必要。

**对应内部阶段：** S6 + S7 + S8。先完成 canonical profile 双读单写和 migration warning，
再通过删除门，最后移除 public legacy surface 并做最终 ratification。

**包含工作：**

- filesystem/network/environment/process/external 的唯一 profile settings 入口；
- backend selection/image/runtime 与授权 intent 分离；
- 只翻译语义等价且 backend 可表示的 legacy rules，不迁移 ASK/command-prefix allow/
  不可强制 glob allow；
- `/permissions` 区分 intent、installed facts、semantic guards、unsupported 与 parked request；
- 删除 `PermissionChecker`、`Decision.ASK`、混合 `PermissionMode`、旧 settings/snapshot wiring；
- migration errors、README/中文 README、`.env.example`、CLI/status 更新；
- 全部平台负向 integration、所触发的 live eval 和七个 dogfood 场景。

**发布边界：** 若仓库已有需要跨版本迁移的外部用户，G3 内部保留 Release A（双读单写）与
Release B（删除旧面）两个发布 checkpoint；若没有外部兼容承诺，可在同一 goal/branch 内按
相同先后顺序完成，不需要人为拆成两个 `/goal`。

**完成证据：**

- 同 intent 得到同 profile fingerprint，strict profile 对冲突/不可表示项 fail closed；
- snapshot/reviewer/runtime/status 使用同一 profile，legacy 输入不被静默忽略或扩大；
- `src/` 无 `Decision.ASK`、AUTO-to-ALLOW、`TierBasedPermissionChecker`、
  `permission_checker` production wiring；
- permission-review/tool-choice/goal-judge 的适用 live gates、完整质量门、平台 integration 与
  dogfood 全部通过；
- 不存在依赖 legacy checker 才能守住的 production safety invariant。

**可直接执行：**

```text
/goal 执行 Permission 收敛计划 G3“canonical product surface 与 legacy removal”。按
S6→S7→S8 先建立 canonical profile 双读单写和安全 translator，证明所有删除门后再移除
legacy API/config/snapshot wiring；不可表示规则明确报错或收窄，禁止静默扩大。完成文档、
适用 live eval、平台负向 integration、七个 dogfood、最终 rg 与完整质量门后停止。
```

### 执行节奏

- 用户只需依次启动 G1、G2、G3；goal 内部按对应 S 阶段自主持续执行。
- goal 内每个危险切流点仍需“目标测试 RED → 替代路径 GREEN → 切流 → 回归 GREEN”，但
  不要求用户重新创建 goal。
- G1 结束时新 authorization core 可用但 local dispatch 未切；G2 结束时 verified async path
  已完全切换但 legacy product surface 未删；G3 才允许批量删除 public legacy layer。
- typed Bash capability declaration 是条件项；没有足够证据时，base-boundary fail-closed 比
  新增模型自述协议更可靠。

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
3. execution-domain evidence union + external authorization runtime + permission-review eval；
4. plan capability shaping + tool-choice re-ratification；
5. verified local dispatch cutover + optional Bash typed declarations；
6. split postures + autonomous startup gate + snapshot migration；
7. canonical profile settings + legacy translator；
8. remove legacy APIs/config；
9. docs + final applicable evals + dogfood evidence。

这些是 G0–G3 内部的建议 commit/checkpoint，不是需要用户逐个启动的 `/goal`。

任一提交不得同时删除旧防线并引入其替代物；替代物必须先以测试证明生效，下一提交
才能删除旧路径。

## 完成定义

本计划完成时，下面这句话必须可以从代码和测试中直接证明：

> OpenHarness 不再通过预测一个工具调用“看起来是否安全”来授予执行权。用户先授权
> session 能力，backend 把它编译成 verified boundary；边界内动作直接执行，边界外或
> external effect 只接受一次精确授权，无法决定时持久化 park。Auto 只替换 reviewer，
> 从不替换授权语义。
