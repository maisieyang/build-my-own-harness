# Learnings — Phase 5e (plugin hook discovery via entry points)

> Phase 5e 起止 / 2026-05-17(单日,接 Phase 5d retro 后立即开启)
> 4 capabilities (P5e-T1…T4) / ~8 sub-units / 5 commits / ~290 行
> 生产代码(bundles/hook_plugins.py 190 + cli/settings/apply 接线 ~100)
> / 全 module coverage 95%+;total 97.16%
>
> 本文件回答的题:**做完 Phase 5e,关于"in-subsystem extension"和
> 5d 的 cross-layer composition 的对比 — 什么样的扩展属于"扩内"
> 什么样的属于"扩外",抽象边界的判断在哪里。**

---

## 1. 数据点

| 维度 | Phase 5d(cross-layer composition) | Phase 5e(in-subsystem extension) |
|---|---|---|
| Capability | 5 (T1-T5) | **4 (T1-T4)** |
| 生产代码量 | ~680 行(bundles/ 全包) | **~290 行**(hook_plugins.py 190 + 接线 100) |
| 改层数 | 4(prompts/tools/perms/hooks composed) | **0**(extension within bundles/) |
| Zero-diff 子系统 | 10(perms/hooks/engine/obs/mcp/compaction/skills/protocols/tools/execution) | **11**(再加 commands —— 5d 改了 commands/model.py +1 field,5e 不动 commands) |
| 触动 module | commands/model.py +1 field, commands/expand.py +1 helper, cli.py +bootstrap, bundles/ 全新 | **cli.py +flag+wire, config/settings.py +1 field, bundles/{__init__,hooks,apply,hook_plugins}.py** —— bundles/{model,store,registry,errors}.py 0 |
| Phase 新增 tests | ~74 | **~30** |
| Phase 修改后总 tests | 1158 | **1186 passed + 7 skipped** |

**关键观察**:Phase 5d 是「跨 4 层组合」,Phase 5e 是「在已有子系统内
增加一个 catalog source」。两个都是 zero-diff 其它层,**但抽象边界
的形状不一样**:

- 5d:Bundle 是新概念,要把 metadata 翻译成 4 个层的 primitive 修改 → 抽象点在"如何把组合表达成 factory"
- 5e:catalog 是已有概念(`BUILTIN_HOOKS` 在 5d 就有了),新增的是 catalog 的另一个来源(entry points)→ 抽象点在"如何在不动消费者的情况下扩 catalog"

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P5e-T1 — `bundles/hook_plugins.py`(discovery scaffolding)** | `HookSpec` frozen dataclass(event + hook)+ `hook_spec(event)` decorator + `discover_plugin_hooks(*, entry_point_source=None)` 纯函数。`entry_point_source` test seam(D20.6)让测试**完全不需要 pip install 真包**,duck-typed `_StubEntryPoint` + Protocol `_EntryPointLike` 让 mypy --strict 干净。skip-not-fail discovery(5 个 error path:load 抛 / 不是 HookSpec / 内置碰撞 / 插件碰撞 / 外层 metadata 抛)。 |
| **P5e-T2 — wire plugin catalog into `resolve_hook` + `apply_bundle_to_context`** | 两个签名各加一个 `plugin_catalog` / `plugin_hook_catalog` kwarg,default `None`。**Phase 5d 调用点零修改**(默认 None 保持 byte-identical)—— 这是"additive kwarg"模式的教科书应用。Built-in shadow plugin 在 `resolve_hook` 里**双重防御**:discovery 时已经过滤碰撞,resolve 时再 first-check-builtin —— 即使 discovery 被绕过(测试 bypass),built-in 依然 wins。 |
| **P5e-T3 — Settings + CLI + bootstrap** | `Settings.enable_plugin_hooks: bool = False`(env: `OPENHARNESS_ENABLE_PLUGIN_HOOKS`)+ `--enable-plugin-hooks` Typer flag + `_run_ask` 一行 conditional 调 `discover_plugin_hooks()`。**`monkeypatch.setattr(cli_module, "discover_plugin_hooks", ...)`** 是测试 seam —— CLI 模块级 import 让 monkeypatch 在 module attr 替换无需改 bundles/hook_plugins.py。`test_no_flag_overrides_env_var` 用"如果调用就 raise"的 sentinel **证明 off-path 不调 discovery**(default path 必须 cheap)。 |
| **P5e-T4 — invariant + README + retro** | `TestPhase5dCrossCuttingInvariant` forbidden set 加 3 个新 identifier(`HookSpec`/`hook_spec`/`discover_plugin_hooks`)—— **46 个 protected module 继续 zero ref**。Formal git-diff vs 5d close(`878d80a`):11 个 protected 目录全 0 行。README + 本文件。 |

---

## 3. Framework-level 主题 — Phase 5e 真正学到的

### 3.1 ⭐ "Additive kwarg" 是扩展已有 API 的正确形态

Phase 5e 要扩 `resolve_hook` 和 `apply_bundle_to_context` 两个 Phase
5d 的函数。三种可能形态:

| 形态 | 优点 | 缺点 |
|---|---|---|
| 改签名(positional arg) | 强迫 caller 升级 | 破坏所有现有 caller,测试要全改 |
| 改签名(keyword arg + 默认 None) | 现有 caller 零修改 | 函数 surface 变大,需要 doc 说清新 kwarg |
| 全新函数(`resolve_hook_v2`) | 旧 API 一行不动 | API surface 翻倍,长期 maintenance burden |

我选了第二种(additive kwarg)。**关键检查**:Phase 5d 写的 17 个
`apply_bundle_to_context` 测试 + 6 个 `resolve_hook` 测试 **没有一个
需要修改**就 GREEN。`test_default_none_preserves_phase_5d` 是显式
regression test,但即使没写,既有测试本身就证明了 backward-compat。

这是"how to extend a stable API"的标准答案,但**很多人会偷懒
直接改 positional**。值得记住的判断:**新 kwarg 是不是真的可选?
default 行为是不是真的等于旧行为?** 两个都 yes 才能 additive。

### 3.2 双重防御 —— discovery filter + resolve order

Phase 5d 的 `BUILTIN_HOOKS` 是 source of truth。5e 加 plugin catalog
要保证 plugin 不能覆盖 built-in。两个地方做防御:

1. **Discovery 时过滤** —— `discover_plugin_hooks` 检查 `ep.name in BUILTIN_HOOKS` 直接 skip。
2. **Resolve 时 first-check-builtin** —— `resolve_hook` 即使 plugin_catalog 里有同名 entry,也先返回 BUILTIN_HOOKS 的版本。

为什么两个?**defensive in depth**。第一道防御失效的场景:测试
直接构造 `plugin_catalog={"audit_log": ...}` 绕过 discover_plugin_hooks。
第二道防御保证即使如此,built-in 依然 wins。

这种"两道防御"在 framework 里很有价值 —— 单道防御一旦被 bypass
就 game over,双道防御让 bypass 一道不够。但**不要无脑铺**:每多一
道都增加测试 + 心智负担。本场景的双道是 by-design(测试自己就能
bypass discovery),不是 paranoia。

### 3.3 `entry_point_source` test seam —— 纯函数设计让插件系统可测

`discover_plugin_hooks(*, group="openharness.hooks",
entry_point_source=None)` 是纯函数。`entry_point_source` 是 keyword-
only 的 test seam(D20.6),production codepath 用默认值
`importlib.metadata.entry_points`,测试注入 stub。

**没有这个 seam 的代价**:测试要 `pip install -e` 一个 dummy 包,
declare entry points,然后 reload `importlib.metadata` —— 慢、脆,
而且依赖具体 Python version。

**有这个 seam 的好处**:12 个测试全部用 in-memory `_StubEntryPoint`
list,跑得快、跨版本稳定、能精确测 collision policy 的各种边界。

这条经验普适到任何插件系统:**discovery 函数永远暴露 source 注入
点**,不要在函数内部硬编码 stdlib 调用。

`_EntryPointLike` Protocol 是配套设计 —— production 收 stdlib
`EntryPoint`(satisfy Protocol structurally,`cast()` 让 mypy 接受),
测试塞 `_StubEntryPoint`(显式 satisfy Protocol)。

### 3.4 "Opt-in" 是 hook-like extension 的正确默认

5e D20.3 决定:plugin hook discovery 默认 OFF,需要 `--enable-plugin-hooks` 才打开。

理由不是"装了插件的用户也要再开一次"麻烦,而是 **plugin hook 的
blast radius**:

- 一个 plugin hook 能 deny/modify 任何 tool call(`HookResult.deny`)
- 一个 plugin hook 能在每次 tool 调用前后 inject 任意 side effect(网络、文件、数据库)
- transitive dependency 可能在用户不知情下 ship `openharness.hooks` entry point

对比 plugin TOOL(假设的 Phase 5f-ish):tool 只在 LLM 主动调用时才执行;hook 是 *自动* 注入到 dispatch loop 的。**自动注入 → 必须显式 opt-in**。

这条普适到所有 framework extension point:**如果 extension 会被
自动调用而不是按需调用,opt-in 是正确默认**。

### 3.5 In-subsystem extension vs cross-layer composition —— 抽象边界的不同形状

Phase 5d 是 cross-layer composition:Bundle 跨 4 层把 metadata 翻成
QueryContext primitive 修改。Phase 5e 是 in-subsystem extension:
catalog source 在已有 bundles/ 子系统内新增一个来源,**不跨层**。

两种"扩展"的抽象点不同:

| 维度 | Cross-layer composition(5d) | In-subsystem extension(5e) |
|---|---|---|
| 谁是新概念 | Bundle | Plugin Hook(catalog 的 source) |
| 谁是 factory / coordinator | Bundle 作为 QueryContext factory | 无 —— 扩 catalog 不需要 factory |
| 抽象 stress 在哪 | engine + perms + hooks + tool catalog 都必须**保持 minimal** | bundles/hooks.py 的 `resolve_hook` 必须**允许多 source** |
| 风险 | layered model 在跨 4 层时崩溃 | 已有 API 在扩 source 时破坏 backward-compat |
| 防御策略 | engine / perms / hooks 零 diff | additive kwarg + default = byte-identical regression |

**5d 验证了 Phase 3 的 layered model 站得住**。**5e 验证了 Phase 5d
自己的 hook catalog API 是 extendable 的**。两个都是 zero-diff,但
zero-diff 的对象不同 —— 5d 是"原有层零 diff",5e 是"原有 caller 零
调整"。

理解这层差别让 framework-builder 在面对新 phase 时能问对问题:
**这个 phase 是要跨层组合,还是要在某层内扩展?** 答案决定了
抽象点放哪、风险检查放哪。

### 3.6 Phase 5e 节奏对比

- 5d:2 天,5 capabilities,~680 行,因为是「第一次 cross-layer」
- 5e:1 天,4 capabilities,~290 行,因为是「在 5d 留好的扩展点上加 source」

5e 这么快 **不是因为 5e 简单**,是因为 5d 把扩展点(`BUILTIN_HOOKS`
dict + `resolve_hook` 函数)留得好:dict 加一个 lookup key 顺序,
函数加一个 kwarg,完事。

这是 framework design 的复利效应:**5d 多花一点时间把扩展点设计
对,5e 就能 1 天搞定**。如果 5d 把 `BUILTIN_HOOKS` 写成 hardcoded
if-elif 链,5e 就要先 refactor 再扩。

---

## 4. 5e 没做的(以及为什么)

| 不做 | 理由 |
|---|---|
| Filesystem hook plugins(`~/.openharness/hooks/*.py`)| Phase 5f。markdown-style discovery convention 在 commands/skills/bundles 已经验证,加 `*.py` 是同形;但代码执行 surface 比 markdown 大,defer 到真有需求(MVP 是 entry points + opt-in,够了)。 |
| `HookSpec` 加 description / version / source 字段 | YAGNI。Python docstring 已经做 description,`pyproject.toml` 已经做 version,entry point `value` 已经做 source。**Additive 拓展**是 non-breaking(default None),Phase 8 catalog UI 才有载体,那时候加。 |
| Per-bundle plugin scoping(`bundle.plugin_hooks: [hook_name]`)| YAGNI。当前 plugin 一旦 discovery enabled 就全局可见,bundle 引用即生效。"只让这个 bundle 看到 plugin_X"是过度设计,真需要可以用唯一命名(`my_bundle_specific_hook`)绕。 |
| Plugin hot reload | `oh chat`(Phase 7+)才有意义。`oh ask` 单次 invocation 不需要。 |
| 真插件 smoke(pip install 真包 + 跑) | Captured-context 测试 + sentinel test 已经验证 discovery 路径 + 不调路径都对。手动 smoke 留给用户(README 的 plugin author workflow section 是 self-contained)。 |
| 反向 precedence(plugin shadow built-in) | D20.4 决定。详见 §3.2;malicious plugin 能 silently 替 `audit_log` 注入 no-op → 关掉 compliance trace,blast radius 太大。 |

---

## 5. 给下一阶段的人(可能还是我)

- **Phase 5f filesystem hook plugins** 如果做:形态参考 5b/5c/5d 的 markdown convention。代码执行 surface 比 markdown 大,opt-in 默认 OFF 同 5e。`@hook_spec` 装饰器复用,只是从 entry point load 变成 `importlib.util.spec_from_file_location` load。
- **Phase 8 markdown_store/ 抽公共模块**(5b/5c/5d 三次重复)依然是第一候选。`hook_plugins` 是 Python file discovery 不在这个抽象内,不是 markdown 的。
- **Plugin hook catalog 现在没有 UI** —— 用户不知道 enable 后到底加载了什么。Phase 8 可以加 `oh hooks list` 子命令打印 BUILTIN_HOOKS + plugin catalog(name / event / description-from-docstring)。
- **Plugin hook 超时保护**:目前 plugin hook 跑慢会拖整个 dispatch chain。Phase 8 可以加 per-hook timeout(decorator kwarg:`@hook_spec("PreToolUse", timeout=5.0)`)。但**不要默认开** —— 大多数 hook 是同步快速的;timeout 反而引入新 failure mode。
- **`entry_point_source` Protocol 命名考虑**:目前叫 `_EntryPointLike`(private),如果将来 plugin tool 也用类似 pattern,可以把 Protocol 提到 `bundles/_discovery.py` 公共模块。但**只在第二个 caller 出现时再抽**(rule of three 还差一次)。

---

> **本 Phase 一句话总结**:
>
> Phase 5d 留好的 `BUILTIN_HOOKS` + `resolve_hook(name)` 扩展点,
> 5e 加了一个 kwarg + 一个新 module + 一个 Settings flag,就把
> "framework 扩 catalog" 这件事做完。**Cross-layer composition 验证
> layered model,in-subsystem extension 验证 catalog API 自身的可拓展
> 性**。两个 zero-diff 模式,不同抽象 stress。
