# loop-runtime L3′ — 语义验证闸（真正的 /goal）实现记录

> **上游**：`loop-runtime-plan.md`（epic capability 地图，§2 新增 L3′ 行）+
> `loop-runtime-autopilot-reference.md` §4（"验证闸：autopilot 硬闸 vs Claude Code /goal
> 软闸"——这个命名的来源，也是这次要建的东西）。
> **纪律**：TDD 是脊梁，本模块 5 个垂直切片全部走完 RED→GREEN。全量测试 2307 通过 + `mypy
> --strict` 122 源文件全过 + `ruff` 全过。
> **状态**：**已实现（2026-07-01），MVP GREEN + 真模型冒烟验证通过**。留档不删——这份 plan
> 本身就是这次实现过程中沉淀下来的资产，不留下下次就只能重新读代码。

---

## 0. 这个模块交付什么（一句话）

给 L3（硬闸，命令 exit code 判定）加一个**平级**的软闸：`--goal-condition <自然语言条件>`，
由一个独立的 LLM 裁判读 transcript 判断 yes/no + 理由，接入 L4 外层修复循环；跟 L3 互斥，
一次运行二选一。

---

## 1. 锁定立场（实现过程中拍板的决策，不是事后编的）

1. **不是替换 L3，是新增平级模块**——`loop-runtime-plan.md` §4 不变量 #1"验证闸是 gate，
   不是 prompt"是写给 L3（硬闸）的边界，不是全项目禁止语义裁判。软闸作为用户显式选择
   （`--goal-condition`）的第二条路径存在，不稀释 L3 的确定性契约。
2. **fresh-context 不变量原样保留**——软闸裁判读的是当前这一次 attempt 自己的 transcript
   （`PrintResult.text`），不是跨 attempt 累积的历史。跟硬闸每次独立跑 `--verify` 命令完全
   对称，没有为了跟官方 `/goal`（持续 session、累积 transcript）对齐就推翻既有不变量。
3. **裁判机制零新建，全复用已有基建**——`services/summarize.py` 的一次性调用入口（绕开
   `engine/query.py` 完整 agent loop）、`eval/rubrics.py` 的 5 条 prompt 写法约定（reason
   -first、二元 0/1、双语 PASS/FAIL 例子、<500 字、严格单行 JSON）、`eval/scorers.py` 的
   手工 JSON 解析 + fail-closed 写法（去 fence → `json.loads` → 取 `score`/`reason`，任何
   异常/畸形一律 `passed=False`）。
4. **`GateResult` Protocol 泛化 `build_repair_prompt`**——`verification/repair.py` 引入
   `GateResult`（`passed`/`feedback`），让 `VerificationResult`（硬闸，带 `.steps`）和
   `SemanticGateResult`（软闸，无 `.steps`）共用同一份 repair-prompt 格式化逻辑（`getattr
   (verification, "steps", ())` 兜底）。**踩过一个坑**：Protocol 属性默认要求可写变量，但
   两个具体类型都是 frozen dataclass（只读）——mypy `--strict` 直接报错；改成 `@property`
   声明后，只读属性也能结构化满足 Protocol。
5. **`--verify`/`--goal-condition` MVP 阶段互斥**——同时给出会直接报错（"choose one
   gate"），AND 语义（两闸都过才算数）留作后续，避免这次先定义一个含糊的"谁说了算"。
6. **`--goal-condition` 只接 `--output-format json`，不接 `stream-json`**——实现中途从
   "跟 `--verify` 一样 json/stream-json 都支持"收紧成"只支持 json"：软闸目前只接进了
   `_run_ask` 的 json 分支，`render_stream_json` 没接，如果放行 stream-json 会静默不触发
   裁判（用户以为在跑软闸，实际上没跑）。**报错比留一个悄悄的功能缺口更安全。**

---

## 2. Out of scope（写死，留给后续）

- `goal_judge_model` 独立可配置的裁判模型（照抄 `SnapshotSettings.focus_state_model` 先例，
  默认回退主模型）
- `collect_print_result` 扩展工具调用轨迹——目前裁判只看得到 `PrintResult.text`（助手最后
  一轮的自述文本），看不到中间工具调用/文件改动的原始证据，某种程度上还是在读"助手自己说
  做了什么"。后续可以顺带收集一份精简工具调用轨迹一起喂给裁判，增强证据基础。
- **必走的 eval**——`loop-runtime-plan.md` §5 明确写了"L3 验证闸用 grader agent 那一支 →
  LLM-judge，必走 eval"，这次新增的正是这一支。CLAUDE.md 定义 eval 目前是 draft、不设成
  完成门，但项目自己对这个具体模块的要求是要做，只是不阻塞这次 GREEN。
- `--verify` + `--goal-condition` 组合成 AND 语义（两闸都要过才算通过）
- `render_stream_json` 路径接入软闸（跟上面第 6 条锁定立场对应的后续）

---

## 3. 任务（已全部执行完成，垂直切片 · 按依赖排）

| # | 任务 | 状态 | 落点 |
|---|---|---|---|
| 1 | L3′ 裁判纯函数 | ✅ GREEN | `verification/semantic_gate.py`（`SemanticGateResult` + `run_semantic_verification` + `maybe_run_semantic_verification`）+ `tests/verification/test_semantic_gate.py`（13 tests，覆盖合法 JSON/fence 剥离/异常/畸形全部 fail-closed） |
| 2 | 泛化 `build_repair_prompt` 接受 `GateResult` Protocol | ✅ GREEN | `verification/repair.py` + `tests/verification/test_repair.py`（+2 tests：无 `.steps` 的 stub 走 feedback-only 分支，既有 steps 分支回归锁不变） |
| 3 | CLI `--goal-condition` 选项 + 校验 | ✅ GREEN | `cli.py`（新增 option + 5 条校验：需 print mode、只接 json、与 `--verify` 互斥、`--max-iter` 二选一其一）+ `tests/cli/test_goal_condition.py`（新文件） |
| 4 | 接线 `_run_ask` 调用软闸裁判 | ✅ GREEN | `cli.py`（`AskOutcome.verification` 类型放宽为 `GateResult`）+ `_stream_render.py`（`build_result_obj` 的 steps 序列化 `getattr` 兜底） |
| 5 | 端到端修复循环测试 | ✅ GREEN | `tests/cli/test_goal_condition.py`（3 个新 E2E 测试类，镜像 `test_repair_loop.py` 的 PassesOnSecondAttempt/CapHit/PromptThreadsFailure 三件套，换成 `--goal-condition` + stub 裁判） |

**真模型冒烟验证**（2026-07-01）：

```bash
OPENHARNESS_PERMISSIONS__ALLOW="Edit(*),Write(*),Bash(*),Agent(*)" \
  uv run oh ask -p --output-format json \
  --goal-condition "README 里提到了这个新功能" \
  --max-iter 3 "在 README 加一节介绍新功能"
```

跑通：`attempts: 1`、`verification.passed: true`，裁判给出的 `feedback` 是独立判断（读
transcript 后自己下的结论），不是助手自我复述的转述。真实文件改动落地（`README.zh-CN.md`
+55 行，内容准确），冒烟测试完成后已撤销（`git checkout`），未提交。

**冒烟过程中暴露的、跟这个模块本身无关的既有行为**（不是 bug，如实记录避免下次重新踩坑）：
- headless 模式下 `tool=Bash`/`tool=Agent` 会被 `headless_failclosed` 拦（loop-runtime L2
  既有设计），需要 `OPENHARNESS_PERMISSIONS__ALLOW` 显式放行，规则语法 `ToolName(*)`。
- `json.dumps()` 默认 `ensure_ascii=True`，中文内容会被转义成 `\uXXXX`——整个 CLI 的 json
  输出路径都有这个问题（不止 `/goal`），待修。

---

## 4. 后续（未做，按需捡起）

见 §2 Out of scope 五项。debrief 待做。
