# Dogfood — D48 `/goal` v1 首跑 + 三天后的产物验收(F17/F18)

> 两段时间拼成的一次完整 dogfood:**2026-07-24** 作者亲手在 `oh chat` 里
> `/goal todo-mvp/GOAL.md`(D48 落地当日,产出 D48.9 与 mode-spectrum §五);
> **2026-07-28** 补跑「验产物」——把生成的 API 真的装起来、curl 一遍验收
> 标准。补验的初衷是**检验判官那次判决对不对**,结果撞出两个更硬的东西:
> 判决**根本没能留痕**(F17,真 bug),以及 goal 循环**死在权限墙上**(F18)。
>
> 证据源:on-disk snapshot
> `~/.openharness/snapshots/build-my-own-harness-4cbb25d41221/history/6af5280-20260724101455.json`
> (27 messages,该 session 最后一份)+ 7-28 的 curl 实测。

## 一、7-24 那次 goal 循环实际发生了什么(读快照,非回忆)

消息流骨架:

| # | role | 内容 |
|---|---|---|
| 0 | user | `[goal-status] set: todo-mvp/GOAL.md`(哨兵,D48.7) |
| 1 | user | `[goal set] Work toward this goal now: ...immediately start working`(kickoff,D48.9) |
| 2-21 | — | Read GOAL.md → 探目录 → Write ×3(package.json / server.js / store.js) |
| 22 | assistant | "Now let me install dependencies and run the full CRUD verification." → Bash |
| 23-25 | — | **Bash 被权限拦截**;模型试 `--auto` 未果("can't control that from here") |
| 26 | assistant | 列出已创建文件,停 |

**D48.9 得到实证**:哨兵 + kickoff 两条都在,模型第 2 条消息就动手,零后续
用户输入——"set 即点火"生效。

**没有任何 `[goal-status] met` / `[goal checker] not met` 消息**。而且
10:14:55 是该 session 最后一份快照——**其后没有第二个 turn 跑过**,所以
「判官判负 → 自动续跑」这条路被排除。剩下两种可能(判官判达成后清除 /
作者在判官跑完前退出)**无法从留痕区分**——原因就是 F17。

## 二、F17 — goal 达成/清除哨兵永远晚快照一拍,退出即丢

**机制**(代码路径钉死,非推测):

- 快照写在**引擎内部**:`run_query` → `engine/query.py:141
  write_session_snapshot(messages=final_messages)`,时点 = user turn 结束;
- goal 判官跑在**引擎外部**:`cli.py:2306` 起,在 `await run_query`
  (2234)与 `history = captured`(2247)**之后**;
- 达成哨兵 `history.append(build_goal_sentinel("met", ...))` 在 `cli.py:2323`
  ——**追加进的是一个刚刚已经被落盘过的 history**。

于是:`set` 哨兵(turn 之前追加)进得去快照——快照 message[0] 即为实证;
`met` / `cleared` 哨兵(turn 之后追加)**只有当之后还有一个 turn 跑过**才
被顺带持久化。

**后果(用户可见)**:goal 判达成 → 响铃 → 作者满意地退出 → 下次
`oh chat --resume` → `find_active_goal` 扫到孤零零的 `set`,后面既无 `met`
也无 `cleared` → **复活一个已经达成的 goal,并立刻 kickoff 开跑**。
`/goal clear` 后立刻退出同理。这是 D48 里最"贵"的一种错——不是不工作,是
**在人不在场时自作主张地重新开始**。

**为什么 41 个测试没抓到**:`find_active_goal` 的测试喂的是**内存里的
message 列表**,`--resume` 的测试喂的是**手工构造的 snapshot dict**。两端
各自成立,**没有一个测试跨过"哨兵怎么从内存进到磁盘"这道缝**——缝正是
bug 所在。典型的 unit 绿、接缝红。

**D48.7 的假设漏了一个前提**:决策原文写"对话流是唯一事实源,崩溃安全,
零状态同步"。成立的前提是**事实源的写入点归我管**。实际上写入点归引擎
所有(per-turn),而 goal 的状态变更发生在引擎之外、turn 之后——**两个时钟,
D48.7 默认它们是同一个**。

**修法候选**(未裁决):①判官前置到引擎 turn 内(动 engine,面大);②REPL
在追加 met/cleared 哨兵后显式补写一次快照(小,但 REPL 拿到 snapshot 写入
口子是新耦合);③退出时 flush(只治退出,治不了崩溃)。倾向 ②。

## 三、F18 — 续跑式 goal 撞上动作门(ASK)必然停摆

7-24 的循环停在 message 23:模型要 `npm install`,Bash 被权限拦,模型明说
"`--auto` 是启动参数,对话中途无法生效"——**F11 的第二次发作,这次是在
goal 循环里**,后果比上次严重一档:

- F11(Day 2)的后果是**一次对话被打断**,人在场,重开即可;
- 在 goal 循环里,后果是**整个"人可以走开"的前提塌掉**。goal 的全部价值
  是把人从"续接函数"位置上撤下来(§5.3 中层);而动作门要求人在场按 y/n
  ——**两者结构互斥**。而 OH 连当场按的入口都没有(F11),所以不是"卡一下
  等你按",是**死在墙上,判官对着一份没验证过的产物判**。

这把 §5.1 的"排干动作门三件套"从一条理论主张升级成**阻塞项**:goal 模式
下,`allow 规则 / sandbox / 可逆性` 不再是"让体验更好",是**能不能用**。
优先级:goal 涉及任何需要执行的任务(装依赖、跑测试、起服务)——即绝大
多数——都会撞这堵墙。

**顺带证实**:模型撞墙时的行为是**诚实报告 + 停**(不是编造成功),与 Day 2
D2-5 同族的正样本。运动员没有为了讨好判官而谎报。

## 四、产物验收:5 条验收标准全过,一道裂缝

7-28 实测(`npm install && node server.js`,curl 逐条打):

| GOAL.md 验收标准 | 结果 |
|---|---|
| 1 启动监听 3000 | ✅ `Todo API running on :3000` |
| 2 完整 CRUD | ✅ 201/200/200/**204 无 body**;倒序正确;真 uuid-v4 |
| 3 重启后数据仍在 | ✅ 另起冷进程读到持久化数据 |
| 4 无效请求 400 + JSON | ⚠️ 见下 |
| 5 未知路由 404 + JSON | ✅ `{"error":"not found"}` |

五类字段校验错误(空 title / 非 string / 超 200 / PATCH 空 body /
`completed` 非 bool)和两类 404 的**文案逐字对上契约**。

**裂缝**:`POST` 残缺 JSON body(`{"title":`)→ body-parser 抛错走 Express
默认 HTML 错误处理器,吐完整堆栈 + 绝对路径:

```
[400] <!DOCTYPE html> ... <pre>SyntaxError: Unexpected end of JSON input
   at parse (/Users/.../todo-mvp/node_modules/body-parser/lib/types/json.js:96:19)
```

状态码对,格式不对。违反 `server.js 规格` 明写的「统一错误格式
`{ error: string }`」;对验收标准 4 则取决于读法(字面"无效请求"含它 = fail;
契约小节只枚举字段校验 = 规格留白)。缺一个 4 行的 JSON error handler。

**这条裂缝的位置有意思**:它写在 spec 的「server.js 规格」小节,**没有进
「验收标准」清单**。执行侧漏掉的,正好是条件文档里没被列成验收项的那一
条——§5.3「条件文本是行为的引力场」在**执行侧**又得一证。判定侧本来想
拿这条做对照(判官漏没漏同一条),但判决没留痕(F17),**这半边没有证据,
不写结论**。

## 五、发现台账

| # | 发现 | 去向 |
|---|---|---|
| **F17** | goal `met`/`cleared` 哨兵追加在引擎快照写入**之后**,退出前无第二个 turn 即丢盘 → `--resume` 复活已达成/已清除的 goal 并自动 kickoff。测试两端各自绿,没有跨"内存→磁盘"接缝的用例 | **已修**(当日,候选 ②,D48.10):`append_messages_to_snapshot`(不 rotate、其余字段原样)+ REPL `_extinguish_goal` 同步落盘。先见 RED(磁盘上 goal 仍报活着)再修;补 3 例跨缝 + 8 例服务层,并用两次变异检验确认会咬 |
| **F18** | 续跑式 goal 撞 Bash ASK 动作门必然停摆——goal 的前提(人走开)与 ASK 的前提(人在场)结构互斥,叠加 F11(无当场批准入口)= 死在墙上。判官因此对着未验证的产物判 | **升级 F11 优先级**:§5.1 三件套从"体验"变"可用性"。Ch7 + Ch17 素材 |
| F19(轻) | todo-mvp 残缺 JSON body → Express 默认 HTML 错误页,泄露堆栈 + 绝对路径,违反"统一错误格式" | dogfood 产物层,非 harness bug。留作**条件写法**的样本(见下),不修 |
| 正样本 | D48.9 "set 即点火" 快照实证:哨兵 + kickoff → 模型第 2 条消息即动手,零后续输入 | D48.9 验收证据 |
| 正样本 | 模型撞权限墙时诚实报告 + 停,未谎报成功(Day 2 D2-5 同族) | 判官输入可信度的旁证 |
| 方法论 | 「验收标准」小节事实上是执行侧的引力场边界;写在 spec 里没进验收清单的要求会被漏掉 | 条件写法最佳实践,喂 §5.3 / Ch17 |
| 方法论 | 判决不留痕 → 三天后无法复盘"判官判了什么"。这既是 F17 的产品后果,也是**当场落盘**的又一次教训 | RUNLOG 纪律 |

## 六、章节素材包

- **Ch7(权限/隔离)**:F18 是 F9→D44→F11 这条线的**终局论证**——权限设计
  的代价直到"人真的走开"那一刻才全额显现。"ASK 是给人在场的世界设计的"
  这句话,需要一个 goal 循环死在墙上才讲得清楚。
- **Ch17(接触面/模式)**:F17 是**状态归属**的教科书案例——两个组件各自
  正确、各自有测试,bug 长在"谁拥有写入时点"的缝里。配 §5.3 的引力场,
  一正一反:条件文本的影响力在执行侧被证实,而**状态持久化的责任边界**
  是同一个模块里没想清楚的另一半。
- **Ch9/Ch10(eval)**:F17 是"unit 绿、接缝红"的样本——**这个眉头值得换成
  代码**:一条跨内存→磁盘→resume 的接缝测试。同时它也说明 dogfood 的
  不可替代性:41 个测试全绿的模块,一次真实退出就露馅。

## 七、D48 收口影响

- §四 acceptance 的「dogfood」一条**不能勾**:本次跑出 F17(阻塞)+ F18
  (设计级冲突);「不可达条件 → 上限暂停」「`--resume` 恢复活跃 goal」
  两条体感仍未跑——而 `--resume` 那条现在**预期就是红的**(F17)。
- §六 wiring audit 需回填两处:`services/snapshot` 的 verdict 从
  `unchanged` 改判(F17 的修法 ② 会动它);`prompts/` 实测为 `unchanged`
  (`goal_prompt_section` 落在 `repl.py`)。
