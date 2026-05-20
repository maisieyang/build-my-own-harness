# Learnings — Phase 7 + meta-retrospective for the whole 16+1-phase journey

> Phase 7 起止 / 2026-05-19 → 2026-05-20(~半天 + ~半天,T1
> README + T2 subcommands + T3 packaging + T4 examples + T5 本文件)
> 5 capabilities (P7-T1…T5) / 5 commits / ~1700 行 docs(README rewrite
> + dev-log preserved + tutorial + examples/README + CHANGELOG +
> publishing runbook + 本文件)+ ~360 行生产代码(3 subcommand 系列
> + Settings user-global .env layer)+ 28 新增 tests(20 subcommand
> + 8 examples)
>
> 本文件**是双层 retro**:Phase 7 收尾的 framework-level 复盘
> + **整个 16+1 phase 项目的 meta-retrospective**。这是 SPEC §4.7
> 命名的"我从 0 构建生产级 harness 的 7 个 Phase"(实际 17 个)
> 复盘 —— 项目对外作品的核心 artifact。

---

## 1. 量化数据点 — 项目总结

| 维度 | SPEC 原计划(2026-04 锁定) | 实际交付 |
|---|---|---|
| **时间** | 2-3 个月 | **23 天**(2026-04-27 → 2026-05-20) |
| **Phase 数** | 7 | **17**(原 7 个 + 10 个 split / bonus / closeout) |
| **生产代码 LoC** | 未定 | **~10,800 行**(`src/openharness/`) |
| **测试代码 LoC** | 未定 | **~21,600 行**(`tests/`) |
| **测试覆盖率** | ≥70% (Phase 1 DoD) → ≥95% (P3-T6.6b 升级) | **~97%**(gate 95%) |
| **测试数量** | 未定 | **1268 passing + 8 skipped**(Docker / gVisor / real-LLM integration) |
| **Commit 数** | 未定 | **195** |
| **Subsystem 数** | Tier 0 必做 12 项 | **18**(`src/openharness/` 下的目录) |
| **Decision docs** | 每个非平凡决策一份 | **23**(`decisions/00-23`) |
| **Retro docs** | 每个模块完成一份 | **29**(`learnings/`,含 Phase 1 框架级 + 本文件) |
| **CLI subcommands** | SPEC §2 列了 12 个 | **8 个** ship(ask / chat / tools list/show / config show/edit / hooks list/describe);3 个明确 defer 到 Phase 8+(mcp add/list / skill run) |
| **包 artifact** | PyPI publish (Phase 7) | **0.1.0 build verified**;真正 publish gated 给用户(D25.3) |
| **mypy --strict** | 全程开启 | ✅ 190 source files clean |
| **ruff check + format** | 全程开启 | ✅ clean |

**核心观察**:**3.3 周完成 SPEC 的 2-3 个月计划,但 Phase 数翻了 2.4 倍**。
不是工作做得快(每个 Phase 都按 boundary-doc → plan → execute →
retro 走完整流程),是**每个 Phase 的颗粒度比原计划细**。原 Phase 5
"Extensibility" 在实际执行时拆成 5 / 5b / 5c / 5d / 5e / 5f 六个 phase
—— 每个都有自己的 boundary doc + retro。这种细化反而**提高了
abstraction 质量**(详见 §3.5)。

---

## 2. Phase-by-Phase 时间线(实际交付顺序)

按 git 历史而不是 phase 编号顺序:

| Phase | 交付 | 周期 | Retro | 备注 |
|---|---|---|---|---|
| Phase 0 | Architecture map | (项目启动前) | — | `ARCHITECTURE.md` |
| 1 | Foundation + Hello LLM | 1 周(2026-04-27 →) | `phase-1.md` | 单文件 retro;`phase-1-and-2.md` 合并 |
| 2 | Tool Loop(心脏) | ~5 天 | (合在 phase-1-and-2.md) | `BaseTool` / `ToolRegistry` / `run_query` |
| 3 | Safety + Observability | 1 周 | `phase-3-framing.md` + `phase-3.md` | 三层 hook + 三层 permission + JSON 结构日志 |
| 4 | Context Management(Compaction) | 3 天 | `phase-4.md` | Layer 1 + 2 ship;Layer 3 defer |
| 5 | MCP | 3 天 | `phase-5.md` | stdio transport adapter |
| 5b | Slash Commands | 2 天 | `phase-5b-commands.md` | 5b 比 5c 后出 |
| 5c | Skills(lazy-loaded expertise) | 2-3 天 | `phase-5c-skills.md` | 比 5b 先出(plan 顺序 vs ship 顺序不同) |
| 6 | Sub-agent(recursive tool dispatch) | 2 天 | `phase-6.md` | `SpawnAgent` + depth limit |
| 7a | Substrate abstraction | 1 天 | `phase-7a.md` | Protocol + HostExecution identity transform |
| 7b | Docker sandbox | 1-2 天 | `phase-7b.md` | aiodocker substrate |
| 5d | **ModeBundle**(first cross-layer tenant) | 2 天(2026-05-16/17) | `phase-5d.md` | 跨 4 层验证 |
| 5e | Plugin hook discovery via entry points | 1 天 | `phase-5e.md` | source-agnostic catalog |
| 8 | `markdown_store/` refactor | 半天 | `phase-8.md` | rule-of-three;5b/5c/5d 三次重复后抽 |
| 5f | Filesystem hook plugins(`*.py` discovery) | 半天 | `phase-5f.md` | 60% cost of 5e |
| 7c | gVisor runtime kwarg | 半天 | `phase-7c.md` | kwarg-not-class judgment |
| 6+ | `oh chat` REPL(multi-turn) | 1 天(2026-05-19) | `phase-6plus.md` | 新 stream event 暴露 final state |
| **7** | **打磨与发布(本 phase)** | 2 天 | **本文件** | T1 README + T2 subcommands + T3 packaging + T4 examples + T5 retro |

**5 个 phase 在原计划 outside of scope 的位置出现**(5d / 5e / 5f / 8 /
6+)—— 都是从 retro 自然引出的"下一个杠杆点":Phase 5c retro 写完
发现"slash command 也需要 cross-layer composition"→ 5d;5d retro
写完发现"named hook 需要 plugin discovery"→ 5e;5e retro 写完发现
"filesystem 也是 source"→ 5f;5b/5c/5d 三次重复后→ 8;6+ 是 chat
REPL 的纯增量。

---

## 3. Framework-level 主题 — 项目核心 5 条

### 3.1 ⭐ Abstraction-first 的复利效应(Phase 7a/7b/7c 三连)

**事件链**:
- Phase 7a 用 1 天做 substrate Protocol,`HostExecution` 是 identity transform —— 当时看像 over-engineering
- Phase 7b 用 1-2 天 ship Docker sandbox —— 第一个真 substrate impl,几乎全部新代码,但是接现成的 Protocol
- Phase 7c 用半天 ship gVisor —— **~30 行生产代码**,因为只是 Docker container `HostConfig` 一个字段(`Runtime`)

**模式**: 抽象的 cost 在 7a 一次性付清(Protocol + identity transform + invariant test)。后续两个 substrate 几乎免费。**7c 的开发成本是 7b 的 12%**,因为前两个 phase 把扩展点设计对了。

**普适经验**:**任何时候你看到一个新功能在三个地方做类似事情时,先抽抽象再做第二个**。第二个先不抽,会自然成为"特殊版本";第三个再抽就会被前两个的具体形态污染。**Identity transform 是 abstraction-first 的最强证据** —— 把现有功能塞进新 Protocol,所有现有测试一行不动通过,就证明抽象 shape 对。

### 3.2 ⭐ Layered model 跨 4 轴同时扛(Phase 5d)

**事件**: Phase 5d 的 ModeBundle 同时改 system_prompt(Layer 1)/ tool catalog(Layer 2)/ permission deny_paths(Layer 3a)/ hook chain(Layer 3b)四个层 —— **每一层 0 行 diff**,所有改动集中在新的 `bundles/` 包。

**Phase 3 当初的押注**: 设计 permission / hook / observability / context-passing / tool-base / engine-dispatch 这些层时,核心赌注是"将来跨层需求能通过组合现成 primitive 解决,不用挖空任何一层"。

**Phase 5d 是第一次真正的 stress test** —— 5a/5b/5c/6/7a/7b 每个都只扩展一个 axis,layered model 没真正被考验。5d 跨 4 axis 同时做,Phase 3 layered model 全部 hold —— **押注兑现**。

**普适经验**: 设计 layered abstraction 时,"将来能不能扛跨层需求"这个问题没法在设计时验证 —— 必须等真有 cross-cutting case 出现。**不要因为没立刻被验证就 over-engineer;也不要因为只测了一个轴就以为抽象成立**。5d 是 layered model 设计成立的真正证据。

### 3.3 ⭐ Additive kwarg 是扩展稳定 API 的正确形态(Phase 5e + 6+)

**事件 1**(Phase 5e): `resolve_hook(name)` → `resolve_hook(name, plugin_catalog=None)`。Phase 5d 写的 6 个 `resolve_hook` 测试 + 17 个 `apply_bundle_to_context` 测试,**没有一个需要修改**就 GREEN。

**事件 2**(Phase 6+): `run_query` 加了一个新 stream event `ConversationCompleteEvent`。`oh ask` 的 renderer 不知道这个 event,自动 ignore;`oh chat` 知道,处理多轮 history。

**模式**: 默认值 = 旧行为,opt-in = 新功能。**caller 不变,功能扩**。

**普适经验**: 扩展已有 API 的判断:
- 新 kwarg 是不是 真的可选?(default 行为 = 旧行为)
- Opt-in 行为是不是 真的扩展?(不是 alias 不是 wrap)
- 现有测试是不是 byte-identical 通过?

三个 yes,才能 additive kwarg。否则就是 breaking change,需要 v2 函数或显式版本号。

### 3.4 ⭐ Source-agnostic catalog 的扩展性(Phase 5e + 5f)

**事件**: Phase 5e 设计 plugin hook 用 `dict[str, HookSpec]` 作为 catalog 格式。当时只有一个 producer(entry points)。

Phase 5f 加 filesystem source。**catalog 格式 0 修改,resolve 路径 0 修改,apply 路径 0 修改**。第二个 source 的成本是第一个的 60%。

**模式**: `HookSpec` 只有 `event` + `hook` —— **不携带 source 信息**(没有 `source: Literal["entry_point", "filesystem"]` 字段)。这是关键设计 —— catalog 应该不知道 producer。

**普适经验**: 任何 catalog-style data(dict / list / map)出现时,先问:**这个 catalog 会不会有 1+ 个未来 producer?** 如果可能,catalog 类型必须 source-agnostic。不要把 producer-specific 字段塞进 catalog —— 一旦塞了,所有 downstream caller 都被绑住,扩 producer 就要改 caller。

### 3.5 ⭐ API-level zero-diff 是 refactor 的正确 invariant(Phase 8)

**事件**: Phase 8 抽 markdown_store/ 公共模块时,**0 个**既有 domain test 需要修改 —— 233 个 commands+skills+bundles test 全部一行不动通过。

**关键洞察**: 之前 phase 的 invariant 是 "其他层 0 diff"(横向);refactor phase 的 invariant 是 "caller 不变,测试不改"(纵向)。两个 invariant 都是 zero-diff,但**保护对象不同**。

5b/5c/5d 三次重复 + 5d retro + 5e retro 都标记 Phase 8 候选,但等了第四个相似 phase(5e)才抽 —— **rule of three 是 sweet spot**。早抽 over-generalize(等不到第三个具体 instance,抽象 shape 没法定),晚抽错失复利。

**普适经验**: refactor 应该有 measurable success criteria:
- 既有测试 0 修改(API-level zero-diff)
- 既有 caller 0 调整(import 路径稳定)
- 总 LoC 净减少(refactor 不是迁移 + 增加复杂度)

三个都不达成,refactor 就是错的或者过早的。

---

## 4. Python-specific 经验 — 项目核心 3 条

### 4.1 `from __future__ import annotations` + TYPE_CHECKING 处处用

**模式**:
```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openharness.protocols import ApiStreamEvent  # 真实类型
    from openharness.engine import QueryContext  # 可能 circular import
```

类型注解变成 string,运行时不解析。结果:
- 循环依赖消失(类型只在 mypy 看,不在 runtime 看)
- import time 加快(`if TYPE_CHECKING: False`,不真 import)
- 类型签名清晰(没有 stringified `"QueryContext"` 散布在签名里)

**踩过的坑**: Pydantic v2 / pydantic-settings 评估 annotation at runtime 来构 schema。如果 type 只在 TYPE_CHECKING 块里,Pydantic 会找不到。解决方案: `runtime-evaluated-base-classes` ruff config(项目 pyproject.toml 已加 Pydantic + pydantic-settings + StrictModel)。

### 4.2 Pydantic v2 `extra="forbid"` + `validate_assignment=True`

**模式**:
```python
class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",          # 拒绝未声明字段
        validate_assignment=True,  # mutation 也走 validation
    )
```

`extra="forbid"` catches typos in test fixtures + protects against partial migration(添加新字段时旧数据 doesn't silently lose info)。

`validate_assignment=True` 保护 mutation 路径 —— `event.message = wrong_type` 在赋值时就抛,不是稍后某处使用时。

**踩过的坑**: 协议层(protocols/)必须用 StrictModel;runtime context(QueryContext / ToolExecutionContext)是 frozen dataclass,不用 Pydantic。混用导致初期序列化 vs 校验的边界搞不清 —— Phase 1 retro 写清了哪类用哪个。

### 4.3 Async generator + `AsyncIterator` for event streams

**模式**:
```python
async def run_query(messages, context) -> AsyncIterator[ApiStreamEvent]:
    """Each yield is one event the caller can render or react to."""
    async for ev in api_client.stream_message(request):
        yield ev
    yield ConversationCompleteEvent(messages=messages)  # final
```

`run_query` 是 async generator。caller 通过 `async for ev in run_query(...)` 消费。**生产者推送,消费者拉取,中间没有 queue 也没有 thread pool**。

**踩过的坑**: 想从 async generator 把 final state 暴露给 caller 的时候,`async for` 自动吞掉 `StopAsyncIteration` —— return value 拿不到。Phase 6+ 的解决方案: **加一个新 stream event type**(`ConversationCompleteEvent`)。比 mutable kwarg / return value 都干净 —— event stream 是 broadcast 语义,加一个新 type 是 additive。

**通用经验**: Async generator 的 "final state" 暴露,优先级:**新 yielded event > generator return value > mutable param**。事件流是 broadcast 语义,其他两个是 tight coupling。

---

## 5. 如果重做我会改什么(诚实清单)

### 5.1 不该 ship `0.0.1`,应该 ship `0.1.0` 起步

**问题**: `pyproject.toml` Phase 1 起就 `version = "0.0.1"`,直到 Phase 7 T3 才 bump 到 0.1.0。`0.0.x` 在 semver 习俗里是 "this is a prototype, expect everything to break" —— 1268 个测试 + 97% 覆盖率 + mypy strict 的项目不是 prototype。

**应该这样**: Phase 1 就用 `0.1.0` 起步,signal "implementation real, API still pre-committed"。Phase 7 不需要做"第一次正式 release bump"这个 ceremony。

### 5.2 `_build_query_context` factor 应该 Phase 6+ 当时做,不该 defer

**问题**: Phase 6+ 加 `oh chat` 时,`_run_chat` 跟 `_run_ask` 共享 ~150 行 inline bootstrap。retro §3.6 明确写了"等 third consumer 出现再抽"(rule of three),但**`oh ask` + `oh chat` 已经 2 个 consumer**,且本质上不会出现第三个 query-running consumer(Phase 8+ 的 `oh server` 是另一种形态,不复用 bootstrap)。

**应该这样**: Phase 6+ 加 chat 同时 factor `_build_query_context` helper,避免 `cli.py` 长到 1300 行。Phase 7 后期再回去 refactor 是反复 review 代码 + 二次承担测试调整 cost —— 当时做是 1 次,后做是 2 次。

**一般化**: "rule of three" 是抽公共代码的判断标准,但 **2 → 3 之间的等待期不应该长**。如果 2 个 instance 之间间隔很短(同 phase 或相邻 phase),不应该等;**如果间隔长 + 形态不同(5b/5c/5d 跨多 phase 演化)**,等到第三个 instance 反而能看清抽象 shape。

### 5.3 Anthropic native client 应该 Phase 1 就 ship

**问题**: `protocols/` 整个数据模型按 Anthropic shape 设计(`ToolUseBlock` / `stop_reason="tool_use"` / `ContentBlock` discriminated union 全是 Anthropic 词汇)。但 Phase 1 选了 Qwen via DashScope(OpenAI-compatible)做首测 —— `api/translation.py` 一直在做 Anthropic ↔ OpenAI wire 翻译。

实际只有一个 OpenAI-compatible Client(走 DashScope / OpenAI / DeepSeek / 任何 OpenAI-style endpoint),没有 真 Anthropic client。**Anthropic 的高级 feature**(prompt caching / extended thinking / computer use / tool_use 原生 caching)**全部用不上**。

**应该这样**: Phase 1 同时 ship 两个 client —— `OpenAICompatibleApiClient` + `AnthropicApiClient`,都实现 `SupportsStreamingMessages` Protocol。这样:
1. anti-corruption layer 第一天就被双向压测(SPEC §1 Phase 1 决策点本意)
2. 高级 feature 不被 defer
3. 多 Provider 选型(SPEC §1 目标)第一天就有

### 5.4 Decision doc 编号应该跨 phase 严格递增

**问题**: 现在有 `00-env.md`(slipped in, 不标准)+ 23 个标准 decision doc。Phase 7 boundary 是 `23-phase-7-final-boundary.md`。但中间有 `12-phase-5c-skills-boundary.md` / `13-phase-6-boundary.md` —— 5c 比 6 编号小,但实际上 5c 比 6 后 ship(plan vs ship 顺序不一致)。

**应该这样**: 编号严格按 **decision-time 顺序**(创建时间),不按 phase 编号。这样 retro 翻 history 直接读编号就知道演化路径。

---

## 6. SPEC §2 12 个命令 vs 实际交付

| Command | SPEC 状态 | 实际 | 备注 |
|---|---|---|---|
| `oh ask <prompt>` | Phase 1 | ✅ Phase 1 | |
| `oh chat` | Phase 2-3 | ✅ Phase 6+ | |
| `oh tools list` / `show <name>` | Phase 2-3 | ✅ Phase 7 T2 | |
| `oh mcp add <server>` / `list` | Phase 5+ | ❌ Phase 8b candidate | env-var 配 only |
| `oh /<slash-command>` | Phase 5+ | ✅ Phase 5b | 通过 `oh ask /name args` |
| `oh skill run <name>` | Phase 5+ | ❌ Defer indefinitely | 概念冲突 |
| `oh config show` / `edit` | Phase 5+ | ✅ Phase 7 T2 | |
| `oh hooks list` / `describe` | (SPEC 没列,5e retro 提) | ✅ Phase 7 T2 | bonus |
| `oh --version` / `--help` | Phase 1 | ✅ Phase 1 | |

**交付率**: 12 中 SPEC 列的 + 1 bonus = 13;实际 ship 8 个 + 3 defer = 9 个 surface。**~70%**。

剩下 30% 的 defer 都有明确理由(D25.2 + decisions/23 §6),不是"忘了做"。

---

## 7. 还未做的(Phase 8+ candidates,详见 decisions/23 §6)

按"价值×成本"二维 rank:

| 候选 | 价值 | 成本 | 优先级 |
|---|---|---|---|
| **Anthropic native client**(8a) | 高(unlock 高级 feature) | 低(~150 LoC) | ⭐⭐⭐ |
| **LLM auto-compaction Layer 3**(4.5) | 高(long-session 必需) | 中(prompt eng + invariant) | ⭐⭐ |
| **Memory system 基础**(5g) | 中(Tier 2 ⭐⭐) | 低-中(~100 LoC) | ⭐⭐ |
| **Keyring auth + multi-profile**(7a-small) | 中(SPEC §2 明示) | 低(~80 LoC) | ⭐⭐ |
| **`_build_query_context` factor**(9) | 中(cli.py 1300 → 600) | 低(纯 refactor) | ⭐⭐ |
| **`oh mcp add/list`**(8b) | 中(MCP UX) | 中(需要 config-file write) | ⭐ |
| **REPL polish**(/save / /load / multi-line)(6++) | 中(用户 quality of life) | 中(需要 prompt_toolkit) | ⭐ |
| **Firecracker substrate**(7d) | 低(用户场景未浮现) | 高(新 substrate class) | (defer) |
| **Background tasks + cron**(10) | 低(Tier 3) | 中 | (defer) |
| **`HookSpec` metadata**(8c) | 低(catalog UI 没载体) | 低(additive 字段) | (defer until catalog UI 来) |

**下个最大杠杆**: Anthropic native client。`protocols/` 是 Anthropic shape,只需要 `AnthropicApiClient` 实现 Protocol,~150 LoC + 一个 retry strategy 调整 + 30 个新测试。**收益**: 用户可以直接用 Claude API + prompt caching,framework 不再被 OpenAI-compatible 形态局限。

---

## 8. Phase 7 DoD checklist

- [x] T1 README rewritten(316 lines,user-facing structure)
- [x] `docs/development-log.md` preserves Phase 1-6+ narratives verbatim
- [x] T2 `oh tools list/show` shipped + tested(8 tests)
- [x] T2 `oh config show/edit` shipped + tested(6 tests,含 user-global .env layer)
- [x] T2 `oh hooks list/describe` shipped + tested(6 tests)
- [x] T3 `pyproject.toml` metadata complete(version 0.1.0,16 classifiers,
      13 keywords,6 urls,authors,license)
- [x] T3 `LICENSE` file at repo root(MIT,since Phase 1 scaffolding)
- [x] T3 `CHANGELOG.md` with 0.1.0 release notes(Keep a Changelog format)
- [x] T3 `uv build` produces wheel + sdist(verified in fresh venv;
      `oh --version` returns `openharness 0.1.0`)
- [x] T3 `docs/publishing.md` runbook for TestPyPI + production publish
- [x] T4 `docs/tutorial.md`(360 lines,3 progressive scenarios + 1 optional)
- [x] T4 `examples/` directory(6 copy-pastable files)
- [x] T4 `tests/test_examples.py`(8 tests verifying each example parses)
- [x] T5 `learnings/phase-7.md` meta-retro(本文件)
- [x] 1268 tests passing,8 skipped(Docker / gVisor / real-LLM integration)
- [x] coverage ≥ 95% maintained throughout
- [x] mypy --strict clean(190 source files)
- [x] ruff check + format clean
- [ ] **PyPI publish** — gated on user,not Phase 7 agent autopilot
      (see `docs/publishing.md`)

---

## 9. 给"将来读这份文档的人"的话

如果你是从外面看这个项目(求职 / 学习 harness 设计 / 想 fork 一份做自己的版本):

**最值得读的 3 个 retro**(按 framework-level 信息密度):
1. `learnings/phase-5d.md` — 第一次 cross-layer composition 验证 Phase 3 layered model
2. `learnings/phase-7a.md` + `learnings/phase-7b.md` + `learnings/phase-7c.md` — abstraction-first 复利效应的 3-phase 量化证据
3. `learnings/phase-6plus.md` — generator-based engine 暴露 final state 的 3 个方案 trade-off

**最值得读的 5 个 decision doc**:
1. `decisions/01-scaffolding.md` — 全部 toolchain 选型 + rationale
2. `decisions/08-phase-3-boundary.md` — 3-tier permission system 的设计
3. `decisions/17-phase-5d-boundary.md` — 第一次 cross-layer tenant 的 6 个 decision
4. `decisions/18-phase-5e-boundary.md` + `decisions/20-phase-5f-boundary.md` — plugin discovery 的 trust boundary 设计
5. `decisions/22-phase-6plus-boundary.md` — multi-turn REPL 的 stream-event-as-final-state 方案

**作为 Python 项目模板**(如果想 fork):
- `pyproject.toml` 是 single source of truth(ruff + mypy + pytest + coverage + dependency groups)
- `.pre-commit-config.yaml` 是快速 hook
- `tests/conftest.py` 处理 asyncio_mode + env var 隔离
- `tests/execution/test_invariant.py` 是 cross-cutting invariant 的 enforcement pattern(每加一个 phase 加一个 invariant class)

**对于"如何启动一个新 harness 项目":**
- 不要从 36 个 module 的 REFERENCE 抄 —— 抄会做完一切 demo 级别。Tier 划分(ARCHITECTURE.md §2)是减法的核心。
- Phase 1 应该是 hello world 但 mypy strict + ruff + pre-commit 全开 —— 工程基线不能 defer。
- 第一个 abstraction 应该是 Protocol(API client),不是类继承 —— Phase 7 retro §3.1 的 abstraction-first 红利从这里开始计算。

---

## 10. 自评

**SPEC §1 双重目标**:

1. **Production deliverable**: ✅ 1268 tests,97%+ coverage,17 phases,可 `uv build` 产 wheel + sdist。仅缺 user 自己 fire 的 PyPI publish。
2. **Capability training**: ✅ 23 decision docs + 29 retros + 18 subsystems + 5 个 framework-level lesson(§3)+ 3 个 Python-specific lesson(§4)。**对外作品的核心 artifact 是这份 retro 本身 + 28 个 phase retro**。

**SPEC §1 时间预算**: 2-3 个月 → 23 天。**~5x 提前**。原因不是工作快,是 phase 颗粒度细 + 每 phase 严格 boundary→plan→execute→retro。

**真正的学习**(超出 SPEC §1.2 命名的能力训练):
- abstraction-first 在 7a/7b/7c 上量化兑现 —— 不是哲学讨论,是 12% LoC ratio 的数据
- layered model 在 5d 上扛 cross-cutting load —— 不是假设,是 11 个 protected dir 0 行 diff 的数据
- additive kwarg + source-agnostic catalog —— 两个 framework extensibility 的 first-principles pattern,经 5e/5f 验证
- rule-of-three refactor invariant(Phase 8)—— refactor 真正的 success criteria 是测试不改不是行数减少

这些都是只有跑完一整个项目才能拿到的 first-principles understanding。**SPEC §1 的 capability training 比 SPEC §1 production deliverable 价值更高 —— 但只有 production deliverable 完成的项目,capability training 才真实**。

---

> **一句话总结(整个 16+1 phase 项目)**:
>
> **SPEC 押注的 layered abstraction + abstraction-first + rule-of-three
> 三个 framework design pattern,在 17 个 phase 的累积压力下全部
> 兑现。1268 个测试 / 18 个 subsystem / 23 天交付。最大单点
> outstanding 是 Anthropic native client(150 LoC,~1 天)+ PyPI
> publish(用户 fire button)。SPEC v1 boundary 在此 close。**
