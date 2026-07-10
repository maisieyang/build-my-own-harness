# SWE-bench Lite 全量战役日志（RUNLOG）

> 按时间记录 benchmark 战役中的关键节点：每个失败暴露了什么、策略怎么调。
> 这是失败分类学报告和对外文章的一手素材——写于事发当时，不是事后追忆。
> 证据文件：同目录 `out/predictions.jsonl` + `out/records.jsonl`（git 历史里有各阶段快照）。

---

## 2026-07-08

### 节点 1 — 首次冒烟成功，但署名撒谎（config 漂移 bug ×2）

- **事件**：adapter M1（decisions/40，T1-T7）落地，`psf__requests-2317` 首跑成功，
  13 行 patch 打在正确病灶上。但 record 署名 `openharness-0.3.0+default`。
- **暴露**：
  1. `__version__` 硬编码 0.3.0，pyproject/CHANGELOG 已是 0.4.0——版本漂移（修复 `8d42c9c`）；
  2. 更深：子进程 `oh ask` 的 cwd 是 workspace，项目 `.env` 对它不可见，配置静默漂到
     user-global 层——**第一次冒烟实际跑的根本不是以为的那个模型/端点**。
- **调整**：`_pin_config`——bench 侧解析一次 settings 链，把 key/base_url/model 经 env
  显式注入子进程（env 优先级高于 .env 文件），整批单一配置、记录不说谎（`bc409b4`）。
- **教训**：*别用 proxy 信号反推状态事实*——"跑成功了"不等于"用我以为的配置跑成功了"。
  实验记录的每个字段都要有直接证据链。

### 节点 2 — 小批 5 题：两个系统性失败形态 + 一个又不存在的旋钮

- **事件**：astropy 5 题小批：4 completed + 1 invalid-envelope。
- **暴露**：
  1. `astropy-14182` 死亡链：模型 turn 18 想跑 Bash（headless fail-closed 拒）→ 撞 20 轮
     硬顶 → `LoopLimitExceeded`。**且错误信息让用户 "raise --max-turns"——这个 flag
     当时并不存在**（错误信息承诺了未实现的旋钮，harness 真 bug）；
  2. 3/5 patch 混入模型自建的 repro/test 脚本——无 Bash 环境里模型在写它**永远跑不了**
     的验证脚本（纯浪费 token + 污染 patch，且没有删除文件的工具，连清理都做不到）；
  3. 正面验证：D40"失败也提取 patch"的设计立功——14182 死前写好的 22 行 patch 照常提交。
- **调整**（`99307c0` + `14696d3`）：
  1. `oh ask --max-turns` 落地（核心 CLI 新旋钮），adapter 默认传 40；
  2. 能力面 prompt：无沙箱明说"不可执行、不要建任何新文件、靠静态推理"；
     有沙箱说"可以跑命令但结束前删 scratch"。
- **教训**：prompt 必须如实声明运行环境的**能力面**，否则模型按它想象的环境行动。

### 节点 3 — A/B 对照：修复全部命中 + DashScope 流中断首现

- **事件**：6 题重跑 A/B（基线 `3ecd304`，证据 `be58ebb`）：6/6 completed、
  scratch 文件 3/3 清零、轮次全面下降（19→11、16→4、13→7）、patch 收敛到最小修复形态
  （84→13、70→13、54→23 行）。中途 3 题 DashScope 流中断
  （"peer closed connection, incomplete chunked read"），手工剔行重跑后干净通过。
- **暴露**：
  1. 传输层失败被混在 `invalid-envelope` 里——环境噪声和解析问题在归因里必须可分；
  2. 重试失败题要手工编辑 jsonl——缺一等操作；
  3. **harness 级发现**：`api/retry.py` 的重试不覆盖流中断（mid-stream disconnect）——
     重试策略只认限流/5xx 形态的错误。记入 harness backlog。
- **调整**（`a0ba4ba`，次日）：`api-failed` 独立 status（认 stderr 的
  "Request failed (HTTP..." 签名）+ `--retry-failed` 旗子（prune 非 completed 行，
  借 resume 幂等重跑）。

### 节点 4 — 全量首启，静默死亡

- **事件**：用户手动启动全量 300 题；records 在 20:07 停更于 8 题，**无任何失败记录**。
- **诊断**：不是断网——断网只会让批次快速记一串 api-failed 继续爬；records 完全停更
  = 进程被整个杀掉（终端关闭/睡眠，无法事后确证）。
- **调整**：重启改用 `nohup`（脱离终端）+ `caffeinate -is`（防睡眠）双保险。
- **教训**：无人值守长跑的敌人清单要**穷举**：网络、电源、睡眠、终端生命周期、
  ——以及下一节点才学到的：**账户余额**。

## 2026-07-09

### 节点 5 — 欠费雪崩：272 题 api-failed

- **事件**：nohup 重启后批次"跑完"300/300——但 272 题 api-failed。detail 聚类一发命中：
  全部是 **Arrearage**（"Access denied... account in good standing"）。账户余额在跑完
  16 题后耗尽，其后每题被秒拒，批次数小时内把剩余题目刷成失败。
- **暴露**：
  1. 早晨的连通性预检（curl → 401 应答）只能证明"服务器可达"，**探不出计费状态**——
     余额是独立的预检维度；
  2. 正面验证 ×2：差异化 status + detail 让诊断只花一次 grep（若当初只有笼统的
     "failed"，就得逐题翻 stderr）；`--retry-failed` 前一天刚建好，正好接住 276 题重跑。
- **调整**：充值 → `--retry-failed` prune 276 行 → 重启（nohup + caffeinate 同款）。
- **教训**：预检清单补上**配额/余额**；快速失败的雪崩本身无害（幂等重跑），
  但监控要能在雪崩**开始时**报警，而不是结束后发现（→ 节点 6 的哨兵）。

### 节点 6 — 监控假警报：tail -f 的默认回放

- **事件**：装了失败哨兵（`tail -f | grep` api-failed），秒报一串 sympy 失败 + done 总结
  ——全是**旧日志的末尾回放**（`tail -f` 默认先吐最后 10 行）。
- **调整**：`tail -f -n 0`，只看新增行。
- **教训**：监控自身也要验证——假警报消耗的信任比漏报更贵。

### 节点 7 — 惯犯确认：django-11019 连续两轮 timeout

- **事件**：retry 批次首个信号：`django-11019` 二次 timeout（两轮各顶满 900 秒；
  两轮环境不同——一次正常网络有钱、一次是本轮——排除环境因素）。
- **初步归因**：模型侧收敛失败（啃不动或循环），非 harness/环境。失败分类学的第一个
  "模型责任"预定样本。同型嫌疑 `django-11564` 随后**确认**：同样二轮 timeout（2/2），
  两个惯犯的复现模式一致——qwen3.7-max 在这两题上稳定跑不完。第三个同型样本
  `django-11797`：这轮撞的是**新的 40 轮顶**（412s 烧完 40 轮，零 diff 产出；上轮撞
  20 轮顶）——证明不是预算不够而是模型收敛失败，加轮次只是加倍烧钱。顺带正面验证：
  engine 日志里 `max_turns=40`，`--max-turns` 旋钮确认通到引擎。
- **待验证节点**：`django-11910` 是第一个纯欠费失败题——它 completed 与否是
  "充值生效"的判决信号。

### 节点 8 — 思考模式伏击：provider 在战役中途翻转了模型默认行为

- **事件**：充值后重启，前 4 题全灭（2 timeout 惯犯 + 1 turn-cap + **11910 非惯犯也
  timeout**：900s、零 patch、stderr 零警告）。"零警告的满时长死亡"不像模型笨，像每轮
  极慢——直接探针 API：健康（3.4s 往返），**但响应带 `reasoning_content`**。
- **判别实验**（同一问题 A/B）：默认（思考开）48.0s / 9,176 思考字符 / 2,559
  completion tokens；`enable_thinking: false` 1.4s / 22 tokens。**34× 延迟、100× 输出
  token**——agent 大上下文回合下每轮分钟级，900s 装不下，且输出按 ¥18/M 计费在成倍烧钱。
- **根因**：DashScope 在战役中途（充值/套餐变动窗口）把 qwen3.7-max 的默认翻转为思考
  模式。昨天 16 题快速完成 = 当时思考未开。**实验条件被 provider 单方面改了。**
- **暴露（harness 第 5 个真缺口）**：oh 没有 provider 特有请求参数的透传通道——
  `enable_thinking` 无处安放，benchmark 被堵死。
- **调整**（`6e31c6b`）：`OPENHARNESS_EXTRA_BODY`（JSON）→ `Settings.extra_body` →
  client 并入 SDK `extra_body`。**通用透传，不进任何 per-provider 分支**（对齐
  design-for-strong-model 的契约观）。`_pin_config` 随 key/model 一起注入子进程——
  与节点 1 同一个漂移陷阱，一次修对。
- **教训**：
  1. 云端模型的默认行为是**会被 provider 中途改掉的实验变量**——长战役要么显式钉死
     每个行为开关，要么在 records 里留能事后发现漂移的信号（本次靠"零警告满时长"形态
     + 直接探针破案）；
  2. 诊断路径值得复用：形态反常 → 绕过全栈直接探 API → 最小 A/B 判别实验。

---

## 累计账（滚动更新）

| 维度 | 数 |
|---|---|
| adapter 冲出的 harness 真 bug/缺口 | 5（版本漂移、配置源漂移、--max-turns 缺失、retry 不覆盖流中断、无 provider 参数透传） |
| prompt/策略修复经 A/B 验证 | 2（能力面声明、轮次上限），全部量化命中 |
| 运维/实验教训 | 5（nohup/caffeinate/余额预检/监控回放/**provider 中途翻转模型默认行为**） |
| 被验证的设计决策 | 3（失败也提取 patch、差异化 status、resume 幂等） |

### 节点 9（2026-07-10）— 修复生效 + 惯犯平反：归因被实验条件反转

- **事件**：`enable_thinking:false` 生效后恢复批次，前 5 题 4 completed。
  **`django-11019`——节点 7 里连续两轮 timeout、被归因"模型收敛失败"的惯犯——
  193s/28 轮干净完成，120 行 patch。**
- **归因修正**：11019 平反 = 思考模式受害者，不是模型啃不动。反推思考翻转发生得比
  节点 8 判断的更早（昨日白天批次已中招）——节点 7 的"三惯犯 = 模型责任"结论**作废
  重审**。当前仅存的真·硬骨头候选：`11564`（关思考后仍 40 轮烧尽，但产出 38 行 patch）
  与 `11797`（待本轮复验）。
- **方法论教训（分类学核心样本）**：**归因必须标注实验条件**。同一失败形态在
  "思考开/关"两个条件下结论相反；不控制条件的归因会把环境变量的锅扣给模型。
  报告里每条归因都要带条件戳。

## 当前状态（2026-07-09 暂停点）

- 批次**已暂停**（用户指令：等信号再开始）。24/300 completed 且干净；本轮 5 个失败
  （2 惯犯 timeout、1 惯犯 turn-cap、11910/11964 疑似思考模式受害者）**尚未 prune**。
- 思考模式修复已就位未实战：`.env` 已配 `OPENHARNESS_EXTRA_BODY={"enable_thinking": false}`。
- 恢复命令：`nohup uv run oh bench swebench run --retry-failed --timeout 900 >> benchmarks/swebench/full-run.log 2>&1 &`
  （caffeinate 需确认存活）。恢复后第一件事：**验证首题 completed 且时长回到 ~2 分钟级**
  ——那是 extra_body 链路在真实批次里生效的判决。
