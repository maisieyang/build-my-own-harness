# Learnings — Phase 5f (filesystem hook plugins)

> Phase 5f 起止 / 2026-05-19(单日,接 Phase 8 retro 后开启)
> 3 capabilities (P5f-T1…T3) / 4 commits / ~190 行生产代码
> (bundles/hook_plugins.py 扩展)+ ~30 行 cli wiring / 15 新增测试
> / coverage 97%+
>
> 本文件回答的题:**5e 的 entry-point discovery 已经设计好可扩展
> 的形状,5f 加 filesystem source 实际成本是多少;5e+5f 这两次 discovery
> 落地后,framework "extensibility 抽象"的真正形状是什么。**

---

## 1. 数据点

| 维度 | Phase 5e(entry-point discovery) | **Phase 5f(filesystem discovery)** |
|---|---|---|
| Capability | 4 | **3** |
| 生产代码 | ~290 行 | **~190 行**(60% — 因为 5e 留好了所有 plumbing) |
| 新 Settings 字段 | 1(`enable_plugin_hooks`) | **0**(D22.2 复用 5e flag) |
| 新模块文件 | 1(`hook_plugins.py`) | **0**(扩 5e 的同一个文件) |
| 改动其他层 | 0 | **0** |
| 新测试 | ~30 | **15**(12 discovery + 3 CLI integration) |
| Phase 修改后总 tests | 1186 | **1223** |
| Coverage | 97.16% | **~97%** |
| 时间 | 1 天 | **半天**(5e 的复用让 5f 是 incremental work) |

**关键观察**:Phase 5f 是 Phase 5e 的「同源不同 source」拓展。5e 设
计 `discover_plugin_hooks` 时已经把 `dict[str, HookSpec]` 当作公共
catalog 格式,5f 只是加另一个 catalog producer。**新 discovery source
的成本是 5e 的 60%**,因为所有 downstream wiring(resolve_hook /
apply_bundle_to_context / CLI 接 catalog)在 5e 时已经做好。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P5f-T1 — `discover_filesystem_hook_plugins` + `_default_module_loader`** | `importlib.util.spec_from_file_location` 读 `*.py`,SHA-8 前缀避免 global/project 同名文件冲突,`sys.modules` 临时注册然后清理(避免污染后续 import)。`module_loader` test seam(D22.6)是 5e `entry_point_source` pattern 的复用 —— 测试塞 stub `_StubModule` 实现 `vars()` / `getattr()` duck-typing,完全不碰真文件系统。 |
| **P5f-T2 — CLI bootstrap merges filesystem catalog** | CLI 在 `if enable_plugin_hooks:` 块里加 4 行 —— 调 `discover_filesystem_hook_plugins(global_dir, project_dir)` 后 first-wins 合并进 `plugin_hook_catalog`。**entry-point 先填,filesystem 后填,collision 时 entry-point 赢**(D22.4 — packaged plugin 是更明确的意图表达)。3 个 integration test:filesystem-only / entry-point shadows filesystem / 关掉 flag 后 sentinel 两个 discovery 都不调。 |
| **P5f-T3 — invariant + README + retro** | Phase 5d 的 forbidden identifier 加 `discover_filesystem_hook_plugins`(46 个 protected module 继续零 ref)。Formal git-diff vs Phase 8 close:9 个 protected dir 全 0 行,`config/settings.py` 0 行(没新 Settings 字段)。README 加 5f section。本文件。 |

---

## 3. Framework-level 主题 — Phase 5f 真正学到的

### 3.1 ⭐ Source-agnostic catalog 是 extensibility 的正确抽象

Phase 5e 决定 `dict[str, HookSpec]` 作为 catalog 格式时(D20.5),没
有特别强调「这是 source-agnostic」—— 因为当时只有一个 source(entry
points)。

Phase 5f 加 filesystem source 时**完全不动 catalog 格式 + resolve 路径
+ apply 路径**。这意味着 5e 当时无意识地做对了一件关键设计:**discovery
function 的 output 类型独立于 discovery method**。

类比 unix philosophy: text streams as universal interface。这里的
universal interface 是 `dict[str, HookSpec]`。任何 future source —
remote registry / database / git repo / encrypted store — 都只需要
产出这个 dict,就能接入。

**framework-builder 经验**:当一个 phase 引入 catalog-style data
(dict / list / map)时,先问自己「这个 catalog 还能有多少 producer?」
如果答案是 1+ 个未来可能的 producer,catalog 类型就要 source-agnostic
—— **不要把 producer-specific 字段塞进 catalog**。

5e 的 `HookSpec` 只有 event + hook(没有 `source: Literal["entry_point",
"filesystem"]`)。这是对的。5f 来了也不需要加。如果加了,resolve 路径
就要处理这个字段(忽略它?用它做日志?),所有下游 caller 都被绑死。

### 3.2 sha8 模块命名 —— 避免 sys.modules 命名冲突的便宜手法

不同目录下的同名文件(`~/.openharness/hooks/audit.py` +
`<cwd>/.openharness/hooks/audit.py`)如果直接用 `audit` 作模块名,
第二次 `exec_module` 会 reload 第一个,导致 global 版本的 hooks 失效。

解决:`module_name = f"openharness._user_hook_{sha8}_{stem}"`,sha8 =
绝对路径的 SHA-256 前 8 hex。

为什么 sha8 不是 sha16/full hash?:
- collision 概率 1/16⁸ ≈ 4×10⁻¹⁰ — 用户机器上同时存在 10⁹ 个 hook 文件
  也不会撞(理论容量)。
- 模块名短 → debug error message 易读

为什么是绝对路径不是相对路径?:
- 不同 cwd 下跑同一份 hook 文件,绝对路径稳定,sha8 稳定
- 用户 symlink 多个项目共享 hook,绝对路径 resolved 后唯一

为什么 `sys.modules.pop` 而不是让模块永驻?:
- 永驻会污染后续 `import` —— 用户的项目代码可能 `import audit`,
  本来 should fail 的 import 现在意外成功
- 临时注册只是为了 module 内部的相对 import / `from foo import bar`
  能 resolve;exec_module 完成后立即清理

**通用经验**:filesystem-loaded 模块永远要考虑 sys.modules 注册周期。
默认行为(永驻)是过度承诺。

### 3.3 sentinel test "function should NOT be called" 是关键 fallthrough check

`test_filesystem_not_loaded_when_flag_off` 测试:flag off 时,既不
调 `discover_plugin_hooks` 也不调 `discover_filesystem_hook_plugins`。

实现方式:`monkeypatch.setattr` 把这两个函数替换成 `raise AssertionError(...)`
的 sentinel。如果 CLI 误调了任一,测试立即失败。

**这种 negative path test** 比 "assert state unchanged" 强:
- "state unchanged" 可能因为函数被调但 result 空而看不出来
- "function not called" 直接断言 path 没走

cost:一个 lambda 抛 AssertionError。换来对 "default code path 不
做 work" 的强保证。

普适到:**默认 OFF 的功能**应该有这种 sentinel test 验证 "off 路径
真的什么都不做"。否则 default OFF 是 "default does nothing visible",
不是 "default does no work" —— 这两个不一样。

### 3.4 One flag enables both sources 是 trust boundary 的正确划分

5f boundary D22.2 决定:不加新 Settings 字段,复用 `enable_plugin_hooks`
同时开 entry-point 和 filesystem 两个 source。

候选反对意见:**用户可能想 "trust entry points but not filesystem"**
(npm-style "I trust pip's review process but not random files")。

但这个论点站不住:
1. **entry point 的 review process 也很弱** —— PyPI 是任意上传,没强制
   review。装一个包 = 信任任意作者
2. **filesystem 是 USER's own machine** — 文件就在 `~/.openharness/hooks/`
   下,用户(理论上)知道它存在
3. **真正的 attack surface 是「a hook 能 deny/modify any tool call」**,
   两个 source 都满足这个;trust boundary 是「I let plugin hooks run」,
   不是「我信哪个 source」

如果将来真有「能 disable 单一 source」需求,加 `enable_filesystem_hooks
: bool` 是 additive。但**MVP 不该 ship 第二个 flag** —— 用户认知负担
double 没收益。

**通用经验**:opt-in flag 的粒度应该跟 trust boundary 对齐,不是跟
实现 mechanism 对齐。

### 3.5 Phase 5f 的开发节奏极快 = 5e 设计红利

5e 用 1 天。5f 用半天。**为什么这么快?** 因为 5e 时已经做好:

- `HookSpec` 数据类 + `hook_spec` 装饰器(5f 不用重新设计)
- `discover_*` 函数签名(5f 复制 + 改 source)
- `resolve_hook(plugin_catalog=...)` 接 catalog 的 path(5f 不用改)
- `apply_bundle_to_context(plugin_hook_catalog=...)` 同上
- `Settings.enable_plugin_hooks` flag + CLI flag + bootstrap(5f 不动)
- Test seam pattern(`module_loader` 跟 `entry_point_source` 镜像)

5f 实际新代码集中在:`discover_filesystem_hook_plugins` 函数体 +
`_default_module_loader` 辅助 + 15 个测试。

**这是 framework design 的复利效应**:5e 多花的设计时间(decorator
pattern / test seam / catalog API)在 5f 时全部 pay off。如果 5e
偷懒把 `discover_plugin_hooks` 写成硬编码 + 没 test seam,5f 就要
先重构 5e。

**给下一个 phase 的建议**:遇到「这是 future extension 的扩展点」感
觉时,**多花 30% 时间把扩展形状想清楚**。第二次扩展时省下来的时间
远超过。

---

## 4. Phase 5f 没做的

| 不做 | 理由 |
|---|---|
| Plugin sandboxing | hook 跑在主进程,有任意 host 权限。真要 sandbox 是 Phase 7c-style 子进程隔离,复杂度高,defer 到有具体威胁模型时 |
| `*.py` hot reload | 一次性 bootstrap 是 OK 的;`oh chat` Phase 才需要 reload(同 5e 论点) |
| Plugin metadata catalog UI(`oh hooks list`) | 没有 list 命令的载体;Phase 8.5 候选 |
| 分别控制 entry-point / filesystem 的 flag | §3.4 — YAGNI |
| `.py` 文件的语法 lint / type-check 通过才加载 | 工作量大;skip-not-fail 已经处理 syntax error;用户自己负责 hook 文件质量 |

---

## 5. 给下一阶段的人

- **Phase 7c gVisor / Firecracker substrate** 跟 5f 完全独立(execution
  layer,跟 plugin discovery 无关)。如果要做 hook plugin sandbox,
  形态是 "subprocess-based hook execution"(IPC 跨进程边界),需要
  serialize HookResult。**不是** gVisor 那种 container sandbox。
- **`oh hooks list` / `oh hooks describe <name>`** 是 Phase 8.5
  好候选。catalog source 已经统一,UI 只需 iterate `BUILTIN_HOOKS`
  + plugin catalogs,打 description-from-docstring。
- **Plugin metadata 加 `description` / `version` 字段**进 `HookSpec`:
  additive kwarg,backwards-compat。但 **等真有 catalog UI 再加** —
  没 UI 之前,字段只是 dead weight。
- **filesystem hook 的 watch / reload**:`watchdog` lib 监听
  `~/.openharness/hooks/`,变化时 invalidate 当前 query 的 catalog。
  非平凡(catalog 是 frozen-per-query 的);`oh chat` 出来时再做。

---

> **本 Phase 一句话总结**:
>
> Phase 5e 留好的 source-agnostic catalog 格式让 5f 加新 source 变成
> incremental work。**第二个 discovery source 的成本是第一个的 60%**,
> 因为 catalog 格式 + downstream wiring 都不动。Framework design
> 的复利效应 —— 第一个扩展点设计好,后续扩展便宜得不成比例。
