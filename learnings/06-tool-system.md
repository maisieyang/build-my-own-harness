# Module 6: Tool System Foundation — 复盘

> Phase 2 / P2-T2 / 完成日期：2026-05-08 / 用时 ~半天（5 个 micro-cycle 串行）

## 这个模块解决了什么 harness 问题

第一性原理 §2.2 工具抽象层：LLM 的"动作空间"必须**对扩展开放**（5 个 base tool /
MCP / 插件 / Skill 多种来源），但**对 loop 封闭**（loop 只关心 schema 给 LLM、
execute 调用、ToolResult 喂回）。

P2-T2 定义这个边界——`BaseTool` ABC + `ToolRegistry` 容器。**不**做具体工具
（P2-T3）、**不**做权限检查（P2-T6）、**不**做 loop dispatch（P2-T4）。

附带兑现 P2-T1 hand-off：`engine/context.py` 的 `tool_registry: object` 在 2e
收紧为 `ToolRegistry`——D7.2 占位类型转正第一例。

## 产品决策回顾（D8.1 - D8.9）

| 决策 | 选了什么 | 替代方案 | 什么时候改选替代 |
|------|---------|---------|---------------|
| D8.1 | `BaseTool` = ABC | Protocol（鸭子类型） | 出现"无法继承同一基类"的多源对象时——本项目所有来源都显式继承 |
| D8.2 | `input_model: type[BaseModel]` | `input_schema: dict[str, Any]` | 不会改——MCP 来源用 `pydantic.create_model()` 动态合成，统一了主路径 |
| D8.3 | `BaseTool(ABC, Generic[InputT])` | `args: BaseModel` 子类 cast / `args: dict` | 不会改——5 个真实工具的 typed-args 体验靠这条 |
| D8.4 | `ToolResult.output: str` flat | `list[ContentBlock]` 多模态 | Phase 5+ 接多模态结果时 |
| D8.5 | 可恢复失败 = `is_error=True`，编程错误 raise | 全 raise + loop catch | 如果"统一性 > LLM 自我恢复" 改成全 raise，但目前 LLM 自恢复价值更高 |
| D8.6 | `register(tool)` 普通方法 + 重名 ValueError | 装饰器 `@registry.register` | 出现"工具定义旁就近注册"的需求时（目前没有） |
| D8.7 | `ToolExecutionContext` 仅 `cwd: Path` | 一次到位塞 settings/permissions | 真正出现需求再加，frozen dataclass 加字段非破坏性 |
| D8.8 | `to_api_schema() -> list[ToolSpec]` | `list[dict]` | 不会改——和 protocols 层一致；request.tools 直接吃 |
| D8.9 | `_FakeTool` 直接进 conftest.py | 先 test_base.py 后提取 | 不会改——一次到位省一次重构 |

## Python 模式（继续 TS 出身的 reference 笔记）

### 1. ABC + Generic 解决 LSP 违反

`@abstractmethod async def execute(self, args: BaseModel, ...)` 看似合理，但子类
把 args 收紧到具体 Pydantic 类（如 `ReadInput`）→ **mypy strict 报 LSP 违反**
（参数逆变要求宽松，不能收紧）。

解法：参数化基类。

```python
InputT = TypeVar("InputT", bound=BaseModel)

class BaseTool(ABC, Generic[InputT]):
    name: str
    description: str
    input_model: type[InputT]

    @abstractmethod
    async def execute(self, args: InputT, ctx: ToolExecutionContext) -> ToolResult:
        ...

class ReadTool(BaseTool[ReadInput]):  # 类型参数化
    name = "Read"
    input_model = ReadInput
    async def execute(self, args: ReadInput, ctx: ToolExecutionContext) -> ToolResult:
        ...  # args.path / args.offset 全 typed
```

`ToolRegistry` 内部存 `dict[str, BaseTool[Any]]`——容器不关心具体 InputT。
TypeScript 出身的人这条最熟悉（`<T extends BaseModel>` 模式），Python 写法多一个
`Generic[T]` 基类即可。

### 2. ABC 的"半强制"模式

`BaseTool` 上：
- `name: str` / `description: str` / `input_model: type[InputT]` —— 类属性注解，
  没有 `@abstractmethod`
- `execute` —— 有 `@abstractmethod`

效果：
- 子类**忘了写 `execute`** → ABC 阻止实例化（`TypeError: Can't instantiate abstract class`）
- 子类**忘了写 `name`** → mypy strict 静态报错；运行时访问 `instance.name` 报 `AttributeError`

为啥不所有 4 个都用 `@property + @abstractmethod`？因为子类会用 class var 覆盖
abstract property，mypy strict 经常对这种 "property → class var" 的覆盖报
"signature mismatch"——加一堆 `# type: ignore[misc]` 反而难看。当前模式靠 mypy
strict 静态守门 + ABC 守 execute 一处，**实用主义胜过纯洁**。

### 3. ruff RUF002 ambiguous Unicode

写 docstring 时一不小心打了 `–` (EN DASH)，ruff RUF002 会拦：

```
Docstring contains ambiguous `–` (EN DASH). Did you mean `-` (HYPHEN-MINUS)?
```

中文 IME 环境很容易出。统一用 `-` (HYPHEN-MINUS)。

### 4. tests/ 不是 package 时的跨文件 import

`tests/__init__.py` 不存在，但 `tests/tools/__init__.py` 存在。pytest 把 `tests/`
加到 sys.path，所以子包可以这样 import：

```python
from tools.conftest import FakeInput, _FakeTool  # ✅
from tests.tools.conftest import FakeInput, _FakeTool  # ❌ ModuleNotFoundError
```

也就是说：`conftest.py` 里**不止**可以放 fixture，也可以放普通类——其它测试文件
按 pytest sys.path 的相对路径直接 import。这避开了"为了共享类把 conftest 改成
fixture，再让测试用 fixture 参数"的二次抽象。

## 工程要点

### 1. `field(default_factory=dict)` 不是装饰糖

```python
@dataclass(frozen=True)
class ToolResult:
    metadata: dict[str, Any] = field(default_factory=dict)  # ✅
    # metadata: dict[str, Any] = {}  # ❌ Python 报错：mutable default
```

dataclass 直接禁止可变默认值。`field(default_factory=dict)` 让每个实例拿到
**自己的** dict，不共享。

我专门加了一条测试 `test_metadata_default_is_per_instance` —— 不是测 Python 行为，
是**保护这条契约不被未来重构退化**。某天有人手贱改回 `= {}`，立刻失败。

### 2. `to_api_schema` 跨 layer 翻译

```python
def to_api_schema(self) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_model.model_json_schema(),
        )
        for tool in self._tools.values()
    ]
```

关键观察：`tools/` layer 借用 `protocols/` 的 `ToolSpec`，而不是定义自己的。
理由：`ApiMessageRequest.tools: list[ToolSpec] | None`——CLI 层直接喂
`registry.to_api_schema()` 进 request，无需翻译层。这是**proto layer 是公共词汇表**
的实际兑现。

### 3. D7.2 hand-off 兑现机制

P2-T1 在 `engine/context.py` 留了：

```python
tool_registry: object  # tighten to ToolRegistry in P2-T2
permission_checker: object  # tighten to PermissionChecker in P2-T6
```

P2-T2.2e 收紧第一条：

```python
if TYPE_CHECKING:
    from openharness.tools import ToolRegistry

@dataclass(frozen=True)
class QueryContext:
    tool_registry: ToolRegistry
    permission_checker: object  # tighten to PermissionChecker in P2-T6  ← 留着
```

测试 fixture 跟着改：`tool_registry=object()` → `tool_registry=ToolRegistry()`。
mypy strict 立刻确认：传 `object()` 给 `ToolRegistry` 期望的字段，**type error**。

这是把"hand-off 已兑现"编码进编译器——下次有人想绕过 ToolRegistry 直接传 object，
mypy 立刻挡。

## 可迁移到后续 Phase 的 architecture pattern

| Pattern | 来源 | 迁移到 |
|---|---|---|
| **Generic[T] 基类参数化解 LSP** | BaseTool | 任何"基类抽象 + 子类具体类型收紧"的场景 |
| **ABC 半强制（method 强制 + attribute 静态）** | BaseTool | 任何"ABC + 类属性"的混合抽象 |
| **proto layer 是公共词汇表** | to_api_schema 借 ToolSpec | 任何"内部组件向外发出标准消息"的场景 |
| **D7.2 hand-off 兑现 = 编译器 enforce** | engine/context.py 的 object → ToolRegistry | 后续 phase 把"占位"逐步收紧到具体类型时 |
| **conftest.py 不止放 fixture** | _FakeTool 直接是普通类 | 跨测试文件共享类型/常量时 |

## 一句话总结

> P2-T2 的 5 个 micro-cycle 把"工具抽象"骨架立了起来——`BaseTool[InputT]` 用
> Generic 解 LSP；`ToolRegistry` 是 dict 包了一层；`to_api_schema()` 把抽象桥接到
> protocols 的公共词汇表；2e 兑现 P2-T1 D7.2 hand-off，用 mypy strict 锁住。
> P2-T3 起可以开始堆 5 个真工具，每个都是独立的 `class XTool(BaseTool[XInput])`。
