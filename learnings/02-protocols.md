# Module 2: Wire-level Protocols & Data Models — 复盘

> Phase 1 / 完成日期：2026-04-28 / 用时 ~3 天（含一次 plan 重新拆分）
>
> 配套读物：
>
> - 决策记录：[decisions/02-protocols.md](../decisions/02-protocols.md)
> - 阶梯定位：[docs/learning/capability-ladder.md](../docs/learning/capability-ladder.md) §7-§8

---

## 1. 这个模块解决了什么 harness 问题

**Module 2 = harness 和 LLM 之间整套 wire 契约的实现**。

具体说：建立了 **anti-corruption layer 的入口** —— 一组 7 个 Pydantic 文件，14 个
公共类型，让上层（engine / CLI / UI / 压缩 / 持久化）只依赖**我们的协议**，
不直接依赖 Anthropic SDK。

没有 Module 2，每一层模块都要手写 dict 解析；每加一个 Provider 都要改所有上层代码。
有了 Module 2，**换 Provider 时只动 API client 一层，上面所有代码不动**。

它对应能力阶梯的 L0-L4 范围内的"基础数据形态"——是后续所有用户可见能力的物理前提。

---

## 2. 产品决策回顾

### 进入模块前已锁的（来自 decisions/02-protocols.md）

| 决策 | 选了什么 | 反例 | 什么时候改选反例 |
|------|---------|-----|--------------|
| D2.1 | **Pydantic v2** | dataclass / msgspec / attrs | 流式 hot path 出现性能瓶颈（profile 证明 > 5% time in serialization） |
| D2.2 | **Discriminated Union by `type`** | Class hierarchy / 单一类 + optional 字段 | 几乎不会改——这是 Anthropic wire format 的硬约束 |
| D2.3 | **Mutable models** | Frozen + `model_copy(update=...)` | 出现"被意外修改"的并发 bug 时考虑 |
| D2.4 | **ID 只在 `tool_use` / `tool_result`** | 给所有 block 加 id | 几乎不会改 |
| D2.5 | **子模块拆分**（7 文件） | 单文件 | protocols 总行数 < 200 时考虑扁平化 |

### 模块进行中新做的决策（没有 decision record，**这次记下来**）

| 决策 | 选了什么 | 为什么 | Revisit trigger |
|------|---------|------|----------------|
| `ApiMessageRequest.stream` 默认 `True` | 默认 stream | harness **永远** stream（SPEC §6 规定） | 加非交互场景（批量推理）时显式传 False |
| `tools: list[ToolSpec] \| None` 默认 `None`（不是 `[]`） | 区分"不发"和"发空"两种语义 | Anthropic API 这两个语义不同 | 不会改 |
| `max_tokens: int = Field(gt=0)` | 0/负数 schema 层拒绝 | 节省一次 round-trip 给 API 拒绝 | 不会改 |
| `ApiStreamEvent` 抽象成 **3 种**（不是 SDK 的 7+ 种） | TextDelta / MessageComplete / Retry | 多 Provider 中性 + 上层只关心粗粒度 | 需要"工具调用进度"等细粒度时加 `ApiToolUseDeltaEvent` |
| 总覆盖率 `fail_under = 70` | 锁 Phase 1 DoD 底线 | 防回归，CI 自动卡 | Phase 完成后可提到 80 |
| `__all__` 字母序 | 服从 ruff RUF022 | 行业默认；diff 友好 | 不会改 |

---

## 3. Pydantic v2 / Python 模式沉淀（TS 出身的 reference 笔记）

### 3.1 Discriminated Union 全套

**Pydantic v2 表达 TS 风格的 tagged union**：

```python
class TextBlock(StrictModel):
    type: Literal["text"] = "text"
    text: str

class ToolUseBlock(StrictModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]

ContentBlock: TypeAlias = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]
```

要点：

- `Literal["text"] = "text"` → 类型即默认值，构造时可省略
- `Annotated[X | Y, Field(discriminator="type")]` → 告诉 Pydantic 按 `type`
  字段 dispatch
- `TypeAlias` → 让 mypy 认识这是个类型别名

### 3.2 `TypeAdapter` 操作非 BaseModel 类型

要 validate / serialize 一个 type alias（不是 BaseModel 子类），用 `TypeAdapter`：

```python
_BLOCK_ADAPTER: TypeAdapter[ContentBlock] = TypeAdapter(ContentBlock)
block = _BLOCK_ADAPTER.validate_python(some_dict)
```

mypy strict 必须显式 generic 参数，否则报 `[var-annotated]`。

### 3.3 `StrictModel` 共享基类（DRY）

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
```

所有协议模型继承它 → 自动获得：

- 多余字段抛错（防 typo）
- 字段赋值时再校验（mutable 模型的安全网）

### 3.4 Field 约束做 schema 级校验

```python
max_tokens: int = Field(gt=0)         # > 0
delay_seconds: float = Field(ge=0)    # >= 0
attempt: int = Field(ge=1)            # >= 1
input_tokens: int = Field(ge=0)
```

要点：约束在**schema 层**生效，不是手写 `if` —— 序列化、JSON Schema、错误消息全自动。

### 3.5 `dict[str, Any]` 表达开放结构

```python
class ToolSpec(StrictModel):
    input_schema: dict[str, Any]   # JSON Schema 是结构开放的，故意保留
```

mypy strict 接受 `Any` 显式使用（只禁 `disallow_any_generics`）。**但要在文档/注释里说明
"这是个 JSON Schema"**——类型提示丢失的部分用 docstring 补回来。

### 3.6 `Annotated[..., Field(...)]` 的 ruff 陷阱

ruff TCH001 默认会建议把"只用在类型注解的 import"挪到 `if TYPE_CHECKING:`。
**Pydantic 在运行时解析类型注解，所以这建议是错的**——会引发 `NameError`。

修法：

```toml
[tool.ruff.lint.flake8-type-checking]
runtime-evaluated-base-classes = [
    "pydantic.BaseModel",
    "openharness.protocols._base.StrictModel",
]
```

每个 Pydantic + ruff 项目都需要这一段，建议在 `decisions/01` 里就配好。

### 3.7 `# type: ignore[assignment]` 测试故意非法赋值

```python
def test_assignment_revalidation(self) -> None:
    block = TextBlock(text="hi")
    with pytest.raises(ValidationError):
        block.text = 42  # type: ignore[assignment]
```

mypy 会拒绝 `block.text = 42`（类型不匹配）；但我们**故意**测试运行时校验。
`# type: ignore[error-code]` 让 mypy 在这一行闭嘴 + 标明意图。

### 3.8 测试 helper 与 DAMP > DRY

```python
def _user_msg(text: str = "hi") -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])
```

测试中重复构造的对象用模块级 helper。**每个 test method 看起来仍然完整自含**
（DAMP），但样板被消除（DRY 在测试场景的精确应用）。

### 3.9 `@property` vs `@computed_field`

```python
class UsageSnapshot(StrictModel):
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
```

`@property` → 计算但**不**序列化（`model_dump()` 不包含 `total_tokens`）。
`@computed_field` → 计算**且**序列化。我们用前者，因为 wire format 没这字段。

### 3.10 `__all__` 字母序

ruff RUF022 强制。**让步**：alphabetical 是行业默认，semantic 分组通过注释组织源码而不是 `__all__`。

---

## 4. TDD 节奏调整学到的

### 4.1 走过的弯路

`2e-1` / `2e-2` 是**过细分**的失败案例：

- `2e-1`：ApiMessageRequest 加 `model + max_tokens + messages` 三个字段 + 1 个测试 → 1 commit
- `2e-2`：加 `system` 字段 + 2 个测试 → 1 commit

每个 commit 1 行字段差。**TDD 仪式开销 > 实际产出**。

### 4.2 触发反思的契机

走完 `2e-2` 后我主动调用 `/plan` 重新审查任务粒度。这是关键节点——**用户视角看代码越来越小，节奏不对**就是信号。

### 4.3 修正后的规则

写进 SPEC §6：

> **Micro-cycle = one complete logical unit** (e.g., one Pydantic class with all fields + one full test class). NOT "add one field". Field-level splitting was over-fragmentation.

实测验证：`2e`（合并版）+ `2f` + `2g` 都按"一个类 = 一个 cycle"做完——commit 颗粒度合理，每个 commit message 都讲得通。

### 4.4 真正的认知

**TDD 不是机械的"先写一个测试"**，是"为一个完整能力定义一个测试套件，然后让它绿"。
对于声明式代码（Pydantic 模型 / 配置 schema），一个"完整能力"= 一个类的所有字段 + 它的所有不变量测试。

如果硬要按字段拆，最后会写 50 个 1-行 commit——回头看完全失去合并感和叙事性。

---

## 5. 框架视角的内化（这个模块最大的"啊哈"瞬间）

### 5.1 触发瞬间

走完 2f 后我说："**我感觉到我在写一个框架。这个框架的底层是基于 LLM 的调用，
我们先做了第一步，规范了和他的交互的规范，怎么给输入，怎么接受输出**。"

这是从 **代码视角 → 契约视角** 的瞬间切换。具体说：

- 之前：我以为我在"写一些 Pydantic 类"
- 之后：我意识到我在"**定义** harness 和 LLM 之间的整套 wire 契约"

### 5.2 这个视角带来的连锁认知

一旦切换到"框架视角"，下面这些自动成立：

1. **`protocols/` 是 anti-corruption layer 的入口** —— 不是"工具人代码"
2. **2e + 2f 是对称的输入/输出规范** —— 不是"两个独立模块"
3. **多 Provider 抽象是设计就内嵌的** —— 不是"以后的事"
4. **`__init__.py` 的 `__all__` 是公共 API 锁定** —— 不是"为了让 import 短"
5. **`fail_under = 70` 是工程纪律** —— 不是"为了让 CI 不红"

每一条都把"代码层面的事"翻译成"框架级别的承诺"。**这就是产品工程师 vs 普通工程师的差别**。

### 5.3 这个视角能不能教？

部分能。**但更多是练出来的**——你必须亲手写完 300 行 protocols/，才会有"哎，这是契约"的瞬间。
读 LangChain 教程不会有这个瞬间，因为 LangChain 把契约藏起来了。

---

## 6. 数据点

| 维度 | 数字 |
|------|-----|
| 协议层文件数 | 7 (`_base` / `content` / `messages` / `usage` / `requests` / `tools` / `stream_events` + `__init__`) |
| 公共类型数 | 14 |
| 测试数 | 72（其中 2 个集成测试） |
| 协议层覆盖率 | 100% |
| 项目总覆盖率 | 89.62% |
| commit 数 | ~12（含过细分的 2e-1/2e-2 共 2 个） |
| 决策记录 | 1 个 ([decisions/02-protocols.md](../decisions/02-protocols.md)) |
| 实现时长 | 3 天（含 1 次 plan 重新拆分） |

---

## 7. 如果重做我会改什么

1. **一开始就讨论 stream / tools / max_tokens 校验**作为 ApiMessageRequest 的初版字段——不要 2e 时再回头加。**字段补漏是协议层的常见返工**。
2. **一开始就锁 "one Pydantic class = one micro-cycle"** 的 TDD 规则——避免 2e-1/2e-2 弯路。这条规则现在写进 SPEC §6 了。
3. **`docs/learning/capability-ladder.md` 应该比 SPEC.md 更早建立** —— mental model 早于代码，理解才能驱动设计。这次它是后期补的。
4. **集成测试（2g）一开始就列入 todo** —— 它不是"收尾活动"，是"协议层闭环验证"。
5. **coverage gate 可以更早设**（比如 2a 就锁 70%）—— 早期建立纪律比晚期补好。
6. **ruff TCH 配置应该在 decisions/01 就预埋** —— 我们等到 2c 写 `messages.py` 时才发现 TCH001 误报，回头改 pyproject.toml + decisions/02。**已知会用 Pydantic 的项目，TCH 配置是 day-0 工作。**

---

## 模块完成意味着什么

P1-T2 完成 = harness 和 LLM 之间的整套 wire 契约已经定义并测试。

接下来：

- ⏸ **P1-T3（Anthropic API client）** —— 把这套契约**接到真实的 Anthropic SDK**
- ⏸ **P1-T4（CLI + 真 API 端到端）** —— 让用户能跑 `oh ask "hi"`
- ⏸ **P1-T5（Phase 1 验收 + 复盘）** —— Phase 1 整体收官

P1-T3 是 Module 2 的真正"考验"——如果协议设计合理，API client 的代码会**异常简单**
（基本就是 SDK call → 翻译事件）；如果设计有问题，API client 会要求修改 protocols/——
那就要诚实写一个 `decisions/<NN>-protocol-revision.md` 解释。
