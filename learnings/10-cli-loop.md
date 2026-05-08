# Module 10: Permissions Implementation + CLI Loop Integration — 复盘

> Phase 2 / P2-T6 / 完成日期：2026-05-08 / 用时 ~半天（6 个 micro-cycle 串行）

## 这个模块解决了什么 harness 问题

Phase 2 终章——把所有静态零件（5 个 base tool / run_query loop /
PermissionChecker 接口 / build_system_prompt）**接到 cli.py 的血管**。完成后
`oh ask "..."` 真能跑工具循环：LLM 看 prompt → 选 Bash → loop 调度 → DenyListChecker
拦危险命令 → 真 Bash 执行 → 结果喂回 → LLM 写答案。

附带兑现：
- D6.2 deny-list 的实现侧（接口在 P2-T4.4c 已就位）
- `--auto` / `--dry-run` 两个 CLI flag（D12.8）
- ApiStreamEvent 5 个变体的渲染层（P2-T6.6d 用 ToolExecution* 事件）
- D7.1 第二次修订（QueryContext 加 `permission_mode` 字段）

## 产品决策回顾（D12.1 - D12.8）

| 决策 | 选了什么 | 替代 | 什么时候改选替代 |
|------|---------|---------|---------------|
| D12.1 | DenyListChecker 同 `permissions/checker.py` | 新建 `deny_list.py` | Phase 3 9-step 算法上来时再拆 |
| D12.2 | Deny patterns 仅 Bash | 全 tool 检 | 不会改——Write/Edit 已有 D9.2 cwd 守护，Read/Grep 只读 |
| D12.3 | 子串包含（不 regex）+ 7 模式 | 正则 / 完整黑名单 | 出现 substring 误报时改 regex（远期） |
| D12.4 | `PermissionMode` Enum 3 值 | Literal | Enum 类型更安全且可序列化为 string |
| D12.5 | DRY_RUN 走 `QueryContext.permission_mode` 字段 | DRY_RUN 加进 Decision 三元 | 不会改——保 Decision 二元、loop 一处条件清晰 |
| D12.6 | `[ToolName]` 简单前缀渲染 | box drawing / 颜色 / 缩进 | Phase 3 polish 时再加 |
| D12.7 | cli wraps prompt str → ConversationMessage | run_query 接 str | D7.4 已锁，不重谈 |
| D12.8 | `--auto` / `--dry-run` 互斥 | 三态 single flag | 不会改——两个独立 flag 的语义差异分明 |

## Python 模式（继续 TS 出身的 reference 笔记）

### 1. PermissionChecker Protocol 用得上的 duck typing

```python
class DenyListChecker:
    def evaluate(self, tool_name, args, context) -> Decision:
        if tool_name != "Bash":
            return Decision.ALLOW
        # args 是 Pydantic BaseModel，但我们不导入 BashInput——
        # 用 getattr 鸭子访问 .command
        command = getattr(args, "command", None)
        if not isinstance(command, str):
            return Decision.ALLOW
        if any(pattern in command for pattern in _DENY_PATTERNS):
            return Decision.DENY
        return Decision.ALLOW
```

不 `isinstance(args, BashInput)` 因为：
- 会引入 `permissions/` → `tools/bash/` 的 import 依赖
- 测试时不需要造真的 `BashInput`，造 `_BashLikeArgs(command="...")` 即可

`getattr(args, "command", None)` 对任何"有 .command 字段的 Pydantic model"都 work——
P2-T2 D8.1 选 ABC 是因为想显式继承（5 个 base tool 都继承 BaseTool 是常态）；
P2-T6 检查器选 duck typing 是因为想在 layer 间保持解耦。**两个 layer 不同
trade-off，都在 Python 工具箱里。**

### 2. Mutually exclusive CLI flags

Typer 自身没有 mutually-exclusive option 内置语法。手动检查：

```python
@app.command(...)
def ask(
    auto: bool = typer.Option(False, "--auto", ...),
    dry_run: bool = typer.Option(False, "--dry-run", ...),
) -> None:
    if auto and dry_run:
        typer.echo("error: --auto and --dry-run are mutually exclusive", err=True)
        raise typer.Exit(code=2)
    ...
```

`typer.Exit(code=2)` 用 exit code 2（"misuse of shell command"）而非 1（"general
error"）—— Click/Typer 对参数错误的标准 exit code。和 `--max-tokens 0` 触发
Click 的"Invalid value" exit 2 同档。

### 3. 测试 CLI 时通过 monkeypatch 捕获深层状态

挑战：怎么测 `--dry-run` 真的把 `PermissionMode.DRY_RUN` 塞进了 `QueryContext`？
QueryContext 是 cli.py 内部对象，没有 publish。

解法：monkeypatch `cli_module.run_query`：

```python
async def _capturing_run_query(initial_messages, context):
    captured.context = context
    yield ApiMessageCompleteEvent(stop_reason="end_turn", ...)

monkeypatch.setattr(cli_module, "run_query", _capturing_run_query)
```

这利用了 Python 的"模块属性查找在调用时进行"特性：cli.py 写 `events =
run_query(...)`，Python 每次执行查 `cli_module.run_query`——monkeypatch 改完，
后续调用看到的就是替身。

替身既要捕获 context 又要 yield 一个 end_turn 事件让 render_stream 完整退出。

### 4. saw_text 在 multi-turn 中的重置

P2-T6.6d 修了 `_stream_render` 的隐性 bug：原版 `saw_text` 一旦设 True 就永远
True，multi-turn 下第二轮的 text deltas 会缺末尾换行。

```python
elif isinstance(event, ApiMessageCompleteEvent):
    final = event
    if saw_text:
        out.write("\n")
        out.flush()
        saw_text = False  # ← 重置
```

每个 ApiMessageCompleteEvent 开启一个独立的"是否已经写过 text"会话。**bug
在 P2-T4 多 turn loop 测试覆盖前不会被触发**——这是"渲染层 + 引擎层独立测试，
集成时才看到"的典型例子。

### 5. 测试集成而不重复底层契约

`test_loop_integration.py` 验证 chain 跑通——但它**不**重新验证 DenyListChecker
的 7 个 pattern，那些是 6a 的事。集成测试只验证：
- `[Bash error]` 渲染线出现（说明 deny-list 在链路里被调用）
- "permission denied: Bash" 文本出现（说明 6c 的 dispatch 路径处理了 DENY）
- LLM 的恢复文本出现（说明 loop 没崩、turn 2 真的跑了）

每一层的契约**只在该层的测试里**验证一次。集成测试只断言"链路通了"。

## 工程要点

### 1. D7.1 二次修订 — 模式建立

P2-T1 D7.1 锁了 6 字段，P2-T4.4d 加 model + max_tokens（第一次修订），P2-T6.6b
加 permission_mode（第二次）。

**这不是缺陷**——这是抽象层 spec 跟实现层 demand 的**自然张力**。Three-Axis 在
loop body 还没写时讨论，loop 需要什么字段在写 body 时才浮现。

模式：
1. 现场修订（不停下来开会议）
2. commit 信息明确写"D7.1 second amendment"
3. 在 context.py docstring 里更新字段历史（哪个 P 加的）
4. learnings 单独说明

下次 hexagonal 抽象再遇类似情况，按这个流程走，**不**返回 P2-T1 改 Three-Axis
（那是不可逆 churn）。

### 2. 测试集成 vs 单元 — 分层互不重复

P2-T6 的 6 个 sub-unit 测试**没有**重复：

| 层 | 测试什么 | 什么不测 |
|---|---|---|
| 6a | DenyListChecker 7 个 pattern + Bash-only scope | 不测 loop 调用 / 不测 cli 链路 |
| 6c | run_query DRY_RUN short-circuit + AUTO behaves like DEFAULT | 不测 cli flag / 不测 deny-list |
| 6d | 5 event 渲染 + multi-turn newline reset | 不测 loop / 不测 cli |
| 6e | cli flag 解析 + 互斥 + permission_mode 注入 QueryContext | 不测 deny-list / 不测真 Bash |
| 6f | 端到端 chain（mock client + 真其它） | 不测 7 个 pattern / 不测 5 event 形态 |

**总测试数：21 + 5 + 3 + 5 + 5 + 3 = 42 个测试**，没有重复。集成测试 3 个比
unit 多但便宜——因为它们只断言"链路通"，不重新验证每层契约。

### 3. cli.py 重写时保留旧测试不动

P1-T4 时写了 11 个 cli 测试。P2-T6.6e 重写 `_run_ask`——担心要改 11 个测试。
**结果一个不用改**。理由：

- 旧测试 monkeypatch `_build_client` 返回 stub
- stub 只需 `stream_message(req)` 方法
- 重写后 cli 通过 `run_query` 间接调 `client.stream_message(req)`
- 调用形态相同（接受 ApiMessageRequest，返回 AsyncIterator）
- stub 的 `last_request` 仍能捕获到（loop 第一轮就 set）

**抽象边界划得对**：cli 重写不破坏 cli 测试。Phase 3 改 deny-list 算法不破坏
P2-T6.6a 测试（pattern 接口不变）。Phase 4 加 memory 不破坏 P2-T5 测试（prompt
section 是结构断言）。

## 可迁移到后续 Phase 的 architecture pattern

| Pattern | 来源 | 迁移到 |
|---|---|---|
| **duck typing 跨 layer** | DenyListChecker 用 `getattr(args, "command")` | Phase 3 hooks 跨层访问 / Phase 5 plugin adapter |
| **mutually-exclusive CLI flags 手写检查** | --auto vs --dry-run | Phase 3 加 `--confirm-each` / `--quiet` 等组合时 |
| **monkeypatch 深层状态捕获** | cli_module.run_query 替身捕获 QueryContext | 任何"通过公开接口测内部状态"的场景 |
| **测试分层互不重复** | 6a/6c/6d/6e/6f 各管一层契约 | Phase 3+ 任何多层 capability |
| **重写不破坏旧测试** | _run_ask 重写后 P1-T4 测试零改动 | 任何"换实现保接口"的重构 |
| **D7.1 二次修订流程** | model/max_tokens (4d) + permission_mode (6b) | Phase 3+ Three-Axis 后发现的 oversight |

## 一句话总结

> P2-T6 把 Phase 2 的所有静态零件接到 cli.py。8 条 D12.X 决策都是 shape
> 选择，没 P2-T4 那种 LSP 关键 trick。**整个 Phase 2 完结**——`oh ask "..."`
> 现在真能跑：LLM 看 prompt → 选 Bash → DenyListChecker 拦危险 → 真 subprocess
> → 结果喂回 → LLM 答完。**351 个测试、mypy strict + ruff 全干净、coverage 93%+。**

## Phase 2 Capability 全景

| Capability | 文件 | 测试数 | 关键决策 |
|---|---|---|---|
| P2-T1 Engine skeleton | `engine/` 4 文件 | 19 | D7.1-D7.5（Three-Axis） |
| P2-T2 Tool system | `tools/base.py` | 23 | D8.1-D8.9 |
| P2-T3 5 base tools | `tools/{read,write,edit,bash,grep}.py` | 45 | D9.1-D9.6 + decisions/07 |
| P2-T4 run_query loop | `engine/{query,errors}.py` | 19 | D10.1-D10.5 + D7.1 修订 |
| P2-T5 Prompt assembly | `prompts.py` | 13 | D11.1-D11.6 |
| P2-T6 Permissions + CLI | `permissions/` + `cli.py` rewrite | 42 | D12.1-D12.8 + D7.1 修订 |

**Phase 2 = ~850 行实质代码 + ~2000 行测试 + 47 个产品决策（D6 + D7-D12）**。

下一步：写 `learnings/phase-1-and-2.md` 跨 Phase 复盘 → 进入 Phase 3
（Safety + Production Hardening）。
