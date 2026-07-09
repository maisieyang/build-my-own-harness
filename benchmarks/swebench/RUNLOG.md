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
  "模型责任"预定样本。同型嫌疑：`django-11564`（同为旧轮 timeout）。
- **待验证节点**：`django-11910` 是第一个纯欠费失败题——它 completed 与否是
  "充值生效"的判决信号。

---

## 累计账（滚动更新）

| 维度 | 数 |
|---|---|
| adapter 冲出的 harness 真 bug | 4（版本漂移、配置源漂移、--max-turns 缺失、retry 不覆盖流中断） |
| prompt/策略修复经 A/B 验证 | 2（能力面声明、轮次上限），全部量化命中 |
| 运维教训 | 4（nohup/caffeinate/余额预检/监控回放） |
| 被验证的设计决策 | 3（失败也提取 patch、差异化 status、resume 幂等） |
