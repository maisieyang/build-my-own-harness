# Module 3: OpenAI-compatible API Client — 复盘

> Phase 1 / 完成日期：2026-04-28 / 用时 ~2-3 天 / 5 sub-units
>
> 配套读物：
>
> - 决策记录 (策略层)：[decisions/03-api-client-strategy.md](../decisions/03-api-client-strategy.md)
> - 决策记录 (实现层)：[decisions/04-api-client-implementation.md](../decisions/04-api-client-implementation.md)
> - 阶梯定位：[docs/learning/capability-ladder.md](../docs/learning/capability-ladder.md) §7-§8

---

## 1. 这个模块解决了什么 harness 问题

P1-T3 是 harness 真正"接上 LLM"的一步。Module 2 定义了 wire 契约
（输入/输出协议），Module 3 把这个契约**落地到一个真实 Provider**
（Qwen via DashScope，通过 OpenAI-compatible API）。

模块完成后，harness 第一次拥有了**完整的"发请求—收响应"能力**：

- **请求侧**：把 Anthropic-shape 的 `ApiMessageRequest` 翻译成 OpenAI-shape
  的 dict，调用 SDK
- **响应侧**：把 OpenAI 流式 chunks 翻译成 Anthropic-shape 的
  `ApiStreamEvent`s
- **韧性**：失败时按 `RetryPolicy` 自动退避重试，并显式 emit
  `ApiRetryEvent` 让 UI 渲染
- **错误处理**：SDK 异常被翻译成我们的 4 类错误（Auth / RateLimit /
  Request），保留 `__cause__` 链

**最关键的一点**：P1-T3 是**反腐败层（anti-corruption layer）的第一次实测**——
[capability-ladder §8](../docs/learning/capability-ladder.md) 的核心论点
是"protocols/ 设计如果对，client 层翻译就够；不需要回去改 protocols"。
**结果**：protocols/ 一行没动。论点证实。

---

## 2. 产品决策回顾

### 模块进入前已锁的（来自 [decisions/03](../decisions/03-api-client-strategy.md)）

| 决策 | 选了什么 | 实测后的判断 |
|------|---------|------------|
| D3.1 | Qwen via DashScope (OpenAI-compat) 作为首测目标 | ✅ 选对了——网络稳定、成本低、anti-corruption 第一天就被压测 |
| D3.2 | Protocol-first（先定义 `SupportsStreamingMessages`，再写实现） | ⚠️ 我们没有显式定义 Protocol 类——`OpenAICompatibleApiClient` 实现了一个 *implicit* protocol。下次 Provider 进来时（如果有）就要补一个 explicit `class SupportsStreamingMessages(Protocol)` |
| D3.3 | Mock at our Protocol layer | ✅ `_FakeAsyncOpenAI` + `_FakeStream` + `_FakeChatCompletions` 三层假对象，干净通过 |
| D3.4 | Auth: env var only (`DASHSCOPE_API_KEY`) | ✅ 完全够用——CLI 层（P1-T4）从 env 读 + 实例化 `AsyncOpenAI` 就 OK |
| D3.5 | Retry visibility via `ApiRetryEvent` | ✅ 实现成功——retry events 在 stream events 流之前先 yield，时序明确 |

### 模块进行中的实现决策（[decisions/04](../decisions/04-api-client-implementation.md)）

| 决策 | 选了什么 | Why |
|------|---------|------|
| D4.1 | 错误翻译：6 个 `isinstance` 分支，concrete-first 排序 | `RateLimitError extends APIStatusError`，所以必须细到粗排序；保守 fallback 防漏失 |
| D4.2 | retry events 缓冲后再 yield | 时序明确；测试可断言 |
| D4.3 | `OpenHarnessApiError` 直接 reraise（不二次翻译） | 防止 wrap-of-wrap 让 `__cause__` 链混乱 |
| D4.4 | 构造函数注入 SDK 实例（不接受 api_key 参数） | DI 极致；CLI 控制 SDK 配置；测试直接传 fake |
| D4.5 | 测试名 = 用户能感知的产品契约 | 不是"测试某段代码"——这个 framing 是产品工程师的 marker |
| D4.6 | `openai>=1.50,<2.0` runtime dep | 单 SDK 覆盖多 Provider；2.0 出来时再决定升级 |

---

## 3. Python / 工程模式沉淀

### 3.1 Generator 副作用陷阱（这一轮最大的坑）

```python
# ❌ Wrong — 调用方不迭代时，函数体不执行
def consume(self, chunk) -> Iterator[Event]:
    if delta.content:
        self._buffer.append(delta.content)  # ← never runs without iteration
        yield Event(text=delta.content)

# ✅ Correct — eager 处理副作用，return list
def consume(self, chunk) -> list[Event]:
    events = []
    if delta.content:
        self._buffer.append(delta.content)  # ← always runs
        events.append(Event(text=delta.content))
    return events
```

**规则**：**generator 适合纯流式无副作用**；**有状态 / 有副作用的"消费"操作用 eager**。

### 3.2 Concrete-first `isinstance` 排序

```python
# ❌ Wrong — APIStatusError 会先匹配，RateLimitError 走不到
if isinstance(exc, openai.APIStatusError):
    return RequestFailure(...)
if isinstance(exc, openai.RateLimitError):  # 不会执行
    return RateLimitFailure(...)

# ✅ Correct — 子类先，父类后
if isinstance(exc, openai.RateLimitError):
    return RateLimitFailure(...)
if isinstance(exc, openai.APIStatusError):
    return RequestFailure(...)
```

每次写多分支 `isinstance` 链时都要 stop and check 类层级。

### 3.3 `raise X from Y` 异常链保留

```python
# ✅ 保留 __cause__
except openai.AuthenticationError as e:
    raise AuthenticationFailure(...) from e
```

调试时能看到全链路：`openai.Auth → AuthenticationFailure`。**90% 的 Python 代码不做这个**——我们做。

### 3.4 "我的类型？直接 reraise；否则翻译" 模式

```python
async def _establish_stream():
    try:
        return await self._sdk.chat.completions.create(...)
    except OpenHarnessApiError:    # ← 已翻译过，原样
        raise
    except Exception as exc:
        raise _translate_openai_error(exc) from exc
```

**任何"包装层"都需要这个 pattern**——防 wrap-of-wrap。

### 3.5 测试用 httpx 构造真实 SDK 异常

```python
def _make_status_error(error_class, status, message):
    req = httpx.Request("POST", "https://api.test/v1/chat")
    resp = httpx.Response(status, request=req)
    return error_class(message=message, response=resp, body=None)
```

不是"mock 异常对象"——而是**用 SDK 自己的真实构造方式**生成异常。
测试覆盖 SDK 真实抛出路径，可信度 ↑↑。

### 3.6 三层 Fake 对象模式

```python
class _FakeChunk:                  # 单个 chunk（有 model_dump()）
class _FakeStream:                 # AsyncIterator of chunks
class _FakeChatCompletions:        # 配置每次 create() 返回什么
class _FakeAsyncOpenAI:            # 暴露 .chat.completions
```

每一层只暴露 SDK 用到的最小 API。**比 unittest.mock 可读 100 倍**——
mypy strict 也开心。

### 3.7 `2.0 ** int` vs `2 ** int`

`int.__pow__(int)` 在 typeshed 里返回 `Any`（因为指数为负时是 float）。
`float ** int` 永远是 float。**所有指数运算想要严格 float 类型，
基数用 `2.0` 不用 `2`**。

### 3.8 Default 值类型推断陷阱

```python
# ❌ Wrong — random_fn 类型推为 Any
def compute_delay(*, random_fn: Callable[[], float] = random.random): ...

# ✅ Correct — 包一层显式 -> float 函数
def _system_random() -> float:
    return random.random()
def compute_delay(*, random_fn: Callable[[], float] = _system_random): ...
```

mypy strict 在 default 值上比想象的严格。

### 3.9 `if TYPE_CHECKING:` 块（再次）

`Awaitable` / `Callable` / `AsyncIterator` 这种**只在类型注解里用**的 import，
配合 `from __future__ import annotations` 应该全部进 TYPE_CHECKING 块。
ruff TC003 会自动提示。

---

## 4. TDD / build skill 节奏学到的

### 4.1 3c 的 mid-implementation split 是正确的判断

3c 原计划是"一个 sub-unit 完成 client + 翻译"。开始写测试时立刻意识到
"7 个 wire 不一致 + streaming 状态机 + retry 集成"=三个独立逻辑，
**主动调用 /plan 拆成 3c.1（翻译）+ 3c.2（client）**。

经验：**写测试遇到"我要 import 5 个不同模块"信号 = 应该拆分了**。
3c 当时这个信号很明显。

### 4.2 每个 sub-unit 的 RED → GREEN → COMMIT 严格执行

```
3a errors:     RED → GREEN → COMMIT (~30 min)
3b retry:      RED → GREEN → COMMIT (~45 min, 含 3 个 mypy 陷阱)
3c.1 transl:   RED → GREEN → COMMIT (~60 min, 含 generator bug)
3c.2 client:   RED → GREEN → COMMIT (~75 min, openai dep 引入)
3e re-exports: RED → GREEN → COMMIT (~10 min)
```

**节奏稳定**——每个 sub-unit 都是一个清晰的产品价值点。

### 4.3 mypy strict 这一轮"贡献"了 3 个真实 bug

不是"语法噪声"——这 3 个：

1. `int.__pow__(int) -> Any` 让 `compute_delay` 的返回类型变 Any
2. `random.random` 默认值让 `random_fn` 参数类型推为 Any
3. `Callable` / `Awaitable` import 不在 TYPE_CHECKING 块（ruff TC003）

每一个都是 **mypy strict 不开就发现不了的真实类型不严格**。这次进一步
确认：mypy strict 是这种重型类型项目的必需品。

### 4.4 Pre-commit hooks 节奏化——不再是负担

P1-T1 时被代理问题搞了一轮的 pre-commit，到 P1-T3 这一轮已经完全顺手——
trim trailing whitespace / fix end of files / check yaml / ruff —— 每次
commit 自动跑 0.5 秒，发现问题立刻修。**纪律工具 internalize 了**。

### 4.5 docs commit 拆分得当

每个 sub-unit 有 chore commit；4 个 sub-units 完成后 1 个 docs commit 入档
`decisions/04`。**没有把决策塞进 feat commit message** —— decision record
是独立资产，不是 commit message 的过路客。

---

## 5. 框架视角的内化（继续深化）

### 5.1 Anti-corruption layer 的实测验证

学到 (capability-ladder §8) 时 anti-corruption 还是个"漂亮的设计原则"。
P1-T3 让它变成**实测的产品事实**：

- **protocols/ 一行没动**——整个 OpenAI 翻译工作完成期间
- **engine（Phase 2）/ CLI（P1-T4）** 不感知"在和 OpenAI 还是 Anthropic 对话"
- **下次加 Provider** 时只动一个 client.py + 一个 translation.py + 一组测试

如果 protocols 设计错了，这次就会撞墙——会被迫回去改 protocols/，
写 `decisions/05-protocol-revision.md`。**没撞墙 = 设计对了。**

### 5.2 Provider 抽象的"模板"已经成形

未来加 Anthropic-native client：

```
src/openharness/api/anthropic_client.py    ← 类似 client.py
src/openharness/api/anthropic_translation.py ← 类似 translation.py
                                              （可能更薄，因为 protocols
                                               已经是 Anthropic-shape）
src/openharness/api/__init__.py             ← 加 AnthropicApiClient export
tests/api/test_anthropic_client.py
tests/api/test_anthropic_translation.py
```

**4-组件依赖图（errors / retry / translation / client）已经是 Provider 添加的项目级模板**。

### 5.3 测试设计已经从"代码层"上升到"产品契约层"

D4.5 测试哲学的成熟标志：
- 不再写 `test_translate_openai_error_with_401` 这种代码视角的测试
- 改写 `test_authentication_error_not_retried` 这种产品视角的测试

每个测试名是用户能 feel 的契约。**这个 lens 一旦 internalize，会自动用到
后续每个模块**。

---

## 6. 数据点

| 维度 | 数字 |
|------|-----|
| Sub-units 完成 | 5 (3a / 3b / 3c.1 / 3c.2 / 3e) |
| Commits | ~12 个（含 docs / chore / feat） |
| 新增源代码 | ~700 行（errors 75 + retry 117 + translation 280 + client 135 + __init__ 50） |
| 新增测试代码 | ~1100 行（test_errors 161 + test_retry 280 + test_translation 440 + test_client 330） |
| 新增测试数 | 73 个（19 errors + 22 retry + 22 translation + 10 client） |
| 总测试 | 144（P1-T2 末是 72） |
| 协议层覆盖率 | 100% (没退化) |
| API 层覆盖率 | 95%+ (新增) |
| 项目总覆盖率 | 91.71%（gate 70%） |
| 决策记录 | 2 个 (decisions/03, decisions/04) |
| 实现时长 | ~2-3 天（含 1 次 plan 重新拆分 3c → 3c.1 + 3c.2） |
| mypy strict 命中的真实 bug | 3 个 |

---

## 7. 如果重做我会改什么

1. **更早写一个"empty consume"测试**——能更早发现 generator 副作用 bug。
   `test_consume_returns_no_events_on_finish_only_chunk` 这种边界测试一开始
   就该写，而不是等到中间发现。

2. **3c 一开始就拆 3c.1 + 3c.2**——我们 mid-implementation 才拆。如果在
   plan 阶段就识别出"翻译 + 客户端 = 两个独立单元"，就少绕一步。
   **判断标准**：sub-unit 的"应该测试什么"如果有 2+ 类（翻译类 + 客户端类）
   = 应该拆。

3. **`SupportsStreamingMessages` Protocol 应该显式定义**——D3.2 说选了
   "Protocol-first"，但实际上我们直接写了具体类，没有显式
   `class SupportsStreamingMessages(Protocol):`。**下次加第二个 Provider
   时一定要先补这个 Protocol**——否则每个 Provider 都是 implicit interface。

4. **`fast_policy` 这种测试 fixture 应该走 conftest.py**——`test_client.py`
   里多次出现 `_FAST_POLICY = RetryPolicy(0.001, 0.001, ...)`。下次同类项目，
   fixture 抽到 conftest 减少重复。

5. **更早在 `pyproject.toml` 里调 `openai` 版本范围**——`>=1.50,<2.0` 是按
   感觉写的。**应该 ✅ 先去 PyPI 看实际最新版**，再决定上限。已 SPEC 写到
   "if 2.0 ships, write decisions/05" — OK 但流程更严谨的话第一步就该看。

6. **error translation 应该专门有自己的 test file**——目前
   `_translate_openai_error` 通过 `test_client.py` 间接覆盖。**下次重做**
   会有 `tests/api/test_error_translation.py` 专门白盒测试每个 isinstance
   分支——更清晰。

---

## 模块完成意味着什么

P1-T3 完成 = harness 第一次"真的能和 LLM 对话"（虽然还没接 CLI）。

**接下来 P1-T4** 是 Phase 1 最后一个 task：

- 把 `OpenAICompatibleApiClient` 接到 Typer-based CLI
- 从 env 读 `DASHSCOPE_API_KEY` + 实例化 `AsyncOpenAI`
- 写 `oh ask "hi"` 命令，流式打印响应
- 一个集成测试用真 API（gated by env var）

P1-T4 是 Phase 1 的"上线时刻"——**用户第一次能跑命令**。如果 P1-T3 的
设计对了，P1-T4 应该是非常薄的一层（< 200 行）——把 client 接到 Typer。

**Phase 1 验收** 也在 P1-T4 完成时——届时写
`learnings/phase-1-retrospective.md` 复盘整个 Phase 1。
