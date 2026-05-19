# Learnings — Phase 8 (`markdown_store/` extraction)

> Phase 8 起止 / 2026-05-19(单日,接 Phase 5e retro 后立即开启)
> 5 capabilities (P8-T1…T5) / ~7 commits / 新增 ~300 行(markdown_store/)
> + 删除 481 行(commands/skills/bundles 三个 domain 的 duplicate)
> = 净 ~-180 行 / 全 module coverage 95%+;total 97.28%
>
> 本文件回答的题:**rule-of-three 触发的 refactor,实际操作中
> 的"API-level zero-diff" invariant 是什么形状,跟前面 phases 的
> "layer zero-diff" invariant 的差别在哪。**

---

## 1. 数据点

| 维度 | Phase 5e(in-subsystem extension) | **Phase 8(rule-of-three refactor)** |
|---|---|---|
| Capability | 4 | **5(markdown_store/ scaffolding + 3 domain refactor + invariant)** |
| 生产代码新增 | ~290 行 | **~300 行**(markdown_store/) |
| 生产代码删除 | 0 | **~481 行**(三个 domain 重复) |
| 净 LoC | +290 | **-180** |
| Invariant 形状 | 已有 layer 零 diff | **API-level 零 diff(public class names + log event names + 测试不改)** |
| 已有 tests 是否改 | 0 | **0**(233 个 domain test 一行不改通过) |
| Phase 修改后总 tests | 1186 | **1206**(+20 markdown_store/ 新测试) |
| Coverage | 97.16% | **97.28%** |

**关键观察**:Phase 8 跟 5a-5e 都是 zero-diff,**但 zero-diff 的对象不
同**。前面 phase 是「新功能不改其他层」,Phase 8 是「内部重构不改
caller」。前者保护横向 layer 隔离,后者保护纵向 API 稳定。

---

## 2. 每个 task 的 1-line takeaway

| Task | 一句话总结 |
|---|---|
| **P8-T1 — `markdown_store/` foundation** | 三个文件 + 20 个测试:`constants.py`(NAME_PATTERN + FRONTMATTER_FENCE)/ `parse.py`(split + read_frontmatter_dict 两个纯函数)/ `store.py`(`MarkdownDocument` Protocol + `FilesystemMarkdownStore[T]` generic + `EmptyMarkdownStore[T]`)。**Protocol 用 `@property`-style declaration** 是关键 —— frozen dataclass 的 attribute 在 mypy --strict 下不满足 mutable-attribute Protocol,但满足 `@property` 只读 Protocol。 |
| **P8-T2 — refactor `commands/`** | 79 + 64 = ~140 行删,~50 行新(import + thin subclass)。**80 个 commands test 零修改通过**。`parse_command` 现在是 `read_frontmatter_dict` + 域特定字段提取(`mode`)+ 二次构造保护 —— 函数体从 100 行降到 50 行,domain-specific 部分清楚可见。 |
| **P8-T3 — refactor `skills/`** | 同 T2 形态。`parse_skill` 保留 `version` 字段的 int→str coercion。**138 个 skills+commands test 零修改通过**。 |
| **P8-T4 — refactor `bundles/`** | 同形态,但 Bundle 的 field 多(4 层 override),所以 domain-specific 部分还是较长(~60 行)—— 这是 expected,Bundle 本身复杂度就在那。**233 个 commands+skills+bundles test 零修改通过**。 |
| **P8-T5 — invariant + README + retro** | `TestPhase8MarkdownStoreInvariant`(26 protected modules 全 0 ref 8 个 forbidden identifier)+ inverse test(三个 consumer 必须 import markdown_store)+ formal git-diff vs 5e close 验证 9 个 protected dir 0 行。README + 本文件。 |

---

## 3. Framework-level 主题 — Phase 8 真正学到的

### 3.1 ⭐ "API-level zero-diff" 是 refactor 的正确 invariant 形状

Phase 5a-5e 的 zero-diff 是「不改其他 layer」—— 因为每个 phase 都是
**新增**功能,所以保护对象是横向边界。

Phase 8 是**纯内部重构**,定义上要动代码,横向 zero-diff 没意义。
正确的 invariant 是:

1. **Public API byte-identical** —— 类名、函数名、签名、返回值 shape 都不变
2. **Log event 名 byte-identical** —— `jq` 消费者 / dashboard / 已部署的告警规则不破
3. **既有测试零修改通过** —— 任何测试需要改,就说明 invariant 已经破了

第三条是最强的 acceptance check。Phase 8 起手就定了「不改测试」,
T2/T3/T4 任何一步如果测试需要改,立刻知道 refactor 偏了 —— 实际上
没有一个测试需要改,233 全部通过。

**这是「自动化判断 refactor 对不对」的最便宜机制**。比 mypy 还便
宜(mypy 只看类型,不看行为)。Phase 8 的真正成本是「让测试不改」
这条约束 forced 我设计 backwards-compat 的接口(`logger_name` kwarg
传递、subclass-for-naming pattern)。

### 3.2 Subclass-for-naming pattern —— 保留公开类名的最便宜方式

`FilesystemMarkdownStore[T]` 是 generic,直接暴露给 caller 会让代码
看起来像:

```python
store: FilesystemMarkdownStore[Command] = FilesystemMarkdownStore[Command](
    global_dir=..., project_dir=..., parser=parse_command, log_event_prefix="command"
)
```

vs subclass 后的:

```python
store: FilesystemCommandStore = FilesystemCommandStore(
    global_dir=..., project_dir=...
)
```

后者:
- 类名告诉读者「这是 commands 用的」,不需要 mental decode 泛型参数
- `isinstance(store, FilesystemCommandStore)` 这种检查照常工作
- 测试 fixtures 和 type hints 不动(零迁移成本)

cost:每 domain 写一个 6 行的 `__init__` 转发。三个 domain = 18 行
boilerplate,换来 233 个测试零改 + 所有外部 caller 不动。**便宜的
boilerplate**。

这个 pattern 普适到任何「抽公共基类后想保留 nominal 类型 identity」
的场景。Python 的 generic + subclass 组合特别适合这种情形。

### 3.3 `@property`-style Protocol 让 frozen dataclass 满足

mypy --strict 下,Protocol 声明:

```python
class MarkdownDocument(Protocol):
    name: str
    source_path: Path
```

意思是「实例属性 name 可读可写」。frozen dataclass 的 attribute
实际上 read-only(`__setattr__` 抛 FrozenInstanceError),mypy 认为
这跟 mutable attribute Protocol 不兼容(variance 问题)。

解决:

```python
class MarkdownDocument(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def source_path(self) -> Path: ...
```

`@property` 是 read-only 声明,frozen dataclass 的 attribute 满足。

**通用经验**:Protocol declared attribute 默认是 mutable;描述
read-only 接口(frozen dataclass 暴露字段、API client 返回不可变数据)
要用 `@property`-style。

这条以前在 Python typing PEP 里写得清楚,但实际遇到才记住。

### 3.4 `logger_name` kwarg 让 backward-compat log events 自然保留

原 3 个 parse_X 函数各自有 `_logger = get_logger("commands"/"skills"/"bundles")`
和 4-5 个 `_logger.warning("<domain>_<event>", ...)` 调用。

重构后,公共函数 `read_frontmatter_dict(path, *, logger_name)`:

```python
def read_frontmatter_dict(path, *, logger_name):
    log = get_logger(logger_name)
    log.warning(f"{logger_name}_read_failed", ...)
    log.warning(f"{logger_name}_missing_frontmatter", ...)
    ...
```

call site:`read_frontmatter_dict(path, logger_name="command")` →
events:`command_read_failed`, `command_missing_frontmatter`,
`command_yaml_parse_failed`, `command_frontmatter_not_mapping` —
**和原来 byte-identical**。

**关键洞察**:把 domain identifier 作为 kwarg 传进通用函数,既保留
domain-specific 输出又复用逻辑。这是 DRY without DRYing-up-too-far
的一个清晰例子 —— 公共逻辑共享,domain 标识不共享。

### 3.5 三次重复才抽的判断:rule-of-three vs 过早抽象

5b retro / 5c retro / 5d retro 都明确写了「这个形状会重复,但**不
现在抽**」。理由:

- **5b 之后**:第二次出现,可能巧合,可能后面其他 phase 会改变形态
- **5c 之后**:第三次还没出现,抽象会 over-generalize 到「未来可能不需要」的形态
- **5d 之后**:第三次出现,但 5d 自己是 cross-layer composition,叠抽公共模块会让 5d 的 stress test 不清晰
- **5e 之后**:5e 是 in-subsystem extension,纯加 source,**这时候抽公共模块最安全** —— 三个 instance 都稳定了,抽象的 right shape 看得清楚

实际抽完发现:
- `parse_X` 函数体从 50-100 行降到 30-50 行(domain-specific 部分)
- `FilesystemXStore` 从 60-70 行降到 10 行(thin subclass)
- 共抽出 ~300 行进 markdown_store/

**如果在 5c retro 时抽**,会少看到 Bundle 的 4-layer override 模式
(那是 5d 才有),`read_frontmatter_dict` 可能会被设计成 Skill/Command
的简化形态 —— 抽完 Bundle 来用时又要重构。**等到 rule-of-three 完
全成立**,抽象的 shape 自动浮现。

这个判断的真正成本是**忍受 duplication 不舒服**。三次复制粘贴的
心理负担 → 第四次出现时再抽 → 等到 5e 出现新 source(plugin)时
已经太晚。**抽得不能太早,也不能太晚**;rule of three 是 reasonable
sweet spot。

### 3.6 Refactor phase 用什么 acceptance check

Phase 8 的 acceptance:

1. ✅ 所有 233 个既有 domain test 零修改通过(API-level zero-diff)
2. ✅ 20 个新 markdown_store/ test 覆盖 generic 部分(避免泛型 bug 被掩盖)
3. ✅ `TestPhase8MarkdownStoreInvariant` 26 个 protected module 0 ref
4. ✅ Formal git-diff:9 个 protected dir(`permissions/`/`hooks/`/`engine/`/...)0 行 diff
5. ✅ Total coverage 不降(97.28% vs 5e close 的 97.16%,实际 +0.12pp)

第 1 条是最 important —— 测试是 behavior contract。任何 refactor 改测
试就是有 behavior drift,必须 root-cause 不能跳过。

第 2 条是因为泛型代码新增,所以 invariant 检查不够 —— 还要直接测
generic 行为(stub `_FakeDoc` + stub parser)。

第 3-4 条是 layer 边界保护,refactor 不应该 leak 到其他层。

第 5 条是 sanity check:refactor 不应该降覆盖率(意味着新代码没测全)。

---

## 4. Phase 8 没做的(以及为什么)

| 不做 | 理由 |
|---|---|
| 把 5e 的 `hook_plugins.py` 也用 markdown_store/ | hook plugin 是 `.py` 文件,不是 markdown。形态本质不同(Python file load via importlib vs YAML frontmatter parse)。要抽得是「filesystem discovery」更上层,不是 markdown_store —— 当 Phase 5f filesystem hook plugins 落地后,看「filesystem discovery」是否变成新的 rule-of-three 触发点。 |
| 把 `McpServerConfig._NAME_PATTERN` 也指向 markdown_store.NAME_PATTERN | McpServerConfig 不读 markdown,只是名字 regex 一样。**「一样的常量」≠「一样的概念」**;mcp 的命名约束以后可能演化(添加 `@org/` 命名空间之类),跟 markdown 文件名约束解耦更安全。 |
| 抽出更 generic `FilesystemDiscoveryStore[T]` 支持 .md / .yaml / .json / .py | 现在三个 consumer 都用 .md,抽更 generic 是 speculative。等下一个非 .md consumer 出现再扩(Phase 5f 是候选)。 |
| `MarkdownDocument` Protocol 加 `description` 字段 | 三个 dataclass 都有 description,Protocol 加上让 catalog UI 之类的工具能 generic 处理。但目前没 catalog UI,YAGNI。 |
| 把 `commands/__init__.py` / `skills/__init__.py` 的 export 收紧 | 当前每个 domain 的 `__init__.py` 仍 re-export EmptyXStore / FilesystemXStore;refactor 后这些只是 thin wrapper,理论上可以直接暴露 generic。但**这会破坏 import 路径** —— `from openharness.commands import FilesystemCommandStore` 这种代码会出错。保留现状。 |

---

## 5. 给下一阶段的人

- **Phase 5f filesystem hook plugins** 如果做:`*.py` discovery 不能直接复用 markdown_store —— shape 不同。但可以参考 markdown_store 的 store 部分(`FilesystemMarkdownStore[T]` 改成 `FilesystemPyModuleStore[T]` 取 `parser=lambda path: load_and_validate_py(path)`),global + project 两层依然适用。
- **Phase 8.5 (hypothetical) `discovery/` 超层抽象**:如果 5f 后真的有两个非-markdown 的 filesystem-discovery instance(.py + .json + ...),才考虑抽 `discovery/` 超层。**不要现在抽**,markdown_store/ 本身就是「等三个 instance 才抽」的胜利。
- **`MarkdownDocument` Protocol 演化**:目前只有 name + source_path。如果某天 hook plugin 用 markdown 形态(YAML frontmatter + Python docstring body),可能加 `description: str` 字段进 Protocol。但 frozen-dataclass 的 description 是 attribute 不是 property,需要 `@property` 样式声明 —— **同 §3.3**。

---

> **本 Phase 一句话总结**:
>
> 5b/5c/5d 的三次重复,5d 和 5e retro 标记 Phase 8 候选;现在抽完
> markdown_store/ 净 -180 LoC、233 个 domain test 零修改通过、9 个
> protected dir 0 diff。**Refactor 的 zero-diff 是 API-level,不是
> layer-level**;测试零修改是验证 invariant 是否成立的最强 check。
> Rule of three 是 sweet spot —— 早抽 over-generalize,晚抽错失复利
> 时机。
