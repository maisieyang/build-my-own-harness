# loop-runtime Track B — worktree 隔离 + sandbox 复用 + journal + run 级 resume（统一设计）

> **上游**：`loop-runtime-plan.md`（epic capability 地图，§9.2 的耦合分析促成了本次统一
> 设计，§9.6 记录本模块的落地摘要）。
> **纪律**：TDD 是脊梁，本模块按下面的 TDD 垂直切片（Wave 0-3）走 RED→GREEN。
> **状态**：**设计已批准（2026-07-03，native Plan Mode），实现未开始**。留档不删——这份
> plan 本身就是设计阶段沉淀下来的资产，下次不用重新摸一遍代码就能接上实现。

---

## 0. 这个模块交付什么（一句话）

把 L7（worktree 物理隔离）、L4 限制①（sandbox 每轮重启容器）、L9（状态机+journal）、
L4 限制②（`--resume`/`--max-iter` 互斥）这四项**统一**设计成一套架构——因为它们全都要改
`_run_repair_loop`/`_run_ask` 同一段控制流，`loop-runtime-plan.md` §9.2 早就分析过不能
像 L5/L6/L8 那样各自独立并行做。

---

## 1. 为什么要统一设计，不能拆开并行做（背景，摘自 §9.2）

四项耦合的原因：L7 要在每轮插入 worktree 生命周期、L9 要在每轮状态转换点插入落盘、
L4 限制②要改同一个 prompt 构造/循环入口逻辑、sandbox 复用要改同一个循环的容器生命周期
——全都改 `_run_repair_loop`/`ask()` 那几十行同一段代码。分开四个 `/goal` 各拉分支并行做，
git worktree 只能防止四个进程同时物理覆盖同一份文件，防不住四份**设计**互相冲突（比如
journal 记录的"一次 attempt 边界"如果没考虑 worktree 的生命周期，日志和实际执行单元就
对不齐）——所以必须先出一次统一设计，把"一轮 repair 现在长什么样"想清楚，再决定哪些实现
切片能真正独立并行（见 §5 的 Wave 划分）。

---

## 2. 摸底摸到的地面事实（file:line 级引用，实现阶段直接对着抄）

用三个并行 Explore agent 摸透的现状（不是推测）：

- **`_run_ask`**（`cli.py:446-1023`）每次调用都是全量重新 bootstrap：新 client、新
  `ToolRegistry`、新 `McpClientPool`、新插件/skill/command/bundle store、新
  `HookRegistry`、新 `QueryContext`（`cli.py:858-919`）。若开 sandbox，会在自己的
  `AsyncExitStack`（`cli.py:710`）里现建现拆一个 `SandboxExecution` 容器
  （`cli.py:843-856`）——`SandboxExecution` 自己的文档说得很明确："Per-query lifecycle...
  not re-entrant"（`execution/sandbox.py:73-77`），bind mount 在构造时（`_spawn_container`，
  `sandbox.py:150-172`）一次性钉死 `f"{self._cwd_host}:{_CONTAINER_CWD}:rw"`
  （`sandbox.py:160`），没有 remount API。
- **`_run_repair_loop`**（`cli.py:1026-1108`）只把"喂给下一轮的 prompt 文本"（
  `build_repair_prompt(goal, attempt, verification)`，`verification/repair.py:37`）和累计
  token 数（`total_input_tokens`/`total_output_tokens`/`total_num_turns`，靠
  `dataclasses.replace` 累加）往下传；会话消息、client、sandbox 容器每轮都是全新的。
- **cwd 唯一真源头**：`EnvironmentInfo.cwd = Path.cwd()`（`prompts/system.py:72`），一路灌到
  `ToolExecutionContext.cwd`（`tools/base.py:76`）；今天 `_run_ask` 没有任何 cwd override
  入口，`config/settings.py` 也没有代表"agent 操作目录"的字段。
- **仓库里没有任何 git worktree 用法**，也没有 journal/状态机概念（grep 全仓库确认过）。
  最接近的既有模式：
  - `services/autopilot.py`（L6）：`Card` 冻结 dataclass + 一份**共享**的 JSON 数组队列文件
    + `fcntl.flock` 排他锁包住 load-mutate-save（`_queue_lock`/`with_queue_lock`，
    `autopilot.py:157-189`）——原子写+锁的**机制**可抄，"共享队列"的**拓扑**不对（journal
    要的是"一个 run 一份 append-only 日志"，不是所有 run 抢同一个文件）。
  - `services/snapshot.py`：per-cwd-hash 目录 + `history/` 轮转（`_rotate_current_to_
    history`/`_gc_history`，`snapshot.py:486-619`）——结构上最接近，但也是整份快照轮转，
    不是增量事件；`_current_git_head` 的"钉死解析后 HEAD SHA"手法直接可抄到 worktree 的
    分叉基准上。
- **`--resume`/`--max-iter` 互斥的既有拒绝逻辑**（`cli.py:2219-2226`，同款拒绝也用在
  `--decompose`，`cli.py:2235-2242`）**依然正确**，不该被"融合"掉——resume 会话历史和
  resume 一次 repair-loop 的 run 状态是两个不同维度，硬融合会把陈旧的完整对话历史注入
  每一次"应该是新鲜上下文"的 attempt，破坏 L4 本身的不变量。
- **L8 的既有设计**（`permissions/tier_based.py:280-333` 无条件拒绝 `git commit`/
  `git push`）意味着 worktree 跑完时留下的只会是**未提交的工作区改动**，不是一串可合并的
  commit——这个事实直接决定了"运行结束后 worktree 怎么处理"不需要设计一个"要不要自动合并"
  的开关，压根没有可自动合并的东西。
- **Claude Code 自己的 worktree 设计**（`Agent` 工具的 `isolation: "worktree"` 参数 +
  独立的 `EnterWorktree`/`ExitWorktree` 工具）：门槛是"会不会冲突/想不想保护真实工作区"，
  不是"是不是多轮循环"；清理策略是"没改动自动清理"。这次统一设计跟用户对齐后，**确认**
  `--isolate` 跟 loop 场景解耦（任意 `-p` 调用都能单独开），清理策略精化为"没改动自动清理、
  有改动永不自动删"。

---

## 3. 锁定设计

### 3.1 一个新概念：`RunSession`——四项耦合的公共宿主

四项耦合的根源是"需要一个活得比单次 `_run_ask` 调用长、但不到整个进程生命周期的对象"
（worktree 路径 / 容器 / journal 句柄 / resume 游标）。新增
`services/run_session.py::open_run_session(...)`，一个 async context manager，作为
worktree + sandbox 容器这条 `AsyncExitStack` 生命周期的唯一新宿主——不挂在 `_run_ask`
（今天的宿主，作用域太小）也不挂在 `_run_repair_loop`（`--decompose` 时多个子目标要共用
同一个 worktree，宿主得再往上提一层），而是挂在 `ask()` 的分发点：现有三条独立的
`asyncio.run(...)` 调用（`cli.py:2288-2326`，对应 decompose / max_iter>1 / 单次三个分支）
合并成一个 `asyncio.run(_dispatch_ask(...))`，`_dispatch_ask` 里 `async with
open_run_session(...) as session:` 包住这三个分支原来的逻辑。

`open_run_session` 何时是真正的会话、何时是直通空实现：
- `--isolate` 未传、且不是 `--max-iter>1`/`--decompose` → 返回 `None`（纯直通，
  `cwd_override=None`/`execution_env_override=None`/`journal=None`），**今天最高频、
  测试覆盖最全的单次路径字节级不变**。
- `--isolate` 传了 → 建 worktree（不管是不是 loop 场景，已跟用户确认解耦）。
- `--max-iter>1`/`--decompose` 传了（不管 `--isolate` 开没开）→ journal 默认开
  （见 3.4.3 的理由：journal 是独立于 `--isolate` 的默认能力，不是 L7 的附属品）。

### 3.2 `_run_ask` 只新增两个通用参数，不是四个专用参数

```python
async def _run_ask(
    prompt: str,
    *,
    ...,
    cwd_override: Path | None = None,
    execution_env_override: ExecutionEnvironment | None = None,
    ...,
) -> AskOutcome:
```
- `cwd_override`：`env = detect_environment()`（`cli.py:728`）之后，若给了就
  `env = dataclasses.replace(env, cwd=cwd_override)`（`EnvironmentInfo` 是
  `@dataclass(frozen=True)`，`prompts/system.py:44`）。这一个 override 点同时改了
  工具执行 cwd**和** `--verify` 命令的 cwd（`maybe_run_verification(..., cwd=env.cwd,
  ...)`，`cli.py:997` 一起受益），不需要第二个参数。
- `execution_env_override`：给了就跳过 `cli.py:843-856` 自己建 `SandboxExecution` 那段，
  直接用传入的——这就是"sandbox 容器复用"（L4 限制①）的落地点：`open_run_session` 建一次
  容器，之后每次 `_run_ask` 调用都传同一个 `execution_env_override` 进去，容器本身在
  run 结束时才被 `open_run_session` 的 `__aexit__` 拆掉。

**为什么 `_run_repair_loop`/`_run_decomposed_loop` 幸运地几乎不用改签名**：它们已经用
`**run_ask_kwargs: Any` 透传（`cli.py:1034`、`1118`），`ask()` 只要把这两个新 key 塞进
已有的 `common_run_ask_kwargs` 字典（`cli.py:2253`），就能一路透传到每个 attempt/每个
子目标，不用碰这两个函数的循环体本身。

`_run_repair_loop` 真正需要的新增（因为这是"每轮"层面的关注点，`_run_ask` 看不到）：
`journal: RunJournal | None`、`start_attempt: int = 1`、`seed_verification:
GateResult | None = None`（给 `--resume-run` 用，见 3.4.2）、`sub_goal_label: str | None`
（给 decompose 场景打标签用）。

### 3.3 新模块

**`services/worktree.py`**（L7，白纸新建）：
```python
@dataclass(frozen=True)
class WorktreeHandle:
    path: Path; branch: str; base_ref: str; repo_root: Path

class WorktreeError(Exception): ...  # fail-closed：非 git 仓库/工作区不干净/git 缺失

async def create_worktree(repo_root, *, run_id, branch_prefix="openharness/run",
                           worktrees_root=None) -> WorktreeHandle
async def remove_worktree(handle, *, force=False) -> None
```
- 用 `asyncio.create_subprocess_exec`（argv 列表，不用 shell）调 `git worktree
  add`/`remove`——不是执行 agent 写的 shell 文本，没理由接受 shell 注入面。
- **建 worktree 前工作区必须干净**（`git status --porcelain` 非空就 `WorktreeError`）：
  不然会静默地从一个排除了用户未提交改动的 stale HEAD 分叉，是真实的正确性陷阱，不是
  边角情况。MVP 不做 stash 逃生舱。
- 分叉自**解析后的 HEAD SHA**（`git rev-parse HEAD`），不是移动的分支名——跟
  `snapshot.py` 的 `_current_git_head` 同一手法，防止长时间无人值守跑的时候基准分支
  被人挪动。
- `worktrees_root` 默认建在 `repo_root` **同级**（不在 `~/.openharness` 里，那是
  OpenHarness 自己的状态目录，不该混进用户代码 checkout）。

**`services/run_journal.py`**（L9，白纸新建，机制抄 autopilot/snapshot，拓扑不抄）：
```
~/.openharness/runs/<basename(cwd)>-<sha1(cwd)[:12]>/<run_id>/
├── journal.jsonl   # append-only，一行一个事件，审计源头
└── state.json      # 最新状态的原子快照，读缓存（复用 snapshot.py 的 tempfile+os.replace）
```
四个事件：`run_started`（goal/verify/max_iter/worktree 路径）→ `attempt_started`（第几轮）
→ `attempt_finished`（stop_reason、gate 结果、token 数）→ `run_finished`（成/败/崩、
总轮数）。`append()` 用 `fcntl` 排他锁包住写入（跟 `autopilot._queue_lock` 同一机制）——
不是防今天的并发，是防"进程崩了但没真的死、这时候来了个 `--resume-run`"这种交叉写。

**`services/run_session.py`**（新编排层，见 3.1）：`RunSession`（`run_id`,
`cwd_override`, `execution_env_override`, `worktree`, `journal`, `status`）+
`open_run_session(...)`——内部：`--isolate` 就调 `create_worktree`；`--max-iter>1` 或
`--decompose` 就默认开 journal（不管 `--isolate` 开没开，见 3.4.3）；sandbox 开就建**一个**
`SandboxExecution` 挂在这层自己的 `AsyncExitStack` 上，mount 的是 worktree 路径（或者
没隔离就是原 cwd）。`__aexit__` **永远**拆容器（资源不能漏）、**永远**写终态 journal
事件；**永远不**调 `remove_worktree`——除非 diff 为空（见 3.4.4 的精化清理策略）。

### 3.4 三个具体行为决定

**3.4.1 `--isolate` 的校验**（已确认：跟 loop 场景解耦）：只要求 `-p`/`--print`（跟其它
loop-runtime flag 同一约束），不要求 `--max-iter`/`--decompose`。运行期失败（非 git
仓库、工作区不干净、`git` 缺失）走 `WorktreeError` → `ask()` 现有异常翻译分支
（`cli.py:2327-2386`）→ `exit 1`（运行期问题，不是 flag 组合问题，这个文件里 `exit 2`
专门留给后者）。

**3.4.2 `--resume-run <run-id>`（新 flag，不碰现有 `--resume`/`--resume-id`）**——这就是
L4 限制②的真正解法：不融合，另开一条正交的轴。语义：重新打开这个 run 已有的
worktree（如果当初开了 `--isolate`；从 `state.json` 读，不看这次调用自己传的
`--isolate`/`--no-isolate`——**用当初的为准**，冲突就报 warning，否则容易出现"这次忘了带
`--isolate`，后续 attempt 悄悄写回真实 cwd"的坑），读 journal 找到最后一次完成的 attempt
和它的 gate 反馈，从 `start_attempt = last_attempt + 1` 继续跑同一个 `_run_repair_loop`
循环体——`attempt N+1` 依然是**全新**上下文（`build_repair_prompt(goal, N+1,
seed_verification)`），只是循环的**入口状态**从磁盘重建，不是循环本身的机制变了。
校验：`--resume-run` + `--resume`/`--resume-id` → `exit 2`（两个维度不能混，理由同
`--max-iter`+`--resume` 的既有拒绝）；`--resume-run` + `--decompose` → `exit 2`（MVP
不做，"具体是哪个子目标卡住了"是更大的设计，不该半支持）；`run_id` 查不到 →
`exit 1`；正文 `prompt` 参数如果给了且跟 `state.goal` 不一致 → `exit 2`（fail-closed，
不能静默选一个）。sandbox 容器**不跨 resume 复用**（原 run 结束时已经拆了），resume
总是建一个新容器 mount 同一个重新打开的 worktree 路径——这不是留白的选择，是被"容器已拆"
这个事实决定的，写清楚免得以为是漏做了。

**3.4.3 结果 JSON 新增 `"run"` 字段**（加法式扩展）：
```json
{"...既有字段...", "attempts": 3, "run": {
    "run_id": "...", "worktree_path": "...", "branch_name": "...",
    "journal_path": "...", "status": "completed"
}}
```
`worktree_path`/`branch_name` 在没开 `--isolate` 时是 `null`；`journal_path`/`run_id`/
`status` 只要开了 `--max-iter>1` 或 `--decompose`（不管有没有 `--isolate`）就有值——
**journal 默认开、不绑定 `--isolate`**：`autopilot_run_next`（`cli.py:4001` 附近）今天
直接调 `_run_repair_loop`，卡片跑挂了只有 L6 自己队列层面的状态（`loop-runtime-plan.md`
§9.4 那次修的③号问题），给它免费追加一层独立的 journal durability，零新增必需 flag，
纯收益。

**3.4.4 精化后的清理策略**（已确认，参照 Claude Code 自己 `isolation: "worktree"` 的
"没改动自动清理"）：run 结束时（成功/耗尽/崩溃都一样）检查 worktree 的 `git status
--porcelain`——**diff 为空** → 自动 `remove_worktree`（没有留着的价值）；**diff 非空**
→ 永不自动删，在 JSON 里报路径+分支名，人自己去看。因为 L8 无条件拒绝
`git commit`/`git push`（`permissions/tier_based.py:280-333`），worktree 里留下的
只可能是**未提交**的改动，"要不要自动合并"这个问题本身不存在——没有可合并的东西，
人工步骤天然是"自己 diff、自己 commit"。

---

## 4. 已知限制（写进文档，不现在解决）

- **worktree 里没有未跟踪的依赖**（`.venv`/`node_modules`/`.env`）——`git worktree add`
  只会具体化受跟踪内容。`--verify` 命令假设已有 venv 会立刻失败，这是开 `--isolate`
  后大概率第一次就会撞到的坑，不是边角情况。MVP 只在 `--isolate` 的 help 文本和 README
  里显著提示"你的 `--verify` 命令必须自举"，不做任何自动补救。
- 没有 worktree 垃圾回收机制——"有改动永不删"策略下磁盘会一直涨，`oh run gc` 之类的
  命令是明确的后续项，不在 Track B 范围内。
- `--decompose` 场景下所有子目标共用一个 worktree（子目标 N 要看得到子目标 N-1 的
  改动），journal 用 `sub_goal_label` 打标签，但 worktree 里的 diff 本身不会按子目标切开
  ——这是可接受的默认，不是 Track B 要解决的问题。

---

## 5. TDD 垂直切片（依赖序，标注哪些能并行）

| # | 切片 | RED 一句话 | 能否并行 |
|---|---|---|---|
| T0 | 从 `_run_ask` 抽出 `_resolve_sandbox_config(...)` 纯函数 | 既有 sandbox 测试不变 + 新增 override 优先级单测 | 否——序幕，T3/T4 的前置 |
| T1 | `services/worktree.py` | 非 git 仓库/工作区脏/git 缺失→`WorktreeError`；成功路径钉死在解析后 HEAD SHA | 能——独立新文件 |
| T2 | `services/run_journal.py` | `state.json`/journal 行损坏→`ValueError`；`fcntl` 锁下并发 `append()` 不撕裂 | 能——跟 T1 并行 |
| T3 | `_run_ask` 的 `cwd_override`/`execution_env_override` | override 生效时 `QueryContext.cwd`/execution_env 走 override，不走默认 | 否（依赖 T0）——可与 T4 并行 |
| T4 | `services/run_session.py` | worktree 只建一次；sandbox 只建一次（mock 调用计数）；`__aexit__` 即使 body 抛异常也拆容器；空 diff 自动删、非空 diff 不删 | 能——依赖 T0/T1/T2，可与 T3 并行 |
| T5 | `_run_repair_loop` journal 接线（`journal`/`start_attempt`/`seed_verification`/`sub_goal_label`） | 循环在正确节点写 `attempt_started`/`attempt_finished`；`start_attempt=3` 不重跑前两轮 | 否——跟 §9.2 早就点名的"同一块肌肉"，T3+T4 落地后单人顺序做 |
| T6 | `ask()` 接线：`--isolate` flag + 校验 + 三分支合并成 `_dispatch_ask` + JSON `"run"` 字段 | `--isolate --max-iter 2` → JSON 有非空 `run.worktree_path`；两个 flag 都不带 → JSON 没有 `run` 键（跟今天字节级一致） | 否——T5 之后顺序做 |
| T7 | `--resume-run <id>` | 校验矩阵：`+--resume`→exit 2；`+--decompose`→exit 2；id 查不到→exit 1；goal 冲突→exit 2；`--isolate` 冲突→warn+以状态为准 | 否——T6 之后顺序做 |
| T8（可选/stretch） | `oh run show <run-id>` 只读查看器 | 打印 `state.json` + journal 尾部 | 能——随时可做，不阻塞别的 |

**并行执行安排**（沿用 L6+L8 的两个真并行 `/goal` 先例）：Wave 0 = T0 独做；Wave 1 = T1
∥ T2（两个后台 `/goal`）；Wave 2 = T3 ∥ T4（两个后台 `/goal`）；Wave 3 = T5→T6→T7
**严格顺序、单人做**——这是 Track B 里真正不能并行的部分，三个切片都改
`_run_repair_loop`/`ask()` 同一段代码；T8 随时可插空。

**Eval 备注**：T0-T8 都不碰 prompt/概率性行为（`build_repair_prompt` 本身不变，只是
调用方的入口状态变了），按仓库既有约定不需要 eval。

---

## 6. 验证（整块 done 的判据）

```bash
oh -p "尝试一个高风险重构" --isolate --output-format json
# JSON 里 run.worktree_path/run.branch_name 有值；worktree 有改动就留在磁盘上

oh -p "修好失败测试" --isolate --verify "pytest -q" --max-iter 3 --output-format json
# 每轮都在同一个 worktree 里改；sandbox 开的话容器只建一次
# 崩溃/中断后：oh -p ... --resume-run <run-id> --verify "pytest -q" 能从上次的 attempt 继续

uv run pytest -q && uv run mypy --strict src/ && uv run ruff check
```

---

## 7. 执行流程（沿用既有的 `/goal` 委派模式）

1. 主 loop 按 Wave 0→3 顺序为每个 T 写 RED 测试、跑、人确认见红。
2. GREEN：Wave 1/2 各自两个切片交两个**真正并行**的 `claude -p "/goal ..."` 后台进程
   （沿用 L6+L8 验证过的模式）；Wave 3 三个切片单人顺序做（不搞并行，跟 L5 一样）。
3. 每个 T 完成后主 loop 独立重跑全量测试 + mypy/ruff，不只信 `/goal` 自我汇报。
4. 全部落地后过一轮高强度 `Workflow({name:"code-review", args:"high ..."})`，修复后
   commit（大概率仍是每个 Wave 一个 commit，参照 L6+L8/L5 的粒度）。
5. 结果沉淀回 `loop-runtime-plan.md` §9.6——Track B 全部完成，loop-runtime 主线
   epic 收口。
