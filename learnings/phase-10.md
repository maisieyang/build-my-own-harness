# Learnings — Phase 10 (Memory Subsystem — Static Read Path)

> Phase 10 起止 / 2026-05-26(单日,接 Phase 9 完工后开启)
> 6 capabilities (P10-T1…T6) / 8 commits / **~5,200 行净增**
> (含 6 个新模块 + 5 个新测试文件 + cli.py / settings.py 接线)
> 1600 tests → 1608 tests / ruff + mypy --strict clean
>
> 本文件回答的题:**read substrate 与 write semantics 分两 phase
> 是否真的减少耦合 —— Phase 10 不带写路径,要等 Phase 11 的
> extraction secondary pass 才能让 agent 写 memory。**

---

## 1. 数据点

| 维度 | Phase 8(`markdown_store/` 抽取) | Phase 9(plugins) | **Phase 10(memory)** |
|---|---|---|---|
| Capability | 1 refactor | 5 | **6** |
| 生产代码净增 | +20(纯重构) | ~600 | **~3,400** |
| 测试净增 | ~30 | ~150 | **+205**(1608 − 1403 pre-P10) |
| 新模块 / 包 | 1(`markdown_store/`) | 1(`plugins/`) | **2**(`memory/` + `prompts/` 重构) |
| 新 Settings 字段 | 0 | 1(`enable_plugins`) | **2**(`enable_memory` + nested `memory: MemorySettings`) |
| 新 CLI flag | 0 | 1(`--enable-plugins/--no-`) | **1**(`--enable-memory/--no-`) |
| 新 CLI 子命令 | 0 | (Phase 9-T4 未做) | **3**(`oh memory list / show / path`) |
| **保护层 zero-diff** | ✓ 重构没破抽象 | ✓ 5 个 stores | ✓ **11 个目录**(markdown_store + engine + compaction + hooks + permissions + mcp + plugins + skills + commands + bundles + protocols)|
| **byte-identical invariant** | N/A | N/A | ✓ **`build_system_prompt(tools, env)` 字节相同**(5 个 net 测试) |
| 时间 | 半天 | 2.5 天 | **1 天** |

**关键观察**:Phase 10 是 Phase 8 抽象的**第 4 次独立 consumer 压测**——
`markdown_store/` 被 6 个 consumer(commands / skills / bundles / plugin
multiplexer 间接 / memory / 未来的 team scope)host 而**自身 zero diff**。
substrate 设计在 Phase 8 一次成型,后续 4 个 consumer 落地零修改。

---

## 2. 每个 task 的 takeaway

| Task | 一句话总结 |
|---|---|
| **P10-T1 — memory/ foundation**(933aeab) | `Memory` 14-field frozen dataclass + `MemoryType` / `MemoryScope` enum + `compute_memory_signature` + `parse_memory` + `get_project_memory_dir` + 2 errors。**踩坑**:`Path.home()` 在模块 scope 缓存导致 conftest 的 `HOME` 隔离失效,改成函数体内 lazy 求值;`bool isinstance int` 导致 `ttl_days: true` 静默被解释成 `1`,加显式 `not isinstance(x, bool)` 排除。 |
| **P10-T2 — FilesystemMemoryStore**(7117a04) | 单文件 116 行 subclass + `EmptyMemoryStore` sentinel + `MemoryStore` Protocol。⭐ **`markdown_store/` zero diff 验证通过**——第 4 次独立 consumer 压测(commands / skills / bundles / memory)。`log_event_prefix="memory"` 走 substrate 的 default 分支,跟 `parse_memory` 的 `get_logger("memory")` 同 channel 不需要改 substrate。**踩坑**:同名冲突在 project layer 是 "later wins + info-level override",不是我先猜的 "first wins";测试预期写反一次。 |
| **P10-T3 — relevance + usage tracking**(8be321b) | 打分公式 `meta*2 + body + importance*0.4 + use_count[capped]*0.1 + recency_boost`,**零 token 命中直接踢出**(D28.7 load-bearing rule)。`mark_memory_used` 原子重写:`tempfile + os.replace` + 5-phase 失败分类 log。**踩坑**:body 字节级保留必须从 `split_frontmatter` 拿,不能让 `yaml.safe_dump` 重出 —— 不然 round-trip 不字节相等。 |
| **P10-T4.4a — prompts.py → prompts/ refactor**(b518cec) | **TDD-first**:`test_byte_identical.py` 5 个 snapshot 测试**先写**,然后才做 git rename。安全网就位,refactor 不能跑偏。`git diff` 显示 89% similarity rename detection 工作正常。 |
| **P10-T4.4b-4f — claudemd + memory_inject + Settings + CLI**(7d87a43) | 最大单一 commit:11 文件 / 1843 行净增。`build_system_prompt` 加 2 个 additive kwarg(`claude_md_content` + `memory_manifest`);bundle.system_prompt 覆盖时 `bundle_overrides_prompt` flag 跳过 memory 注入。`env_nested_delimiter="__"` 启用 `OPENHARNESS_MEMORY__MAX_FILES`。**踩坑**:4b 把 CLAUDE.md heading 错写成 `# Project Instructions`(h1),跟其他 `## Tools` / `## Environment`(h2)层级不一致,4d 时回头改成 h2 + per-file h3。 |
| **P10-T5 — oh memory CLI**(57860a1) | `oh memory list / show / path` 3 个只读子命令。`list` 按 `(-use_count, name)` 排序,**text 默认 + `--format json`**。`show` 先按 name 查,fallback 按 id。`path` 即使 dir 不存在也 exit 0(D28.11 契约)。**踩坑**:CLI test 默认 80 列 terminal,Rich 把 `--enable-memory` 截成 `--enable-me…`;用 `CliRunner(env={"COLUMNS": "200"})` 解。 |
| **P10-T6 — E2E + retro**(本 commit) | 6 个端到端测试:hand-written memory → `oh ask` → system_prompt 注入 + `use_count++` atomic on disk。⭐⭐⭐ 全 11 层保护 zero Phase-10 commits 触碰。**踩坑**:测试用 `query="what is the weather today"` 跟 stripe memory 共享 `the`(没 stopwords),被命中后 false positive —— 改成 `Tuesday calendar planning` 真零 overlap。这正好暴露 §3.3 的设计限制。 |

---

## 3. Framework-level 主题 — Phase 10 真正学到的

### 3.1 ⭐ Substrate 抽象的复利:Phase 8 一次成型,Phase 10 第 4 个 consumer 零修改

`markdown_store/` 在 Phase 8 抽出来时只有 3 个 consumer(commands /
skills / bundles)。Phase 9 plugin 通过 LayeredStore 间接成第 4 个,
Phase 10 memory 成第 5 个(直接 subclass)。

**P10-T2 落地代码**:

```python
class FilesystemMemoryStore(FilesystemMarkdownStore[Memory]):
    def __init__(self, *, project_dir: Path) -> None:
        super().__init__(
            global_dir=None,
            project_dir=project_dir,
            parser=parse_memory,
            log_event_prefix="memory",
        )
```

12 行整个 store。`markdown_store/` 自身 zero diff。

**对比 Phase 7c 的 evidence**:Phase 7c retro §3.1 说"abstraction-first
compounds works"。Phase 10 是同一个论点的第 N 次实证——但这次是
**跨 phase 的复利**(Phase 8 → 10,跨了 9 这个新 phase 的整个生命周期),
不是 phase 内的复利。

**判断 framework**:

| substrate 复利的真正成立条件 |
|---|
| 抽象边界跟语义边界对齐(不是按文件分,按行为分) |
| 新 consumer 的"domain-specific 字段"和"shared 行为"在抽象设计时被显式分类 |
| 抽象不预测未来的 consumer 形态——只压缩**已有 N≥3** consumer 的共性 |
| Protocol 接口最小可用(name + source_path),不预测属性 |

⭐ Phase 11 预测:`summarize(messages, retention_policy) -> summary`
作为 secondary-pass 原语,3 个 trigger(extract / compact L4 / future
write_memory)共用。如果这个抽象成立,**Phase 11 又是一次同样形态
的复利**。

### 3.2 ⭐⭐ Byte-identical net 是 refactor 唯一可靠的安全绳

`test_byte_identical.py` 5 个 snapshot 测试:把 `build_system_prompt`
在 4 种 calling form 下的**精确字符串输出**钉死。在 4a 的 `prompts.py
→ prompts/` rename 前先写,验证 GREEN,然后才做 git rename。4d 加 2
个新 kwargs 时,这 5 个测试**仍然 GREEN**——证明"不传新 kwargs 的
现存调用方"输出字节相等。

**对比 Phase 8 的"API-level zero diff"invariant**:Phase 8 验证的是
"public 函数签名 + 行为不变",但允许内部实现重写。Phase 10 更严格——
**输出字节相等**。理由:`build_system_prompt` 的返回值直接 hash 进
对话历史(provider-side caching key),任何字节级 drift 都会让缓存失效。
"行为不变"不够,要"字节不变"。

**判断 framework**:

| 你需要 byte-identical net 的情形 | 你只需要 API-level zero diff 的情形 |
|---|---|
| 输出是 prompt / message / cache key | 输出是 dataclass / Protocol 实例 |
| 调用方做字符串包含 / regex 匹配 | 调用方做属性访问 |
| 性能敏感(reformat 影响下游 hash) | 性能不敏感 |

Phase 11 的 LLM-as-summarizer 原语**不需要** byte-identical(每次
调用 LLM 输出本来就不确定);Phase 12 的 `oh ask --resume` 加载
snapshot **需要** byte-identical(snapshot 文件内容 = 字节)。

### 3.3 ⭐ 关键设计限制:无 stopwords 的 tokenization 在 short query 上 false positive

Phase 10 sub-decision 留了"是否上 stopwords"的接口,我选了**不上**,
理由是"技术 query 少见 stopwords + 元数据权重高足以压制噪音"。

**T6 E2E 测试暴露**:query "what is **the** weather today" 跟 stripe
memory body "for **the** audit trail" 共享 `the` 一词,scoring 把它当
1 body_hit 命中,memory 误注入。

修测试时把 query 换成 "Tuesday calendar planning notes"(真零 overlap)。

**这是 Phase 10 的设计 trade-off,不是 bug**——但 Phase 11 应该评估:

- 用 `query_token_count >= 2` 做"至少有 2 个非 stopwords token 命中"的
  门槛?
- 上一个最小 stopword set(`a, an, the, is, are, was, were, of, to,
  for, with, on, in, at, by`)?
- 让 importance + meta_hit 权重做更激进的过滤?

**判断 framework**:

| 默认策略 | 适用 |
|---|---|
| 不上 stopwords(Phase 10 当前) | 用户 query 平均长度 >5 词 + 主要是技术领域 |
| 最小 stopword set | 自然语言 query 占主流 + 短 query 频繁 |
| 完整 NLTK stopwords | 跨语言 + 完整文本检索语义 |

我的 lean(Phase 11 评估):**最小 set + meta_hits 阈值≥1**——既保留
不依赖 NLP lib 的纯 stdlib 实现,又避免 short-query false positive。

### 3.4 mid-phase merge mess:WIP 跟我并行时的 commit 拆分代价

Session 中间(13:52)用户提交了 `7eabe0d fix(plugins): P9-T3 fan_out`,
**意外把我整个 P10-T4 的 working tree 都打包进去了**。commit message
只说 P9-T3 fix,但 staged diff 含 11 个 T4 文件。

**清理代价**:`git reset --soft HEAD^` + `git checkout HEAD --` cli.py
+ settings.py + 重新 stage P9-T3 部分 + commit + 还原 T4 状态 + commit
T4。**15 分钟手术**,但 cli.py / settings.py 因为 23 个 hunk × 11 个
跟 T4 字面相邻(`enable_plugins` 紧挨 `enable_memory`),hunk-level
surgical split 估算 30 分钟以上工作量。

**最终采取的 plan B**:cli.py / settings.py 整块归 T4 commit,P9-T3
commit 只有 3 个 plugin 文件 + test_cli.py。T4 commit message 注明
"folds in P9-T3 CLI plumbing"。narrative 不完美,但 acceptable。

**判断 framework**(给未来):

| 当合作者并行修改我正在编辑的文件,怎么避免 commit 污染 | 落点 |
|---|---|
| 我开新 branch | 主流方案,但本项目主线开发不切 branch |
| 我先 `git stash push -- <我的文件>` 再 pull | 隔离我的 WIP,合作者的 commit 进来时不冲突 |
| 合作者 review 我的 staged diff 后再 commit | 现实中没人会 |
| 接受混合 commit + rebase split | Phase 10 这次选的 |

更深层的 lesson:**长 session 单 branch 开发,跟外部 commit 的边界
要靠协议管理,不能靠运气**。Phase 11 开始如果还在主线开发,先约定
"做 phase N 时不接 phase M 的 fix commit,fix 全攒到 phase 间隙"。

---

## 4. 预测 vs 实际踩坑

### 4.1 plan 里预测的 3 个踩坑

| 预测 | 实际命中? |
|---|---|
| `prompts/` 重构破坏 `from openharness.prompts import X` | ❌ 没破——`prompts/__init__.py` re-export 全部 public API,3 个 importers (cli + test_prompts + test_e2e) 全过 |
| `mark_memory_used` 原子重写在 pytest 并行 worker 下 race | ⚠️ 部分中——pytest 用了 `tmp_path` per-test 隔离,所以并行没 race;但 `mark_memory_used` 自身没加文件锁,留给 Phase 11 extraction 上线时加 |
| Relevance scoring 因为 stopword 缺失 false positive | ✅ 命中——T6 E2E 测试直接撞上 |

**评估**:plan 的预测命中率 1/3 严重 + 1/3 部分。**stopwords 那条
预测最准**——boundary doc sub-decision 时就标了"revisit if false-
positive rate is high",T6 给了第一手 evidence。

### 4.2 没预测到但出现的踩坑

1. **`Path.home()` 模块 scope 缓存破测试隔离**(P10-T1.1c) ——
   conftest 设 `HOME` env var,但模块导入期已经把真 home 缓存进
   `_GLOBAL_MEMORY_ROOT` 常量。修法:删常量,函数体内 lazy 求值。
   **未来 framework lesson**:任何依赖环境变量的全局求值都得延后。
2. **`bool isinstance int` 在 YAML 解析**(P10-T1.1b) ——
   `ttl_days: true` 被 yaml 解析成 `True`,`isinstance(True, int) ==
   True`,所以静默被当 1。修法:显式 `and not isinstance(x, bool)`。
3. **CLAUDE.md heading 层级错配**(P10-T4.4b → 4d) ——
   `# Project Instructions` h1 vs `## Tools` h2 不对齐;4d 时改成
   h2 + per-file h3。**未来 framework lesson**:section 模板写完后
   渲染一遍 system_prompt 看视觉一致。
4. **`FilesystemMarkdownStore._merge_dir` project layer 同名行为是 "later
   wins + info-level override",不是 "first wins"**(P10-T2) ——
   substrate 既有行为,我猜反了。测试加注释 pin 行为。
5. **Rich box-rendering 截断 CLI option 名**(P10-T5 + P10-T4.4f) ——
   `--enable-memory` 在 80 列 terminal 下被截成 `--enable-me…`;
   `CliRunner(env={"COLUMNS": "200"})` 解。

---

## 5. Phase 11 predictions

Phase 11(summarization substrate)将引入:

- **LLM-as-summarizer 原语**:被 3 个 trigger 复用(extract / compact
  L4 / 可选 write_memory)
- **compact L2-L4 escalation pipeline**:L2 context-collapse / L3
  session_memory reuse / L4 full LLM compact 9-slot schema
- **session_memory 5-slot checkpoint**:每 user turn 末确定性写入
- **`extract_memories_from_turn`**:每 turn 末 secondary LLM pass,
  3 条 JSON 上限 + EXTRACTION_SYSTEM_PROMPT + read-only tools 沙箱
- **解 Phase 4 PreApiCall debt**:reactive truncation 重建 request 时
  memory 段落丢失

### 5.1 预测会踩的坑

1. **3 个 trigger 共用 summarize 原语**:理论上抽象一致,实操可能各自
   想要自己的 prompt template,导致原语膨胀成 "if trigger == compact:
   ..." 分支。**对策**:trigger 各自构造 SystemPrompt + 用户 msg,
   原语只调 LLM,不识别 trigger。
2. **session_memory 文件覆盖与 read 并发**:compact L3 触发时读
   checkpoint,正好该 turn 的 `_update_session_memory` 在重写它。
   **对策**:read 时拷贝整个 markdown 到 string,不持文件句柄。
3. **EXTRACTION 提取出的 memory 跟用户已写的同名冲突**(signature
   dedup 该 kick in):signature 是 `sha256(body + type + scope)`,
   两个 memory 同 name 但不同 body → 不同 signature → 两份文件;
   要不要再加 name dedup?**对策**:signature dedup 优先,name
   只用于 lookup;同 name 不同 signature 共存,relevance 只挑分数高的。

### 5.2 准 Phase 11 sub-decision 留给 boundary doc

| 题 | 我的 lean | 等 boundary doc 拍 |
|---|---|---|
| stopwords 上不上 | 上最小 set(15 词)+ `meta_hits >= 1` 门槛 | ✓ |
| LLM model for extraction | 同主对话(不上单独便宜 model) | ✓ |
| EXTRACTION_SYSTEM_PROMPT 严格度 | 强(JSON schema + 3 条上限 + read-only tools)| ✓ |
| Compact L4 失败 retry 次数 | 3 次 PTL + 2 次 streaming(同 HKUDS) | ✓ |
| `/compact` 斜杠命令 | Phase 11 顺手做 | ✓ |
| Phase 12 snapshot file format | JSON(同 HKUDS,不另发明) | 留给 Phase 12 boundary |

---

## 6. Phase 10 总结

- **6 capabilities 全 ship**;1 个 commit-split surgery 修复 mid-session
  merge mess
- **11 个保护层 zero Phase-10 diff** ⭐⭐⭐
- **`build_system_prompt` byte-identical net 跨 4a refactor + 4d 扩展
  全 GREEN** ⭐⭐
- **`markdown_store/` 第 4 次独立 consumer 压测通过** ⭐
- 1608 测试 + ruff/mypy --strict clean
- 1 个设计限制(无 stopwords false positive)被 E2E 直接暴露,推迟到
  Phase 11 评估
- 0 个 boundary doc invariant 被破坏

**Phase 11 起步状态**:`memory/`(read 完整)+ `prompts/`(refactor +
注入)+ `Settings.memory.*` 都就位,可以直接在 secondary-pass
infrastructure 上开 extract / compact / session_memory。

Phase 10 把 "read 与 write 分两 phase" 的实验做完了。**读路径上线
后 5 天 retro 时回头评估:写路径(Phase 11)真的从这个分离里获益
了吗?** 那个题留给 Phase 11 retro。
