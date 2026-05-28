# Learnings — Phase 12 (Session Snapshot + Resume)

> Phase 12 起止 / 2026-05-28(单日 session,接 Phase 11 完工后开启)
> 6 capabilities (P12-T1…T6) / **8 commits**(boundary + plan + T1-T6)
> 2,866 行净增(899 src + 1,967 tests)
> 1789 tests(Phase 11 close)→ **1897 tests** (+108) / ruff + mypy --strict clean / cov 95%
>
> 本文件回答的题:**Phase 11 的 `session_memory` 设计了写入接口但没实
> 际接线 → Phase 12 接上 + 增加 snapshot 第二个 consumer。"single
> producer / two consumers" 抽象在 N=1 → N=2 跨 phase 的延迟下是否
> 真的还成立?**
>
> 以及:**resume + snapshot 作为 Phase 11 substrate 之上的纯 additive
> 扩展,11 个保护目录能不能继续 zero diff?**

---

## 1. 数据点

| 维度 | Phase 10(memory) | Phase 11(summarize substrate) | **Phase 12(snapshot + resume)** |
|---|---|---|---|
| Capability | 6 | 7 | **6** |
| 生产代码净增 | ~3,400 | ~2,370 | **899** |
| 测试净增 | +205 | +181 | **+108** |
| 新模块 / 包 | 2(`memory/` + `prompts/` 重构) | 1(`services/`,3 个原语) | **1 file**(`services/snapshot.py`)|
| 新 Settings 字段 | 2(`enable_memory` + nested) | 2 nested(compact + extraction) | **1 nested**(`snapshot`,2 leaf 字段) |
| 新 CLI flag | 1 | 3 | **2**(`--resume` + `--resume-id`)|
| 新 CLI 子命令 | 3 | 0 | **0** |
| 新 QueryContext 字段 | 0 | 9 | **2** + 1 classmethod |
| **保护层 zero-diff** | ✓ 11 个目录 | ✓ 9/10 目录(bundles 一处 additive) | ✓ **11/11 目录** ⭐⭐⭐ |
| **engine 删除行数** | (没追踪)| (没追踪)| **0** ⭐⭐ |
| 时间 | 1 天 | 1 天 | **<1 天** |

**关键观察**:Phase 12 是 Phase 11 留下的 ``services/`` substrate 的
**第 6 次独立 consumer 压测**——
``services/snapshot.py`` 跟 ``services/session_memory.py`` 形态高度
相似(`get_*_dir` + `_serialize_*` + `write_session_*` + atomic write
+ `load_*` + staleness check),且共享同一个 ``tool_metadata`` producer。
这是 substrate 共用模式从"verify in same phase (Phase 11 T1→T4)"升
级到"verify across phase (Phase 11 T1 + T2 →Phase 12 T2)"的第一个
跨 phase 验证。

---

## 2. 每个 task 的 takeaway

| Task | Commit | 一句话总结 |
|---|---|---|
| **boundary doc** | c055215 | D30.1–D30.9 + 5 个用户 ratify 的设计决策(scope / format / id / trigger / metadata producer)+ 4 个支持决策(staleness / factory / settings / Phase 11 debt fold-in)。10 protected 目录预声明 |
| **plan** | d2a57dd | 6 capabilities + 双 capability-level 写法(没有过早 sub-task 分解,留给 agent autonomously)+ 1 critical checkpoint per task |
| **P12-T2 — services/snapshot.py**(优先,无依赖)| 82eaea6 | ~250 行单文件:`get_snapshot_dir` + `_current_git_head`(1s subprocess timeout + 4 种失败模式 silent fallback to None)+ `write_session_snapshot` atomic + `_serialize_snapshot` pure + `load_snapshot` 3-staleness branches + `SnapshotError` 4-subclass hierarchy。**踩坑**:`dict(tool_metadata)` 是 shallow copy,nested ``recent_files`` list 共享 ref 导致 producer-side 后续 mutation 污染已写 snapshot;`copy.deepcopy` 修。Test 直接 pin 这个 invariant |
| **P12-T1 — Phase 11 debt closure**(producer + session_memory writer wiring)| 1dd8a79 | Phase 11 留的 `update_session_memory_file` helper 第一次被 engine 真的调用——之前 L3 一直读 None。`collect_turn_metadata(messages, prior_metadata=None)` 纯函数 producer + engine finally block 调用。`prior_metadata` 参数为 resume 场景做了 accumulation 设计——但 P12-T5 实际 resume 走另一条路径(`from_snapshot` 不传 prior_metadata)。`prior_metadata` 留下来给 Phase 13 评估 |
| **P12-T3 — SnapshotSettings + 第二个 consumer wiring**| 4a268a8 | `_maybe_write_turn_end_metadata` 扩展为同时调用 session_memory writer + snapshot writer。`collect_turn_metadata` **fire 一次**喂两个 consumer——D30.6 的 single-producer 设计,用 spy test 严格 pin。**踩坑**:`git add -A` 把两个 pre-existing 未 tracked 文件(`docs/articles/` + `examples/hooks/cost_tracker.py`)误并入 commit;`git reset --soft HEAD^` + `git restore --staged` + 重新 commit 做了 surgical 修复 |
| **P12-T4 — QueryContext.from_snapshot 工厂**| 95f4bc5 | classmethod 加在 QueryContext 上;snapshot 加载 agent-state(model / max_tokens / permission_mode / system_prompt / messages),caller 必须传 runtime-state(api_client / tool_registry / permission_checker / cwd 4 个 required + 13 个 optional with defaults)。messages 通过 `ConversationMessage.model_validate` round-trip。**契约 D30.2**:`system_prompt` 字面加载不重渲染——agent reasoning chain 跟 snapshot 时刻一致,即使 skills 变了 |
| **P12-T5 — CLI --resume + --resume-id + banner**| 9f08c53 | 镜像 ask + chat。`_load_resume_snapshot` 把 4 个 SnapshotError subclass 映射到 CLI 行为(NotFound 无 id→warn 继续 / NotFound 有 id→exit 1 / Cwd/Version/Malformed→exit 1)。`--resume-id` 隐含 `--resume`(用户不用打两次)。chat banner 显示 message count + git_head |
| **P12-T6 — E2E + invariant + retro**(本 commit)| (pending) | 3 个 E2E 闭环(snapshot round-trip via engine / L3 actually-hits / chat resume via CLI)+ 11/11 invariant zero diff verification + 本 retro。⭐⭐⭐ L3 hits 测试关 Phase 11 retro §4.2 的 "no L3 hit observed yet" debt |

---

## 3. Framework-level 主题 — Phase 12 真正学到的

### 3.1 ⭐⭐⭐ Invariant 11/11 zero diff — 比 Phase 11 更纯的"纯增"

Phase 11 zero diff 是 9/10(bundles/ 因 D29.7 加了 kwarg),Phase 12 是 **11/11**:

| 保护目录 | Phase 12 commits |
|---|---|
| `markdown_store/` / `skills/` / `commands/` / `bundles/` / `plugins/` | **0** ⭐ |
| `mcp/` / `permissions/` / `prompts/` / `protocols/` | **0** ⭐ |
| `memory/` | **0** ⭐(P11-T6-6a 加过 stopwords 后稳态)|
| `hooks/` | **0** ⭐ |

外加 **engine/query.py 删除行数 = 0** 和 **engine/context.py 删除行数 = 0**——
所有改动纯 additive(新 helper + finally-block 新分支 + 新 classmethod)。

**为什么 Phase 12 比 Phase 11 更纯**:

Phase 11 引入 `summarize()` substrate 时,reactive PreApiCall rerun
(D29.7)的设计需要 `HookSpec.re_run_on_reactive_rebuild` 字段——这
个字段必须加在 `bundles/hook_plugins.py` 里(plugin hook 的 spec
入口),所以 `bundles/` 不可能 zero diff。**Phase 12 没有任何这种
"必须穿透别人的边界"的需求**:snapshot 是新文件,from_snapshot 是
classmethod 加在 QueryContext 内部,resume CLI flag 加在
``cli.py`` 内部。

**判断 framework**:

| invariant 真正 100% 守住的前提条件 |
|---|
| 新 feature 不需要给已有抽象加字段(否则一个抽象会扩散到 N 个 site) |
| 新 feature 的 data flow 完全在 engine 内部 / 服务模块内部完成 |
| 新 feature 的 user-facing surface 只在 CLI / Settings 两个边界扩展 |
| 已有抽象的 schema 不需要 migration(只读旧 schema,写新 schema) |

Phase 12 全部满足。**这是 Phase 8 markdown_store substrate + Phase 10
memory 读写分离 + Phase 11 services/ substrate 三层抽象的复合复利**——
任何一层没有,Phase 12 都得动更多目录。

### 3.2 ⭐⭐ 单 producer / 双 consumer 设计在跨 phase 验证下成立

Phase 11 retro §3.1 主张 `summarize()` 在同 phase 内被 2 个 consumer
共用就够证明 substrate 设计正确。但同 phase 共用有可能是"作者同时
设计就硬塞进去"的 confirmation bias。

Phase 12 给了**跨 phase** 的 evidence:

- P12-T1 加 `collect_turn_metadata` producer + session_memory writer
  consumer = N=1
- P12-T3 加 snapshot writer consumer = N=2
- **`collect_turn_metadata` 没改一行就被第 2 个 consumer 直接复用**

而且 D30.6 的"single producer / two consumers"在 engine finally
block 实施时,我加了 spy test 严格 pin:

```python
def test_both_writers_share_one_collect_turn_metadata_call(self, ...):
    call_count = {"n": 0}
    ...monkey patch counting wrapper...
    assert call_count["n"] == 1  # NOT 2
```

如果未来有人加第 3 个 consumer(比如 LangSmith export),spy test
会 catch 任何 producer-double-call 的回归。

**判断 framework**(跨 phase substrate 复利的真正成立条件 vs Phase 11 同 phase 版):

| Phase 11 同 phase 版条件 | Phase 12 跨 phase 加强版条件 |
|---|---|
| 抽象边界跟语义边界对齐 | + 边界在 phase 间没 drift(没人偷加字段)|
| 不预测未来 consumer 形态 | + 跨 phase 时确实没有未来 consumer 强迫你回改 |
| Protocol 接口最小可用 | + 加 spy test 严格 pin 共用 invariant,防回归 |

### 3.3 ⭐ `prior_metadata` 参数:为没出现的需求设计,然后不被实际路径使用

P12-T1 我给 `collect_turn_metadata` 加了 `prior_metadata` 可选参数,
设想是 resume 场景:

> Resume 时第一个新 turn 应该继承上一 session 的 `recent_files`,
> 这样 `last 10 touched files` 跨 session 持续累积

实施后 P12-T5 的 resume CLI 路径走的是 `from_snapshot` 直接加载
完整 messages 历史,**根本不需要 `prior_metadata` 介入**——engine 重
新跑 `collect_turn_metadata(messages)` 就能从加载的 messages 里
推出 `recent_files`。`prior_metadata` 参数留下来但实际无 caller。

这是 CLAUDE.md 警告的反面:"Don't add features ... beyond what the
task requires"。**我设计 T1 时假想了 T5 会怎么 caller,但 T5 实际
走另一条路径**,假想错了。

代价:9 行参数代码 + 2 个测试(`test_prior_metadata_seeds_recent_files` +
`test_dedupe_across_prior_and_current`)是死代码。**收益**:future-
proof 给 Phase 13 LLM-authored `task_focus_state` 提供 hook(那个
场景下 LLM 可能想保留前轮 focus_state)。

**判断 framework**:

| "做了但没用上"的代码 | 该删 / 该留 |
|---|
| 参数完全没 caller + 没未来 caller 候选 | 删 |
| 参数有 caller 候选但暂未 caller | 留,但加 TODO 标记什么场景会触发 |
| 参数有 caller 候选 + 有测试覆盖死路径 | 留 — 测试本身就是文档 |

`prior_metadata` 落在第 3 类。Phase 13 评估时再决定。

### 3.4 mid-session 的"`git add -A` 误并 untracked 文件"踩坑(重复 Phase 10 lesson)

Phase 10 retro §3.4 记录了"WIP 跟我并行时的 commit 污染"踩坑:用户
的 `7eabe0d fix(plugins)` commit 把我的 P10-T4 working tree 一起
打包。Phase 12 T3 commit 我自己做了同样的事:**`git add -u` + `-A`
组合误把 pre-existing untracked 文件(`docs/articles/` +
`examples/hooks/cost_tracker.py`)一起 commit**。

修复:`git reset --soft HEAD^` + `git restore --staged docs/ examples/` +
重新 commit。15 秒手术。

**比 Phase 10 那次更严重的地方**:Phase 10 是用户 commit 污染了我的
WIP,我清理是合理的。Phase 12 是**我自己污染了自己**——review-before-
commit 该 catch 这个但我没仔细看 stage 的文件列表。

**Phase 11 的 "GREEN 后先 review 再 commit" feedback** 应该升级:
review 不只是看 diff 内容,还要看 **stage 文件列表 vs 这次 capability
的 expected file set**。

**判断 framework**(永久 lesson):

| commit 前 review 的最低 checklist |
|---|
| diff 内容映射到 acceptance criteria(已有的) |
| stage 文件列表 vs 这次 capability 的预期文件集(新加的) |
| 是否有 pre-existing untracked 文件不该被 swept up(新加的) |

T3 我没做第 2 + 3 步,后果是污染 commit。下次 phase 第一个 task 之前
我自己应该在 boundary doc 或 plan 里列 expected file set。

### 3.5 ⭐ L3 终于真的 hit 了 — 5 天前的 Phase 11 retro §4.2 debt 关闭

Phase 11 retro §4.2 第 5 条踩坑:

> Phase 11 ships compact L3 reading the checkpoint, but the engine
> never WRITES the checkpoint, so L3 has been a no-op since landing.
> No L3 hit observed in any test yet.

P12-T1 加了 writer + T6 E2E test 7b 直接构造"checkpoint 新鲜 + 消息
量过阈值"的场景,assert `compact_kind == "session_memory"` 而不是
`"full"`。LLM stub 用 `_NeverCalledStub` 显式 raise AssertionError——
任何 L4 fallback 都会让测试失败。

**判断 framework**(retro debt closure 的最有力 evidence):

| Debt 真正关闭的证据强度梯度 |
|---|
| 弱:加了 code path 让 debt 在概念上消除 |
| 中:加了 unit test 覆盖新 code path |
| 强:加了**反证测试**(如果旧 debt 仍存在,测试会用具体方式失败)|

`_NeverCalledStub` 是反证测试——如果 L3 没 hit,会拿到具体的
`AssertionError("L4 LLM should NOT have been called when L3 hits")`。

---

## 4. 预测 vs 实际踩坑

### 4.1 plan §Risks 预测的 9 个风险

| 预测 | 实际命中? |
|---|---|
| `model_dump(mode="json")` 不 round-trip `ToolResultBlock` | ❌ 没中——pydantic discriminated union 工作正常(/tmp/check_roundtrip.py 验证过)|
| `git rev-parse HEAD` 超时 / 不存在 / hang | ❌ 没中——1s timeout + 4 种失败模式都 covered,test 用 monkeypatch 模拟全部 |
| `from_snapshot` 字段维护负担 | ⚠️ 部分中——14 个 optional kwargs 已经有点多,但还可以;Phase 13 加新 QueryContext 字段时需要同步加 from_snapshot 参数(test 用 spread 调用会自动 catch missing) |
| snapshot writer + session_memory writer race on tool_metadata | ❌ 没中——spy test 直接 pin single-producer invariant |
| 已有 system_prompt 加载 vs 当前 registries drift | ⚠️ 部分中——D30.2 contract "verbatim" 设计层面解决了,但 user 看到的体验可能 confusing(我后来在 CLI help 里 spelt out "--model 等 flag 被 ignored on resume")|
| concurrent `oh ask --resume` race | ❌ 没中——last-writer-wins(`os.replace` atomicity)如设计,没出现 race condition test fail |
| 测试隔离 snapshot dir leak | ❌ 没中——conftest 的 HOME isolation 直接覆盖 snapshot dir(同 root)|
| `--resume-id` prefix ambiguity | ❌ 没中——Phase 12 只有 `current.json`,所以 prefix 要么 match 要么不 match;Phase 13 history/ 上线后才会出现 ambiguity 风险 |
| snapshot JSON 不限增长 | ❌ 没中——同 Phase 11 session_memory cap 逻辑;snapshot 只 overwrite `current.json` |

**评估**:9 个风险 0 严重 + 2 部分。**比 Phase 11 的 1/8 + 2/8
更好**——Phase 12 设计阶段几乎所有风险都在 boundary doc / plan 里
就被 mitigation 了。

### 4.2 没预测到但出现的踩坑

1. **`dict(tool_metadata)` 浅拷贝 bug**(T2-2b)——`recent_files`
   nested list 共享 ref;`copy.deepcopy` 修。**未来 framework lesson**:
   任何"持久化产生的 dict 必须跟 producer 的 dict 完全 detach"——
   shallow `dict(...)` 不够。
2. **`_AllowAllChecker.check()` vs `evaluate()` 接口签名错**(T1-1b)
   ——我抄了一个 stub 但用了错的方法名;tests/engine/conftest.py
   已有正确版本但我没复用。**未来 framework lesson**:写新 stub 之
   前先 grep `class.*Checker:` 看现成的。
3. **`git add -A` 误并 untracked 文件**(T3)—— §3.4 已详述。
4. **`asyncio` import 在 e2e test 里只为 silence 警告**(T6)——
   `_ = asyncio` 是 hack;实际上 ruff 后来自动 fix 把 import 删了。
   **未来 framework lesson**:`uv run ruff check --fix` 在 commit
   前跑一次,省得 ruff-format hook 后还要重 commit。

---

## 5. Phase 13 预测

Phase 13 候选(boundary doc §Out of scope + plan §Risks NOT mitigated):

- **`oh memory add` 显式写 CLI** —— extraction + snapshot 都覆盖了大
  部分场景,但 "我想手工归档这个发现"仍有 case
- **snapshot rotation / `history/` 目录** —— Phase 12 reserve 但没
  populate;Phase 13 加 `oh snapshot list` + 自动 GC
- **auto-dream subprocess** —— 跨进程定期 consolidation,Phase 14 候选
- **LLM-authored `task_focus_state`** —— 现在 placeholder None,Phase
  13 评估 secondary LLM 成本是否划算
- **`oh snapshot diff <id1> <id2>`** —— tooling 类需求

### 5.1 预测会踩的坑

1. **history/ 目录的 retention policy**:存多少天?多少条?用户自己
   配?**对策**:抄 git reflog 的 90-day rolling 默认,可配
2. **rotation 跟 atomic write 的交互**:rotate 时 `current.json`
   move 到 `history/<sha>.json` 同时 new `current.json` 写入——move
   不是 atomic 跨文件 sequence。**对策**:用 hardlink 复制旧
   `current.json` 到 `history/<sha>.json`,然后 atomic overwrite
   `current.json`,最后 unlink 不再需要的 hardlink。
3. **LLM-authored task_focus_state 的 fallback**:LLM 调用失败时
   `task_focus_state` 是 None 还是上次的?**对策**:fallback to None
   (跟 Phase 12 一致),log warning。

### 5.2 Phase 12 后的"待 mark" sub-decisions

| 题 | 我的 lean | 等 Phase 13 boundary doc 拍 |
|---|---|---|
| `prior_metadata` 参数是否删 / 留 | 留,理由 §3.3 | ✓ |
| snapshot rotation 默认窗口 | 90 days / 100 entries(抄 git reflog)| ✓ |
| `oh snapshot list` 是否做 | 做(便宜)| ✓ |
| LLM-authored `task_focus_state` 是否做 | 不做(收益小,成本明显)| ✓ |

---

## 6. Phase 12 总结

- **6 capabilities 全 ship** / 8 commits / 单 session
- **11 个保护目录 git log 验证:11/11 zero diff** ⭐⭐⭐
  (Phase 11 是 9/10,Phase 12 是更纯的 additive 扩展)
- **engine 删除行数 = 0** ⭐⭐(纯 additive helper + finally-block 新分支 + classmethod)
- **`collect_turn_metadata` single producer 在 N=1 → N=2 跨 phase 验证下不需要修改** ⭐⭐⭐
- **Phase 11 retro §4.2 "L3 没 hit" debt 被反证测试明确关闭** ⭐⭐
- 1897 测试 / ruff + mypy --strict clean / **cov 95% 保持**(无需 backfill,新 code 跟 test 同步落地)
- 1 个 mid-session commit 污染(T3)被 surgical reset 修复;启发 §3.4 review checklist 升级

**Phase 13 起步状态**:`services/`(summarize / compact / session_memory /
extract / snapshot)+ `memory/`(read + write)+ snapshot read/write
+ resume CLI 全部 ready。可以直接在 history/ rotation 或 LLM-authored
metadata 之上开 Phase 13。

Phase 11 retro 留了一个问题:**substrate 复利在跨 phase 验证下是否
还成立?**Phase 12 给的答案是 **YES**——`services/` substrate 接了
第 6 个 consumer(`snapshot.py`),`tool_metadata` producer 接了第 2 个
consumer,**两个 substrate 都不需要回头修一行**。如果 Phase 13 加
history rotation + LLM-authored metadata 时还能保持这个 invariant,
Phase 7c retro §3.1 的 "abstraction-first compounds" 论点就有了 6
个 phase 跨度的连续证据。
