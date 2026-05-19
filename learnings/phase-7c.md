# Learnings — Phase 7c (gVisor sandbox runtime)

> Phase 7c 起止 / 2026-05-19(单日,接 Phase 5f retro 后开启)
> 3 capabilities (P7c-T1…T3) / 2 commits / **~30 行生产代码**
> + 1 个 Settings 字段 + 1 个 CLI flag / 8 unit tests + 1 gated
> integration smoke / coverage 持平
>
> 本文件回答的题:**runtime 选择作为 SandboxExecution 的 kwarg
> 而不是新 substrate 类的判断 —— 什么时候该子类化、什么时候该
> 加 kwarg。**

---

## 1. 数据点

| 维度 | Phase 7b(Docker substrate,新类) | **Phase 7c(runtime kwarg,加字段)** |
|---|---|---|
| Capability | 4 | **3** |
| 生产代码 | ~250 行 + 200 行 SandboxExecution 主体 | **~30 行**(1 kwarg + 1 HostConfig 字段 + 1 Settings + 1 CLI flag) |
| 新 substrate 类 | 1(`SandboxExecution`)| **0**(kwarg 加到现有类) |
| 新 Settings 字段 | 6(image / network / memory / cpus / pids / enabled)| **1**(runtime) |
| 新 CLI flag | 5 | **1** |
| 改动其他层 | 0 | **0** |
| 新测试 | ~50 | **9**(8 unit + 1 gated integration) |
| Phase 修改后总 tests | 1061 | **1230** |
| 时间 | 1-2 天 | **半天** |

**关键观察**:Phase 7c 是 Phase 7b 的「同 substrate, 不同 OCI runtime」拓展。
**新代码集中在 Docker container 创建 RPC 的一个字段**;framework
其他部分 0 改动。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P7c-T1 — `SandboxExecution.runtime` kwarg** | `__init__` 加 `runtime: str = "runc"`;`HostConfig` 字典加 `"Runtime": self._runtime` 一行。**D23.2 决定不做 client-side allowlist** —— `Literal["runc", "runsc"]` 锁死了 future runtime(kata / sysbox / 自定义)。3 个 mock test:default runc / explicit runsc / 任意字符串 passes through。 |
| **P7c-T2 — Settings + CLI flag + bootstrap** | `Settings.sandbox_runtime: str = "runc"` + `--sandbox-runtime` Typer option(无 Choice 约束,同 D23.2)+ bootstrap `runtime=sandbox_runtime` 传入 SandboxExecution。**踩了 P7b 既有测试的坑** —— 测试用的 `_FakeSandbox.__init__` 列出所有 kwarg(没用 `**kwargs`),加 runtime 后必须显式加 param。改了 1 行测试 fixture。 |
| **P7c-T3 — Real-gVisor smoke + README + retro** | `_gvisor_available()` 检查 `docker info | grep runsc`,双重 gate(daemon reachable AND runsc registered)。**用户机器没装 gVisor 时 SKIP,不 FAIL** —— 同 7b 的 docker available 模式。README 加 7c section + 本文件。 |

---

## 3. Framework-level 主题 — Phase 7c 真正学到的

### 3.1 ⭐ Kwarg vs 新类:看 behavior delta 大小

最初的反射:「Docker runtime 不同 → 应该有 `GVisorExecution` 类」。
冷静一下:

`runc` vs `runsc` 的 behavior delta:
- 容器创建 RPC 多一个 `Runtime` 字段
- 容器内 syscall path 不同(内核 vs sentry)—— 但**这是 host kernel
  的事,framework 视角看不到**
- ExecutionEnvironment Protocol 接口(`__aenter__` / `__aexit__` /
  `run_command`)100% 相同

→ subclass 会让每个 method 都 override 一行就为了改这一个字段,**code
duplication 不对称于 behavior delta**。

**判断 framework**:

| 情形 | 选 subclass | 选 kwarg |
|---|---|---|
| Behavior 整体不同(method 行为变了) | ✓ |  |
| 单点配置不同(一个字段值变了) |  | ✓ |
| 类型识别需要(`isinstance` 判别) | ✓ |  |
| 工厂模式需要选择实现 | ✓ |  |
| 同接口同实现,只是参数化 |  | ✓ |

Phase 7c 落在最后一行:同 Protocol、同 substrate 整体实现、只有
HostConfig 一个字段不同。Kwarg。

跟 Phase 5e/5f 的对比:5e 加 entry-point discovery,5f 加 filesystem
discovery —— **两种 discovery method 整体不同**(`importlib.metadata`
vs `importlib.util.spec_from_file_location`),所以 5f 加新函数
`discover_filesystem_hook_plugins`,而不是给 `discover_plugin_hooks`
加 source kwarg。

### 3.2 不做 client-side allowlist —— 让 Docker 做权威验证

`--sandbox-runtime <runtime>` 用 Typer 的 plain `str` option,不是
`Literal["runc", "runsc"]`(Choice 约束)。

理由:
- **未来 runtime 多**:kata、sysbox、kata-cc、gVisor variants、新出的
  WASM runtime。Literal 锁死了今天能想到的;framework 没办法不发新版本
  就支持新 runtime。
- **Docker 是权威**:用户系统装了什么 OCI runtime,只有 daemon 知道。
  Framework 二次验证是冗余 + 容易过时。
- **错误 UX 同样好**:不识别的 runtime → Docker daemon 抛
  `Unknown runtime: foo`,在 framework 的 SandboxSetupError path
  surface 出来,用户看到明确错误。

**通用经验**:**delegate validation to the authoritative system**。
Framework 做 validation 的成本是「保持 list 跟 upstream 同步」,收益
是「错误信息提前几毫秒」—— 一般亏的。

Compare Phase 7b 的 `sandbox_network` 字段:为什么那个用了
`Literal["none", "bridge"]`?因为 Docker 接受任何字符串(包括
`host`),但 framework 想故意限制(`host` 暴露 host 网络栈,违背
sandbox 目的)。**那里是 framework 加价值,这里 framework 不加价值**。

### 3.3 Doubly-gated integration smoke —— 嵌套 skip 是干净的

Phase 7b 的 integration smoke 顶部加 `pytestmark` skip docker:

```python
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not _docker_available(), ...)]
```

Phase 7c 的 gVisor smoke 需要 **doubly gated**:docker reachable
AND runsc registered。两种方法:

1. 合并到 module-level `pytestmark`(影响所有 test)—— 不行,7b 的
   non-runsc test 在没 gVisor 的机器上应该照常跑
2. **class-level** `pytestmark`(只影响该 class)—— ✓

```python
class TestGVisorRuntime:
    pytestmark = pytest.mark.skipif(
        not _gvisor_available(),
        reason="gVisor (runsc) not installed",
    )

    async def test_runsc_runs_echo(self, tmp_path):
        ...
```

Class-level pytestmark 跟 module-level pytestmark 累加 —— 一个 test
被 skip 当任一条件 skip 它。

**通用经验**:integration smoke 应该「按 capability 分层 gate」。
Docker 是 P7b 的;gVisor 是 P7c 的;`OPENAI_API_KEY` 是 CLI integration
的。每层独立 skip,用户的 partial environment 也能跑相关部分。

### 3.4 Pre-existing tests 的 over-specification 是 refactor 的隐性 cost

T2 改 CLI bootstrap 把 `runtime=sandbox_runtime` 传入,P7b 既有的
`_FakeSandbox.__init__` 显式列了 6 个 kwarg(没用 `**kwargs`):

```python
class _FakeSandbox:
    def __init__(self, *, cwd, image, network, memory, cpus, pids):
        captured_init.update(cwd=cwd, image=image, ...)
```

加 runtime 后必须改这个 fixture —— 加 `runtime` 到 param list + 加
到 `captured_init.update`。一个 line 改动,但 cognitive cost > 0。

如果当初 `_FakeSandbox.__init__(self, **kwargs)` + `captured_init.update(kwargs)`,
现在零修改。

**通用经验**:test fixture 的 `**kwargs` 比显式 listing 更适合
forward-compat。失去的是「fixture 文档明确列出当时的接口」 ——
但 spec 已经在 production code 里,fixture 重复列就是 over-specify。

(这跟 P5e 的 additive-kwarg 设计哲学是同源:**API 的 extension
shouldn't require caller-side change**。fixture 是 caller。)

### 3.5 Phase 7c 节奏极快 = 7a/7b 抽象的红利

P7c 半天搞定;P7b 1-2 天搞定。**为什么 P7c 又快了一半?** 因为 P7a
和 P7b 已经做好:

- ExecutionEnvironment Protocol(7c 不动)
- SandboxExecution 主体类(7c 加 1 kwarg + 1 HostConfig 字段)
- aiodocker async 流(7c 完全复用)
- Settings + CLI flag pattern(7c 加 1 个 mirror 现成 5 个)
- Bootstrap chain `AsyncExitStack`(7c 不动)
- Integration smoke skip-on-missing-runtime pattern(7c 复用 + 加
  一层 nested skip)

**新 LoC ~30,新测试 9 个。这是「framework abstraction 复利」的极致**。
Phase 7a 当初做 Protocol-based substrate(D17.x)看似 over-engineer,
**7c 时已经 pay off 三次**(7a HostExecution 是 identity transform、
7b runc 是 substrate impl、7c runsc 是 runtime variation)。

### 3.6 「gVisor 没在用户机器上」不是问题 —— SKIP 不 FAIL

P7c-T3 写的时候我自己没装 gVisor。3 选 1:

1. **不写 real test**(只 mock) —— framework 没有 end-to-end 验证
   gVisor 实际工作
2. **写 + 强制跑** —— CI 必须装 gVisor;local dev 也必须
3. **写 + gated SKIP** —— 装了 gVisor 的机器 / CI 跑;没装就 SKIP

选 3。这跟 P7b 的 Docker daemon gate 同形:**framework 的 integration
test 不应该假设用户机器有什么**。开发者跑 unit test 就够;有 gVisor
的环境跑 integration 验证。

**通用经验**:framework code 的 acceptance check 应该有两层:

1. **Unit test(mocked)** —— 永远跑,验证 framework 接线对
2. **Integration test(real environment, gated)** —— 装了真东西的人
   跑,验证 framework 跟真东西配合对

第 2 层 SKIP 不是测试 fail。设计 integration test 的时候,SKIP 的
reason 字符串要够 informative,让 someone 看到 SKIP 后能 retry
环境配置后再跑。

---

## 4. Phase 7c 没做的

| 不做 | 理由 |
|---|---|
| Firecracker substrate(microVM,完整 kernel 隔离) | 不是 OCI runtime swap,要新 substrate class(Docker daemon 不管 Firecracker)。Phase 7d 候选。 |
| gVisor 性能基准 | ~3x syscall 开销是 gVisor docs 数据;framework 不重做基准。 |
| 自动检测 host gVisor 并切默认 | 用户可能装了 gVisor 但不想用它跑 framework(开发场景 / 性能敏感)。**默认 runc 不变**(D23.4)—— 用户 explicit opt-in `runsc`。 |
| `--sandbox-runtime` 的 shell completion | Typer 不自动 generate Choice completion(因为没 Literal);用户记不住 runtime 名 → 看 `--help` 提示。够了。 |
| runsc 不支持的 syscall 检测 | gVisor docs 列了 partial support;framework 不维护这个 list。用户碰到 missing syscall 错误自己 debug。 |

---

## 5. 给下一阶段的人

- **Phase 7d Firecracker** 如果做:形态完全不同。Firecracker 不是
  Docker daemon 的 OCI runtime —— 它是独立 VMM 进程,通过 JSON API
  控制。需要新 substrate class `FirecrackerExecution` 实现
  `ExecutionEnvironment` Protocol;不能复用 SandboxExecution 主体。
  ~boot time penalty ~125ms,memory overhead ~5MB/microVM —— 对延迟
  敏感场景慎用。
- **gVisor sub-features**:`--platform=ptrace`(慢但兼容)vs
  `--platform=kvm`(快但要内核支持)。可以加 `sandbox_runtime_args:
  list[str]` kwarg,passed to Docker as `RuntimeArgs`。**等真有用户
  需求再加**。
- **OCI runtime catalog UI**:`oh sandbox list-runtimes` 子命令,跑
  `docker info | grep Runtimes` 然后输出。Phase 8.5 候选(同
  `oh hooks list`)。
- **Runtime 在 Bundle 里 override**:理论上 bundle frontmatter 可以
  指定 `sandbox_runtime: runsc`,让某个 mode 强制 gVisor(对应「这个
  code-review mode 跑陌生代码要更安全」)。**架构上能做**,Phase 5d
  的 bundle.deny_paths 同 pattern。但需求未浮现,YAGNI。

---

> **本 Phase 一句话总结**:
>
> Runtime selection 在 OCI 层只是 HostConfig 一个字段,**所以
> kwarg 不是新 class**。Framework 委托 Docker 做 runtime 验证(不
> 加 client-side allowlist)—— let the authoritative system decide。
> Doubly-gated integration smoke(daemon reachable AND runsc
> registered)是 environment-dependent test 的正确分层模式。
> P7a/7b 的抽象红利让 P7c 半天完事。
