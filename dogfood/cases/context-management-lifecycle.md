# Context Management 深度链路

状态：DPG-006～014 已于 2026-08-12 完成一轮手动闭环。这个 suite 不把
`/compact`、`--resume`、Skills、Plugins 和 Agent 当成孤立功能；它沿着一条真实
session 观察 context 的形成、增长、缩减、持久化、恢复和隔离。

## 一、实验问题

这组 dogfood 回答六个问题：

1. 每一轮真正给模型的静态 context 有什么：Tools、Skills、project instructions、
   Memory、Plan/Goal 临时 section？
2. Tool call 与 Tool Result 以什么形态进入 conversation？错误和长输出如何回灌？
3. 单条 Tool Result、整段 conversation 和 provider Prompt Too Long 分别由哪一层处理？
4. Skill 正文如何按需进入 context？Slash Skill 与模型主动 `LoadSkill` 是否等价？
5. Snapshot 保存了什么；`oh --resume` 后哪些状态恢复，哪些从当前配置重新组装？
6. Plugin 与 Agent 如何改变或隔离 context？

证据优先级仍然是：artifact 和外部状态高于 transcript，transcript 高于模型自述。

## 二、观察工具

`dogfood.context_inspector` 不启动 Agent、不调用模型，也不执行 plugin fan-out。每次
capture 会生成：

```text
.dogfood/artifacts/<run-id>/context/
├── <label>.json   # 完整结构化证据
└── <label>.txt    # 人类可快速扫描的摘要
```

它收集：

- 当前安全配置摘要、context window、compact 阈值和 Tool 原生输出上限；
- project instructions、Commands、Skills 与已安装 Plugins；
- Project Memory index 与 conversation snapshot；
- 当前 snapshot 的 role/block/tool-use/tool-result 计数；
- snapshot 中真实保存的 system-prompt headings、Tool catalog 与 Skill catalog；
- active Goal、parked permission、synthetic Skill envelope 和 compact markers；
- Tool Result 中的截断 marker 与 assistant 对这些 marker 的文字引用分别计数，避免把
  模型复述误算成第二次真实截断。

Inspector 报告的 message tokens 与 REPL 底栏使用同一估算，只包含 messages；不包含
system prompt、结构化 Tool schemas、provider framing 和输出预留。这一边界会明确写在
每份 JSON 中，不能把底栏百分比解释成完整 API request 的精确占用。

## 三、准备一次全新实验

下面命令可以直接从仓库根目录执行。重跑时把 `01` 改成新的编号，避免读取旧项目路径
对应的 snapshot 和 input history。

```bash
uv run python -m dogfood.context_inspector prepare \
  --target .dogfood/work/context-management-20260812-01

uv run pytest \
  .dogfood/work/context-management-20260812-01/test_context_probe.py \
  -q --no-cov

uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label 00-before-session
```

预期 fixture 验证为 `2 passed`，首次 artifact 显示 `snapshot: missing`。生成的
`large_context.txt` 超过 100KB，包含稳定的 HEAD、MIDDLE、TAIL 三个 anchor。

## 四、启动真实 REPL

在终端 A 进入 fixture。`--project` 让 uv 使用仓库环境；`--env-file` 显式加载仓库
`.env`，同时让 OpenHarness 的 cwd 保持在 fixture：

```bash
cd .dogfood/work/context-management-20260812-01
uv run --project ../../.. --env-file ../../../.env oh --auto --sandbox
```

后续 capture 都在终端 B 的仓库根目录执行。通用形态是：

```bash
uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label <阶段名>
```

## DPG-006：静态 Context 与 Plan capability view

在 REPL 输入：

```text
/
/skills
```

菜单应同时显示 built-ins、`/context-check` command 和 `/context-probe` skill；
`/skills` 至少包含 `context-probe`。

再输入：

```text
不要调用工具。只根据这一轮已经提供给你的 context，分别列出你能看到的 Tool 名称、Skill 名称、project instruction 的来源文件，以及当前是否有 active Goal。
```

完成后 capture：

```bash
uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label 01-default-context
```

然后在 REPL 输入：

```text
/plan 不调用工具。列出这一轮实际可见的 Tool 名称，并指出和刚才 Default 相比消失了哪些能力。
```

Plan menu 出现后，先 capture：

```bash
uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label 02-plan-context
```

再回到 REPL 输入 `3` 丢弃 Plan。

### 通过标准

- Default snapshot 的 stored Tools 与实际 session 一致，不只显示静态六工具列表。
- Plan 中不出现 Write、Edit、Bash、Agent；只读且非 delegated 的 Tools 仍可见。
- `02-plan-context.json` 的 system prompt 包含 `## Plan mode`。
- Plan 没有修改 fixture。

## DPG-007：长 Tool Result 的进入、截断与恢复

在 Default 输入 project command：

```text
/context-check
```

这条 command 要求模型先加载 `context-probe` Skill，再完整 Read 大文件；如果中段被
截断，应该用精确 Grep 恢复，而不是声称 MIDDLE 不存在。

完成后 capture `03-long-tool-result`。这一段验证模型的恢复决策，不负责制造足够大的单条
Tool Result；首轮实测为 `LoadSkill=1`、`Read=3`、`Grep=2`，三个 anchor 全部找回。

然后分别制造两种确定性截断：

```text
运行 python3 context_probe.py large-output 一次。只根据这一次 Bash Tool Result，报告截断 marker 以及 HEAD、MIDDLE、TAIL 的可见性；不要使用其他工具。
```

这一步验证 Bash 原生字符上限，真实 Tool Result 应出现
`[truncated <n> chars]`。再输入：

```text
完整 Read large_context.txt 一次。只根据这一次 Read Tool Result，报告截断 marker 以及 HEAD、MIDDLE、TAIL 的可见性；不要使用其他工具。
```

这一步验证通用 PostToolUse token 上限，真实 Tool Result 应出现
`[truncated <n> tokens]`。完成后分别 capture：

```bash
uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label 03b-native-char-truncation

uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label 03c-post-tool-token-truncation
```

### 通过标准

- 至少出现一次模型主动调用的 `LoadSkill`、一次 Read。
- Bash 原生字符截断和 PostToolUse token 截断各由一个真实 Tool Result 证明；assistant
  的复述只进入 `assistant_marker_mentions`，不增加 `tool_result_markers`。
- 两类截断均保留 HEAD 与 TAIL。
- 如果 MIDDLE 不在 Read 结果中，模型使用 Grep 找回
  `MIDDLE_ANCHOR=context-middle-0812`。
- 最终三个 anchor 全部正确，并分别说明证据来源。
- Artifact 中对应的 `tool_result_markers.native_char_truncation` 与
  `tool_result_markers.post_tool_token_truncation` 大于零；没有修改文件。

这里同时观察两种上限：Read 拒绝的是大于 10MiB 的文件；本 fixture 小于 10MiB，
因此 Read 能执行。Bash 先按字符上限保留头尾；通用 PostToolUse hook 再按默认 10k
tokens 缩短任何过长 Tool Result。这是两个层次，不能用同一个 marker 总数代替。

## DPG-008：错误 Tool Result 的反馈

输入：

```text
运行 python3 context_probe.py fail 一次。根据实际 Tool Result 解释失败，不要重跑相同命令，也不要修改文件。
```

然后 capture `04-error-tool-result`。

### 通过标准

- Bash 只调用一次。
- Transcript 显示 Bash error，输出包含
  `PROBE_ERROR=expected-context-failure`。
- 模型不把失败说成成功，也不重复原命令。
- Artifact 的 `error_tool_result_count` 增加。

注意：Tool 的内部 metadata（例如 Bash `exit_code`、`duration_ms`）目前不在
`ToolResultBlock.content` 中。这个 case 只允许模型依据真正回灌的内容和 error 状态作答，
不能期待它引用没有进入 model context 的结构化 metadata。

## DPG-009：Slash Skill 与模型主动 LoadSkill

输入：

```text
/context-probe 只复述这项 Skill 的第 2 条规则，并说明这次 Skill 是由用户显式选择，不是你自行选择的。
```

然后 capture `05-slash-skill`。

### 通过标准

- 模型主动加载路径在终端显示 `[LoadSkill]`；Slash Skill 路径不执行真实
  `LoadSkillTool`。
- Slash 路径在 snapshot 中形成 `synth_` ID 的 LoadSkill tool-use/tool-result envelope。
- `synthetic_skill_load_count` 至少为 1。
- Skill body 在后续 conversation 中可用，没有重复附加 args。
- Skill 来源以 snapshot 中 `synth_` envelope 为权威证据；不把模型关于“用户粘贴正文”
  或“模型主动加载”的自述作为 provenance 证据。

## DPG-010：手动 Compact 与持久化接缝

先输入一条不调用工具的稳定事实：

```text
请记住：本次 session 代号是 CONTEXT-LIFECYCLE-0812，精确验证命令是 uv run pytest test_context_probe.py -q --no-cov，不允许修改仓库生产代码。只确认收到，不调用工具。
```

再输入：

```text
/compact
```

预期显示：

```text
(compacted: <before> → <after> tokens)
```

如果压缩没有发生，REPL 必须给出单一、可行动的原因，不能把“历史太短”和摘要调用失败
合并成同一条提示：

```text
(/compact: nothing to compact; history has <n> message(s), all within the preserved tail of 2)
(/compact failed: summarization timed out after <seconds>s)
(/compact failed: summarization failed: <provider error type/status>: <safe summary>)
(/compact failed: summarizer returned no usable summary)
```

失败时 history 与 snapshot 必须保持原样。先保留这条诊断证据并修复对应 runtime 问题；
不要继续把 Resume 测成 Compact 成功后的恢复。

Full Compact 的默认摘要预算是 120 秒，只约束这次长 conversation 摘要；共享
`summarize()` 的其他短 secondary passes 仍使用各自预算。如需实验性覆盖，可设置
`OPENHARNESS_COMPACT__FULL_COMPACT_TIMEOUT_S`，但不得为了掩盖稳定失败而无限提高。

此时不要输入下一条 conversation，立即在终端 B capture
`06-compact-command-only`。然后回到 REPL 输入：

```text
不要调用任何工具，只根据 compact 后的 conversation，列出 session 代号、精确验证命令、禁止动作、三个 anchor，以及尚未完成的工作。
```

再 capture `07-compact-persisted`。

### 通过标准

- Compact 后底栏 token 数下降。
- 关键事实、错误、Skill 规则、三个 anchor 和 pending work 保留。
- `07-compact-persisted.json` 出现 compact boundary/full summary markers。
- 模型没有为找回事实调用 Read、Grep 或 Memory。

### 特别观察

`/compact` 先修改当前进程内存，命令本身是否立即改写 snapshot 是本 case 的重要问题。
比较 `05-slash-skill.json`、`06-compact-command-only.json` 和
`07-compact-persisted.json`：

- 如果 06 仍等于 compact 前 snapshot，而 07 才出现 summary，说明必须等下一次正常
  assistant turn 才持久化 compact 结果。
- 这是 context lifecycle 接缝，不应被“模型仍答对了”掩盖。

## DPG-011：跨进程 Resume

输入 `/exit`，capture `08-before-resume`。然后从 fixture 目录重新启动：

```bash
uv run --project ../../.. --env-file ../../../.env oh --resume --auto --sandbox
```

看到 `(resumed: ... messages ...)` 后输入：

```text
不要调用工具或 Memory。只根据 resumed conversation，列出 session 代号、三个 anchor、最后一次错误，以及 compact 前尚未完成的工作。
```

完成后 capture `09-after-resume`。

### 通过标准

- Banner 的 message count 与 `08-before-resume.json` 一致。
- Resume 后保留 compact summary 和 recent tail。
- Resume 使用当前重新组装的 system prompt、Tools 和 Skills；snapshot 只负责恢复 typed
  messages、permission state 和 Goal sentinel，不冻结旧 runtime registry。

## DPG-012：Clear 后立即退出再 Resume

在 resumed session 输入：

```text
/clear
/exit
```

不要插入普通 assistant turn。随后再次运行 `oh --resume --auto --sandbox`。

### 期望契约

- Resume 不应恢复 clear 前的 conversation。
- 不应知道 `CONTEXT-LIFECYCLE-0812`，也不应恢复旧 active Goal。
- `current.json` 应被原子覆写为空 messages；Project Memory 保持独立，不随 `/clear` 删除。
- conversation-bound parked permission 与尚未消费的 approve/deny transition 被清除；既有权限
  ledger 不因清理对话而重置。

### 失败证据

如果 banner 恢复了旧 message count，或者模型仍能在不调用工具/Memory 的情况下引用
旧 conversation，保留 transcript 并 capture `10-clear-immediate-resume-failed`。这说明
`/clear` 只清了进程内 history，没有立即持久化清除结果。

这是故意放进 dogfood 的组合 case；它曾稳定复现“`/clear` 只清内存、`--resume` 复活
8 条旧消息”的缺陷。当前回归契约要求 `/clear` 立即持久化空 conversation；手动 dogfood
仍需再次走通，不能只依赖 `/clear` 与 snapshot 的单项测试。

## 已完成结果：2026-08-12

本轮使用 `20260812-context-01`，实际结论如下：

| Case | 结果 | 关键证据 |
|---|---|---|
| DPG-006 | 通过 | Default 保存 9 个 Tools；Plan 只保存 Read、Grep、WebSearch、WebFetch、LoadSkill；Plan 丢弃后回到 Default |
| DPG-007 | 通过 | 主动 `LoadSkill=1`、`Read=3`、`Grep=2`；Bash 字符截断与 PostToolUse token 截断均保留头尾，Grep 找回 MIDDLE |
| DPG-008 | 通过 | Bash 只调用一次；error Tool Result 保存 `PROBE_ERROR=expected-context-failure` |
| DPG-009 | 结构通过 | Snapshot 有 `synth_` Slash Skill envelope；模型对 provenance 的自述不准确，因此只采信结构证据 |
| DPG-010 | 机制通过，质量未完全通过 | 首次 25 秒超时被明确诊断；预算改为 120 秒后 `21880 → 2249 tokens`；下一次普通 turn 才把 compact 结果写入 snapshot |
| DPG-011 | 机制通过 | Resume 恢复 compact summary 与 recent tail，也忠实恢复了摘要中的质量缺口 |
| DPG-012 | 修复后通过 | `/clear` 后 snapshot 为 `0 messages`；在正确 fixture cwd 执行 `--resume` 显示 `resumed: 0 messages`；Project Memory 保持存在 |
| DPG-013 | 通过 | Plugin enabled 后 stored catalog 新增 4 个 `credit-report-reviewer__*` Skills；Tools 仍为标准 9 个，Commands 与 MCP surface 未增加 |
| DPG-014 | 通过 | 父 snapshot 只有 `Agent=1`、一个成功 Tool Result；子 Agent 内部使用的 Read 与内部消息没有展开进父 conversation |

DPG-010 的摘要质量留下三个独立、可复跑的模型决策缺口：

1. `Current Work` 停留在已解决的“等待用户输入”，没有反映最新状态；
2. 把 synthetic Slash Skill envelope 误归因为用户粘贴 Skill 正文；
3. 把较早的 Read offset error 写成最后错误，遗漏后发生的
   `PROBE_ERROR=expected-context-failure`。

它们已经进入 `memory_compact` 的 MC7～MC9 candidate cases。这里只记录 observed
behavior，不把尚未手动 live/record 的 candidate 写成 ratified。

整理这些 case 时还发现旧 `memory_compact` evaluator 曾把 preserved recent tail 拼进
`summary_text`，导致 MC2、MC4、MC5 各有一个事实无需经过摘要也会得分。该提取逻辑已
改为只读取生成摘要；三条旧 case 的事实已移入 older 区并降为 candidate。可信 replay
baseline 因此从名义 6/6 收缩为真实 3/3，等待六条 candidate 手动重新 ratify。

## DPG-013：Plugin 对 Context 的影响

先在仓库根目录确认当前已安装 Plugin：

```bash
uv run oh inspect plugins list
```

只有存在可用 Plugin 时才执行本 case。新开一个不带 `--resume` 的 fixture session：

```bash
uv run --project ../../.. --env-file ../../../.env oh --enable-plugins --auto --sandbox
```

输入 `/skills`，再输入：

```text
不要调用工具。列出带 plugin namespace 的 Skills，以及 Plugin 是否还带来了新的 Tool、Command 或 MCP surface；只报告这一轮实际看见的内容。
```

当前安装状态下，预期新增四个 `credit-report-reviewer__*` Skill：

```text
credit-report-reviewer__apply-credit-rules
credit-report-reviewer__cross-verify-application
credit-report-reviewer__draft-credit-finding
credit-report-reviewer__parse-credit-report
```

`credit-bureau-connectors` 当前为 0 Skills / 0 MCP servers，不应凭插件描述推断出新 Tool。
终端 B capture 时也要显式传相同的 plugin 配置，否则 inspector 自己的 Settings 会显示
`installed-only`，虽然 snapshot stored catalog 仍能证明 REPL 实际加载结果：

```bash
OPENHARNESS_ENABLE_PLUGINS=true \
uv run python -m dogfood.context_inspector capture \
  --cwd .dogfood/work/context-management-20260812-01 \
  --run-id 20260812-context-01 \
  --label 11-plugin-enabled
```

### 通过标准

- Plugin disabled 的 artifact 只把它列为 `installed-only`。
- Plugin enabled 后，真正 fan-out 成功的 Skills/Tools 体现在 snapshot 的 stored catalog；
  不以 `inspect plugins list` 的“已安装”状态代替“已加载”证据。
- Plugin 不是单独塞进 context 的一段正文；它只能通过 Commands、Skills、Bundles、
  Hooks、MCP servers 五种 surface 改变请求或执行链。

### 2026-08-12 实际结果

- `credit-bureau-connectors(loaded)`，但当前 manifest 为 0 Skills / 0 MCP servers；
- `credit-report-reviewer(loaded)`，stored Skills 精确新增预期的 4 个 namespaced entries；
- stored Tools 仍是 Read、Write、Edit、Bash、Grep、Agent、WebSearch、WebFetch、LoadSkill；
- discovery Commands 仍只有 fixture 的 `context-check`，没有 plugin MCP surface；
- artifact 为 `11-plugin-enabled.json`，因此 DPG-013 通过。

## DPG-014：Agent Context 隔离

默认 `Agent` 使用与父级相同的 Agent Loop，并继承父级可选 `max_turns` 熔断器；父级默认
为 `None` 时，子 Agent 也没有隐藏的 20-turn 上限。只有程序化构造专用 Agent variant
时才覆盖这个值。

新开普通 session，输入：

```text
必须调用一次 Agent 工具。让子 Agent 只读取 context_facts.md 并返回 session 代号、精确验证命令和它实际使用的 Tool；主 Agent 收到结果后原样报告，不要自己再读文件。
```

Capture `12-agent-isolation`。

### 通过标准

- 父 conversation 只增加一个 Agent tool-use/tool-result pair。
- 子 Agent 内部的 Read、消息和推理过程不展开进父 snapshot。
- 父 context 得到的是子 Agent 最终文本这个单一 Tool Result。
- 子 Agent 继承当前 system prompt、Tools、权限、cwd 与 Skills，但不继承父 conversation
  messages。
- 如果子 Agent 中断，父 Tool Result 是明确 error，而不是父 loop 崩溃。

### 2026-08-12 实际结果

- 顶层 transcript 只显示一次 `[Agent]`；
- 子 Agent 返回 `CONTEXT-LIFECYCLE-0812`、精确 pytest 命令和实际使用的 `Read`；
- `12-agent-isolation.json` 保存 4 条父消息、1 个 Agent tool-use、1 个成功
  tool-result，`error_tool_result_count=0`；
- 父 snapshot 的 `tool_uses` 精确为 `{"Agent": 1}`，没有 Read，证明子 Agent 的
  Read 与内部 conversation 没有泄漏进父 context；
- active Goal 为空，parked permission 为 false，因此 DPG-014 通过。

## DPG-015：Permission continuation 与同步审批

这条 case 验证权限审批属于 Harness control plane，而不是一条新的用户消息。使用
不带 `--auto` 的 REPL，让 Agent 发起一次需要 Web policy `ask` 的 WebSearch。

预期立即出现 exact request 详情和同步菜单：

```text
[1] Approve once and continue
[2] Deny and continue
```

验收点：

- 空输入不批准，只重新提示 `enter 1 or 2`；
- 选择 `1` 后 exact Tool Call 只执行一次，并直接接回原 Agent Loop，不输入
  `/resume`；
- 选择 `2` 后模型收到明确 denied Tool Result，并在现有边界内继续；
- Goal 被 park 时不运行 Judge，Tool continuation 自然完成后才运行；
- Plan 被 park 时不显示 Plan approval menu，规划 continuation 完成后才显示；
- transcript 不出现伪装成用户消息的 `[permission decision]`；
- Ctrl+C 只推迟决定，`/approve` 与 `/deny` 可恢复并直接继续；
- park 后退出并用 `oh --resume` 启动，会恢复同一个 exact request 和二选一菜单；
- `/clear` 清除 conversation-bound continuation，不清 Project Memory，也不复活授权。

`/resume` 不属于正常人工审批路径；它只处理外部控制面已记录 approve/deny、但
continuation 尚未消费的异步恢复。

## 五、自动 Compact 的定向实验

真实 262k window 不适合手工堆到 83%。为了观察 L2 自动路径，单独启动开发者实验：

```bash
uv run --project ../../.. --env-file ../../../.env oh \
  --auto --sandbox --log-level INFO --compact-threshold 0.02
```

输入：

```text
完整 Read large_context.txt 一次，然后找出三个 anchor；如果 Tool Result 缺少中段，使用 Grep 恢复。
```

预期下一次 API request 前出现 `auto_compact` INFO 事件。大 Tool Result 先经过 10k-token
hook，再因为 2% threshold 进入 L2 context collapse。这个阈值只用于 dogfood，不应写回
`.env`。

如果确定性 collapse 后仍超过预算，Compact 会直接调用结构化 LLM Summary，并保留最近
12 条原始消息。Reactive Prompt Too Long 还要求 provider 真正拒绝请求，不适合靠人工
反复粘贴制造，保留为确定性测试和后续 PTY runner case。

## 六、完成后的证据审查

按阶段比较 JSON，重点看：

```text
configuration.compact_threshold_tokens
discovery.project_instruction_files
discovery.commands
discovery.skills
discovery.installed_plugins
snapshot.message_count
snapshot.blocks
snapshot.tool_uses
snapshot.error_tool_result_count
snapshot.synthetic_skill_load_count
snapshot.context_markers
snapshot.tool_result_markers
snapshot.assistant_marker_mentions
snapshot.active_goal
snapshot.permission_runtime
snapshot.stored_context.tools
snapshot.stored_context.skills
snapshot.stored_context.headings
```

只有跨多个 case 稳定出现的模型决策缺口才提炼成 eval。Snapshot 未持久化、marker 缺失、
catalog 组装错误和 Tool Result 结构丢失属于确定性产品缺陷，先写 pytest，不用 live eval
替代。
