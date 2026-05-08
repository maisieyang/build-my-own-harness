# Module 9: System Prompt Assembly — 复盘

> Phase 2 / P2-T5 / 完成日期：2026-05-08 / 用时 ~半小时（2 个 micro-cycle）

## 这个模块解决了什么 harness 问题

LLM 不知道"它在哪、它能做什么"——P2-T5 给它最后一块 context。

`build_system_prompt(tools, env) -> str` 是 cli.py 在 P2-T6 构造 QueryContext
**之前**调用一次的纯函数。结果字符串塞进 `context.system_prompt`，loop 每一轮的
request 都带着它（P2-T4.4d `request.system = context.system_prompt or None`
已经验证透传）。

**这是 Phase 2 静态侧的最后一块**——loop runtime 已经能跑、工具已经接好、错误
已经能恢复，就差告诉 LLM "你是谁、你看到的是什么环境、你有什么工具"。

phase-2-plan.md 把这个函数标为 "Phase 3 personalization / Phase 4 memory 的扩
展点"——签名稳定，body 增量。

## 产品决策回顾（D11.1 - D11.6）

P2-T5 的 6 条决策都是 shape 选择，没有 P2-T4 那种 LSP 级别的关键 trick。

| 决策 | 选了什么 | 替代 | 什么时候改选替代 |
|---|---|---|---|
| D11.1 | EnvironmentInfo 5 字段 | + git_branch / venv / locale | P3 personalization 出现真实需求时 |
| D11.2 | `detect_environment()` 不抛 | 抛 OSError 让 caller catch | 不会改——cli.py 不应该为 host 探测写 try/except |
| D11.3 | Markdown 章节（不 XML） | XML 标签 / plain text | LLM 在 Markdown 上跑得不好时（Qwen-plus 实测无问题） |
| D11.4 | Tool description 全文 | 截断到 1 行 | 5 个 tool description 加起来超过 1k tokens 时（远未触及） |
| D11.5 | Base instructions ~50 字 + 错误恢复指南 | 长篇格式指引 / 风格手册 | 不会改——Phase 2 不调 prompt engineering |
| D11.6 | 空 tools 走 `(no tools registered)` 哨兵 | 跳过整个 `## Tools` section | snapshot test 风格变得复杂时 |

## Python 模式

### 1. "结构化 prompt"的极简形态

```python
return "\n\n".join([
    _BASE_INSTRUCTIONS,
    _format_tools_section(tools),
    _format_environment_section(env),
])
```

3 行 join 写完整个 prompt 装配。每个 section 是一个 helper 函数，返回完整的
`## Header\n\nbody` 块。**无模板引擎、无 f-string 嵌套、无 jinja**——纯 Python
字符串拼接就够。

P3 加 personalization、P4 加 memory，只是在这个 list 里多塞一个 `_format_xxx_section(...)`
调用。append 而非 rewrite。

### 2. "Don't truncate when context is cheap"

D11.4 决定不截断 ToolSpec.description 到 1 行。背后的算账：
- 5 个 base tool 的 description 加起来 ~250 字 ≈ 60 tokens
- context window: 数十 K
- 截断成本：丢失 LLM 必须看的细节（如 Read 的 ≤10MB 限制）

工程经验值：**当 token 成本 < 1% 时，倾向"信息忠实"而不是"信息精简"**。截断是优化，
信息忠实是默认。

### 3. 测试断言"结构"而非"文本"

```python
def test_contains_section_markers(self, tools, env):
    prompt = build_system_prompt(tools, env)
    assert "## Tools" in prompt
    assert "## Environment" in prompt

def test_section_order_is_base_then_tools_then_environment(...):
    prompt = build_system_prompt(tools, env)
    tools_idx = prompt.index("## Tools")
    env_idx = prompt.index("## Environment")
    assert prompt.index("OpenHarness") < tools_idx < env_idx
```

**断言 markers 而非 wording**——Phase 3 改 base instructions 措辞、Phase 4 加新
section，这些测试**不应该**碎掉。snapshot 测试的死法就是断言完整文本，每改一字
都得 update snapshot。

更细的：用 `prompt.index(...)` 不是 `assertIn` —— index 比 in 检查更严格地确认
了"位置"，不是"出现过"。

### 4. monkeypatch fixture 测环境感知

```python
def test_shell_falls_back_when_env_var_unset(self, monkeypatch):
    monkeypatch.delenv("SHELL", raising=False)
    env = detect_environment()
    assert env.shell == "/bin/sh"

def test_shell_uses_env_var_when_set(self, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
    env = detect_environment()
    assert env.shell == "/usr/local/bin/fish"
```

`monkeypatch.delenv(name, raising=False)` 是惯用法——`raising=False` 在 var
本身就缺失时不抛 KeyError。pair 起来测 fallback path 和 happy path。

测试结束后 monkeypatch 自动复原，不污染其它测试。

## 工程要点

### 1. `prompts.py` 顶级 vs `prompts/` 子包

我把它放在 `src/openharness/prompts.py`（顶级单文件）而不是 `src/openharness/prompts/`
子包。理由：
- 当前内容 ~80 行，单文件够
- P3/P4 加 personalization / memory 的逻辑可能比 prompt 装配复杂——那时再拆
- 不预先做 packaging，省去现在的 `__init__.py` 维护

未来扩展信号：`prompts.py` 超 300 行 / 出现 3+ 独立子模块（rules / memory_index /
template）时，再拆成 `prompts/` 包。

### 2. 不放在 `engine/` 的理由

prompts 不是 loop 内部状态——它的 consumer 是 cli.py（构造 QueryContext 之前
跑一次），不是 run_query。

`engine/` 是 "loop 跑的时候用什么"；`prompts/` 是 "loop 跑之前喂什么"。职责切分
清晰。

### 3. `_BASE_INSTRUCTIONS` 是 module 常量而非函数

```python
_BASE_INSTRUCTIONS = (
    "You are OpenHarness, an LLM agent. ..."
)
```

不写成 `def _base_instructions() -> str: return "..."` 因为：
- 没有任何动态拼接
- module 常量更清晰地表达"这是固定文本"
- 测试 patch `_BASE_INSTRUCTIONS` 比 patch 函数更直接（如果 P3 需要的话）

### 4. `## Tools` empty-registry 哨兵的另一个收益

D11.6 决定空 registry 仍发 `## Tools` section + `(no tools registered)`。除了
"prompt 结构稳定"以外，还有一个隐性好处：**LLM 看到 `(no tools registered)`
就知道工具机制存在但当前没工具**——它不会"凭空想象"工具调用，也不会以为这是普通
chat 模式。明确比模糊好。

## 可迁移到后续 Phase 的 architecture pattern

| Pattern | 来源 | 迁移到 |
|---|---|---|
| **append-only 章节扩展** | `_BASE_INSTRUCTIONS / _format_tools_section / _format_environment_section` | P3 personalization / P4 memory 的章节注入 |
| **签名稳定 + body 增量** | `build_system_prompt(tools, env) -> str` | 任何"现在简单、未来扩展"的纯函数 |
| **结构断言而非文本断言** | `assert "## Tools" in prompt` | 任何"内容会演化但结构稳定"的输出测试 |
| **monkeypatch.delenv + setenv pair** | SHELL fallback + custom test | 任何"读环境变量并 fallback"的测试 |
| **顶级文件而非子包** | `prompts.py` | < 300 行 + 单一关注点的工具模块 |

## 一句话总结

> P2-T5 用 ~80 行写完整个 system prompt 装配。EnvironmentInfo 5 字段 + Markdown
> 3 章节，签名稳定可扩。Phase 2 的静态侧拼图全部就位——loop 跑、工具能调、错误
> 能恢复、prompt 知道"我在哪能做什么"。**剩下的 P2-T6 就是把这一切接到 cli.py
> 的血管**：`oh ask "..."` 真的能跑工具循环了。
