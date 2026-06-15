# Intent — PLAYBOOK 重构

> 由 interview-me 收敛,2026-06-15。来源 session `97d7d854`(因 1M 上下文撑爆 529,内容已抢救至 `recovered-session-97d7d854.md`)。

## 确认意图(最终版)

重构 `PLAYBOOK.md`(主)+ `PLAYBOOK-PM.md`(轻量从属),从第一性原理重写、旧 phase-loop 方法论**不兼容直接覆盖**(git history 留痕足够,不另存归档)。

- **Outcome**:两份都重写。方法论那份当**主招牌**,反映现在真用的 solo-dev 流(interview→plan→build/test/eval、测试即 spec);PM 那份做**轻量从属**,点到为止证明 product sense。
- **User**:eng 面试官(主押 eng;"今天的 eng ≠ 传统纯写码",product sense 是加分)。次要受众=作者自检"这套方法论现在到底信不信"。
- **Why now**:README 已重写成 eng-facing artifact 且满意,但末尾 `How it was built` / `Other reader lenses` 两段还链着描述**已废弃旧方法论**的这两份文件,是 artifact 硬伤。
- **Success**:面试官点开 repo——`PLAYBOOK` 读到"能从项目提炼可复用工程方法论"的强信号;`PLAYBOOK-PM` 轻补一手 product sense,不抢 C 位、不让人困惑投 eng 还是 PM。
- **Constraint**:不为延续保留旧 phase-loop/decisions/boundary-doc/R1–R6 叙述;PM 篇幅/权重不得压过方法论;README en + zh-CN 两版链接描述同步更新。
- **Out of scope**:不做 PM 与方法论等重的双巨著;不另存旧内容归档(原地覆盖,git history 即留痕)。

## 判断规则(作者立的)

「必须 → 从第一性原理完全重构,旧的不兼容直接干掉;不必须 → 删」。判定结果:**两份都必须 → 都重构**。

## 第一性原理素材源

- `build-my-own-harness/CLAUDE.md` —— solo-dev 流 / module loop 的当前真相
- `my-skills` 三个自有 skill(reverse-spec / roadmap→并入 / debrief)+ build/test/eval skill
- `learnings/` `decisions/` `tasks/` —— 真实推理留痕(作为证据,不作为方法论叙述)

## 下一步

A 路径:存意图(本文件)→ 读 CLAUDE.md + my-skills → 出新 `PLAYBOOK.md` 骨架审稿 → 点头后写正文 → 再处理 PLAYBOOK-PM(轻量)→ 同步 README en/zh-CN 链接描述。
