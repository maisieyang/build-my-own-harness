# 下一阶段：Node + Ink — 一个 Web 前端的 CLI 交互路径

> 写于 2026-06-07 · 中文版
>
> 配套读物：
> - 项目方法论：[CLAUDE.md](../../CLAUDE.md)
> - 上一篇第一性原理：[docs/ideas/tui-vs-web-frontend-first-principles.md](./tui-vs-web-frontend-first-principles.md)
> - OpenHarness 的相关决策：[Phase 15 boundary](../../decisions/30-phase-15-rich-live-boundary.md)（拒绝 Textual，选 Rich Live）
>
> 这一篇不再讨论"OpenHarness 要不要做 TUI"——那个问题上一篇已答完。
> 这一篇是**个人路线决策**：如果我要在 CLI 交互这件事上做到专家水准，去哪做、用什么栈。结论先放：**Node + Ink，独立项目，不和 OpenHarness 同进程**。

---

## 〇、决定

✅ **下一阶段开一条独立轨道：用 Node + Ink 复刻 Claude Code 级 CLI 交互。**

- **不是**给 OpenHarness 套 UI——Phase 15 已 ratify 不上 Textual
- **不是**做 Python+Ink 跨语言架构——上一篇 §七 已否决
- **是**单独的 Node 项目，单独的进程，单独的 phase loop
- **现在不做**——沉淀想法，作为 OpenHarness 收尾后的 next step

理由一句话：**做 TUI = 自带浏览器 + 自带 React。我已经会 React。所以把学习预算集中在"自带浏览器"那 20% 上。**

---

## 一、和上一篇《终端不是浏览器》的关系

上一篇 6-02 写的，主体是 OpenHarness，问 "Python harness 要不要做 TUI"。结论是 §七：

> 对 OpenHarness 自己的决策，意味着两条优先级更高的路：Python 端的 Textual 或者保留 stdout 模式 + 用 `rich.Live` 增强。"Python 引擎 + Ink 前端 + 自造协议"这条路不是技术上做不到，而是业界没有一个先例验证过它值得做。

这一篇的主体换成"**我**"，问的不是项目要不要做 TUI，是**我个人要不要去 CLI 交互这件事上做到专家水准**。两个问题答案不同：

| 维度 | 上一篇（OpenHarness） | 这一篇（我） |
|---|---|---|
| 主体 | 项目 | 个人 |
| 问题 | 项目要不要全屏 TUI | 我去哪当 CLI 交互专家 |
| 时间 | 当下 phase 决策 | 下一阶段开新轨道 |
| 候选 | Python+Textual / Rich Live / Python+Ink 跨语言 | Node+Ink **独立项目** |
| 结论 | Rich Live（Phase 15 已 ship） | Node+Ink，新 repo |
| 是否矛盾 | **不矛盾**——OpenHarness 留 Python 不变，新项目是平行轨道 | |

上一篇否决的是"同一项目跨语言"，这一篇做的是"两个独立项目并行"。**两条结论叠加才是完整的 narrative**。

---

## 二、我现在在哪 / 要去哪

### 现状

- **OpenHarness**（Python）已推进到 Phase 19，CC compat 三维已覆盖：
  - D1 内容渲染（Rich Live）✅
  - D2 触发语义（namespaced `/<plugin>__<skill>`）✅
  - D3 安装分发（`cp -r CC plugin`）✅
  - D4 输入前视觉 affordance（`/` popup 等）❌ 未 ratify
- **REPL 仍是 cooked mode**：`await asyncio.to_thread(input, ">>> ")`，readline + gnureadline 做行内编辑，但 `/` 按下的瞬间收不到字节
- **Python TUI 生态**：Rich/Textual 我有 working knowledge，**不是专家**
- **Node + React 生态**：我有 5+ 年工作经验，**是真正的专家**

### 目标

- 在 **CLI 交互**这件事上达到专家水准
- "专家"的定义：能 from scratch 实现 Claude Code 级别的所有 D4 维度——`/` 实时 popup、anchored input region、plan mode 边框、tool call 卡片折叠、`@` 文件提及、流式 token 不挤掉输入框、resize reflow、theme

### 凭什么这么决定

**唯一的决策驱动**：我已经会 React 五年。Ink **literally is React**——`useState`、`useEffect`、`useMemo`、hooks 全可用，组件树和 Web 完全同形。Node + Ink 让我把 5 年肌肉记忆**零损耗**复用到一个新的渲染目标上。

走 Python + Textual 的等价路径，意味着要学一个**像 React 但不是 React** 的 reactive 系统（reactive properties + CSS-like + 不同的组合 primitives）——同时学两个新东西。Node + Ink 让我只学一个：**终端是怎么被当成渲染目标的**。

---

## 三、核心框架：做 TUI = 自带浏览器 + 自带 React

这是这篇文档**唯一的 load-bearing 比喻**，所有后续决策都从它推出。

### 上一篇说"浏览器是公共运行时"——这次精炼

Web 程序员的认知盲点：我们以为我们"写 UI"，其实我们写的是**浏览器的客人代码**。浏览器替我们做了 90% 的事，五年下来已经默认它免费、永存、零成本：

- 渲染引擎（DOM + CSS + paint）
- JS 运行时（V8）
- 事件循环（主动派发 click / keydown 到你 handler）
- 网络栈（fetch / TLS / 缓存）
- 安全沙箱（CORS / CSP / Same-Origin）

**React 在这张图里其实只是浏览器之上的声明式 API**——它不是 UI 框架本身，是 "浏览器渲染管线" 的薄包装层。Component → Virtual DOM → 真 DOM 操作，最后一步还是浏览器执行。

到终端，这 90% 突然没人替你做了。**Ink 这个 npm package 之所以巧妙：它把"浏览器替你做的那一半" + "React 本体"打包在了一起**。

### 三栈对比图

```
                  Web                  Terminal (stdout)         Terminal (Ink)
              ─────────────          ──────────────────         ────────────────────
              你 5 年熟的             你 OpenHarness 现状        你下阶段要去的

  你的代码    React 组件               typer.echo / print           Ink 组件
              ─────────────          ──────────────────         ────────────────────
  UI 框架     React 本体                  ——                    React 本体  ← 同一个
              ─────────────                                      ────────────────────
  渲染层      DOM / CSS / paint       字符往 stdout 追加           Ink renderer + Yoga
                                                                  ────────────────────
  事件循环    浏览器 event loop       readline 等回车              Node + Ink 事件循环
                                                                  ────────────────────

              ▼ 用户机器自带 ▼         ▼ 终端 emulator 自带 ▼      ▼ 用户机器自带 ▼

  显示设备    像素屏幕                字符 cell grid              字符 cell grid


              ──────────              ──────────                 ──────────
              你只写最上 1 层          你只写最上 1 层             最上 3 层都你 ship
              下面 4 层浏览器给        交互天花板 = 行回车         但 React 那 1 层
              这是基础设施红利         没有 popup / 锚定 / 折叠    你已经会
                                                                  新东西只在中间 2 层
```

**这张图压缩成两条结论**：

1. **Web 是基础设施红利**：你只写最上 1 层，浏览器替你跑下面 4 层。这是为什么 React 工程师入行第一天就能交付 UI——95% 的工作量用户机器上的浏览器替你做了。

2. **TUI 是反过来**：你必须 ship 自己的渲染层 + 事件循环。终端 emulator 给的不是浏览器，是字符 cell grid——一个**字面意义上的哑设备**。Ink/Textual/Bubble Tea/Ratatui 这些库本质上是**自带的便携浏览器**，每个 TUI 进程都在自己里面装一个。

---

## 四、Ink 拆开看：已会 vs 真正要学

承接上一节的图，把 Ink 这个 npm package 拆开：

| Ink 的组成 | 我已经熟练 | 全新认知点 |
|---|---|---|
| React core（hooks、reconciler、组件模型） | ✅ 5 年 | — |
| Yoga（Facebook 的 flexbox 引擎） | ✅ CSS flexbox 心智模型 | — |
| Terminal renderer（cell grid 管理、ANSI escape emit） | — | ⚠️ 真正要学 |
| Raw mode stdin（byte-level 事件流、按键解析） | — | ⚠️ 真正要学 |
| 终端能力探测（truecolor / 256 / unicode 宽度） | — | ⚠️ 真正要学 |

**学习预算分配** ≈ 80% 已熟练 / 20% 新认知。新东西全集中在"终端 cell grid + ANSI + raw mode"——也就是 PTY/TTY 那一层底座。这一层的知识可以**长期复用**：将来真要换栈到 Ratatui 或 Bubble Tea，底座知识平移 100%，换的只是上面的 React vs Elm vs immediate-mode。

**关键判断**：cell grid + ANSI 这一层是不可避免的学习成本——任何 TUI 栈都要学。但**只有 Ink 让我能在学这层底座的同时不重新学一遍 React**。其他栈都让我同时学两个新东西。

---

## 五、为什么不是 Python + Textual（路径决策）

三条候选路径：

### 路径 A — Python + Textual ⛔

**优点**：和 OpenHarness 同栈，单语言闭环。
**缺点**：
- Textual 是 **CSS-driven + reactive properties** 的系统，**像 React 但不是 React**
- 学习成本 = 终端 cell grid（同样要学） + Textual 的 reactive 系统（新东西）
- 对一个"Python harness 作者 + Web React 5 年"的人，这等于**同时学两个新东西**
- 业界 agent CLI 用 Textual 的几乎没有——Aider 走的是 prompt_toolkit + Rich，不是全屏 Textual

**判决**：⛔ 不选。同时学两个新东西的预算不划算。

### 路径 B — Node + Ink ✅

**优点**：
- Ink **literally is React**——5 年肌肉记忆直接转移
- 学习成本只剩 cell grid 底座那 20%
- 业界 80% agent CLI 用同一个栈：Claude Code / Gemini CLI / OpenCode / Cursor Agent 全部 Node + Ink，**学到的东西迁移率 100%**
- 组件市场成熟：`ink-text-input` / `ink-select-input` / `ink-spinner` / `ink-table` 等可重用
- 长期：Ink 的"React + 终端 renderer"模式如果将来有"agent UI kit"事实标准，大概率从这里长出来（上一篇 §十二 预测）

**缺点**：
- 不和 OpenHarness 进程共享。**但反过来想**，上一篇 §七 警告的"跨语言 + 自造协议"问题正是要 OpenHarness + Ink 跨语言。如果**做独立项目就根本不跨**——绕开了。

**判决**：✅ 选这条。

### 路径 C — Python stdout + Rich Live ✅（已 ship，但不是专家路径）

**优点**：OpenHarness 现状路径，Phase 15 已 ratify。
**判决**：✅ 保留——这是 OpenHarness 的内容渲染路径（D1 维度），**不是个人交互专家路径**（D4 维度）。两条路径并行，不互斥。

### 总结

- OpenHarness 走 C 维持现状（Python + Rich Live）
- 我个人下阶段走 B（Node + Ink 独立项目）
- A（Python + Textual）作为 alternative 留档，但**当前预算条件下不选**

---

## 六、行业地图（承接上一篇 §九-§十一，浓缩）

上一篇已经详细写过 L1/L2/L3 成熟度分层，这里只回锚结论：

| 层 | 成熟度 | 选 Ink 之后我能拿到什么 |
|---|---|---|
| **L1 TUI 框架** | 完全成熟 | Ink 本身，零成本 |
| **L2 基础 widget** | 基本就位 | `@inkjs/ui`、`ink-text-input`、`ink-select-input` 等组件库 |
| **L3 Agent UX**（流式 markdown / tool call / approval / `/` popup / `@` 补全） | **还在野** | **这一层是我要写的**——也是业界还没标准化的层 |

**直接参考的开源代码**：

- **OpenCode** (TS + Ink) — 最接近 Claude Code 的开源 clone，L3 参考样本
- **Crush** (Go + Bubble Tea) — Charm 家工艺最考究的 agent CLI，看 L3 怎么设计可借鉴
- **Aider** (Python + prompt_toolkit + Rich) — 走 line-based 而非全屏 TUI，反面参考（看"不全屏的极限在哪")
- **Gemini CLI**（TS + Ink，Apache 开源）— 工业级 Ink 应用，看真实工程怎么组织

**关键观察**：L3 还在野的窗口期就是我个人能切入的窗口期——L1/L2 选 Ink 拿到全行业最好的免费基础设施，L3 是我的发挥空间。

---

## 七、OpenHarness 不动 — boundary 明确

这一节是为了**防止下阶段 narrative 失控**——明确 OpenHarness 和新项目的边界。

**OpenHarness（Python，本仓）**：

- 继续按 4 步 phase loop 推进
- D1（Rich Live）+ D2（namespaced 触发）+ D3（CC plugin compat）已 ship，方向不变
- Phase 15 拒绝 Textual 的决策**仍然有效**
- 如果将来要在 OpenHarness 内做 D4 input affordance，最多走 prompt_toolkit（cooked → raw 但不全屏），**不引入 Textual / Ink**

**新项目（Node + Ink，独立 repo）**：

- 独立 repo（不在本仓内）
- 独立进程（不通过 RPC/IPC 和 OpenHarness 通信）
- 独立 phase loop（沿用本项目 CLAUDE.md 的方法学——boundary doc + 4 步循环 + retro + §六 wiring audit）
- 目标是 L3 Agent UX 复刻，不重新发明 LLM agent 引擎
- 后端可以直接调 OpenAI/Anthropic SDK——这是该项目的"agent 引擎"，不复用 OpenHarness 的 Python engine

**两个项目的关系**：

- 代码：不共享
- 进程：不共享
- 方法学：**共享**（这是 OpenHarness 23 天 17 phase 的最有价值产出之一）
- 心智：本仓的 `tui-vs-web-frontend-first-principles.md` + 本文是新项目的认知前置——所以这份文档**写在本仓**，而不是新项目开始时再写

---

## 八、Next step 而非 Now step

**不立即开工**。原因：

1. OpenHarness 还有 phase 要推进（M3 declarative sub-agent，Phase 20+）
2. 一个项目还在 mid-flight 时开第二条轨道，会**稀释方法学的纪律性**——4 步 phase loop 的载体是连续的，跨项目并行 = 跨 phase 并行 = 失去节奏
3. 沉淀想法本身有价值：这份文档 + 上一篇就是沉淀——**开工之前先把 boundary 和 invariant 想清楚**，等同于新项目的 Phase 0

**Next step 真正启动的触发条件**：

- OpenHarness 主线推进到一个明显的"自然停顿点"（Phase 20+ M3 close、或 M3 retro 后判断 M4 不紧急）
- 上一篇 + 这一篇的认知至少经过 1-2 周沉淀（避免热血决策）
- 新项目有名字（现在没有，是 placeholder）

**沉淀期可以做的事**（不算开工）：

- 读 OpenCode / Gemini CLI 的源码，建立 L3 Agent UX 的样本库
- 跑 10 行 raw mode demo 实测 cell grid + ANSI 那 20% 新认知点
- 读 Ink 文档建立 component model 到终端的具体映射

这些**不构成 phase loop 的 phase**——是沉淀期的素材采集，不写 boundary doc、不写 retro。

---

## 九、一句话

> 上一篇我意识到 Web 给前端的不是 HTTP 是浏览器。这一篇我决定下一阶段去自己 ship 一个浏览器——但是用我已经会 5 年的 React 装在里面。Ink 是 (我没见过的终端 renderer) + (我熟到反射的 React)，所以学习预算精确地落在 20% 的新认知点上，**这是我能找到的最高 ROI 的 CLI 交互专家路径**。
