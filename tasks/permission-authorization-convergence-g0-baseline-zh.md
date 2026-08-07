# Permission 授权收敛 G0 当前事实基线

> 状态：GREEN baseline，2026-08-07。
>
> 范围：只描述当前 production code、tests 与 README 能证明的行为；不把本文件当成
> production 行为来源。后续 goal 改变行为时，必须同步更新或删除对应 characterization。

## 1. Legacy 行为画像

| 当前调用 | 当前结果 | 直接证据 | 目标落点 |
|---|---|---|---|
| checker `DENY` | dispatch 返回 error，不执行 tool | `tests/engine/test_query.py::test_permission_denied_returns_error_result` | G1 deny-only policy 保留拒绝语义 |
| checker `ASK` + `DEFAULT` | 返回“requires confirmation”，实际不 durable park | `TestLegacyAskCharacterization::test_legacy_ask_is_denied_in_default_mode` | G4 exact park；G11 删除 ASK |
| checker `ASK` + `AUTO` | 直接执行 tool，没有 exact request/grant | `TestLegacyAskCharacterization::test_legacy_ask_is_allowed_in_auto_mode` | G8 删除 reviewer→ALLOW 分支 |
| `DRY_RUN` | checker、reviewer 与 execute 都不进入；返回 synthetic result | `TestRunQueryDryRunMode` | G8 拆成 execution posture |
| hook 修改参数 | 修改后重新 validation，并再次调用 checker | `src/openharness/engine/query.py::_execute_tool_use` 与 hooks integration tests | G1 用 deny-only policy 保留前后两次检查 |

这里的 `ASK + AUTO` 测试是删除门所需的 legacy characterization，不是目标合同。它在 G8
切换 production 语义时必须先变 RED，再被“同 exact request、不同 reviewer”目标测试替代。

## 2. Production registry coverage matrix

默认 registry 的机器基线由
`tests/tools/test_default_registry.py::test_g0_default_registry_domain_and_effect_coverage_matrix`
固定；直接 local tool 到 `SandboxSession.execute(operation)` 的路径由同文件的
`test_g0_direct_local_tools_route_through_verified_operation_path` 固定。

| Tool | execution domain | read-only metadata | verified runtime operation/effect | 当前 legacy host path |
|---|---|---:|---|---|
| Read | `LOCAL_DATA` | yes | `FileReadOperation` / `FILE_READ` | 无 session 时直接 host file read |
| Grep | `LOCAL_DATA` | yes | `FileSearchOperation` / `FILE_SEARCH` | 无 session 时 host `rg` |
| Write | `LOCAL_DATA` | no | `FileWriteOperation` / `FILE_WRITE` | 无 session 时直接 host file write |
| Edit | `LOCAL_DATA` | no | `FileEditOperation` / `FILE_WRITE` | 无 session 时直接 host file edit |
| Bash | `LOCAL_DATA` | no | `CommandOperation` / `COMMAND` | 无 session 时 `execution_env`，默认 host |
| Agent (`SpawnAgent`) | `DELEGATED_RUNTIME` | no | 不直接产生 operation；child `QueryContext` 继承 parent registry、sandbox、profile、boundary、permission runtime | parent 是 host posture 时 child 也继承 host authority |

SpawnAgent 的当前继承事实由
`tests/tools/test_spawn_agent.py::test_g0_sub_agent_inherits_verified_permission_runtime` 固定。
这只证明当前 `dataclasses.replace` 路径确实传播同一 runtime objects；它不证明 autonomous
coverage gate 已存在，也不证明 one-shot grant 不会被 delegate 重复消费。后两项分别属于
G7 与 G9。

条件注册的 production tools/surfaces：

| 来源 | domain/effect 声明 | 当前 enforcement |
|---|---|---|
| LoadSkill | `TRUSTED_CONTROL`, read-only | harness 内部 skill store；不属于 local sandbox data plane |
| MCP adapter | `EXTERNAL_EFFECT` + `MCP` + resolved effect kind/trust | registry 拒绝缺失 metadata；query external policy + exact review |
| WebSearch/WebFetch | `EXTERNAL_EFFECT` + `WEB` + `NETWORK_READ`, trusted | query external policy；不继承 local sandbox trust |
| future model-callable tool | 缺 execution domain 时 registration 失败 | `ToolRegistry.register` fail closed |

Seatbelt 当前 verified boundary 可报告 `COMMAND`、`FILE_READ`、`FILE_WRITE`、`FILE_SEARCH`；
启用 managed network proxy 时另报告 `NETWORK`。`docker-command` 只是 command compatibility
backend，不能作为 file-tool unified coverage 的证明。Backend 是否实际安装必须读取
`EnforcedBoundary`，不能从 settings 或 tool metadata 推断。

## 3. Permission settings compatibility matrix

| 当前 settings surface | 当前职责/事实 | G0 兼容状态 | 后续处置 |
|---|---|---|---|
| `permission_mode` | 混合 DEFAULT/AUTO reviewer 语义与 DRY_RUN execution 语义 | 继续读取；当前值写入 snapshot 并在 resume 恢复 | G8 拆轴并迁移；G11 删除 |
| `permission_auto_review` | AUTO 下是否构造 independent reviewer | 保留，默认 true；不单独写 snapshot | G8 收敛为当前启动 reviewer 选择 |
| `permission_reviewer_model` | exact reviewer model override | 保留；不写 snapshot | G8 保留为 reviewer 配置 |
| `deny_paths` | legacy Tier 2 glob deny | 继续读入 checker；不写 snapshot | G10 只翻译可表示项；G11 删除 |
| `permissions.allow` | legacy positive checker rule | 继续读入 checker；不写 snapshot | G10 仅等价翻译；不可表示 allow 拒绝迁移；G11 删除 |
| `permissions.deny` | legacy checker deny rule | 继续读入 checker；不写 snapshot | G1 shadow；G10 翻译/semantic guard；G11 删除旧入口 |
| `permissions.ask` | legacy checker ASK rule | 继续读入 checker；不写 snapshot | 不迁移；G11 删除 |
| `sandbox_enabled` | 是否尝试安装 local backend | 保留，默认 false；是机制选择，不是授权事实 | G9 autonomous gate；G10 继续作为 backend config |
| `sandbox_backend` | `seatbelt` / `docker-command` | 保留；不写 snapshot | G10 继续作为 backend selection |
| `sandbox_image`, `sandbox_runtime` | Docker implementation selection | 保留；不写 snapshot | G10 继续作为 backend config |
| `sandbox_network` | legacy `none`/`bridge` compatibility spelling | 继续读取并翻译成 NetworkPolicy | G10 迁移为 profile intent 或明确兼容输入 |
| `sandbox_network_policy` | network intent，但仍嵌套在 sandbox config | `_sandbox_profile` 写入 RuntimePermissionProfile | G10 移到 canonical profile settings |
| `sandbox_external_tool_policy` | external tool intent，但命名在 sandbox 下 | 同时进入 profile 与 QueryContext external policy | G3/G4 解耦 evidence/runtime；G10 唯一入口 |
| `sandbox_memory`, `sandbox_cpus`, `sandbox_pids` | backend resource knobs，与 ProcessPolicy intent 部分重叠 | 继续读取；不写 snapshot | G10 区分授权 intent 与 backend implementation |

当前还没有完整 `RuntimePermissionProfile` 的正式 user-facing settings field。
`cli._sandbox_profile` 以 `workspace_runtime_profile()` 为基底，只覆盖 network 与 external
tools；filesystem/environment/process 的多数 intent 来自硬编码 profile 或独立 sandbox
knobs。这是 G10 的明确前置缺口，而不是 G0 要修复的行为。

## 4. Snapshot v1 compatibility matrix

当前 schema 是 `openharness.snapshot.v1` / version 1。

| v1 字段 | 当前 writer | 当前 resume | 后续策略 |
|---|---|---|---|
| `permission_mode` | 必写 enum value 字符串 | 从 snapshot 恢复 DEFAULT/AUTO/DRY_RUN | G8 旧 schema 只作 migration input；新 snapshot 不恢复 reviewer/execution authority |
| `extra.permission_runtime` | runtime 存在时写入，否则省略 | snapshot 有该 state 时必须由当前启动提供 verified `PermissionRuntime`，再用当前 profile/boundary/reviewer 调 `from_state` | G3 typed evidence migration；G4 external state 不依赖 local boundary；G8 不从中恢复 reviewer posture |
| runtime `profile_fingerprint` | 必写 | 与当前 profile 校验 | 保留，G3 扩充 typed evidence |
| runtime `boundary_fingerprint` / `backend_fingerprint` | 必写 | 与当前 verified local runtime 校验 | local evidence 保留；external evidence 在 G3/G4 不再伪造这些字段 |
| `parked_request`, `grants`, `denials`, last decision fields | runtime state 内写入 | `PermissionRuntime.from_state` 校验后恢复 | G3/G4 保留 exact lifecycle，按 evidence kind 漂移验证 |
| registry/hooks/execution env/sandbox session | 不写 | 每次由 caller 重建 | 保留：runtime mechanism 必须来自当前启动事实 |

机器证据：

- `tests/services/test_snapshot.py::TestSerializeSnapshot` 固定 writer 顶层字段、字符串
  `permission_mode` 和 exact parked fingerprints；
- `tests/engine/test_from_snapshot.py::TestFromSnapshotPermissionModeRoundTrip` 固定当前 posture
  恢复行为；
- `tests/engine/test_from_snapshot.py::TestFromSnapshotPermissionRuntime` 固定“有 runtime state
  但无当前 verified runtime 时拒绝”的当前合同。

## 5. 后续目标测试矩阵

G0 不提交长期 RED。下表固定预期在哪个 goal 先 RED、为什么 RED、什么证据才允许转 GREEN。

| 目标不变量 | 首次 RED goal | 当前会 RED 的原因 | GREEN 删除门 |
|---|---|---|---|
| deny policy 只有 deny/no-match，没有 allow/ask | G1 | 尚无 ActionDenyPolicy | shadow deny 集合不缩小，hook 前后检查成立 |
| plan request 不暴露 mutation/delegated schemas | G2 | 当前 plan 主要依赖 PermissionRules overlay | schema shaping + forged dispatch deny + live tool-choice 9/9 |
| external exact request 不需要 local boundary evidence | G3/G4 | PermissionDeltaRequest/Runtime 强制 EnforcedBoundary | typed union + no-sandbox park/resume + drift fail closed |
| structured local verified path 不调用 checker | G5 | query 仍无条件先调用 permission_checker | direct SandboxSession path与 filesystem negative integration GREEN |
| verified Bash 不经 legacy ASK 获得正向授权 | G6 | checker 位于 Bash sandbox dispatch 之前 | base boundary/child inheritance/typed deterministic violation GREEN |
| delegate 不扩大或复制 authority | G7 | 当前只依赖 dataclasses.replace，无显式 coverage/ledger gate | inherited facts、one-shot consumption、missing-fact fail closed |
| AUTO 不改变授权结果 | G8 | `_permission_failure` 将 ASK+AUTO 变成 allow | manual/auto byte-equivalent request，只更换 reviewer |
| DRY_RUN 不属于 permission mode | G8 | 当前是 PermissionMode enum member | 零 effect/reviewer/grant，snapshot 不恢复 execution posture |
| autonomous local/delegated run 必须 verified | G9 | no-sandbox host posture 仍可 AUTO/Goal/headless | pre-model coverage gate，无 silent fallback |
| user-facing allow intent 只有 canonical profile | G10 | settings 与 `_sandbox_profile` 分散拼装 | same intent→same fingerprint，backend config 不扩大授权 |
| production 无 ASK/checker/mixed mode | G11 | legacy API/wiring 仍在 | `rg`、负向 integration、eval、dogfood 全部门通过 |

## 6. G0 删除门结论

G0 只允许后续开始安装替代物，不允许删除任何 legacy production layer。当前仍需保留：

- `TierBasedPermissionChecker`、`Decision`/`DecisionResult`/`PermissionMode`；
- `QueryContext.permission_checker` 与 query 的 hook 前后 checker calls；
- `Settings.permission_mode`、`deny_paths`、`permissions.allow/deny/ask`；
- snapshot v1 的 `permission_mode` 与 local-bound `PermissionRuntimeState`；
- no-sandbox host execution compatibility path。

删除只能发生在对应 G1–G10 替代物已经 GREEN 之后，并最终集中在 G11。G0 不宣称
legacy-host posture 具有 async security；它只是当前兼容事实。
