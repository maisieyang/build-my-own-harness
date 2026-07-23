# Decision 46 — 面 #6(sub-agent)eval 边界:设计就绪、建设等触发

> Created 2026-07-23 · 上游:D41(决策面 #6 = SpawnAgent 委派+收割,P3 等触发)、
> D45(secondary-pass eval 范式)、REFERENCE.md §子 agent 三重防护。
> 被测生产代码:`tools/spawn_agent.py`(`SpawnAgent`,cli.py 注册的 shipped tool)。

## 一、Why now

面 #6 是 7 面里**唯一零覆盖的整面**,B2/B3 完成后它是账面上最显眼的缺口。但
D41.4 早把它划在 **P3 "维持等触发"**(与 B4 同档)。本 doc 的职责不是急着建,
而是先把边界钉死:**哪部分归 TDD、哪部分归 eval、eval 那部分的硬 oracle 是
什么、以及现在该建还是该像 B4 一样 park。**

## 二、把面 #6 切成两层:机制(TDD)vs LLM 决策(eval)

`spawn_agent.py` 混着两类东西,eval **只碰第二类**:

| 层 | 内容 | 归属 | 证据 |
|---|---|---|---|
| **机制** | 深度检查(D16.5)、`dataclasses.replace` 上下文继承(D16.2)、turn 预算、`LoopLimitExceeded` 收口、final text 提取 | **TDD** | 已有 3 个测试文件:`test_spawn_agent.py` / `_e2e` / `_invariant` |
| **LLM 决策** | (a) **何时委派**(vs inline 自己做);(b) **委派 prompt 写得完不完整** | **eval**(本面) | ❌ 零覆盖 |

**结论**:机制不是 eval 的活(确定性、已 TDD 覆盖)。eval 只该测 (a)(b) 两个
LLM 行为子面。

## 三、Decisions

### D46.1 — eval 的真载荷是 (b) "隔离下的委派 prompt 完整性",不是 (a) 委派时机

**Chosen**:面 #6 若建 eval,主攻 **(b)**——子任务 prompt 是否自足。**(a) 何时
委派**判断软(该不该委派是价值判断)、且无 dogfood 证据,与 B4 同理 park。

**Why(核心)**:子 context 继承父的一切**除了对话**——sub-agent 的初始消息
`= [args.prompt]` 一条(spawn_agent.py:157,"totally isolated from the parent's
conversation")。所以父只要在 prompt 里留**悬空指代**("上面那个文件"、"我们
刚说的 port"),信息就**静默丢失**,sub-agent 盲飞。这是**结构性 fail-open**:
隔离由代码保证,懒 prompt 必然漏,父还看不出来。

**Reversibility**:easy。

### D46.2 — oracle = 种植上下文回收(复用 B2 的 D45.1 手法)

**Chosen**:dataset 每 case = 一段父对话,**中段埋一个子任务必需的关键事实**
(如"配置在 /etc/foo.yaml"、"用 port 8080"),再构造一个必须委派、且子任务
**依赖该事实**的情形。判**父写的 spawn `prompt` 有没有把事实带进去**(子串
`=`/keyword)。

**Why**:委派 prompt 本身没有唯一正确答案(措辞无穷),但"必需事实有没有被
carry-forward 进隔离 prompt"可枚举、可确定性判——**和 B2 完全同构**:把开放
生成问题降维成封闭存在性检查。悬空指代 = 事实缺失 = FAIL,并点名漏了哪个。

**Alternatives**:①judge 判"prompt 好不好"(软、贵);②跑子 agent 看结果对不对
(引入子 agent 非确定性 + 二层归因,把单步面污染成多轮)。种植上下文回收是唯一
硬 oracle。**注意**:本面**只测父写的 prompt 文本**,不真跑子 agent(那是 L2
轨迹面,不是 L1 单步)。

**Reversibility**:easy。

### D46.3 — 建设 park,触发条件明写(和 B4 同待遇)

**Chosen**:**设计到此为止(oracle + case 形状 + fail-open 已钉死),不立即建。**
触发条件:**dogfood 或 SWE-bench 归因里第一次出现真实委派**(理想是一次因
prompt 太薄导致子 agent 盲飞的失败)。触发后照 B2/B3 范式建,快。

**Why**:B2 是**热路径** fail-open(compaction 每条长对话无条件触发,load-bearing
= 模型全部记忆),故 P1。面 #6 是**冷路径** fail-open——只在 LLM **选择**委派时
才触发,而 D41.4 判定该路径尚未被证实是主要失败形态。**这正是 P1 vs P3 的分界:
不是 oracle 硬不硬(它硬),是路径热不热。** 飞轮纪律(D41.6):case 该来自观察到
的失败,不该来自读代码时对失败模式的想象——冷路径上后者尤其容易猜错。

**Anti-scope**:明确**不**为凑满面覆盖率(3/4→满、6/7→满)而建。覆盖率不是尺,
load-bearing 才是。若用户以"关掉最后一个零覆盖面"为由要现在建,那是合法的
coverage 目标覆盖 flywheel 纯度——需用户显式拍板,不默认。

## 四、Acceptance(触发后建设时,非现在)

- [ ] `evals/subagent_delegate/`:dataset ≥6 case,每 case 父对话埋 ≥1 必需事实
- [ ] 种植上下文回收 scorer(`=`/keyword)+ 至少一个"悬空指代必漏"反向 case
- [ ] 四声明头 + 引 D46.2 + 复用 substrate + N=4 画像后定 bar
- [ ] cassette 化,replay 进 CI 回放门(第 6 个 eval)
- [ ] 全仓质量门 + dataset_card

## 五、Wiring audit

| Layer | Verdict | 一句话 |
|---|---|---|
| `tools/spawn_agent.py` | unchanged | 只作被测对象被读,机制已 TDD 覆盖,不改 |
| `eval/` | (触发后)extension | 新增 1 consumer 目录 + scorer,复用 cassette/protocol substrate |
| `tests/eval/test_replay_gates.py` | (触发后)extension | 加第 6 个 replay 断言 |
| 其余全部 | unchanged | 纯设计文档,当前 0 代码改动 |

**Conclusion**:本 doc 当前 0 代码改动——纯边界设计。结论:**面 #6 eval 设计
就绪(oracle=种植上下文回收,主攻隔离下 prompt 完整性),建设 park 待触发**,
与 B4(D45.3)同处置。理由:硬 oracle 有,但冷路径 fail-open,无 dogfood 驱动。

## 六、References

- decisions/41-eval-systematization.md(#6 = C2,P3 等触发;D41.4 硬度阶梯 / D41.6 飞轮)
- decisions/45-secondary-pass-evals.md(D45.1 种植事实回收范式;D45.3 B4 park 先例)
- src/openharness/tools/spawn_agent.py(被测对象;:157 隔离保证)
- REFERENCE.md §子 agent 三重防护(工具禁用 + 权限默认拒 + 结构性深度上限)
