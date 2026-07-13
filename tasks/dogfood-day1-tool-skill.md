# Dogfood Day 1 — 面 #2 工具选择 + 面 #5 skill 触发

> eval 周计划 Day 1(tasks/eval-week-plan-2026-07.md)。两面均已有 eval
> (tool_choice bar 8/8、skill_trigger bar 7/9),所以今天是 **review 型**:
> 拿体感去审 dataset——凡"体感与画像对不上"的,就是飞轮进料。
> 巧思:练习 4-6 把 skill_trigger 的 **eval fixture 提升为真实 skill**,
> 你的体感与 eval 画像同分布,差异直接可归因。
> 结果记录体例照旧:题目/预期/结果/分析,收进 learnings/dogfood-day1-*.md。

## 准备(一次性,复制整段)

```bash
mkdir -p ~/.openharness/skills
# 把 eval 的 4 个 fixture skill 装成真 skill(描述与 evals/skill_trigger/dataset.yaml 逐字一致)
cat > ~/.openharness/skills/parse-credit-report.md << 'EOF'
---
name: parse-credit-report
description: 解析个人征信报告(PDF 抽取文本或原文粘贴), 提取账户明细、逾期记录、硬查询次数, 输出结构化摘要. Use for any task about reading or extracting facts FROM a credit report.
---
解析流程: 先分区(账户/逾期/查询), 再逐区抽取字段, 最后输出三段式结构化摘要(账户概览/风险信号/查询统计)。
EOF
cat > ~/.openharness/skills/loan-contract-review.md << 'EOF'
---
name: loan-contract-review
description: 审查贷款/借款合同条款, 标注对借款人不利的条款(利率跳升、提前还款罚金、交叉违约), 给出风险等级. Use for tasks about reviewing loan CONTRACT terms, not credit reports.
---
审查清单: 利率条款(浮动机制/跳升触发)、费用条款(提前还款罚金/服务费)、违约条款(交叉违约/加速到期)、担保条款。每项标风险等级(高/中/低)并引用原文。
EOF
cat > ~/.openharness/skills/sql-query-optimizer.md << 'EOF'
---
name: sql-query-optimizer
description: 分析慢 SQL 查询, 给出执行计划解读、索引建议和改写方案. Use when the user shares a slow SQL query needing optimization.
---
优化步骤: 读查询结构 → 识别全表扫描/隐式转换/函数包裹索引列 → 给出索引建议(具体到列序)→ 给出改写版本并说明预期收益。
EOF
# commit-message-style 上轮已装过,确认还在:
ls ~/.openharness/skills/
```

**预期**:列出 4 个 skill 文件。

---

## 练习 D1-1 — 带上下文的参数构造(tool_choice 的 Known gap)

eval 里 TC1 曾断言 command 含 "pytest" 而 4/4 失败于 "make test"——因为
**合成环境无项目上下文,那个断言不成立**(dataset notes 记为 Known gap)。
今天你在真仓库里,上下文齐全(CLAUDE.md 写着 `uv run pytest -q`),
这是 eval 测不到、只有 dogfood 能测的形态。

```
cd ~/2026/aa/harness/build-my-own-harness && oh
>>> 跑一下全量测试,看看现在是不是绿的
```

**看什么**:它构造的 Bash command 是项目正确的 `uv run pytest -q`(读了
CLAUDE.md/Project Instructions)还是裸 `pytest`?
**判读**:前者 = 上下文感知的参数构造成立(L2 任务集的现成正样本);
后者 = 大发现——注入的项目指令没被参数构造消费,飞轮重磅进料。
(测试要跑 ~90 秒,不想等可以看到命令后 Ctrl+C 中断。)

## 练习 D1-2 — 工具辨析(TC2 的真实版)

同一会话连问两条:

```
>>> 统计一下 src/ 里有多少处 TODO 注释
>>> 把 README.md 的第一个小节标题读给我
```

**看什么**:①第一条用 Grep 还是 Bash grep?②第二条用 Read 还是 Bash
cat/head?**判读**:专用工具优先 = TC2 结论在真实环境复现;混用不算错
但记录形态(eval 的 forbidden_tools 判罚是否过严的一手证据)。

## 练习 D1-3 — 克制(TC5/TS3 的真实版)

新开会话(`oh`),连问两条:

```
>>> 今天有点累,随便聊聊
>>> 帮我写一个函数,计算一组贷款的等额本息月供
```

**看什么**:①闲聊句有没有触发任何工具?②第二条有 '贷款' 字眼且金融
skill 已安装——**该不该触发 loan-contract-review?不该**(写代码≠审合同,
这是 TS3-near-miss 的真实版)。触发了 = 关键词党实锤,eval 与体感互证。

## 练习 D1-4 — 委派边界(委派吸引子治愈后的真实验证)

```
>>> 根据当前工作区未提交的改动,按项目规范写一条 commit message(不用真提交)
```

**看什么**:动作顺序——**LoadSkill(commit-message-style) 是不是第一个
动作**(先拿规范再看 diff)?这是 v2 引导语治愈委派吸引子后在真实多步
任务上的复验(eval 里 TS1-release-notes 已 4/4 绿,看真实世界守不守得住)。

## 练习 D1-5 — 直答残留的真实频率(TS5 残留,eval 里 2/4 抖)

新开会话,把一条慢 SQL 直接粘进去:

```
>>> 这条查询要跑 40 秒,帮我优化: SELECT * FROM orders o JOIN users u ON o.uid = u.id WHERE u.city = '上海' ORDER BY o.created_at DESC
```

**看什么**:调不调 LoadSkill(sql-query-optimizer)?eval 里这个形态
2/4 直答。**为了拿频率,这条请重复跑 3 次(每次新开 `oh`)**,记 x/3。
体感频率 vs 画像频率(50%)的偏差本身就是数据。

## 练习 D1-6 — 显式点名 + A3 恢复的现场直播

```
>>> 用 sql-query-optimizer 这个 skill 帮我优化: SELECT count(*) FROM logs WHERE date(created_at) = '2026-07-01'
```

**看什么**:这是 skill-名-当-工具幻觉(eval 里 1/4 噪声)的最强诱导——
用户亲口把 skill 名说出来。两种健康结局:①直接
LoadSkill(name="sql-query-optimizer") ✓;②幻觉调了 `sql-query-optimizer`
工具 → **观察引擎的 tool-not-found 回喂能不能把它救回来**(A3 恢复的
现场直播——单步 eval 判它死刑,多轮引擎给它第二条命,你将亲眼看到
哪个才是真实严重度)。

---

## 收工清单

- 每题记:命令(原样)/ 关键观察(工具调用序列)/ 一句判读
- 特别标注:任何"体感与 eval 画像对不上"的时刻(方向不限——比画像好
  也算,比画像差也算)
- 交回来后我做:结果落盘 learnings/dogfood-day1 + 飞轮沉降(有料的话
  进对应 dataset)+ Ch9/Ch6 素材包
