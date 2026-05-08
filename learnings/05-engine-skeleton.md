# Module 5: Engine Skeleton — 复盘

> Phase 2 / P2-T1 / 完成日期：2026-05-08 / 用时 ~半天（4 个 micro-cycle 串行）

## 这个模块解决了什么 harness 问题

第一性原理 §1 的"core loop"协作者**就位**——`run_query` 的入参（per-query
不变量 = `QueryContext`）和它要操作的状态（messages history = `messages.py`
helpers）都已经定义好。loop **body** 留给 P2-T4，但**契约**已经写死了：

- caller 怎么构造 context（cli.py P2-T6 会用）
- loop 内部怎么扩展 messages（每一步对应第一性原理伪代码的一行）
- 入口签名是什么（`async def run_query(...) -> AsyncIterator[ApiStreamEvent]`）

**为什么先骨架后 body**：loop 本身只有 ~30 行，但协作者多。一上来写 loop 会陷入
"为了写 loop 先写 ToolRegistry 先写 PermissionChecker"的滚雪球。骨架先就位 →
P2-T2 ~ T6 各 capability 独立发育 → P2-T4 写 body 时所有零件已就位。

## 产品决策回顾（D7.1 – D7.5）

| 决策 | 选了什么 | 替代方案 | 什么时候改选替代 |
|------|---------|---------|---------------|
| D7.1 | `QueryContext` 6 字段一次到位 | 先 4 个，P2-T2/T6 再补 | 不会改——一次到位的代价（2 个字段无人读）远小于"每个后续 task 改 QueryContext"的污染 |
| D7.2 | `tool_registry`/`permission_checker` 用 `object` 占位 + 行内 marker | (a) 先动 P2-T2 一小部分 / (b) 用 `Any` | 如果未来要在 P2-T1 内部就跑某个对 registry 的真断言，再改用 (a) |
| D7.3 | `messages.py` = 模块级纯函数 + 返回新 list | `ConversationHistory` class + mutate | 如果 messages 历史出现**频繁突变** + **需要封装的不变量**（不是仅 list），改成 class |
| D7.4 | `run_query(initial_messages: list[ConversationMessage], ...)` | `run_query(initial_message: str, ...)` | 不会改——保住 `run_query` 的单一职责 |
| D7.5 | `system_prompt: str`（持有结果，不持有 callable） | 持有 `Callable[[], str]` 延迟 build | 如果 Phase 4 出现"loop 内动态切 prompt"需求，改 callable |

## Python 模式（继续 TS 出身的 reference 笔记）

### 1. `async def + yield` 的 stub 形态

`async def f() -> AsyncIterator[T]:` 必须 body 含 `yield` Python 才识别为 async
generator。**没 yield 就是普通 coroutine**——caller 只能 `await f()`，不能
`async for x in f()`。

stub 写法：

```python
async def run_query(...) -> AsyncIterator[ApiStreamEvent]:
    raise NotImplementedError("run_query body lands in P2-T4")
    yield  # type: ignore[unreachable]
```

`yield` 在 raise 之后是 **静态不可达** 的，但 AST 里有它就够 Python 识别为 async
generator。`# type: ignore[unreachable]` 让 mypy 的 `warn_unreachable=True` 闭嘴。

### 2. `list[Subtype]` → `list[Supertype]` 加宽

list 是 invariant，`list[ToolResultBlock]` **不能**直接传给期望
`list[ContentBlock]` 的位置。两条路：

```python
# ❌ cast — 像在逃逸 mypy
content = cast("list[ContentBlock]", list(results))

# ✅ 变量注解 — 声明意图
content: list[ContentBlock] = list(results)
return ConversationMessage(role="user", content=content)
```

变量注解读起来是"我*想要*这个类型"，cast 读起来是"我让 mypy 闭嘴"。同样运行时，但
意图差别大。

### 3. `inspect.isasyncgenfunction` vs `inspect.isasyncgen`

- `isasyncgenfunction(f)` —— 检查**函数定义**是否是 async generator function
- `isasyncgen(f())` —— 检查**调用结果**是否是 async generator instance

测 stub 的契约用 `isasyncgenfunction`——不需要构造 instance，不需要清理。

### 4. ruff 的"自销毁 stub"模式

stub 的三个标记：

```python
async def run_query(
    initial_messages: list[ConversationMessage],  # noqa: ARG001  -- P2-T4 body uses
    context: QueryContext,  # noqa: ARG001  -- P2-T4 body uses
) -> AsyncIterator[ApiStreamEvent]:
    raise NotImplementedError("run_query body lands in P2-T4")
    yield  # type: ignore[unreachable]
```

P2-T4 填 body 时：
- `yield` 变可达 → `warn_unused_ignores` 报 ignore 多余
- 参数被 body 使用 → ruff `RUF100` 报 noqa 多余

**stub 状态被编码进 lint 规则**，把"忘了清理 stub 标记"变成机器自动报警。

## 工程要点

### 1. `TYPE_CHECKING` 块导入未来类型 vs `object` 占位

D7.2 当时讨论的是"TYPE_CHECKING 导入未来类型 + 运行时 object"。落地时发现**未来类型
现在还不存在**——`from openharness.tools import ToolRegistry` 在 P2-T1 期间会 ImportError。

实际做法：

```python
@dataclass(frozen=True)
class QueryContext:
    tool_registry: object  # tighten to ToolRegistry in P2-T2
    permission_checker: object  # tighten to PermissionChecker in P2-T6
```

行内 marker 同时是 grep 锚点：P2-T2/T6 完成后 `rg "tighten to"` 就能找到所有要
收紧的位置。

### 2. 反向断言测试

helpers 不在 package root 可见——直觉上没什么可测，但写一个反向断言：

```python
def test_messages_helpers_are_not_re_exported_at_package_root() -> None:
    import openharness.engine as engine_pkg
    for helper in ("append_user_text", ...):
        assert not hasattr(engine_pkg, helper), f"{helper!r} unexpectedly promoted"
```

理由：随手 `from openharness.engine.messages import *` 后塞进 `__init__.py` 是
常见的"扩张性退化"。反向断言把它锁住——未来谁要把 helper 升级成公开 API，就得
**显式删除这个测试**，触发讨论。

### 3. coverage 对 stub 的处理

`pyproject.toml` 里：

```toml
[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
```

`raise NotImplementedError` 被排除——所以 1c 的 stub `engine/query.py` 跑出
**100% coverage**（3/3 statements），即使 body 实际只是抛异常。这个 exclude
是 Phase 1 设的，对 stub 友好。

## 可迁移到后续 Phase 的 architecture pattern

| Pattern | 来源 | 迁移到 |
|---|---|---|
| **frozen dataclass + 行内 type-tightening marker** | QueryContext D7.2 | 任何"骨架先行、字段类型分多 phase 收紧"的场景 |
| **stub 自销毁模式**（ignore + noqa 反向触发） | run_query D7.4 + 工程要点 | Phase 3/4/5 任何"先签名再实现"的 capability |
| **反向断言锁住 API 边界** | test_messages_helpers_are_not_re_exported | 任何"想保持小公开 API"的模块 |
| **变量注解驱动 list 加宽** | append_tool_results D7.3 | 任何 `list[Subtype]` 要传给 `list[Supertype]` 的地方 |

## 一句话总结

> P2-T1 的 4 个 micro-cycle 各 1 commit，都是**"契约层契约 + tripwire"**的写法
> ——契约用类型表达，tripwire 用 lint 表达。loop body 还没跳，但**协作者全部就位**，
> P2-T2 ~ T6 可以开始独立发育。
