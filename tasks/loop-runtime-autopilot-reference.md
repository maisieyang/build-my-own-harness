# loop-runtime 参照系 — 逆向 OpenHarness `autopilot` 子系统

> **这是什么**：对标项目 [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness) 的
> `autopilot` 子系统的**聚焦逆向认知地图**，作为 build-my-own-harness 的 `loop-runtime`
> epic（L1-L4，见 [`loop-runtime-plan.md`](./loop-runtime-plan.md)）的 **§参照系**。
>
> **声明（按 reverse-spec 纪律）**：
> - **来源**：HKUDS/OpenHarness，MIT。**独立重建、无代码拷贝**——本文记录"它怎么解决"，不 follow 它的实现。
> - **版本锚点**：`main` @ `9b2efd7`（pushed 2026-06-04，`pyproject.toml` 仍自报 `0.1.9`，无更新 release）。上游活跃演进，指针过期请重新定位。
> - **范围**：**只逆向 `autopilot` 这一条 loop-runtime 链路**及其依赖底座，不重做整体——整体见已冻结的 `REFERENCE.md`（v0.1.9）。
> - trade-off 标 **(上游明说)** / **(推断)**；看不出的写"未见说明"，不编。

---

## §1 一句话定位 + 目录树镜头

**定位**：`autopilot` = OpenHarness 的 **loop-runtime 本体**——一个把"issue / PR / idea / claude-code 候选"归一成评分队列，逐个拉进**隔离 worktree** 跑 agent、过**确定性验证闸 + 远程 CI**、失败则**重喂修复**、直到达标或撞预算栏、最后**开 PR**（默认人工把关合并）的**状态机**；由 **cron 守护进程**驱动，**无人值守到 PR 为止**。

**目录树（如实捕获，每行一句职责）**：

```
src/openharness/
├── autopilot/
│   ├── service.py      ← 心脏：RepoAutopilotStore(持久/intake/评分) + RepoAutopilotService(run_card 主循环)  [2239 行]
│   ├── types.py        ← 数据模型：RepoTaskCard / RepoVerificationStep / RepoRunResult + 13 状态枚举
│   └── __init__.py
├── swarm/
│   └── worktree.py     ← WorktreeManager：git worktree 隔离执行场（autopilot 直接 import）
├── services/
│   ├── cron.py         ← cron 注册表（JSON+文件锁，只存不跑；croniter 校验）
│   └── cron_scheduler.py  ← 进程内异步守护：30s 轮询注册表、到点起子进程跑 job
├── permissions/
│   ├── modes.py        ← PermissionMode 三档枚举
│   └── checker.py      ← 9 层决策链（含不可覆盖的 sensitive-path 底线）
├── ui/
│   ├── app.py          ← run_print_mode（oh -p 无头入口）
│   └── runtime.py      ← build_runtime / start_runtime（装配 engine bundle，autopilot 经此调 agent）
└── engine/             ← submit_message（内层 agent 循环；autopilot 不碰，只消费）
```

> 注：`autopilot` 把 §3 的多数要素压在 `service.py` 一个文件里——**目录 1:1 ≠ 要素 1:1**。下面 §3 按"无人值守跑目标必须解决的硬问题"重组，不照这棵树。

---

## §2 架构骨架 + 数据流

**分层（栈式）**：

```
┌─ 触发面    oh autopilot scan|tick|run-next|run|install-cron （CLI）            ┐
├─ 节律      cron_scheduler 守护（30s 轮询 → 起 `oh autopilot scan/tick` 子进程） │ 无人值守驱动
├─ 编排      RepoAutopilotService.run_card —— 13 状态的状态机（本子系统的脑）      │
├─ 执行底座  WorktreeManager(隔离) · engine.submit_message(内层 agent) ·          │
│            subprocess(验证闸) · gh(PR/CI) · permissions(权限)                   │
└─ 持久      registry.json · journal(append-only) · runs/*.md · dashboard         ┘
```

**数据流（一张 card 从进到出）**：

```
[源] github_issue/pr · manual_idea · ohmo_request · claude_code_candidate
  │  scan_*()  ← gh issue/pr list；claude-code 目录扫描
  ▼
enqueue_card  ── 指纹去重(source_ref→fingerprint) ── _score_card(源基分+bug/urgent/新鲜度)
  ▼
pick_next_card (按 score 取最高)            ──cron: tick 每2h / scan 每30min──┐
  ▼                                                                          │
run_card:                                                                    │
  create_worktree(隔离分支 autopilot/<id>)                                   │
  ┌─ for attempt in 1..max_attempts(默认3): ──────────────────────────┐     │
  │   sync_worktree_to_base(首轮 reset)                                │     │
  │   prompt = _prepare_repair_prompt(执行prompt [+ 上轮failure_stage]) │     │
  │   assistant = _run_agent_prompt(engine, full_auto, max_turns=12)   │     │
  │   steps = _run_verification_steps(返回码闸)                        │     │
  │   ├─ 有 failed/error ─ 未到上限 → 重喂 continue ─ 到上限 → failed    │     │
  │   ├─ 无 git 改动 ──── 未到上限 → 重喂 continue ─ 到上限 → failed     │     │
  │   ▼ 全过                                                           │     │
  │   commit + push + upsert PR                                        │     │
  │   _wait_for_pr_ci(轮询 gh checks，settle/timeout)                  │     │
  │   ├─ CI failed ── 未到上限 → 重喂 continue ─ 到上限 → failed        │     │
  │   ▼ CI 过                                                          │     │
  │   _automerge_eligible? ─是→ merge(merged) ─否→ human-gate(completed)│    │
  └────────────────────────────────────────────────────────────────────┘    │
  撞 max_attempts 全程未达标 → failed(repair_exhausted)                       │
        每步 update_status + append_journal + 写 runs/*.md ──────────────────┘
```

---

## §3 核心要素（从 §2 数据流提炼，非目录复刻）

每个要素四段：解决什么问题 / 关键 trade-off / 最小接口形状 / 上游实现指针。

### ① 工作项归一化 + 优先级（"需求从哪来 + 先做哪个"）
- **问题**：异构来源（issue / PR / idea / 外部候选）要变成一个**可排序、可去重、可恢复**的统一队列，否则无从"自动挑下一个"。
- **trade-off**：用**启发式打分**而非模型决策来排序（源基分 + bug/urgent 标签 + 文本命中 + 新鲜度衰减）——**(推断)** 确定性、可解释、零 token，把"规划椅"做成可读规则；代价是不懂语义深浅。
- **最小接口**：`enqueue_card(source_kind, source_ref, title, body, labels, metadata) -> (RepoTaskCard, created)`；`_score_card(card) -> (int, reasons[])`；`pick_next_card() -> RepoTaskCard|None`。
- **指针**：`service.py:265`(enqueue/去重)、`:1935`(_score_card)、`:43`(_SOURCE_BASE_SCORES: ohmo100/pr85/idea80/issue75/候选45)、`:502-625`(scan_*)。

### ② 隔离的执行场（无人值守敢放手的前提）
- **问题**：无人盯着时 agent 自由改文件，必须**关住爆炸半径**，且支持并行不互撞、崩了能回收。
- **trade-off**：用 **git worktree**（共享 `.git` 对象库 + 独立工作目录/分支）而非 clone/容器——**(推断)** 比 clone 省去重新 fetch、比容器轻、结果就是普通 git 分支可审可并；代价是隔离弱（共享文件系统外层、共享对象库）。还 symlink `node_modules/.venv` 省重复。
- **最小接口**：`create_worktree(repo, slug, branch=None) -> WorktreeInfo`；`remove_worktree(slug)`；`cleanup_stale(active_ids) -> list[str]`。
- **指针**：`swarm/worktree.py:150/213/295`；slug 严格校验防路径穿越 `:21-55`。**⚠ 实现缺口**：`agent_id` 从不落盘，`list_worktrees` 恢复出的对象 `agent_id` 恒为 None，导致 `cleanup_stale` 实际识别不出陈旧 worktree（docstring 说有 JSON 元数据文件，代码里没有）——**(上游 bug，标记)**。

### ③ 确定性验证闸（loop 的停止判据，非模型自评）
- **问题**：必须有一个**可执行、确定性可读**的"达标没"判据，否则 loop 退化成"模型说做完了"。
- **trade-off**：跑 `subprocess` 读 **returncode**（0=success，非0=failed），stdout/stderr 各截尾 4000 字符，1800s 超时——**(上游明说)** 闸 = 命令退出码。两道闸：本地命令闸 + 远程 **GitHub CI** 闸。argv 形默认 `shell=False`，含 shell 元字符 `;&|`$<>` 必须显式 `shell:true` 才放行——**(上游明说，注释)** 防注入。
- **最小接口**：`_run_verification_steps(policies, cwd) -> list[RepoVerificationStep{command, returncode, status, stdout, stderr}]`。
- **指针**：`service.py:2094`(跑闸)、`:2082`+`:199`(_looks_available)、`types.py:69`。**⚠ 默认闸的陷阱**：默认 policy 有 `uv run pytest -q` / `ruff check` / 前端 tsc，但 `_looks_available` 按标志物（`pyproject.toml`/`tests/`/`frontend/terminal/package.json`）**静默筛掉**不适用的命令——**在缺这些标志物的外部仓库里命令被丢光 → 闸空过当 success**。即默认值是贴 OpenHarness 自己仓库形状调的。

### ④ 失败重喂 / 修复循环（收敛引擎本体）
- **问题**：一次没过怎么办——要把**失败信息重新喂回**新一轮，且有**硬上限**别无声烧。
- **trade-off**：**fresh prompt + 结构化 repair 上下文**（attempt 数 / 上轮 failure_stage / failure_summary / 上轮 agent 摘要截 600 字 + "打最小补丁、别从头来、改完重跑验证"），**不堆完整历史**——**(推断)** 主动重置抗漂移。按失败类型分流：`retry_on=[local_verification_failed, remote_ci_failed]` 才重喂；`stop_on=[agent_runtime_error, git_error, permission_error, merge_conflict]` 直接终止；"无 git 改动"也算一类触发重喂。
- **最小接口**：`_prepare_repair_prompt(card, *, attempt_count, prior_summary, failure_stage, failure_summary) -> str`；外层 `for attempt in range(1, max_attempts+1)` 持栏。
- **指针**：`service.py:1595`(repair prompt)、`:712`(attempt 循环)、`:840/921/1023`(三类失败→continue)、`:1208`(_max_attempts=max(execution3, repair_rounds2+1))。

### ⑤ 无人值守的权限形态（"没人可问"怎么办）
- **问题**：无 TTY 时不能弹窗等人，但也不能裸奔——要有**不可覆盖的底线**。
- **trade-off**：默认 `full_auto` = 圈内全放；唯一**不可被任何 mode 覆盖**的是 `SENSITIVE_PATH_PATTERNS`（`.ssh`/`.aws/credentials`/`.kube/config`/OpenHarness 凭证…）always-deny——**(上游明说)** 防 LLM/注入越权。`-p` 无头则用 `_noop_permission()→True` 把"该问"一律转放行。**(推断)** 内圈自由 + 凭证底线，靠 worktree(要素②) 兜爆炸半径。
- **最小接口**：`PermissionChecker.evaluate(tool, *, is_read_only, file_path, command) -> PermissionDecision{allowed, requires_confirmation, reason}`；模式 `default/plan/full_auto`。
- **指针**：`permissions/checker.py:75-156`(9层链)、`:18`(SENSITIVE_PATH_PATTERNS)、`modes.py:8`。**⚠ 缺口**：**没有 acceptEdits 细粒度档**（自动改文件但仍拦命令）；`denied_commands` 默认**空**（`rm -rf /` 只是注释示例、非内置护栏）。

### ⑥ 触发节律（无人在场也按时跑）
- **问题**：要有东西在没人时**定时**喊"扫一遍 + 跑下一个"。
- **trade-off**：**进程内异步守护 + JSON 注册表**，而非 OS crontab——**(推断)** 跨平台、能带富 job 元数据（payload/notify/next_run/sandbox 子进程）、自包含 `oh cron start/stop`；代价是 **机器/守护进程关了就不跑、错过的窗口不补**（`next_run` 只向前推），30s tick 是精度上限。
- **最小接口**：`upsert_cron_job(job{name,schedule,command,cwd})`（注册）；`run_scheduler_loop()`（守护，30s 轮询 → `asyncio.gather(execute_job)`）；`install_default_cron()` 装 `autopilot.scan`(*/30) + `autopilot.tick`(0 */2)。
- **指针**：`services/cron.py:73`(注册表)、`cron_scheduler.py:443/312/532`(守护/执行/起 detached 子进程)、`service.py:1174`(装默认 job)。**注**：job 派发**纯靠 command 字符串**（`oh autopilot scan all --cwd …`），name 只是去重键，无 name→handler 分发表；`install_default_cron` 只写注册表、**不启**守护（守护是单独 `oh cron start`）。

### ⑦ 人机交接边界（自治到哪儿为止）
- **问题**：自治必须有**明确的停手线**——哪些动作不可逆、必须留给人。
- **trade-off**：**(上游明说)** 执行 prompt 写死"不要 merge/release/做不可逆外部动作"；默认 `default_human_gate:True` + `merge_requires_human:True`，auto_merge 默认 `label_gated`（需 `autopilot:merge` 标签）才合。**(推断)** 自治范围 = 跑到绿 + 开 PR；越过"合并/发布"这条线交回人，靠策略旋钮可调到 `fully_auto`。
- **最小接口**：`_automerge_eligible(pr_snapshot, policies) -> bool`（mode: `label_gated`/`pr_only`/`fully_auto`）；状态 `completed`(human-gate) vs `merged`。
- **指针**：`service.py:2013`(执行 prompt 的边界句)、`:1574`(automerge)、`:60/79/110`(默认策略 human gate)。

### ⑧ 状态机 + 留痕（无人值守下保持可恢复/可观测）
- **问题**：没人盯着，系统必须**自己记得走到哪、出了什么事、能续**。
- **trade-off**：**(推断)** 13 状态的显式状态机 + **append-only journal** + 每步 `update_status` 落 registry + 每轮 run/verification 报告写 `runs/*.md` + dashboard 导出。`run_card` 入口拒绝已 active 的 card（防并发重入）；attempt_count 落 metadata 支持跨进程续修。
- **最小接口**：`update_status(id, status, note, metadata_updates)`；`append_journal(kind, summary, task_id, metadata)`；13 状态 `queued…running…verifying…waiting_ci…repairing…completed/merged/failed`。
- **指针**：`types.py:9`(状态枚举)、`service.py:340/381`(status/journal)、`:1195`(export_dashboard)、`:655`(防重入)。

---

## §4 横切：跨要素模式 + 与 Claude Code 对比

**反复出现的设计模式**：
1. **"确定性闸 vs 模型自评"贯穿全程**：要素③（returncode）、CI（gh checks）、要素①（评分用规则非模型）——**凡是"判过没过/先做谁"的决策，一律落到确定性可读信号上**，模型只负责"干活"那一段。这正是 loop 区别于 skill 的那条线。
2. **三道栏，各管一层**（要素④⑤⑦）：迭代/预算栏（max_attempts/max_turns/CI timeout）管"烧多久"、权限底线（sensitive-path）管"能碰什么"、人机边界（human gate）管"自治到哪"。**缺一会变玩具或失控。**
3. **每步落盘 + append-only journal**（要素⑧）：无人值守系统的可恢复性靠"状态外置"，不靠进程内存。
4. **重喂只给摘要不堆历史**（要素④）：fresh prompt + failure_stage/summary，区别于被动压缩。

**与 Claude Code 对比**：
- `autopilot` ≈ Claude Code 的 **GitHub Actions / Routines / autopilot** 那一档（issue→PR、turn 间无审批、跑在隔离场）——是**整条产线**，不是 `claude -p` 那个**原子**。
- 验证闸：`autopilot` 是**硬闸**（returncode + 真 CI），对标 Claude Code `/goal` 的**软闸**（Haiku 读 transcript 判）——上游选了硬闸，正是 loop-runtime-plan 不变量 #1 的立场。
- 权限：`autopilot` 的 `full_auto` + sensitive-path 底线 ≈ CC 的 `bypassPermissions` + 内置 deny；但上游**没有** CC 的 `acceptEdits` 细粒度档（要素⑤缺口）。

---

## §5 模块拆分 → 映射到 loop-runtime L1-L4（按依赖排）

把上游 §3 要素切回你 epic 的 build 模块。**上游已有现成参照实现，逐块给指针**：

| 你的模块 | 上游参照（要素→文件） | 这块该建什么 + 上游教训 |
|---|---|---|
| **L1 无头入口** | 要素①边缘 · `ui/app.py:run_print_mode` | 复刻"读一个目标→跑→吐结构化→退出码"。**上游教训**：run_print_mode **不透传 permission_mode、不自己定退出码**（返回 None）——你建时别犯，退出码要在入口明确成/败。 |
| **L2 loop 权限** | 要素⑤ · `permissions/{modes,checker}.py` | 三档 + 不可覆盖的 sensitive-path 底线。**上游教训**：缺 `acceptEdits` 档、`denied_commands` 默认空——你若要"改文件自由、命令仍拦"，得自己补这档。 |
| **L3 验证闸** | 要素③ · `service.py:_run_verification_steps` | 跑命令读 returncode + shell 元字符硬化。**上游教训**：`_looks_available` 静默筛命令会让**外部仓库空过当成功**——你的闸要么显式报"零步=未验证"，要么 fail-closed。 |
| **L4 外层 loop** | 要素④ · `service.py:run_card` + `_prepare_repair_prompt` | attempt 循环 + 结构化重喂 + `retry_on/stop_on` 失败分流 + 双栏（迭代+预算）。这是上游最值得逐行抄思路（非抄码）的一块。 |

**⚠ 重要：上游暴露了 plan 漏列的四块**（loop-runtime-plan §2 只列了 L1-L4 + 后置 L5/L6）：

- **要素①+⑥（intake 评分 + cron 守护）= 你的 L6 触发**，但上游做得比 plan 设想的实——`$GOAL` 的真源头 = 评分队列，不是写死字符串。**plan 把 L6 当"后置、只要手动 kickoff"，上游证明它是 loop 自治的核心入口之一。**
- **要素②（worktree 隔离）= 你 plan §4 不变量提到的"沙箱兜底"的具体形态**——plan 没把它列成独立模块，上游证明它是 L2"敢放手"的物理前提，值得单列。
- **要素⑦（人机交接边界）= plan 不变量 #2 的产品化**——PR-not-merge + human gate + `stop_on` 不可逆动作清单，是一块独立的"自治边界"设计，plan 只当不变量、没当模块。
- **要素⑧（状态机 + journal）= plan 完全没提的"可恢复/可观测"层**——无人值守系统离不开它，建议补进 plan。

---

## 勘误（订正前一轮快速调研，以本次读源码为准）

1. `denied_commands` 默认**空列表**，`rm -rf /` 仅注释示例——前轮误作内置护栏。唯一不可覆盖底线是 `SENSITIVE_PATH_PATTERNS`。
2. `run_print_mode` **返回 None、不 `raise SystemExit`**——前轮说的 `SystemExit(exit_code)` 是 `run_repl` 的。print 模式退出码路径未在该函数内，未能定位。
3. L3 默认 policy **不是"没有"**，而是"有、但被 `_looks_available` 按 OpenHarness 仓库标志物筛，外部仓库会空过"——比"没有默认 policy"更精确。
4. **未能核实**：`autopilot-dashboard/` 前端内部；`ohmo` chat 路径是否复用同一 `run_card`（intake 有 `ohmo_request` 源，未追线）；worktree `cleanup_stale` 的 agent_id 缺口是否在别处补（本文件内未见）。

— 2026-06 逆向（聚焦 autopilot 子系统 · 作为 loop-runtime §参照系 · 建议比照 REFERENCE.md 一并冻结）
