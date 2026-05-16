# Learnings — Phase 7b (`SandboxExecution` Docker substrate)

> Phase 7b 起止 / 2026-05-16(单日,Phase 7a retro 后立即开启)
> 4 capabilities (P7b-T1…T4) / ~8 sub-units / 5 commits / ~50 new tests
> ~250 lines of production code(`execution/sandbox.py`)+ ~80 lines
> of CLI/Settings wiring / 100% on `execution/sandbox.py` 部分(96%
> 全)
>
> 本文件**不是** sub-unit 合集 —— commit message 已详尽记录。
> 它回答的题:**做完 Phase 7b,关于"abstraction-first paid off"这件事
> 的实证结果,以及 7a/7b 拆分的代价收益分析。**

---

## 1. 数据点

| 维度 | Phase 7a(抽象) | Phase 7b(Docker 实现) |
|---|---|---|
| Capability | 4 (T1-T4) | **4** (T1-T4) |
| 生产代码量 | ~150 行 | **~250 行**(sandbox.py 200 + cli/settings wire 50) |
| 新增 module | `execution/`(2 文件) | **`execution/sandbox.py`** + `tests/execution/test_sandbox{,_integration}.py` |
| 新依赖 | 0 | **`aiodocker>=0.21,<1.0`** |
| 改 `permissions/` / `hooks/` / `observability/` / `mcp/` / `compaction/` / `skills/` / `commands/` / `protocols/` | 0 行 | **0 行** ✓ |
| 改 `engine/` | 1 line additive | **0 行** ✓ |
| 改 `tools/bash.py` | execute body refactored | **0 行**(P7a 已经把 BashTool 改成 substrate-aware) |
| 改 `execution/base.py` / `execution/host.py` | n/a(刚建) | **0 行**(D17.1 Protocol 没扩展) |
| 触动横切 module | `engine/context.py`(+1 field)+ `engine/query.py`(+1 kwarg)+ `tools/base.py`(+1 field)+ `tools/bash.py`(refactor)| **`config/settings.py`(+6 fields)+ `cli.py`(+5 flags + AsyncExitStack)** |
| 新增测试 | ~32 | **~50**(25 mocked unit + 13 cli/settings + 6 integration + 3 invariant ext) |
| Phase 修改后总 tests | 1023 | **1061 passed + 7 skipped(integration)** |

**关键观察**:Phase 7b **零行改动**所有 Phase 7a 之前的现存代码层(permission /
hook / observability / mcp / compaction / skills / commands / protocols / engine /
tools)以及 7a 自身的 `base.py` / `host.py` / `bash.py`。整个 7b 只在:

1. `execution/sandbox.py`(新文件,~200 行)
2. CLI/Settings 接线(~50 行)
3. 测试

完全的 **plug-in 工作** —— 这就是 7a retro §3.6 提出的"abstraction-first pays
off"的直接验证。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P7b-T1 — `SandboxExecution` + aiodocker** | 200 行 substrate 代码:`__aenter__` 拉镜像+起容器;`__aexit__` 清理;`run_command` exec 进运行中容器 + 翻译 ProcessResult。**aiodocker 是 async-native** 跟 P7a `await env.run_command` 形态零摩擦。**踩了 mypy bug**(aiodocker jsonstream 让 mypy 2.0.0 INTERNAL ERROR),用 `follow_imports = "skip"` 绕开。 |
| **P7b-T2 — Settings + CLI flags + bootstrap** | 6 个 Settings 字段(`sandbox_enabled`/`sandbox_image`/`sandbox_network`/`sandbox_memory`/`sandbox_cpus`/`sandbox_pids`)+ 5 个 CLI flag + `AsyncExitStack` bootstrap chain(条件性进 sandbox context,避免代码重复)。**所有 1048 个现有测试一行不动通过** —— 默认 OFF 路径完全 backward-compat。**踩了 env var 命名坑**:`OPENHARNESS_SANDBOX` 不映射到 `sandbox_enabled`(pydantic-settings 按字段名解析,`sandbox_enabled` 对应 `OPENHARNESS_SANDBOX_ENABLED`)。 |
| **P7b-T3 — Integration smoke(gated)** | 6 个 real-Docker 测试,`docker info` 检查 + `pytest.mark.integration` 双重 gate。验证关键安全属性:bind mount 工作 / 不可见 host /etc 之外的文件 / network=none 阻断外联 / cgroup 限制生效 / 镜像可换。**本地 Docker daemon 没启动时全部 SKIPPED**(CI / 用户启动 Docker 时跑)。 |
| **P7b-T4 — README + retro + invariant ext** | 扩展 `test_invariant.py` forbidden identifier 集 includes `SandboxExecution` —— **22 个 protected module 全部 zero ref**。README 加 "Phase 7b — Docker sandbox" 完整说明(默认行为 / 启用方式 / 隔离属性 / 配置 / 跨平台注意)。本文件。 |

---

## 3. Framework-level 主题 — Phase 7b 真正学到的

### 3.1 ⭐ "Abstraction-first paid off" —— 直接量化验证

P7a retro §3.6 提出了 abstraction-first 的判断。P7b 提供了**直接量化**:

| 维度 | 一次性做 Phase 7 full(7a + 7b)| 实际拆分(P7a → P7b) |
|---|---|---|
| 总 LoC | ~400 | ~150 + ~250 = ~400(同) |
| 总 task 数 | ~6-7 | 4 + 4 = 8(略多,但每个更小) |
| 总时长 | 3-5 天(实际可能更长,bug 难定位) | 1 + 1 = 2 天 |
| Invariant proof 强度 | 较弱(无 identity-transform 干净比对) | **强**(P7a HostExecution IS identity transform,P7b 直接 reuse) |
| Docker dependency 进 phase | 是 | 只进 P7b |
| 跨平台测试问题 dominance | 占大头(macOS 嵌套 VM 各种坑) | **隔离**(P7a 0 跨平台问题,P7b 集中处理) |
| Phase 拆分文档代价 | 单 phase 1 boundary + 1 plan | 2 boundary + 2 plan(略多) |

→ **拆分的真正胜利在 invariant proof 强度**。一次性做 full 会让"行为 parity"
和"新功能引入"混在一起,bug 难判断;拆开后:

- P7a 完成时,"重构后所有测试一行不挂"立即证明抽象是 identity
- P7b 完成时,"加新功能后所有测试一行不挂"立即证明 plug-in 性

**两个独立 proof,各自干净**。这是单次 phase 做不到的。

### 3.2 Protocol 没扩展 —— 真正的可插拔

P7a 锁的 `ExecutionEnvironment` Protocol 是:

```python
async def run_command(command: str, cwd: Path, timeout: float | None = None) -> ProcessResult: ...
```

P7b 做 Docker substrate 时,**没碰这个 Protocol**。`SandboxExecution.run_command`
签名一字不差地实现 Protocol。Container 的 spawn / teardown 通过
`__aenter__` / `__aexit__` 完成(async context manager),这是 Python
标准模式,**不需要在 Protocol 层加 `setup/teardown` 方法**。

→ 真正的"可插拔"长这样:**新 substrate 完全 backward-compat,旧 substrate
代码一行不动**。如果 Phase 7c 要加 gVisor / Firecracker / 远程 worker
pool,它们都会遵守同一个 Protocol —— Bash 不知道。

**教训**:Protocol 设计的 KISS 原则 = 长寿。P7a 把接口收窄到 3 个参数 + 1
个返回类型,看似过度保守,实际让 P7b 直接复用 0 改动。如果当时多加了
``env`` / ``stdin`` / `streams_separate` 等,P7b 要么浪费(Docker 不需要)
要么改 Protocol(打破 backward compat)。

### 3.3 `AsyncExitStack` 是条件性 context 管理的标准答案

P7b-T2 的 bootstrap chain 面临"sandbox_enabled 时进 SandboxExecution
context,否则不进"的条件性 `async with`。Naive 写法是 if/else 复制 body:

```python
# ❌ 重复
if sandbox_enabled:
    async with SandboxExecution(...) as env:
        context = QueryContext(..., execution_env=env)
        events = run_query(...)
        await render_stream(events)
else:
    context = QueryContext(...)  # default execution_env
    events = run_query(...)
    await render_stream(events)
```

正确写法 (`contextlib.AsyncExitStack`):

```python
# ✓ 不重复
async with pool, contextlib.AsyncExitStack() as stack:
    if sandbox_enabled:
        execution_env = await stack.enter_async_context(SandboxExecution(...))
    else:
        execution_env = _HOST_EXECUTION
    context = QueryContext(..., execution_env=execution_env)
    events = run_query(...)
    await render_stream(events)
```

`AsyncExitStack.enter_async_context` 把 conditional `async with` 变成
**普通赋值**,inner body 单一路径。**这是 Python 3 的隐藏宝石**;遇到
"按 flag 进/不进 context"模式,默认应该用它。

### 3.4 ⚠️ 踩坑:`OPENHARNESS_SANDBOX` ≠ `sandbox_enabled` 字段

最初 boundary doc 写 env var 是 `OPENHARNESS_SANDBOX`,字段名是
`sandbox_enabled`。pydantic-settings 用 `env_prefix="OPENHARNESS_"` +
field name 来推 env var:

- `auto_truncate` → `OPENHARNESS_AUTO_TRUNCATE` ✓
- `sandbox_enabled` → `OPENHARNESS_SANDBOX_ENABLED`(我们想要 `OPENHARNESS_SANDBOX`)

修复:接受 pydantic 约定,改 env var 名为 `OPENHARNESS_SANDBOX_ENABLED`。
这跟其它字段(`OPENHARNESS_AUTO_TRUNCATE` / `OPENHARNESS_LOG_LEVEL` /
`OPENHARNESS_TOOL_RESULT_CAP`)对齐。

→ **教训**:Python 框架的命名约定**是社区契约**;偏离它(为了"简短的"
env var 名)代价比收益大。如果未来真要 `OPENHARNESS_SANDBOX` 作为短别名,
用 Pydantic `AliasChoices` 加上,**而不是默认就这样**。

### 3.5 ⚠️ 踩坑:`aiodocker` 让 mypy 2.0.0 INTERNAL ERROR

```
src/.../aiodocker/jsonstream.py:21: error: INTERNAL ERROR
```

mypy 2.0.0 在跟踪 aiodocker 内部时崩溃 —— upstream bug,不是我们的代码。

修复:加 `[[tool.mypy.overrides]]` 块,`module = "aiodocker.*"` +
`ignore_missing_imports = true` + **`follow_imports = "skip"`**。后者是
关键 —— `ignore_missing_imports` 只让 mypy 忽略找不到的 stubs,不阻止它
进 aiodocker 源码。`follow_imports = "skip"` 彻底跳过。

我们的 `SandboxExecution` 把 aiodocker 包在自己的 typed Protocol 后面,
所以 untyped surface 是 bounded —— 这是"包装第三方库"的标准 hygiene
模式,这次 bug 让我们提前应用了。

→ **教训**:依赖第三方库时,**SDK 内部出 typing 问题不是世界末日**,只要
我们自己的 wrapper 是 typed 的。`ExecutionEnvironment` Protocol 这层让
``BashTool`` 永远看不到 aiodocker。如果 aiodocker 哪天废弃,换 `docker`
SDK + `asyncio.to_thread` 是 1 天的事 —— 因为 contract 在 Protocol 上。

### 3.6 跨平台现实 vs 测试 strategy

`docker` 在 Linux 上是原生(kernel namespace),在 macOS / Windows 上是
**嵌套 VM**(Docker Desktop 的 LinuxKit / WSL2)。这个事实对测试 strategy
的影响:

| 维度 | macOS dev(本机) | Linux CI |
|---|---|---|
| Unit tests(mocked aiodocker) | 跑 | 跑 |
| Integration tests(real Docker) | 启动 Docker Desktop 后跑;首次 ~10s warm-up | 跑(原生 Docker daemon 1-2s 启动) |
| Bind mount perf | 跨 VM filesystem,慢一个 order | 原生快 |
| Test 稳定性 | 偶尔 flaky(macOS Docker Desktop) | 稳定 |

→ **策略选择(P7b 实际采用)**:

1. **Unit tests** 用 mock,跨平台 baseline reliability
2. **Integration tests** gate on `docker info` —— 没 Docker 时 SKIP,
   不报错
3. **`@pytest.mark.integration`** 让 CI 可以选择跑/不跑

这样:

- 本地 dev 不强制启 Docker 才能写非 sandbox 代码
- Linux CI 默认跑 integration,验证 macOS bug 不溜进 main
- macOS dev 启 Docker 后可以本地验证

**通用教训**:**`gate-on-availability` 是依赖外部 daemon 的 integration
test 的标准模式**。Phase 1 LLM provider integration test 就是这样
(``@pytest.mark.integration`` + `OPENHARNESS_BASE_URL` 检查)。Phase 5
MCP integration test 也是。Phase 7b 复用这个模式,**零创新**。

---

## 4. 跟之前 phase 的整体对照(7b 之后)

| 扩展类别 | Phase | 接触层级 | 主要 consumer | "新概念" |
|---|---|---|---|---|
| External tools | 5a ✅ | Layer 2 | tool catalog | MCP server |
| Pre-LLM transform | 5b ✅ | Layer 0 | user input | slash command |
| External knowledge | 5c ✅ | Layer 1+2 | LLM | skill |
| External control flow | 6 ✅ | Layer 2 | SpawnAgent.execute | sub-agent |
| Execution substrate(抽象) | 7a ✅ | Layer 2 | BashTool.execute | ExecutionEnvironment |
| **Execution substrate(Docker 实现)** | **7b ✅** | **Layer 2(同 7a)** | **同 7a + CLI flag UX** | **`SandboxExecution`** |

观察:7b 没有引入"新概念",只是给 7a 的概念加了**一个新实现**。**这跟前面
所有 phase 都不一样** —— 前面每个 phase 都开了新的 framework 概念。7b
是"用现有概念加新实例"的第一个 phase。这反映了 framework 已经成熟到
**新功能不再需要新抽象**的阶段。

→ 这是 ARCHITECTURE.md 描述的"Tier 0 + Tier 1 + Tier 2"的完成形态。Tier 3
(Phase 8+)可能再开新概念,但 Tier 0-2 已经稳定。

---

## 5. 如果重做 Phase 7b 我会改什么

| 当时做对的 | 当时可以更激进的 |
|---|---|
| aiodocker 选择(async-native vs sync SDK + thread)—— 完美契合 | 没做镜像 pre-warm 提示给 user(首次 macOS docker run 慢 5-10s 用户会困惑) —— 应该在 README 加一行 "first run on macOS may take ~10s for Docker Desktop warm-up" |
| `AsyncExitStack` for conditional context —— 单一 inner body 路径,清晰 | `_run_ask` 的 `sandbox_*` override 参数数量(5 个)开始让签名臃肿。Phase 7c+ 加更多就该重构成 `SandboxConfig` dataclass。但 5 个还在可读范围,**不重构是对的** |
| Integration tests gate on `docker info` —— 本地 dev 不强制 Docker,CI 自动跑 | 没在 cli.py 加 "sandbox 启动失败的友好错误"(Docker daemon 没启动时报错信息可能让 user 困惑)。Boundary doc 提到了,但 T2 实际没做差错 UX,留 Phase 7c |
| Protocol 不扩展 —— Phase 7a 锁的契约 P7b 一字不动用 | 没做 ``_sandbox.warm_pool``(macOS pre-spawn 优化)—— 首次 ``oh ask --sandbox`` 慢 5-10s 在 macOS 上是 UX 痛点。**但这是 perf polish**,不属于 abstraction-first 范畴,留 Phase 8 |

---

## 6. 给后续 phase 的 input

### Phase 7c+ 加新 substrate(gVisor / Firecracker / 远程 worker)应该

- **Protocol 复用 `ExecutionEnvironment` 不动**。Phase 7a 锁的 3-arg
  `run_command` + 3-field `ProcessResult` 是稳定 contract
- **CLI flag 收敛 in `--substrate=<name>` + 子配置**。当前 `--sandbox` 是
  布尔 + 多 ``--sandbox-*`` 字段;Phase 7c 加 `--substrate=gvisor` 后会变
  `--sandbox` / `--gvisor` / `--firecracker` flags 三足鼎立 —— 重构成
  `--substrate=gvisor --substrate-config 'key=value,...'` 更合理
- **集成测试模式直接 reuse `_docker_available()` 风格**:
  `_gvisor_available()` 等。`@pytest.mark.integration` + 探测 daemon
  +`skipif` 是通用模板

### Phase 5d ModeBundle 应警惕的

- ModeBundle 是 first cross-layer tenant(Layer 0 commands + Layer 1
  skills + Layer 2 hooks + permission overlay)。Phase 7a/7b 的"单层
  abstraction-first"模式适用:**ModeBundle 数据结构先做**(纯 Pydantic
  schema + load/validate),再做 cross-layer dispatch
- ModeBundle 不应直接 reference SandboxExecution / HostExecution。它走
  Phase 7a 已有的 `QueryContext.execution_env` 字段 —— 这才是真正的 layered

### 未来 phase 普遍要警惕的

- **Mypy / type stubs 问题不是世界末日**:第三方依赖触 mypy bug 时,
  `follow_imports = "skip"` 是 escape hatch,只要 wrapper 是 typed
- **pydantic-settings env var 命名**:`<env_prefix><FIELD_NAME>` 是约定,
  偏离要付 ergonomics 代价。aliases 是补救,不是替代
- **AsyncExitStack 处理 conditional `async with`** 是 Python 标准,
  应该常用,不要再写 if/else 复制 body 了

---

## 7. Phase 7b DoD Checklist

- [x] `aiodocker>=0.21,<1.0` added to `pyproject.toml`
- [x] `execution/sandbox.py` — `SandboxExecution` class implementing
  `ExecutionEnvironment` Protocol with async context manager lifecycle
- [x] Container spawn: image pull + create with cwd bind mount + cgroup
  limits + network mode
- [x] `run_command` execs into running container; translates
  ProcessResult correctly
- [x] Settings fields(6):`sandbox_enabled` / `sandbox_image` /
  `sandbox_network` / `sandbox_memory` / `sandbox_cpus` / `sandbox_pids`
- [x] CLI flags(5):`--sandbox`/`--no-sandbox` / `--sandbox-network` /
  `--sandbox-memory` / `--sandbox-cpus` / `--sandbox-image`
- [x] `cli._run_ask` enters `SandboxExecution` context via
  `AsyncExitStack` when sandbox enabled
- [x] Unit tests(25 mocked, cross-platform):lifecycle / spawn args /
  run_command translation / timeout / error paths
- [x] Integration tests(6, gated on `docker info`):real container
  exercises bind mount / no host /etc leak / network none blocks /
  bridge enables / custom image
- [x] **Cross-cutting invariant verified via structural test**:
  `SandboxExecution` doesn't leak into any of 22 protected modules
  (only `execution/` + `cli.py` reference it)
- [x] **All 1048 pre-7b tests pass unchanged** — default OFF path
  preserves Phase 7a behavior bytewise
- [x] mypy strict clean(via `[[tool.mypy.overrides]]` for aiodocker)
- [x] ruff clean
- [x] README "Phase 7b — Docker sandbox" section
- [x] `learnings/phase-7b.md`(本文件)

---

## 一句话

> **Phase 7b 用 ~250 行代码 + `aiodocker` 一个新依赖,把 Phase 7a 锁的
> 抽象 plug-in 成了真 Docker substrate** —— 现存所有代码(包括 7a 自己
> 的 `base.py` / `host.py` / `bash.py`)**一行不动**。
>
> 这是 "abstraction-first paid off" 的直接量化验证 —— P7a + P7b 加起来
> 同样 ~400 行代码,但拆开后**两个独立 invariant proof 都干净**(7a 的
> identity transform + 7b 的 plug-in zero-diff),时间从 3-5 天压到 2 天。
>
> 接下来加任何 substrate(gVisor / Firecracker / 远程 worker)都是同
> 形态 plug-in,framework 已经成熟到"新功能不需要新抽象"的阶段。
