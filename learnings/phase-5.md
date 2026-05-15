# Learnings — Phase 5 (MCP Client / Federated Tool Registry)

> Phase 5 起止 / 2026-05-15 / 用时 ~1 天
> 7 capabilities (T1-T7) / ~15 sub-units / 8 commits / 788 tests / 96% coverage
>
> 本文件回答:**做完 Phase 5,关于 "把 framework 从 closed-tool 变成 plugin
> platform" 这件事,学到了什么 framework-level 的东西。**
>
> Phase 5 的核心赌注:Phase 3 的"统一 dispatch 路径"抽象是否对了。

---

## 1. 数据点

| 维度 | Phase 4 | Phase 5 |
|---|---|---|
| Capability(task) | 5 | **7** (T1-T7) |
| Sub-units | 12 | **~15** |
| Decision records | 1 (D14) | **1** (D15, 已存在 c5130e2) |
| 总测试数 | 709 | **788** (+79) |
| 总覆盖率 | 97% | **96%** (gate 95%) |
| 总 commits | 6 | **8** |
| Phase 5 加的 module | — | `mcp/` (5 文件:config / client / adapter / pool / __init__) |
| 新增 log events | 2 | **3** (mcp_server_start / _stop / _error;10 → 13) |
| 新增 BaseTool 字段 | — | **1** (trust_source) |
| 新增 Settings 字段 | — | **2** (mcp_servers / trusted_mcp_servers) |
| 修改 dispatch 路径行数 | — | **1** (trust_source 加到 tool_dispatch 日志) |

**预测 vs 实际**:Phase 5 plan 估 ~7-10 days,实际 ~1 天。原因:Phase 3 hook +
permission + observability infrastructure 已经备齐;Phase 5 只是它们的 tenant。
Phase 4 retro 也是同样的"复利"模式 —— framework 投资的回报。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **T1 — McpServerConfig + Settings + trust whitelist** | 数据模型 + env-var JSON-blob 解析。`tuple[McpServerConfig, ...]` + `Annotated[..., NoDecode]` 跟 P3 deny_paths 同款。Name regex 防 namespace collision 和 shell injection。 |
| **T2 — McpClient (single-server lifecycle)** | `AsyncExitStack` 把 SDK 两个嵌套 context manager(stdio_client + ClientSession) 合成 1 个 `async with`。init timeout + 3 log events。**跟实测踩坑**:`tests/mcp/` 跟 SDK 同名导致 import 解析冲突,rename 成 `tests/mcp_pkg/`。 |
| **T3 — McpToolAdapter (BaseTool subclass)** | `pydantic.create_model()` 从 JSON Schema 动态合成 input_model。Trust gating(D15.6)在构造时一次性决定。**dynamic Pydantic 的 mypy 代价**:测试不能 `.x` 访问字段,改用 `.model_dump() == {...}`。 |
| **T4 — McpClientPool (N-server)** | `asyncio.gather(return_exceptions=True)` 隔离单 server 失败。**boundary 的 once-per-query 自动 respawn 实际推后** —— 实现耦合 adapter/pool 两层,Phase 6+ 再做;Phase 5 ship "dead server 保持 dead" 简单模型。 |
| **T5 — CLI bootstrap + INVARIANT** ⭐ | `git diff` 验证 `permissions/checker.py` / `hooks/executor.py` 0 行改动,`engine/query.py` 只加 `trust_source` log 字段。**Phase 3 抽象通过考试**。 |
| **T6 — Real server smoke** | `@modelcontextprotocol/server-filesystem` 跑通,opt-in skip when no npx。第一次 npx 拉包 ~5s,后续 <2s。 |
| **T7 — Retro** | This file. Coverage 96% (mcp/ 89-100% per file)。 |

---

## 3. Framework-level 主题

### 3.1 ⭐ 不变量(invariant)验证 —— Phase 3 抽象通过了考试

Phase 5 入口 boundary doc 在显眼处写:

> Phase 5 must NOT add a new dispatch path. The following layers must
> remain unchanged: `permissions/checker.py`, `hooks/executor.py`,
> `engine/query.py` dispatch loop.

T5 完成后跑:

```bash
$ git diff 5bbdff5 -- src/openharness/permissions/checker.py
$ git diff 5bbdff5 -- src/openharness/hooks/executor.py
$ git diff 5bbdff5 -- src/openharness/engine/query.py
```

前两个 **完全空**。第三个**只**多了 `trust_source` log 字段(预先在 boundary
里说好的 additive change) + 配套的 try/except 注册查找。**没有任何
`isinstance(McpTool, ...)` 分支、没有任何 MCP-aware import**。

**这是个不平凡的结果**。Phase 3 设计 hook chain + AuthZ + Tier 检查的时候,
我们押的是"将来的扩展 tool 类型(MCP / 远程 / plugin)能用同一个 BaseTool
契约塞进来"。Phase 5 用一个真外部 consumer 把这条契约压测了 —— **押对**。

判决:**Framework 设计的判断力,是看后来的 consumer 能不能不改 framework
就直接用**。Phase 5 的真实交付不是 mcp/ 那 5 个文件,是"Phase 3 抽象稳定
性的实证"。后续 Phase 6 sub-agent / Phase 7 网络 transport 都可以在同一
基础上做。

### 3.2 用 SDK 不重发明协议 —— "把工业标准当 starter pack"

D15.1 锁定 stdio transport 走官方 `mcp` Python SDK。**结果**:`mcp/client.py`
只有 ~200 行,真正的协议复杂度(JSON-RPC 2.0 framing / connection 管理 /
schema 验证)被 SDK 封装。我们做的工作:

- `AsyncExitStack` 把 SDK 两个嵌套 context manager 合一
- init timeout(SDK 不提供,加 `asyncio.wait_for`)
- 错误分类(`McpInitError` / `McpCallError` subclass `OpenHarnessError`)
- 3 个 log events

**如果自己实现 JSON-RPC**:估计 800-1500 行,Phase 5 至少多 2-3 天。

判决:**有官方 SDK 时用 SDK,自己的代码集中在"集成层"(我们的契约 → SDK
契约)**。这是 Pydantic / typer / structlog 系列决策的延续 —— OpenHarness 从
不重发明轮子。

### 3.3 Trust 不能信 server 自报 —— D15.6 的精妙之处

Phase 3 retro 预测 Phase 5 会"加 plugin discovery"。但 MCP server 是**用户
配置启动的外部进程**。如果直接信 server 自报的 `annotations.readOnlyHint`:

```
Untrusted MCP server says "readOnlyHint: true"
→ adapter sets is_read_only=True
→ AuthZ Tier 3 skips strict check
→ tool runs without permission gating
→ user's filesystem gets nuked
```

D15.6 引入 **Settings.trusted_mcp_servers 白名单**(用户显式列出"我信这个
server"):

```python
trust = (cfg.name in trusted_servers)
is_read_only = annotations.readOnlyHint if trust else False
```

**默认 deny / 显式 allow** 是工业标准 AuthN/AuthZ 思路。同样形状:
- P3 D13.2 Tier 2 deny_paths(用户配置 deny 规则)
- P3 D13.3 `is_read_only` 默认 False(框架默认严格)
- P5 D15.6 trusted_mcp_servers(用户配置 trust 范围)

**判决**:**面对外部信任边界,framework 永远默认不信,把"信"的决策权交给
用户配置**。这是 Phase 3-5 一脉相承的安全姿态。

### 3.4 Sub-unit 拆分 vs commit 合并 —— 取舍取决于"独立 review 价值"

Phase 4 retro 已经讨论过这个。Phase 5 继续验证:

- **T1**(3 sub-units)→ 1 commit:McpServerConfig + 2 Settings fields,完整数据模型 1 次 review
- **T2** 计划 3 sub-units 实际 1 commit:McpClient 完整功能 + tests + naming collision fix 一次性
- **T3** 计划 3 sub-units 实际 1 commit:_synth_input_model + McpToolAdapter + execute 紧耦合
- **T4** 2 sub-units 1 commit
- **T5** 3 sub-units 1 commit + invariant verification
- **T6** 单 commit
- **T7** 单 commit(this file + DoD)

**8 个 commit / 7 个 capability = 1 commit/task 平均**。每个 task 是
review 单元;sub-unit 是规划单元(预先想清楚怎么拆)但 commit 时按"逻辑
独立性"合并。

**判决**:**强制 1:1 sub-unit:commit 是 over-engineering**。Plan 的 sub-units
帮你规划思路;build 时按"reviewer 看一个 commit 能否一次明白"决定 commit
边界。

### 3.5 真踩坑 2 个

| # | 坑 | 触发 | 修复 |
|---|---|---|---|
| 1 | `tests/mcp/` 跟 `mcp` SDK 同名 → pytest sys.path 添加 `tests/mcp/` 后,源码里 `from mcp import ClientSession` 解析到我们的 test dir,炸 ImportError | T2 第一次 pytest 启动 | rename `tests/mcp/` → `tests/mcp_pkg/`(测试目录改名是 cheap fix;源码 package `openharness.mcp` 不变) |
| 2 | mypy 4 处 pre-existing `from openharness import cli` 错误(Phase 4 close 时已有但被错误标记 "clean") | 跑 mypy 时被 T3 改动暴露 | 改 4 个 test 文件用 `import openharness.cli as cli_module`;mypy 现在干净 |

判决:**测试目录名要 distinct from 第三方 package 名**。下次新加 `openharness.foo`
package 时,test 直接叫 `tests/foo_pkg/`(双下划线版本)避免类似踩坑。

### 3.6 boundary doc 的 "锁前预判 vs build 中调整" —— D15.4 的 partial defer

D15.4 boundary 文本写"bounded once-per-query auto-respawn"。T4 build 时
发现:

> 真 mid-query respawn 要求 adapter 和 pool 之间的状态共享(adapter 的
> McpClient ref 死了,pool 得知道、再造一个、再把 adapter 的 ref 更新)。
> 这是一个非平凡的状态机改动,跟 Phase 5 "简单 client 接入" 的目标不匹配。

我**没**强行实现 D15.4 的完整规范,而是 ship 了简单版(dead = dead),
boundary doc 里说明 deferred,并在 module docstring 显式记下原因。这跟
Phase 4 D14.5 的"bounded retry" 是同款思路 —— 实施时"够用就好",过度
实现等到真有 user pain。

**判决**:**boundary doc 是契约,但 build 中发现实现成本远超预期时,可以
缩范围 + 显式记录**。重要的是不偷偷砍 —— Phase 5+ retro 必须 surface 这条
"未完整实现的 D15.4 子项",让后续 phase 接手时知道。

---

## 4. Phase 5 的契约预测 —— Phase 6+ 会验证什么

### 4.1 Sub-agent 走 hook 而非 dispatch 改动

Phase 6 加 sub-agent(parent agent spawn 子 agent 执行子任务)。预测:

- 子 agent 是 dispatched 的 tool(`AgentExecutor` 或 `SpawnTool`)
- 它的 `BaseTool.execute` 内部跑一个新的 `run_query` 实例
- Permissions / hooks / engine 0 改动 — 跟 MCP 同模型
- `parent_run_id` 字段加到 trace contextvar(P3-T5 retro 已预测)

如果 Phase 6 build 时**任何** dispatch 层需要 "知道这是 sub-agent" 的特判,
**Phase 3 抽象第二次失败** —— 那时回头重新设计 BaseTool 契约。

### 4.2 HTTP / SSE transport 走新 transport class

Phase 6 加 HTTP MCP servers(production-recommended)。预测:

- 新增 `mcp/transports/http.py` — McpClient 内的"transport" 抽象出来
- `McpClient(config, transport=...)` 接受 transport 参数
- stdio vs HTTP 只在 `__aenter__` 内 dispatch
- 所有上层(adapter / pool / cli) 0 改动

### 4.3 MCP Resources + Prompts 加新 primitive

D15.5 deferred。Phase 7+ 实现时:

- Resources → 新 hook event `PreApiCall` modify request 加 resource 内容
- Prompts → CLI slash command(其他 boundary doc 预演的 D14 ModeBundle)
- 都不动 dispatch 流

---

## 5. 给 Phase 6 的 input

1. **Phase 3 抽象稳定** —— Phase 6 别重新讨论"是否要 isinstance(SubAgentTool, ...)"。第一性原理:**新工具种类都该走 BaseTool**。
2. **trust_source 字段是 load-bearing** —— Phase 6 sub-agent 可能加 "sandbox" / "untrusted-network" / 其他来源,直接扩展枚举。
3. **boundary 的 invariant pattern 可以复用** —— Phase 6 boundary 入口先列"哪些层不可动"。事先讲清楚,build 时容易抓 abstraction failure。
4. **D15.4 once-per-query respawn 未完成** —— 如果 Phase 6 真有 use case(长任务跨进程崩),回来开 work item。

---

## 6. Phase 5 最浓缩的 1 句

> Phase 5 把 OpenHarness 从"内建 5 工具"升级到"接任意 MCP server"。**核心
> 一句:抽象做对了,framework 不用动**。`permissions/checker.py` 和
> `hooks/executor.py` zero-diff,`engine/query.py` 只多一行 log 字段。
> Phase 3 投资的复利。

---

## 7. Pointers

- Phase 5 boundary: [`decisions/11-phase-5-boundary.md`](../decisions/11-phase-5-boundary.md)
- Phase 5 plan: [`tasks/phase-5-plan.md`](../tasks/phase-5-plan.md)
- Phase 5 preview (D14 source — pre-build framing): [`tasks/phase-5-preview.md`](../tasks/phase-5-preview.md)
- Phase 4 retro(对照 framework-level 主题结构): [`learnings/phase-4.md`](./phase-4.md)
- 8 个 Phase 5 commits:`git log --oneline | grep -E "P5-T|phase-5"`
- MCP spec: <https://spec.modelcontextprotocol.io/>
- Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
