# Decision 52 — Permission 授权语义收敛

> Date: 2026-08-07
>
> 上游：D51 统一 permission profile、verified sandbox boundary、exact overlay 与
> durable park/resume。
>
> 实施计划：`tasks/permission-authorization-convergence-plan-zh.md`。

## 一、Why now

D51 已让 local core tools 共享 verified boundary，但生产 dispatch 上方仍保留一套
`ALLOW / ASK / DENY` checker；其中 `ASK` 在 AUTO 下直接变成 ALLOW。两套授权语义
制造重复配置与 ambient-host 第四路径，也让 external exact approval 错误依赖 local
sandbox boundary，因此必须在继续扩展 async agent 前收敛。

## 二、In / Out

**IN**：

- deny-only action policy；
- plan capability shaping；
- local/external typed enforcement evidence；
- reviewer、execution 与 runtime posture 解耦；
- autonomous verified-boundary gate；
- canonical profile 配置与 legacy migration；
- 删除 production `Decision.ASK`、AUTO-to-ALLOW 与 TierBased checker。

**OUT**：

| 推到哪 | 项 | 原因 |
|---|---|---|
| 后续 backend phase | Linux bubblewrap/seccomp | 本决策只要求 unsupported platform 诚实 fail closed |
| 后续 UX | category-wide persistent grants | 本阶段只保留 exact one-shot grant |
| 不做 | 解析 Bash stderr 推断权限 | 普通失败不是 authorization oracle |
| 不做 | 靠 command prefix 构建 containment | shell string 无法证明实际 effects |
| 不做 | Goal-owned permission profile | profile 属于 session，Goal 只是消费者 |

## 三、Decisions

### D52.1 — 一条授权链，没有第四路径

**Chosen**：每个 model-controlled effect 只能在 verified boundary 内执行、消费一个
exact one-shot grant，或 deny/park。

**Why**：任何额外执行路径都会重新获得 harness ambient authority。

**Alternatives**：保留 checker ALLOW 作为并列授权源会产生两个 precedence 与两个事实源。

**Reversibility**：hard——未来扩展必须继续证明三终态不变量。

### D52.2 — ASK 不是授权结果，AUTO 只替换 reviewer

**Chosen**：engine 内删除 generic ASK；需要授权时形成 exact request，AUTO/MANUAL 只
决定 request 交给 LLM reviewer 还是 durable human park。

**Why**：不确定不能因 CLI flag 自动变成授权。

**Alternatives**：`ASK + AUTO → ALLOW` 简单但无法绑定 delta、arguments 与 enforcement。

**Reversibility**：medium——UI 仍可把 exact request 显示为“ask”，内部合同不恢复。

### D52.3 — 执行前 policy 只能收窄，不能授予

**Chosen**：Action policy 返回 `DenyResult | None`，在 hooks 前检查 original arguments，
hooks 修改后再检查 final arguments；它没有 ALLOW/ASK/grant API。

**Why**：保留不可执行动作的快速拒绝，同时避免第三个正向授权源。

**Alternatives**：完全删除 pre-dispatch policy 会丢失 plan clamp 与 semantic handoff。

**Reversibility**：easy——可新增 deny guards，但不能新增 allow constructor。

### D52.4 — Exact request evidence 按 execution domain 分型

**Chosen**：local request 绑定 profile + verified boundary + backend + operation；external
request 绑定 profile + surface + effect + trust + tool/server identity + external policy facts。

**Why**：external authorization 必须独立于 Seatbelt/Docker，又不能缺少可验证事实。

**Alternatives**：给 external request 填空/伪 boundary 会让 fingerprint 没有安全含义。

**Reversibility**：medium——closed union 可加新 evidence variant，已有 variant 不放宽。

### D52.5 — Autonomous local read 也要求 verified boundary

**Chosen**：AUTO、active Goal 或 headless session 只要暴露 LOCAL_DATA 或
DELEGATED_RUNTIME tool，就必须有覆盖相应 effects 的 verified boundary。

**Why**：Read/Grep 可把 host secrets 带入 model context；SpawnAgent 可继承完整 runtime
产生 local effects，“只读”与“委派”都不是无 sandbox 豁免。

**Alternatives**：只保护 mutation 会留下 data exfiltration；按 Bash 文本猜只读不可靠。

**Reversibility**：medium——未来只有在 registry 不含 local/delegated tool 时可免 local
boundary，external policy 仍独立执行。

### D52.6 — Snapshot 不恢复 reviewer authority

**Chosen**：snapshot 持久化 profile/evidence/park/grant facts，但 AUTO/MANUAL 与 DRY_RUN
由当前启动显式选择，旧 `permission_mode` 只做 schema migration/诊断。

**Why**：恢复历史会话不能静默恢复一次旧的自动授权姿态。

**Alternatives**：完整恢复 UI posture 方便，但把历史状态变成新的 authority source。

**Reversibility**：easy——可持久化非授权 UX 偏好，不能自动生效为 reviewer authority。

### D52.7 — Opaque Bash escalation 只接受 typed declaration 或 deterministic violation

**Chosen**：禁止 stderr inference；Bash 可增加与完整 command/final arguments 同
fingerprint 的 typed capability declaration，遗漏声明由 base boundary fail closed。

**Why**：Seatbelt filesystem denial 通常只表现为 command non-zero，无法与普通失败可靠
区分；模型声明只能请求，不能授权。

**Alternatives**：静态 shell parsing 与 stderr matching 都会同时产生误放与误报。

**Reversibility**：medium——declaration schema 可扩展，grant binding 不放宽。

### D52.8 — 替代物先 GREEN，再切流，再删旧层

**Chosen**：deny policy、plan shaping、typed evidence 先并行落地；verified local path
随后切流；最后拆 PermissionMode、配置和 checker API。

**Why**：先删除 AUTO/ASK 会让当前所有 Bash 在替代路径接管前被阻塞或失去保护。

**Alternatives**：big-bang rewrite 难以证明每个中间状态都没有 safety regression。

**Reversibility**：easy——每阶段一个可回滚提交，禁止同提交删除旧防线并首次引入替代。

## 四、Acceptance

- [ ] production `src/` 无 `Decision.ASK`、AUTO-to-ALLOW、`TierBasedPermissionChecker` 与
      `permission_checker` wiring；
- [ ] canonical profile 或 exact grant 是唯一正向授权来源；
- [ ] local/external exact request 使用各自 typed evidence，drift 均 fail closed；
- [ ] plan registry 不暴露 mutating/delegated tools，伪造调用仍被 dispatch deny；
- [ ] autonomous local/delegated session 无 verified boundary 时在 tool execution 前失败；
- [ ] Goal 在 parked permission 前不调用 judge、不消耗 auto-turn；
- [ ] old snapshot/config 显式迁移或报错，不静默扩大权限；
- [ ] full quality contract、平台负向 integration、对应 live eval 与 dogfood 全部通过。

## 五、Tasks

实施顺序与每阶段 acceptance 以
`tasks/permission-authorization-convergence-plan-zh.md` 的 S0–S8 为准：

1. S0：characterization、coverage 与 schema baseline；
2. S1：deny-only action policy shadow；
3. S2：plan capability shaping；
4. S3：execution-domain evidence union 与 external runtime；
5. S4：verified local dispatch cutover；
6. S5：posture split 与 autonomous gate；
7. S6：canonical profile config；
8. S7：legacy API/config removal；
9. S8：integration、eval、dogfood 与完成审计。

每个行为阶段先新增/更新测试并确认因目标缺口 RED，再修改 production 到 GREEN。S0
只提交当前事实的 GREEN baseline，不长期提交未来阶段的 RED tests。

## 六、Wiring audit

| Layer | Verdict | Reasoning |
|---|---|---|
| `permissions/` | requires extension | 拆 deny policy、typed evidence、ledger 与 local overlay resolver |
| `execution/` | requires verification | base boundary/overlay 合同不放宽，新增 Bash declaration 需验证 |
| `engine/context + query` | requires extension | 删除 checker authority，按 domain dispatch |
| `hooks/` | requires verification | 保留 original/final 两次 deny 与 final-argument authorization |
| `services/snapshot` | requires extension | typed evidence schema migration，不恢复 reviewer authority |
| `services/goal_judge` | unchanged | judge prompt/schema 不因 permission convergence 改变 |
| `tools/base + registry` | requires extension | plan view、delegated coverage、可选 capability declaration |
| `tools/spawn_agent` | requires verification | 继承同一 profile/boundary/runtime/policy |
| `mcp + web` | requires extension | external evidence 不再依赖 local boundary |
| `bundles/plugins` | requires verification | overlay 不能重新注入 legacy allow authority |
| `cli.py + repl.py` | requires extension | posture、startup gate、plan shaping、migration UX |
| `observability` | requires extension | 区分 semantic guard、configured intent、installed facts、park |
| `eval/` | requires extension | tool catalog/Bash schema live ratify；新增 permission-review eval |

**Conclusion**：这是跨 runtime 的 authorization convergence，不是局部 cleanup。任何子阶段
如引入 bypass 或无法保持 D52.1 三终态，必须停止并重新 ratify boundary。

## 七、References

- `decisions/51-unified-permission-sandbox-boundary.md`
- `tasks/unified-permission-sandbox-plan.md`
- `tasks/permission-authorization-convergence-plan-zh.md`
- `evals/tool_choice/dataset_card.md`
- `evals/verify_judge/dataset_card.md`
