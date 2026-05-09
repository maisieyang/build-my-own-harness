# Learnings — Phase 1 (cross-module retrospective)

> Phase 1 / 完成日期：2026-05-07 / 用时 ~2-3 周 / 5 tasks (T1-T5) / 4 modules

P1 的目标是 "Foundation + Hello LLM"——交付生产级 Python 工具链，并让用户第一次能跑
`oh ask "hi"` 看到流式响应。Goal hit。这份文档不是各 module 的总和，而是**跨模块涌现
的认知**——单个 module 看不到的东西。

每个 module 自己的细节见 `learnings/01-04-*.md`；这里只看横切。

---

## 1. Phase 1 数据点

| 维度 | 数字 |
|---|---|
| 总测试 | 173（172 passed + 1 integration skipped） |
| 总覆盖率 | 92.83%（gate 70%） |
| Decision records | 5（[01](../decisions/01-scaffolding.md) / [02](../decisions/02-protocols.md) / [03](../decisions/03-api-client-strategy.md) / [04](../decisions/04-api-client-implementation.md) / [05](../decisions/05-cli.md)） |
| Module retros | 4（[01](./01-scaffolding.md) / [02](./02-protocols.md) / [03](./03-api-client.md) / [04](./04-cli.md)） |
| 大的 plan 重拆 | 2 次（2e 字段级合并 / 3c → 3c.1+3c.2） |
| 工作流元-pivot | 1 次（spec-heavy → capability-driven） |
| mypy strict 抓到的真 bug | 3+ |
| 总 commits | ~30 |

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P1-T1 — 项目脚手架** | 生产级 Python 项目的地基（uv / mypy strict / ruff / CI / pre-commit）让后续所有模块"免费"得到产品级质量 |
| **P1-T2 — Wire 协议** | 我们在写**反腐败层的入口**，不是"一些 Pydantic 类"——这个 framing 决定了后面所有模块的设计姿态 |
| **P1-T3 — API 客户端** | 反腐败层的**实测验证**：实现 OpenAI 翻译期间 `protocols/` 一行没动——thesis 成立 |
| **P1-T4 — CLI + 真 API** | harness 第一次"有了用户"。下面三层抽象的设计是否 OK，在这一层一拍即知 |
| **P1-T5 — 验证 + 复盘** | 把 Phase 1 的认知**显式化**——不写下来就不算学到 |

---

## 3. 跨模块涌现的认知（这才是 Phase 1 真正的 ROI）

### 3.1 反腐败层不是设计原则，是可以**测试**的产品事实

T2 时反腐败层还是 [capability-ladder §8](../docs/learning/capability-ladder.md) 里
一个"漂亮原则"。T3 + T4 让它变成**实测事实**：

- T3 实现 OpenAI translation 期间，`protocols/` 一行没动
- T4 实现 CLI 期间，`protocols/` 和 `api/` 都一行没动
- 加新 Provider 的"开发者负担"被压缩到 4 个文件（errors / retry / translation / client）

如果 protocols/ 设计错了，T3 或 T4 任何一步都会撞墙——会被迫回头改 protocols/，
写 `decisions/<NN>-protocol-revision.md`。**两次没撞墙 = 设计对了。**

**可迁移**：以后任何"加一层抽象"的决策，第一个验证标准是 "下次实现新成员时上一层是否一行不动"。

### 3.2 测试名 = 产品契约（从代码视角到契约视角）

T3 D4.5 第一次显式：

| ❌ 代码视角测试名 | ✅ 产品契约测试名 |
|---|---|
| `test_translate_openai_error_with_401` | `test_authentication_error_not_retried` |
| `test_with_retry_callback_invoked` | `test_rate_limit_then_success_emits_retry_event` |

T4 把这条 internalize 了——`tests/cli/test_cli.py` 几乎所有测试名都是"用户能 feel 的契约"
（`test_streams_text_to_stdout` / `test_missing_api_key_prints_config_hint` / ...）。

**这个 lens 一旦 internalize，会自动用到所有后续模块**。它是产品工程师 vs 普通工程师的 marker。

### 3.3 mypy strict 不是仪式负担，是 bug 探测器

T3 一轮抓到 3 个**真 bug**——不是语法噪声：

1. `int.__pow__(int) -> Any`：`2 ** attempt` 让 `compute_delay` 返回类型变 Any。修法：
   `2.0 ** int` 永远是 float
2. `random.random` 直接当默认值参数：mypy 推断 `random_fn` 类型为 Any。修法：包一层显式 `-> float` 函数
3. `Awaitable` / `Callable` 不在 `TYPE_CHECKING` 块（ruff TC003 + mypy 协作发现）

任何一个不开 strict 都发现不了。**重型类型项目（带 generic / discriminated union /
async iter）必上 strict。**

### 3.4 框架视角的瞬间切换（不可强制，只能等它发生）

T2 末尾用户的原话："我感觉到我在写一个框架。这个框架的底层是基于 LLM 的调用，
我们先做了第一步，规范了和他的交互的规范"。

这是从**代码视角 → 契约视角**的瞬间切换。一旦切换，下面这些自动成立：

1. `protocols/` 是 anti-corruption layer 入口（不是工具人代码）
2. T2 的 `ApiMessageRequest` + T2 的 `ApiStreamEvent` 是对称的输入/输出规范
3. 多 Provider 抽象是**设计就内嵌**的（不是"以后的事"）
4. `__init__.py` 的 `__all__` 是公共 API 锁定（不是为了让 import 短）
5. `fail_under = 70` 是工程纪律（不是为了让 CI 不红）

**部分能教，更多是练出来的**——你必须亲手写完 300 行 protocols/，才会有"哎，这是契约"的瞬间。
读 LangChain 教程不会有这个瞬间，因为框架把契约藏起来了。

---

## 4. 工作流的两次纠正（这是 Phase 1 最深的元-学习）

### 4.1 颗粒度纠正一：字段级 → 类级（T2 中段）

`2e-1`：加 `model + max_tokens + messages` 三字段 + 1 测试 → 1 commit
`2e-2`：加 `system` 字段 + 2 测试 → 1 commit

**TDD 仪式开销 > 实际产出**。走完 2e-2 后主动调用 `/plan` 重拆，得出新规则：

> **Micro-cycle = one complete logical unit**（e.g., 一个 Pydantic 类 + 它所有
> 字段 + 全套测试）。NOT "add one field"。

写进 SPEC §6（现在已合并到 §5 Code Style）。后续 2e/2f/2g 实测：颗粒度合理，每个
commit message 都讲得通。

### 4.2 颗粒度纠正二（更深的）：sub-task 级 → capability 级（T4 末段）

T4 启动时 `decisions/05-cli.md` 写了 D5.1-D5.8 八条决策、`tasks/todo.md` 把 T4 拆成
4a/4b/4c/4d/4e 五个 sub-units。这是 Google skill workflow 的产物——spec → plan →
tasks → impl 全流程瀑布。

T4 中段意识到不舒服：很多决策（D5.5 渲染策略 / D5.6 错误措辞 / D5.8 print mode 范围）
是**实现策略**，不是外部约束——它们应该在 build 时 emerge，做完反思，而不是 build 前就钉死。

工作流元-pivot 的判断式：

```
预先思考的 ROI ≈ (协调成本 + 不可逆决策密度) / 实现成本
```

本项目（单人 + Claude 协作 + 学习目标）：分子 ≈ 0，分母 ≈ 0，等式失效。Google skill
workflow 在多人协作 + 高协调成本场景成立，**不能机械搬到这里**。

落地：

- 删全局 `~/.claude/CLAUDE.md` 的 skill mapping（"Starting new feature → spec-driven-development"）
- 写项目级 `CLAUDE.md`：capability 颗粒度 spec → agent runtime 决定 sub-task → 人审 review
- `decisions/` 只留外部约束（D5.1/5.2/5.3/5.4/5.7）；内部策略移到 `learnings/04-cli.md`
- `tasks/plan.md` 不预先写 sub-units

### 4.3 两次纠正的共同模式

**识别"节奏不对"是反思的契机**：

- T2：每个 commit 1 行差 → "节奏不对"信号
- T4：写 spec 比写代码慢 → "节奏不对"信号

**不为仪式而仪式**：TDD 不是机械的"先写一个测试"；spec 不是"必须写完再开工"。
它们是**为正确目的服务的工具**，目的变了工具就要变。

---

## 5. spec-heavy 预测的有效性（一个意外的 data point，但不能滥读）

T4 mid-implementation pivot 后，原 D5.5/5.6/5.8 三条预判被移到 `learnings/04-cli.md` 当 draft。
P1-T4 build 完成后回看，**三条全部按预判实施了**：

- D5.5 append-only print → `_stream_render.py` 完全按预判
- D5.6 4 类差异化 hint → `cli.py` 5 except blocks
- D5.8 Phase 1 仅 streaming text → 没加 `--output json` 等

**这不能作为"预先思考有用"的论据**，因为：

1. 我们不知道"如果不预先想"会不会出来更优策略——没有反事实
2. 这次预对了不代表下次也会
3. 真正的 ROI 计算还要算"预测的认知开销" vs "build 时 emerge 的开销"

**真 takeaway**：spec-heavy 适合**已知边界清晰的小 capability**（CLI 渲染就是这种）；
**不适合探索性工作**（开放领域 / 设计未明 / 多个合理路径）。Phase 1 的 module 都偏前者，
所以 spec-heavy 没翻车——但**不能由此推论 Phase 2+（tool loop / context mgmt /
extensibility）也适合 spec-heavy**。这些后续 phase 不确定性更高，应该用 capability-driven。

---

## 6. Python / 工具链固化下来的规则（可直接搬到 Phase 2）

### 编码规则

- `from __future__ import annotations` 全文件第一行
- src layout（`src/openharness/...`），`uv sync` 后才 import
- `mypy --strict`，禁 `# type: ignore` without `[error-code]` + 注释
- Pydantic v2 + `StrictModel`（`extra="forbid"` + `validate_assignment=True`）
- Discriminated union via `Annotated[X | Y, Field(discriminator="type")]`
- 异常翻译用 `raise X from Y` 保留 `__cause__`
- 异常 `isinstance` 链 concrete-first 排序（子类先父类后）
- 流式有副作用时**用 list 不用 generator**（generator 不迭代不执行）
- `2.0 ** int` 不是 `2 ** int`（mypy strict pitfall）
- 类型注解专用 import 进 `if TYPE_CHECKING:` 块（ruff TC003）

### 测试规则

- 测试名 = 用户能 feel 的产品契约
- Mock 只在 process boundary（HTTP API / 外部 SDK）；不 mock 内部模块
- 三层 Fake 对象比 `unittest.mock` 可读；mypy 也开心
- `tmp_path` fixture 用真实 FS，不 mock
- 集成测试用 `@pytest.mark.integration` gate；conftest autouse + marker carve-out
- Coverage 是 floor 不是 ceiling，70% gate；目标是行为覆盖

### Pydantic 规则

- `Literal["text"] = "text"` 是类型也是默认值
- `dict[str, Any]` 是 JSON Schema 类的开放结构（在 docstring 补回类型信息）
- `@property` 不序列化；`@computed_field` 序列化
- ruff `runtime-evaluated-base-classes` 必须包含 Pydantic 基类（防 TCH001 误报）
- `__all__` 字母序（ruff RUF022）

---

## 7. 可迁移到 Phase 2+ 的架构 pattern

### 7.1 Provider 添加的项目级模板（4 组件）

```
src/openharness/api/<provider>_errors.py        ← Provider-specific 异常翻译
src/openharness/api/<provider>_retry.py         ← 通常复用 retry.py
src/openharness/api/<provider>_translation.py   ← Anthropic-shape ↔ Provider-shape
src/openharness/api/<provider>_client.py        ← 编排：translate → SDK call → translate
```

未来加 Anthropic-native client / DeepSeek / 等等，跟着这 4 组件 walk through。

### 7.2 Module-level seam 注入（取代 SDK mock）

```python
# 在 module 层暴露窄接口
def _load_settings() -> Settings: ...
def _build_client(settings) -> ApiClient: ...

# 测试用 monkeypatch.setattr 替换
monkeypatch.setattr(cli_module, "_build_client", lambda _: stub)
```

测试不需要知道底层 SDK 长什么样。Phase 2+ 加 frontend (HTTP server / TUI) 时同 pattern 可直接复用。

### 7.3 Marker-aware autouse conftest

```python
@pytest.fixture(autouse=True)
def _clean_env(request, monkeypatch):
    if "integration" in request.keywords:
        return  # carve-out: 集成测试要真 env
    for var in (...):
        monkeypatch.delenv(var, raising=False)
```

Unit + integration 共存的标准 pattern，Phase 2+ 加任何外部依赖测试都用得上。

### 7.4 Append-only renderer 的 Unix 哲学

`_stream_render.py` 选择不用 Rich `Live`，最强的理由不是"简单"是 **composable with pipes**：

```bash
oh ask "explain X" | tee transcript.txt    # 转写
oh ask "list packages" | jq                # 假设输出 JSON
oh ask "..." > log 2>&1                    # stderr 也捕获
```

Rich `Live` 写 ANSI escape codes 重渲屏幕，破坏管道。**Unix tools 的内核是
"composable text streams"——任何 CLI tool 都该先满足这条**。

---

## 8. 进入 Phase 2 之前的"该校准"清单

来自各 module 的 retro：

- [ ] 显式定义 `class SupportsStreamingMessages(Protocol)`（[03 #3](./03-api-client.md)）
- [ ] `_FAST_POLICY` 抽到 `tests/api/conftest.py`（[03 #4](./03-api-client.md)）
- [ ] `_translate_openai_error` 单独 test file（[03 #6](./03-api-client.md)）
- [ ] CI 显式加 `-m "not integration"` flag（避免无声跑集成测试）
- [ ] README "How do I try it?" 段（已在 P1-T5 完成）
- [ ] `decisions/00-env.md` 记录代理端口陷阱（[01 #3](./01-scaffolding.md)）

每条都是小动作，进 Phase 2 之前 batch 处理。

---

## 9. 元层面：双重目标的进展

| 目标 | 进展 |
|---|---|
| 1. 交付生产级 LLM harness | ✅ Phase 1 段达成。`oh ask` 能跑、coverage 92.83%、CI 绿 |
| 2. 通过项目实践成为领域专家 | 🟡 早期信号 |

**目标 2 的早期信号**：

- ✅ 能从契约层对话（不再是"加一个字段"的细节级）
- ✅ 能识别"节奏不对"主动 pause 调整（T2 中段、T4 末段两次）
- ✅ 能区分**外部约束**（pre-decide）vs **内部策略**（emerge）
- ✅ 能反向质疑工具（Google skill workflow 不适合本项目，主动剥离）

**进 Phase 2 时观察的 leading indicator**：tool loop 是更复杂的领域（不再是单次请求/响应，
有状态机 / `stop_reason` 驱动 / 工具结果回灌 / 超时...）——能否同样保持框架视角？还是会
退回到"加一个 if 分支"的细节级？

---

## 10. Phase 2 开场白

Phase 2 = Tool Loop（`BaseTool` / `ToolRegistry` / `run_query()` / Read+Write+Edit+Bash+Grep）。

Phase 1 是"harness 能说话"，Phase 2 是"harness 能动手"——LLM 通过 `tool_use` 让 harness
做事（读文件、跑命令、grep 代码），harness 把结果用 `tool_result` 喂回 LLM。这是
agent harness 的核心循环。

进 Phase 2 前的 mindset 锚点：

1. **每个工具都是一个用户契约**（user 视角："我能让 LLM 帮我做什么"）
2. **工具的失败要可读**（`exit code` / `stderr` / 超时 / 错误码差异化处理）
3. **工具循环的状态机要纯**（`stop_reason` 驱动，无副作用，可序列化）

这三条会成为 Phase 2 的设计 anchor——和 Phase 1 anti-corruption layer 一样，是要在
build 中**实测**的产品事实，不是装饰用的设计原则。
