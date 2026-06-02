# Learnings — Phase 15 (Rich `Live` spinner for tool calls)

> Phase 15 起止 / 2026-06-02(单日 session,与 Phase 14 并行进行)
> 1 capability (P15-T1) / **1 commit**(e8202c1)+ boundary + plan + design doc
> 139 行净增生产(`_stream_render.py`)/ 152 行测试净增 / 305 行 design doc(`docs/ideas/`)
> 14 个 renderer 测试(原 10 + 新 4)+ 70 个 invariant 测试 / 0 现有 assertion 修改 / 0 新依赖 / ruff + mypy --strict clean
>
> 本文件回答的题:**append-only renderer 加上一层 transient Live 区,在不打破"byte-identical to v0.2.0 off-TTY"前提下,实际成本和踩坑长什么样?**
>
> 以及一个 meta 问题:**第一次出现"两个 Phase 真正并行" (Phase 14 web tools + Phase 15 renderer) 时,4-step loop 的 staging / smoke / commit 卫生是否还能 hold?**

---

## 1. 数据点

| 维度 | Phase 14(web tools, 并行中) | **Phase 15(Live spinner)** |
|---|---|---|
| Capability | 4 | **1** |
| 生产代码净增 | 数百行(web_search + web_fetch + cli wiring) | **139 行**(单文件 `_stream_render.py`) |
| 测试净增 | +85 | **+152**(4 个新 `TestLiveBranch`,但 stdout 字节级断言零修改) |
| 新模块 | 2(`web_fetch.py` + `web_search.py`) | **0**(本地 1 个 helper class `_ToolSpinnerRenderable`) |
| 新 Settings 字段 | 5 nested | **0** |
| 新 CLI flag | `--enable-web` | **0**(auto-detect via `Console.is_terminal`) |
| 新依赖 | `markdownify` + `bs4` | **0**(rich 已是 typer transitive) |
| **保护层 zero-diff** | 11/11 | **12/12** ⭐(engine/services/protocols/tools/hooks/permissions/skills/commands/bundles/plugins/mcp/markdown_store/memory + 7 个 existing tools) |
| 触发源 | dogfood bug(LLM 在无 web tool 时混用 Grep 局部文件 + 凭空捏造) | **design discussion**(TUI 生态 + Python 方案选择) |
| 时间 | 数小时 / 多 commit | **<2 小时**(含 design discussion) |

---

## 2. Framework-level 主题

### 2.1 ⭐ `auto_refresh + __rich_console__` idiom — elapsed counter 不需要 background task

Plan 的 predicted retro question:

> Was the "elapsed-seconds counter that updates every 100ms" implementable without a background task, or did it require a short-lived task per tool call?

答案:**不需要 background task**。关键是 rich.Live 自带 refresh thread(我配 `refresh_per_second=10`)+ renderable 实现 `__rich_console__`:

```python
class _ToolSpinnerRenderable:
    def __rich_console__(self, console, options):
        elapsed = time.monotonic() - self._start_time
        line = f"[{self._tool_name}] {self._args_repr} ({elapsed:.1f}s)"
        grid = Table.grid(padding=(0, 1))
        grid.add_row(self._spinner, line)
        yield grid
```

Live 的 refresh thread 每 100ms 调一次 `__rich_console__`,它读 `time.monotonic()`。**状态在函数里,动态在框架里**,完全没有 async 协调成本。

如果走 background task(`asyncio.create_task` 每 100ms call `live.update()`)就要处理 task cancel / 异常 propagate / 多 tool 并发隔离等一整套问题。

这是个值得记的 idiom:**把"动态值"挪到 renderable 自身,而不是用外部 task 推动 renderable**。下个 Live region 落地(比如流式 markdown)大概率仍然适用这一招。

### 2.2 byte-identical off-TTY invariant 几乎免费拿到 — io.StringIO 天然 isatty()=False

Plan 预判:

> Output is byte-identical to v0.2.0 — verified by all existing test_render.py assertions passing without modification.

实现期我担心:写测试的人很可能 patch `sys.stdout = StringIO()` 然后 forget 这个会让 Console 自动落入非 TTY 分支。结果发现:

```python
io.StringIO().isatty()  # False
```

Python stdlib 默认行为。`Console(file=StringIO())` 自动 `is_terminal=False`。**289 行原有测试零修改 pass**。

这是个隐性 invariant —— **任何非 TTY 抽象层(StringIO / pipe / file / CI)都 `isatty()=False`**。boundary D30.3 直接借助这个 invariant 把"测试不破"变成几乎免费的事。

### 2.3 boundary → impl 的一个细化:`console.print` → `out.write`

Boundary D30.3 写的是 "TTY 分支用 `console.print(final_line)` 写最终行"。实现时改成了 `out.write(_render_tool_completed(event))`。

`console.print` 会解析 Rich Markup。final line 含 `[Bash]` —— 这是 Python list literal 风格的字符串,但被 Rich 当 style tag(变色或报错)。`out.write` 直接写字符流,跟 non-TTY 分支共享同一个 helper,**保证 final-line 字节内容在两个分支真正一致**。

这是从 D30.3 的 "byte-identical content" 不变量推出的实现细化,不改决策,只改 how。

教训:**boundary 写"用什么 API"是过早细化;真正不变的是"什么不变量"**。下一次 boundary 再涉及 rich,我会避免写 "用 `console.print()`",改成 "在 TTY 分支用 rich,在 non-TTY 分支用 raw write,两条路径产生的可读 token 字节级一致"。

### 2.4 Phase 14 并行带来的三个干扰点

这是 OpenHarness 项目里第一次出现真正的并行 phase。Phase 14 由另一个 session 推进 web tools,Phase 15 本 session 推进 renderer。三个具体干扰:

**(a) Smoke 被堵**:`oh ask` 的 import 链触到 Phase 14 的 `web_fetch.py` 触到没装的 `bs4` → 无法 smoke。解决:写 `scripts/smoke_phase15.py` 直接调 `render_stream`,完全绕过 `cli.py`,smoke 完即删。

**(b) Staging 状态混乱**:`git status` 显示 Phase 14 文件被 staged(另一个 session 触发),我以为是我加的。第一次 `git diff --cached --stat` 看到 9 个文件吓一跳。调查后明白:`git add` 是 additive,别的 session staged changes 还在 index 里。用 `git restore --staged <Phase14 files>` 解锁。

**(c) Pre-commit hook 旧路径**:`.git/hooks/pre-commit` 里硬编码的 `INSTALL_PYTHON` 路径残留(repo 被 move 过,从 `/Users/yangxiyue/2026/aa/build-my-own-harness/` 到 `/Users/yangxiyue/2026/aa/harness/build-my-own-harness/`)。一次性 workaround:`PATH=$PWD/.venv/bin:$PATH git commit`。永久修复:`uv run pre-commit install`(retro 期间已修)。

教训:**多 phase 并行时,"this commit 的范围是什么"必须由人显式 ratify,不能由 git status 推断**。我的 walkthrough 在 commit 前展示了 staged file list 让你 ratify,抓到了 Phase 14 混进来的问题。**4-step loop 的 review-walkthrough 在并行场景下变得更关键,不是 less**。

### 2.5 design discussion 落地为独立文档 — `docs/ideas/` 不是 boundary 的副本

Phase 15 的触发**不是 dogfood bug**(像 Phase 14 那样),而是**一次深入 design discussion**:TUI 生态全景 / web frontend vs TUI 的第一性原理 / Python 端方案选择(Textual vs Rich 增强 vs Ink+IPC bridge)。这个讨论我落到 `docs/ideas/tui-vs-web-frontend-first-principles.md`(~3500 字,含续篇生态调研)。

**这个文档不是 boundary 的子集**。boundary 只 lock 5 个 D30.x 决策。doc 承载的是"为什么走这条路、其他路为什么没选"的 reasoning trail。

教训:**当 phase 的 trigger 是"长讨论的结晶"而不是 bug 时,design rationale 应该独立成文档**,不应塞进 boundary doc 让它膨胀。boundary 保持 "what / not what / invariants" 纪律,文档承载 "why / why not"。这种分工让 boundary 短小可读,让 rationale 可以充分展开。

---

## 3. 预测下一 phase

Plan 留的 next-phase question:

> If streamed assistant text as markdown (capability "B") becomes the next ask, is the renderer's branch structure the right seam to add a second Live region on top of, or does it need re-shaping?

我现在看下来,branch 结构 **可以承担第二个 Live region**,但需要明确以下:

- **退出语义**:tool spinner Live 是 transient(清掉自己);流式 markdown Live 应该是 **non-transient + 增长**(token 一边到,一边在屏幕上长)。两种 Live 的退出方式不同,boundary 要写清楚
- **Re-parse 性能**:每个 delta 重 parse 整段 markdown 会卡。落地大概率需要 "按代码块边界 commit" 策略,在边界之间维持 plain-text fast path
- **并发**:tool Live + markdown Live 同时活跃?engine 是 serial dispatch,LLM 不会一边输出 text 一边 emit tool call,**事实上不会重叠**,但 boundary 要 lock 这个假设
- **非 TTY 降级**:markdown Live 在非 TTY 时降级到原始 text(现状),应该零改动,结构上跟 D30.3 同构

如果走 Plan B,大概率是 Phase 16。估计成本:**比 Phase 15 大 1.5–2x**(markdown re-parse 是真问题,不是接口设计问题)。

---

## 4. 元 — 4-step loop 在两个 phase 并行时的表现

| Step | Phase 15 表现 |
|---|---|
| **Boundary doc** | ✅ 完全独立于 Phase 14,D30.1-D30.5 + invariants 清楚 |
| **Plan** | ✅ 单 P15-T1,capability-level,不拆(单一 capability 不拆是对的) |
| **Execute** | ⚠️ 受 Phase 14 干扰但**显式隔离了**(smoke 用绕过脚本,commit 用显式 file list,unstage 前先让你 ratify) |
| **Retro** | ✅ 本文件,且包含"两个 phase 并行"的元教训 |

**结论:4-step loop 在并行场景下 still works,但 Step 3(Execute)的纪律变得更关键** —— git staging 状态必须由人 ratify,不能由 `git status` 推断。这次的 walkthrough-before-commit 流程在并行干扰下证明了价值。

---

## 5. 一句话

> Phase 15 给 append-only renderer 加了一层 transient Live 区,**0 新依赖、0 现有断言修改、12/12 保护层 zero-diff**,而且发现了 `rich.Live(__rich_console__ + auto_refresh)` 这个 idiom:**动态值放在 renderable 里,让框架的 refresh thread 推动,避开整类 async 协调成本**。
