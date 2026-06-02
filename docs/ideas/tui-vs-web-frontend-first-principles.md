# 终端不是浏览器：从想给 Python harness 加 Ink TUI 说起

> 写于 2026-06-02 · 中文版
>
> 配套读物：
> - 项目方法论：[CLAUDE.md](../../CLAUDE.md)
> - 关联思考：[docs/ideas/why-protocol-standardization.md](./why-protocol-standardization.md)
>
> 这篇不是讨论"OpenHarness 要不要做 TUI"（那是决策的事），
> 而是讨论 **"为什么 web 世界的'前后端分离'在终端世界没有等价物"** ——
> 一个我做了多年 React 开发也没意识到的盲点。

---

## 一、问题是怎么来的

OpenHarness 写到 16 个 phase，UI 一直还是 `print()` 到 stdout —— 谈不上 TUI、谈不上交互式渲染。我想给它加一个像 Claude Code 那样的全屏 TUI：组件化、reactive、有键绑定。

第一反应是 Ink。Claude Code 用 Ink，Gemini CLI 用 Ink，React 的心智模型熟悉，npm 生态完备。

第二反应：**OpenHarness 是 Python，Ink 是 Node**。但我以为这没什么 —— 我前后端不同语言写了五六年，React + Java、React + Go、React + Node，都很顺。

然后我撞墙了。

把这件事讲清楚之前，得先回答一个我从来没意识到的问题：**Web 世界里"前端 X 后端 Y"那么自然，背后到底是什么在支撑？**

---

## 二、浏览器替你做的事，远超你意识到的

写 React + Java 时，我脑子里的图是这样：

```
[用户电脑]                       [你的服务器]
┌─────────────────┐
│  你的 React 代码  │  HTTP   ┌─────────────┐
│   (浏览器里)      │ ◄────► │  Java 后端  │
└─────────────────┘         └─────────────┘
```

这张图骗了我。**真实的图是这样**：

```
[用户电脑]                                  [你的服务器]
┌────────────────────────────────────┐
│  浏览器 (用户已经装好)              │
│  ┌──────────────────────────┐     │
│  │ 你的 React 代码 (JS)      │     │       ┌─────────────┐
│  └──────────────────────────┘     │  HTTP  │  Java 后端  │
│   + DOM 渲染引擎                    │ ◄────► │             │
│   + CSS 引擎                       │       └─────────────┘
│   + JS 引擎 (V8)                   │
│   + 事件循环 (主动派发 click)        │
│   + HTTP 客户端 (fetch)             │
│   + 网络栈 / TLS / 缓存             │
│   + 安全模型 (CORS / CSP / SOP)     │
└────────────────────────────────────┘
```

浏览器是一个**完整的图形化运行时**，预装在每个用户的机器上。它免费给你：

- 一个**渲染引擎**（DOM + CSS + 像素）
- 一个**事件循环**（主动调你的事件 handler）
- 一个**标准化的网络协议**（HTTP，全行业三十年共识）
- 一个**安全沙箱**（CORS / CSP / Same-Origin）

**你的 React 代码不是一个独立程序，是浏览器的"客人"**。浏览器替你做掉 90% 的事：你 `return` JSX，它翻译成 DOM 操作；用户 click，它派发到你的 handler；你 `fetch`，它走 TLS 给你完成请求。

**这才是"前后端分离"真正的基础**。HTTP 是标准协议这件事是表象；**浏览器是公共基础设施**才是因。任何后端语言都能写 web，**是因为浏览器替前端做完了所有事**。前后端之间那根 HTTP 线，是浏览器送给你的标准化"配送链路"。

我做 React 五年都没意识到这件事。我以为是 HTTP 的功劳。

---

## 三、终端不是浏览器的对应物

终端是**哑设备**：

- 显示字符
- 接收按键
- 解析一些控制字符（ANSI escape code，让光标跳到第 3 行 5 列、让这段字变红）

**没有 DOM。没有 CSS。没有 JS 引擎。没有事件循环。没有 HTTP。没有任何"应用运行时"。**

它不会"调用你的代码"，它只会"显示你写出去的字节"。

这意味着：**所有 TUI 程序，都是它自己在扮演"浏览器"的角色**。Ink、Textual、Ratatui —— 这些库不是"在终端里运行的应用"，是"自带渲染引擎、自带布局算法、自带事件循环、把终端当哑设备使用的独立程序"。

把 web 的图改成 TUI 的图，是这样：

```
[用户电脑]
┌────────────────────────────────────────┐
│  终端 (哑设备)                          │
│       ▲ 字节流                         │
│       │                                │
│  ┌────┴────────────────────────────┐  │
│  │  你的 TUI 程序 (一个进程)        │  │
│  │   + Ink / Textual / Ratatui      │  │
│  │   + 渲染引擎                     │  │
│  │   + 布局算法 (Yoga / flexbox)    │  │
│  │   + 事件循环                     │  │
│  │   + 你的业务代码                  │  │
│  │   + LLM / 工具 / agent 引擎       │  │
│  └─────────────────────────────────┘  │
│       一切装在你这一个进程里             │
└────────────────────────────────────────┘
```

注意右下角"agent 引擎"。在 web 世界里，这是**后端**，跑在另一台机器上。在 TUI 世界里，它**没地方可去** —— 因为没有"浏览器"那个公共运行时来扮演前端，**你的 TUI 进程就是浏览器和前端代码的合体**。你的业务逻辑要么和它在同一个进程里，要么通过你自己设计的协议跟它通信。

---

## 四、所以业界全部走"前后端同语言"

我调研了四家做编程 agent CLI 的产品：

| 产品 | 前端 (TUI) | 后端 (agent 引擎) |
|---|---|---|
| Claude Code | TypeScript + Ink | TypeScript（同一个 Bun 进程） |
| Gemini CLI | TypeScript + Ink | TypeScript（同一个 Node 进程） |
| Codex CLI | Rust + Ratatui | Rust（同一个 Rust 进程） |
| Cursor Agent | bundled Node + ?(闭源) | bundled Node（同进程） |

**没有一家做跨语言、跨进程**。Gemini CLI 看上去拆了 `packages/cli` 和 `packages/core`，那只是 npm workspace 的源码组织，运行时仍然是同一个 Node 进程的函数调用 —— 不是跨进程 RPC，更不是跨语言。

更激进的例子是 **Codex**。它 2025-04 开源时是 TypeScript + Ink；到了 2025 年中决定**整体重写成 Rust + Ratatui**，理由公开发表在 GitHub Discussion #1174：Node ≥22 依赖太重、长跑 agent 进程的 GC 暂停影响响应、想要单文件零依赖分发、想要原生 sandbox bindings。

**关键看 Codex 怎么重写**：不是把 TS+Ink 留下来给 Rust 后端当前端（那才是 web 风格的"前后端解耦"）—— 是**把 Ink 整个扔了换 Ratatui**。连 OpenAI 都没把"另一种语言做 agent 引擎 + Ink 做前端"当作中间产物。

为什么？因为在 TUI 世界里，**做"前后端拆分"等于自己造浏览器**。你要：

1. 定义自己版本的 HTTP（协议、序列化、错误恢复）
2. ship 两个运行时给用户（前端 Node + 后端 Python，用户得装齐）
3. 处理两个运行时各自的故障模式（前端崩了怎么重启后端？后端死了前端怎么超时？）

业界小团队的判断是：**这个代价没人想付**。既然反正要 ship 一个 runtime，那就别 ship 两个。

---

## 五、Claude Code 的本质 = 终端版 Electron

有一个类比能把这件事讲清楚。

**Electron 是什么？** 是"把 Chromium + Node 打包给你当桌面运行时"。写一个 Electron 应用，等于"假装浏览器不存在、自己 ship 一个浏览器给用户"。VSCode、Slack、Notion 都是 Electron —— 不是因为不能用浏览器做，是因为想要**不被浏览器限制约束**（窗口、文件系统、操作系统集成）。代价是体积大、内存重。

**Claude Code 就是 Electron for Terminal**：

- 它 ship 了一个 Bun 运行时给你（相当于 Chromium 的位置）
- 里面跑 Ink 渲染（相当于 React + DOM 的位置）
- agent 引擎装在同一个 Bun 进程里（相当于 Electron 里的 Node main process）

Codex 是另一种形式的 "Electron for Terminal"：用 Rust 二进制替代 Bun，用 Ratatui 替代 Ink。**只是渲染引擎和运行时的具体选择不同，"自带运行时"这件事是必须的**。

---

## 六、LSP 是反例，但它解释了门槛在哪

LSP（Language Server Protocol）是这个故事的反例。

VSCode 是 TypeScript（Electron 应用），language server 可以是任何语言：rust-analyzer 是 Rust，pyright 是 Python，gopls 是 Go。通信走 **JSON-RPC over stdio**。这**正好就是"前端一个 runtime + 后端另一个 runtime + 协议桥接"**。

它能 work，前提是三件事都同时成立：

1. **LSP 是标准协议**。微软花了好几年定义、推广、让所有编辑器 + 所有 language server 厂商都实现它
2. **VSCode 把完整 IPC 框架做好了**：连接、生命周期、错误恢复、并发多 server 协调
3. **市场有强需求**：没人想用 TypeScript 重写 Rust 的类型检查器

Coding agent CLI 不在这个状态。**没有 "Agent Server Protocol" 标准**（没人定义过）；各家产品都是小团队，没精力做协议设计 + 生态推广；而且各家的 agent 引擎一开始就是 TS 写的，**没有跨语言诉求**。

如果今天想做"Python agent 引擎 + Ink TUI 前端"，本质是 **"在没有 LSP 的世界里自己造 LSP"** —— 自己设计协议、自己实现两侧的生命周期、自己定义错误恢复。

不是不能做。**是要明白这条路上没有任何现成基础设施帮你**。

---

## 七、Web 的"前后端"其实是一种基础设施红利

回到最开始的盲点。

我写了五年 React 项目，以为"前端用 X、后端用 Y"是一种**架构能力**、一种**抽象成熟度**。

写完这篇我意识到：**那是一种基础设施红利**。

| 真相 | 我的直觉 |
|---|---|
| **浏览器**是 web 世界的公共运行时 | 我以为是 HTTP 给的解耦 |
| 前端代码是浏览器的客人 | 我以为前端是"我自己的程序" |
| 跨语言 = 浏览器 ship 一份，后端 ship 一份 | 我以为是"前后端各 ship 一份"，前端那份没成本 |
| 终端没有浏览器的等价物 | 我以为终端和浏览器都是"UI 容器" |

所有 web 程序员从入行第一天起就在用浏览器，**太熟悉到不会去注意它在干什么**。等想做 TUI 才发现：那个默默替我们做完一切的角色，原来不存在于所有 UI 平台。

对 OpenHarness 自己的决策，意味着两条优先级更高的路：**Python 端的 Textual**（单语言、和 async engine 直接对接）或者**保留 stdout 模式 + 用 `rich.Live` 增强**（不开全屏 TUI，但渲染体验上来）。"Python 引擎 + Ink 前端 + 自造协议"这条路不是技术上做不到，而是**业界没有一个先例验证过它值得做**。

---

## 八、一句话

> Web 给所有前端程序员的免费红利，不是 HTTP，是**浏览器**。React 项目里"前端"是寄生在浏览器里的轻量层；TUI 项目里"前端"自己就是一个完整运行时——这一个差别，决定了 web 能跨语言自由组合而 TUI 不能。

---

# 续：业界 TUI 生态地图

> 写完上面那段第一性原理之后，我又想到一个具体的问题：
> 既然 TUI 没有 React 那种"全行业统一框架"，那今天业界**到底是"各家自己写一切"，还是"已经有共识层可以直接用"？**
>
> 调研下来发现，这件事要分**三层**看，三层的成熟度差得很远。

---

## 九、三层结构：成熟度从下到上递减

```
┌──────────────────────────────────────────────────────┐
│  L3  Agent UX 层 (流式 markdown / tool call /         │
│      approval modal / slash 命令面板 / @file 补全)    │ ← 还在野
│                                                      │
│  L2  基础 widget 层 (input / spinner / viewport /     │
│      markdown 渲染 / 语法高亮 / 滚动列表)             │ ← 基本成熟
│                                                      │
│  L1  TUI 框架层 (Ink / Ratatui / Bubble Tea /         │
│      Textual)                                        │ ← 完全成熟
└──────────────────────────────────────────────────────┘
```

### L1 — TUI 框架层：完全成熟

跟"前端选 React/Vue/Svelte"是同一个量级的状态：

- Ink 在 npm 上每周 200 万+ 下载，已经用了 6 年
- Ratatui（前身 tui-rs）是 Rust 生态最广泛使用的 TUI 库
- Bubble Tea + Charmbracelet 整套工具链异常完整
- Textual 是 Textualize.io 的旗舰项目，甚至支持把同一份代码渲染到 web

**这一层没人会重新发明**。Codex 不重写 Ratatui，Gemini 不重写 Ink。

### L2 — 基础 widget 层：基本就位

"做 TUI 的标配 widget" 各语言都有成熟开源库：

| 类型 | TS/Ink | Rust/Ratatui | Go/BubbleTea | Python/Textual |
|---|---|---|---|---|
| UI 组件 | `@inkjs/ui` | `ratatui-widgets` | `bubbles` | 内置丰富 |
| 样式 | (Ink 内置) | (Ratatui Style) | `lipgloss` | CSS-like |
| Markdown | `marked-terminal` | `termimad` | `glow` | 内置 |
| 语法高亮 | `cli-highlight` | `syntect` | `chroma` | 内置 |

这一层基本"装上就能用"。

### L3 — Agent UX 层：还在野生，每家自己写

**真正还没共识的是"编程 agent CLI 特有的 UX 组件"**：

- 流式 markdown 渲染（token 一边到、屏幕一边长）
- Tool call 显示（可折叠、带 diff、参数高亮）
- Approval modal（暂停 LLM 等用户确认才继续）
- Slash command 面板（`/help` `/clear` `/agents`…）
- 对话历史 + 回滚到某条消息重新生成
- 文件引用补全（`@file.ts` 智能高亮）
- 多行输入 + 历史 + Plan mode 切换
- 状态栏（model / tokens 计数 / permission mode）

**这些 UX 模式几乎完全没有公共开源库**。Claude Code / Cursor / Codex / Gemini 各家写各家的。

---

## 十、为什么 L3 还没标准化

三个原因：

1. **太年轻**。这个产品形态从 Cursor → ChatGPT Code Interpreter → Claude Code 真正成熟，**也就 2 年多**。Web 前端从 jQuery 到 React 标准化花了 10+ 年
2. **UX 还在试错**。"tool call 该长什么样"、"approval flow 怎么设计"、"subagent spawn 怎么显示"——**这些根本性问题根本还没有标准答案**，每家都在自己探
3. **闭源占大头**。Claude Code / Cursor 用户体验最好的部分都不开源，Gemini CLI 虽然 Apache 但工程仍在快速变；社区抄不到具体实现

---

## 十一、能直接看到代码的开源参考

最近 1 年涌现了几个 "clone Claude Code 思路、把它做开源"的项目。具体实现细节可能在变，但项目本身存在：

| 项目 | 栈 | 定位 |
|---|---|---|
| **OpenCode** (`opencode-ai/opencode`) | TS | 最接近 Claude Code UX 的开源 clone |
| **Crush** (`charmbracelet/crush`) | Go + Bubble Tea | Charmbracelet 自家做的 agent CLI，工艺最考究 |
| **Aider** (`aider-chat/aider`) | Python + prompt_toolkit | 编程 agent CLI 里最老牌；走 line-based + rich，不是全屏 TUI |
| **Goose** (Block) | Rust | 类 Claude Code 形态的开源版 |
| **Plandex** | Go | TUI + plan-first 流程 |

**这些项目的代码就是看"行业方案"最快的路径**。Claude Code 闭源，但 OpenCode 是它的镜像；想看 Bubble Tea 怎么搞 agent UI 看 Crush；想看 Python 怎么搞 agent CLI 看 Aider。

---

## 十二、未来 1-2 年的趋势判断

- **某语言里会出现事实标准的"agent UI kit"**。Ink 概率最大，因为 React 生态最善于做 component library —— "用 5 个 component 就能 clone Claude Code" 这种包很可能 1 年内出现
- **跨语言协议大概率不会有**。没人有动机推动 —— 商业产品都是闭源单语言，没有 LSP 那种多方协作的强 motivation
- **开源 clone 会越来越完整**。OpenCode 这类项目 1-2 年内 UX 可能追到 Claude Code 80%

---

## 十三、一句话

> 在没有"浏览器"那种统一运行时的世界里，**生态的标准化只能从底向上慢慢长**。TUI 框架层和基础 widget 层已经成熟得跟 React 生态一个量级，agent UX 层还在野生 —— 别等"行业自顶向下的标准"，看准底座 + 主动决定 L3 是自己造还是抄已有 clone。
