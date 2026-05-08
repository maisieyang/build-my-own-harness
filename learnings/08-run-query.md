# Module 8: run_query Core Loop — 复盘

> Phase 2 / P2-T4 / 完成日期：2026-05-08 / 用时 ~半天（6 个 micro-cycle 串行）

## 这个模块解决了什么 harness 问题

第一性原理 §1 的**心脏**——`while stop_reason == "tool_use"` 这一行循环跳起来了。
P2-T1/T2/T3 把所有协作者（QueryContext / messages helpers / ToolRegistry / 5 个
真工具）就位；P2-T4 把它们串成 loop body。

完成度：
- `engine/messages.py` 的伪代码注释**变成真代码**（P2-T1.1b 兑现）
- 5 个 P2-T1 stub tripwire 全部清理（自销毁机制按设计触发）
- `ApiStreamEvent` 判别联合从 3 个变 5 个（加 ToolExecution 双子事件）
- `OpenHarnessApiError` 树新增 `LoopLimitExceeded`
- `permissions/` 模块从 0 到接口齐全（实现留 P2-T6）
- D7.2 占位类型**两处全部兑现**（`tool_registry: object` → `ToolRegistry` 在 P2-T2.2e；
  `permission_checker: object` → `PermissionChecker` 在 4c）

## 产品决策回顾（D10.1–D10.5 + D7.1 amendment）

| 决策 | 选了什么 | 替代方案 | 什么时候改选替代 |
|------|---------|---------|---------------|
| **D7.1 修订** | QueryContext 加 `model: str` + `max_tokens: int = 1024` | 维持 6 字段不动 | 本次必须改——loop 构造 `ApiMessageRequest` 需要这两个值。原版 D7.1 没考虑到 |
| D10.1 | PermissionChecker 接口提到 P2-T4 | 推迟到 P2-T6 | 不会改——loop 必须 call 接口；接口/实现分离让 P2-T6 更聚焦 |
| D10.2 | `LoopLimitExceeded(OpenHarnessApiError)` | 新建 `OpenHarnessError` 顶层 | P3 hooks 错误进来时一次性重构 |
| D10.3 | `ToolExecutionContext` 每查询构造一次 | per-tool override | P5+ 子代理隔离时再加 |
| D10.4 | 4 类工具失败 → `is_error=True` 喂回 | 全 raise + loop catch | 不会改——LLM 自我恢复价值 > 统一性 |
| D10.5 | execute() 异常**穿透 generator** | 全 catch 并 fallback | 不会改——D8.5 已锁的契约 |

详见 [decisions/06-phase-2-boundary.md](../decisions/06-phase-2-boundary.md) +
P2-T4 Three-Axis 讨论（已合并到本文件）。

## Python 模式（继续 TS 出身的 reference 笔记）

### 1. async generator 的 yield 在哪些位置 OK

```python
async def run_query(...) -> AsyncIterator[ApiStreamEvent]:
    for _turn in range(context.max_turns):
        ...
        async for event in api_client.stream_message(request):
            yield event  # ← 透传 API events
            ...
        if stop_reason != "tool_use":
            return  # ← async generator 的"早返"

        for tool_use in tool_uses:
            yield ToolExecutionStartedEvent(...)  # ← 引擎自己生成的事件
            output, is_error = await _dispatch_one(...)
            yield ToolExecutionCompletedEvent(...)

    raise LoopLimitExceeded(...)  # ← 异常穿透 generator
```

3 种 yield 位置：透传外部、引擎自生、return 早退；外加 raise 穿透。**所有路径
都是 async generator 的合法行为**——caller 用 `async for` 看到事件流，看到
`return` 自然 StopAsyncIteration，看到 raise 异常上抛。

### 2. Recovery vs raise — 两种错误传递通道

```python
async def _dispatch_one(tool_use, context, exec_context) -> tuple[str, bool]:
    try:
        tool = context.tool_registry.get(tool_use.name)
    except KeyError:
        return f"tool not found: {tool_use.name}", True  # ← recovery
    ...
    decision = context.permission_checker.evaluate(...)
    if decision is Decision.DENY:
        return f"permission denied: {tool_use.name}", True  # ← recovery

    result = await tool.execute(args, exec_context)  # ← 异常这里穿透
    return result.output, result.is_error
```

两条通道：
- **`(output, is_error)` 元组**：可恢复失败，喂回 LLM
- **raise**：编程错误（execute 内部异常、registry 内部异常），穿透到 caller

D8.5 的契约在 `_dispatch_one` 体里就能看到：catch 的全是 LLM 行为相关的异常
（KeyError / ValidationError），不 catch execute() 内部的 RuntimeError。

### 3. `tests/` 不是 package 时的跨子目录 import

`tests/engine/conftest.py` 定义了 `_AllowAllChecker` 等 fixture 类，跨 test
文件用：

```python
from engine.conftest import _AllowAllChecker, _StubApiClient  # ✅
```

**注意**：是 `engine.conftest`，不是 `tests.engine.conftest`——`tests/__init__.py`
不存在，`tests/` 不是 Python 包；pytest 自动把 `tests/` 加进 sys.path，所以
**子目录**（`engine/`, `tools/`, `permissions/`）变成顶级可导入。

P2-T2 也踩过这个坑（参考 `learnings/06-tool-system.md` §4）。重复一次的代价是
trivial，但说明这条 P3 应该写进 conftest pattern 文档。

### 4. 防御性入口拷贝

```python
async def run_query(initial_messages, context):
    messages = list(initial_messages)  # ← 防御性拷贝
    for _turn in range(...):
        messages = append_user_text(messages, ...)  # 返回新 list
        messages = append_assistant_message(messages, ...)
```

`messages.py` helpers 都返回**新** list（D7.3 决策），所以 `messages = ...`
重新绑定不会污染 caller。但 caller 可能持有 `initial_messages` 的引用并继续
使用——`list(...)` 拷贝**入口处**就保证 caller 的 list 永远不动。

测试 `test_initial_messages_not_mutated` 用 `original == snapshot` 后置断言锁住。

### 5. Stub self-destruct 链

> "Tripwire chain"——P2-T1 立的炸弹串成一条引信。

| 来源 | tripwire 类型 | 何时触发 |
|---|---|---|
| P2-T1.1c | `raise NotImplementedError("run_query body lands in P2-T4")` | 4d 写 body 时**人**手动删 |
| P2-T1.1c | `yield  # type: ignore[unreachable]` | yield 变可达后 mypy 报 unused-ignore |
| P2-T1.1c | 2 × `# noqa: ARG001` | 参数被使用后 ruff RUF100 报 unused-noqa |
| P2-T4.4d | `raise NotImplementedError("tool dispatch lands in P2-T4.4e")` | 4e 写 body 时手动删 |

每一处都不是孤立的"待办"——它们是**编译/lint 错误**，下一阶段开发时**主动**叫醒
清理。这种"未来必做的事编码进编译器"的模式**抢救**了多少个 TODO 注释才能换来。

### 6. ABC vs Protocol — 选 Protocol 的理由

`PermissionChecker` 用 Protocol 而非 ABC：

```python
class PermissionChecker(Protocol):
    def evaluate(self, tool_name, args, context) -> Decision: ...
```

```python
# tests/engine/conftest.py
class _AllowAllChecker:  # 没继承任何东西
    def evaluate(self, tool_name, args, context) -> Decision:
        return Decision.ALLOW
# 这就直接满足 PermissionChecker Protocol
```

理由：
- Phase 5 plugin / MCP adapter 可能从外部 drop in checker，不愿意继承我们的基类
- 测试 fake 不需要 boilerplate（不需要 import + 继承）
- 实现层（P2-T6）只需结构匹配，不需要语义匹配
- 失去的：runtime `isinstance(x, PermissionChecker)` —— 但 mypy strict 已经
  在静态层面把这个守住了

`BaseTool` 用 ABC（P2-T2.2b）的理由相反——`name` / `description` 是 class attr，
ABC 半强制更合适；外部 adapter（如 MCP）继承 BaseTool 是预期模式。

## 工程要点

### 1. D7.1 amendment — Three-Axis 抽象层抓不到的事

P2-T1 Three-Axis 讨论时定下"QueryContext 6 字段一次到位"，没考虑到 loop 构造
ApiMessageRequest 时需要 `model` 和 `max_tokens`。P2-T4.4d 写 body 时才发现。

**为什么 Three-Axis 没抓到**：那时候 `run_query` 是 `NotImplementedError` stub，
我们没有强制走 ApiMessageRequest 的字段需求；当时只在抽象层讨论"loop 需要哪些
不变量"。

**lesson**：抽象层讨论可以推断协作者**类型**，但**字段集合**只有在真实写 body
时才浮现。"Three-Axis 必须 final" 不可能——记住 Three-Axis 是**首轮收敛**，不是
最终契约。后续发现的 amendment 在 commit 信息 + learnings 里诚实标记。

### 2. `_StubApiClient` 的设计模式

```python
class _StubApiClient:
    def __init__(self, events_per_turn: list[list[ApiStreamEvent]]) -> None:
        self._events_per_turn = events_per_turn
        self._turn = 0
        self.captured_requests: list[ApiMessageRequest] = []

    async def stream_message(self, request) -> AsyncIterator[ApiStreamEvent]:
        self.captured_requests.append(request)
        events = self._events_per_turn[self._turn]
        self._turn += 1
        for event in events: yield event
```

两面都被测：
- **输入面**：`captured_requests` 收每一轮 loop 构造的 request → 测请求 shape
- **输出面**：`events_per_turn[i]` 控制 loop 第 i+1 轮收到什么 → 测 loop 行为

设计得"双向"是关键——单向 stub（只控制返回值）测不到 request shape，
单向 spy（只 capture）测不到 loop 反应。

### 3. 测试 multi-turn 场景的关键数据结构

```python
events_per_turn = [
    [tool_use_event],  # 第 1 轮
    [tool_use_event],  # 第 2 轮
    [tool_use_event],  # 第 3 轮
    [end_turn_event],  # 第 4 轮 → exit
]
```

或：
```python
events_per_turn = [
    [tool_use_event],  # 第 1 轮
    [tool_use_event],  # 第 2 轮
]  # max_turns=2，2 轮都 tool_use → LoopLimitExceeded
```

数组长度 = turn 数；每个内部数组是这一轮 stream_message 吐出的 event 序列。
**结构 = 测试场景的语法树**。

### 4. mypy `[comparison-overlap]` 告警

测 `Decision.ALLOW != Decision.DENY` 时 mypy 抱怨这是 statically obvious。
绕路用 set membership：

```python
assert Decision.ALLOW in {Decision.ALLOW}
assert Decision.DENY not in {Decision.ALLOW}
```

mypy 静态分析够不到 set 的成员检查，runtime 真测了 enum 的 hash + eq 行为。

## 可迁移到后续 Phase 的 architecture pattern

| Pattern | 来源 | 迁移到 |
|---|---|---|
| **stub self-destruct 链** | P2-T1 → P2-T4 5 个 tripwire 全清 | 任何"骨架先行、body 后填"的 capability |
| **(output, is_error) tuple as recovery channel** | _dispatch_one | 任何"4+ 失败模式都要喂回 LLM"的 dispatcher |
| **Protocol vs ABC 的选择规则** | PermissionChecker vs BaseTool | Phase 5 plugins / MCP adapters 接口设计 |
| **双向 stub（输入面 + 输出面）** | _StubApiClient | 任何 stream-based protocol 的 mock |
| **events_per_turn 数据结构** | run_query 测试 | 任何"多轮 stateful 协议"的测试 |
| **D7.1 amendment 模式** | run_query body 发现需要 model/max_tokens | 后续 Three-Axis 后发现 oversight 的处理流程 |

## 一句话总结

> P2-T4 让心脏跳起来——前 3 个 capability 的协作者全部串成 `while
> stop_reason == "tool_use"`。新增的 6 处 sub-units 既清掉了所有 P2-T1 stub
> tripwire，也兑现了 P2-T1 D7.2 hand-off 的最后一处 (`permission_checker`)。
> 接下来 P2-T5 的 system prompt 是 "loop 需要什么 context 喂给 LLM" 的最后一块
> 拼图，P2-T6 的 cli + permission 是把心脏接到血管。
