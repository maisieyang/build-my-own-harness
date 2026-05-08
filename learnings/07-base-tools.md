# Module 7: Five Base Tools — 复盘

> Phase 2 / P2-T3 / 完成日期：2026-05-08 / 用时 ~半天（6 个 micro-cycle 串行）

## 这个模块解决了什么 harness 问题

把 P2-T2 的 `BaseTool[InputT]` 抽象**具象化**为 5 个真工具——LLM 第一次能"做事"
（Read/Write/Edit/Bash/Grep），不只能说话。第一性原理 §2.2 工具抽象层从"骨架"
变成"骨架 + 5 个肌肉"。

附带：`create_default_tool_registry()` 是 P2-T6 cli.py 即将调用的入口，**P2-T3
shipped 的同时把 P2-T6 的工具注入入口也准备好了**。

## 产品决策回顾（D9.1 - D9.6）

| 决策 | 选了什么 | 替代方案 | 什么时候改选替代 |
|------|---------|---------|---------------|
| D9.1 | 相对 → cwd / 绝对 as-is | 强制绝对路径 | 不会改——LLM prompt 大概率用相对路径 |
| D9.2 | Write/Edit 限制 cwd 内 | 全部允许 / 全部限制 | P2-T6 plumbs `--auto` 时**放松**（不是改这条决定） |
| D9.3 | 一文件一工具：`tools/{read,write,edit,bash,grep}.py` | 全塞 base.py | 工具数量 < 3 时改 base.py |
| D9.4 | Bash 不带 deny-list | 双层防御 | 不会改——P2-T6 PermissionChecker 是单一权威 |
| D9.5 | exit_code 进 metadata | 进 output | 不会改——output 给 LLM、metadata 给程序两条通道 |
| D9.6 | 描述性错误 + ~200 char | 终止信号 / 完整 stack | 不会改——LLM 自我恢复需要可读性 |

详见 [decisions/07-base-tools.md](../decisions/07-base-tools.md)。

## Python 模式（继续 TS 出身的 reference 笔记）

### 1. `asyncio.to_thread` 让 blocking IO 不堵 event loop

```python
text = await asyncio.to_thread(
    path.read_text,
    encoding="utf-8",
    errors="replace",
)
```

`Path.read_text` 是同步阻塞调用。包一层 `asyncio.to_thread` 把它扔到 worker
thread 池，event loop 自由。

**P2-T3 阶段串行（D6.3）这条不是必需**——但 P3+ 一旦放开并行 tool execution，
`asyncio.gather([read_a.execute(...), read_b.execute(...)])` 才会真的并发。
现在写好 = 未来无回归。

### 2. asyncio subprocess: `_shell` vs `_exec`

```python
# Bash: 用户命令需要 shell 语义（重定向、管道、环境变量）
asyncio.create_subprocess_shell(args.command, ...)

# Grep: 我们自己拼参数列表（rg --line-number ... pattern target）
asyncio.create_subprocess_exec(*cmd, ...)
```

- `_shell` 走 `/bin/sh -c "..."` —— 给 LLM 写的命令；接受任意 shell 表达式
- `_exec` 走 `execve` —— 我们自己拼的参数列表；不经 shell 解释，更安全

**经验法则**：如果**输入字符串来自 LLM**，shell；如果**我们已经把输入拆成参数**，
exec。Bash 用 shell 是必须的（LLM 写的就是 shell 命令），Grep 用 exec 因为
pattern / glob 都是参数化的。

### 3. SIGTERM → grace → SIGKILL 模式

```python
async def _terminate_then_kill(process: asyncio.subprocess.Process) -> None:
    process.terminate()  # SIGTERM
    try:
        await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_PERIOD_SECONDS)
    except asyncio.TimeoutError:
        process.kill()  # SIGKILL
        await process.wait()
```

POSIX 进程清理的标准模式：先优雅请求退出，给 N 秒清理时间，超时强杀。
**关键**：每个分支都要 `await process.wait()` 收尸，否则 returncode 是 None。

### 4. `Path.resolve(strict=False)` 对未存在路径也安全

Write 的 D9.2 scope guard：

```python
def _inside_project_root(path: Path, cwd: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(cwd.resolve(strict=False))
    except ValueError:
        return False
    return True
```

`resolve(strict=False)` 处理符号链接 + `..` 组件，**即使路径不存在也工作**——
这对 Write 至关重要（它的工作就是创建尚不存在的文件）。`relative_to` 在路径
不在 cwd 子树时抛 `ValueError`，转成 False。

这条 + 一行测试 (`test_relative_dotdot_escape_rejected`) 就把"LLM 用 `..`
逃出 cwd"的问题挡死。

### 5. Pydantic Field 约束是免费的输入验证

```python
class BashInput(BaseModel):
    command: str = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, ge=1)

class GrepInput(BaseModel):
    pattern: str = Field(min_length=1)
    line_cap: int = Field(default=200, ge=1, le=2000)
```

LLM 给空 command / 0 timeout / 5000 line_cap → ValidationError 在 BaseTool
外层抛出，**execute 内部不需要写防御代码**。phase-2-plan.md 的"200/2000 hard
cap" 直接 = `Field(le=2000)` 一行。

### 6. mock.patch 的精确度陷阱

3a 的 file-too-large 测试我第一版写的：

```python
with patch.object(Path, "stat") as stat_mock:
    stat_mock.return_value.st_size = MAX_READ_BYTES + 1
```

挂掉了——`patch.object(Path, "stat")` 把 **Path 类的 stat 方法**全 mock 掉，
`is_file()` 内部调 stat() 也被 mock，但 mock 的 stat() 没 `st_mode` 字段，
炸 TypeError。

**修法**：mock 常量而不是方法：

```python
with patch("openharness.tools.read.MAX_READ_BYTES", 5):
    target.write_text("hello world")  # 11 bytes
    result = await tool.execute(...)  # → file too large
```

Mock 越靠近测试目标，副作用越少。

### 7. `errors="replace"` 两种用法

**输出 decode**（Bash/Grep）：

```python
output = stdout_bytes.decode("utf-8", errors="replace")
```

二进制输出里的非 UTF-8 字节变 `�`——LLM 看到一堆 `�` 就知道是二进制了。

**文件 read 然后 write 回**（Edit）—— **不能** 用 `errors="replace"`：

```python
try:
    original = await asyncio.to_thread(path.read_text, encoding="utf-8")
except UnicodeDecodeError:
    return ToolResult(is_error=True, output=f"file is not valid UTF-8: {path}")
```

如果用 replace，`�` 会替换无效字节然后写回去，**永久毁数据**。Edit 必须用
`errors="strict"` (默认) 然后 explicit catch。

### 8. `pytestmark_skipif` 处理可选系统依赖

Grep 测试需要 `rg` 在 PATH 上。一份 marker 标记整个 class：

```python
pytestmark_rg_required = pytest.mark.skipif(
    shutil.which("rg") is None,
    reason="ripgrep (rg) not installed; skipping real-binary Grep tests",
)

@pytestmark_rg_required
class TestGrepMatching:
    ...
```

CI 装了 rg → 全跑；本地有人没装 → skip 不挂。但**"rg missing"路径本身**仍然
要测——`patch("shutil.which", return_value=None)` 让 happy 测试用真 rg、错误
测试用假场景。

## 工程要点

### 1. 重复的 `_resolve` / `_inside_project_root` —— 暂不抽

read.py / write.py / edit.py / grep.py 各有一份 `_resolve(raw, cwd)`；
write.py / edit.py 各有一份 `_inside_project_root(path, cwd)`。

抽到 `tools/_paths.py` 是一行的事。**不抽**的理由：
- 4 处共 ~30 行重复代码，cost 不大
- 抽出后每个 tool 多一行 import，可读性反而降低
- 如果 P3 出现第 6 个 filesystem-touching tool，**那时再抽**

phase-2-plan.md 关注"capability 完成"，不关注"内部代码 DRY"。

### 2. PEP 487 `Annotated` 自定义参数文档

```python
class WriteInput(BaseModel):
    path: str = Field(description="File path. Relative paths resolve against cwd.")
    content: str = Field(description="Text content to write (UTF-8 encoded).")
```

`Field(description=...)` 写进 Pydantic 模型 → `model.model_json_schema()` 自动
带上 → `to_api_schema()` 把 description 喂给 LLM。**LLM 看到的工具文档 =
代码里的 description 字段** —— 一处定义、多处生效。

### 3. 测试用 macOS-tolerant 路径断言

Bash cwd 测试一开始用 `assert str(tmp_path) in result.output`，但 macOS 把
`/var/folders/...` 解析成 `/private/var/folders/...`，断言挂掉。

**修法**：断言 `tmp_path.name`（trailing 唯一段），跨平台稳定：

```python
assert tmp_path.name in result.output
```

写跨平台测试时**避免对路径做完整字符串比对**，挑路径中**唯一可识别**的部分。

## 可迁移到后续 Phase 的 architecture pattern

| Pattern | 来源 | 迁移到 |
|---|---|---|
| **asyncio.to_thread 包 blocking IO** | Read/Edit/Write 的 read_text/write_bytes | Phase 4 Memory 持久化 / 任何 stdlib 同步 IO |
| **Pydantic Field 约束 = 免费输入验证** | 5 个工具的 Input model | Phase 5 MCP adapter / Phase 5+ Skill 接口 |
| **D9.5 输出/metadata 双通道职责** | Bash exit_code in metadata | 任何"对人 + 对程序"两套读者的 ToolResult |
| **D9.4 单层权限** | Bash 不带 deny-list, P2-T6 拦 | Phase 3 hooks / Phase 5 Sandbox 都是"在外层包一层" |
| **mock 常量而非方法** | 3a Read MAX_READ_BYTES 测试 | 任何"模拟 size limit / 阈值"测试 |
| **跨平台路径测试用 .name** | Bash cwd 测试 | 任何 macOS/Linux 都要绿的子进程测试 |

## 一句话总结

> P2-T3 的 6 个 micro-cycle 把工具抽象从"骨架"补成"骨架 + 5 个肌肉"。每个工具
> 都是 `class XTool(BaseTool[XInput])` 的具体实例化——Generic[InputT] 在 P2-T2
> 立的契约，到 P2-T3 兑现成 5 个独立 typed tool。`create_default_tool_registry()`
> 是 P2-T6 cli.py 的 anchor 点，已就位。下一个 capability（P2-T4 loop body）
> 终于可以写"心脏"了——所有协作者全部齐了。
