# build-my-own-harness — 项目协作约定

## 项目性质

单人 + Claude 协作的学习型 LLM harness 项目。**协调成本 ≈ 0，决策大多可逆。**

- **起点**：`REFERENCE.md`（开源 OpenHarness 完整逆向，固定输入）
- **目标**：(1) 交付生产级 LLM harness  (2) 通过项目实践成为领域专家
- **策略**：从 0 到 1，capability 级 spec → agent 自主 build → 人审 review

## 工作流

利用 Claude Code 内置的 Plan / Execute 模式 + 一个明确的 Review 检查点。

| 阶段 | 工具 / 模式 | 颗粒度 | 谁决定 |
|---|---|---|---|
| **Spec** | Claude Code Plan 模式 | capability | 人 |
| **Build** | Claude Code Execute 模式 | sub-task runtime 决定 | agent |
| **Review** | walkthrough 对话 | capability | 人 |

**Spec 颗粒度**：

- ✅ "P1-T4: oh ask 跑通流式输出 + 错误人话提示 + 集成测试 gated"
- ❌ "4a Settings → 4b mock → 4c 真 client → 4d 集成 → 4e __init__"

**Build 时 agent 主动停下问**：外部契约决策（公开 API / env / 依赖）/
不可逆操作（删文件 / 改 schema / 改公开接口）/ 发现 capability 描述需要修正。

**Review 时机**：capability 完成 + 测试 GREEN 后，**commit 前**。
agent 做代码 ↔ 验收逐条对照的 walkthrough，不要机械 GREEN → COMMIT。

## 文档分工

| 文档 | 角色 | 时机 |
|---|---|---|
| `REFERENCE.md` | 起点：OpenHarness 完整逆向（不动） | 一次性输入 |
| `SPEC.md` | 项目级契约：做什么 / 不做什么 / 边界 | 少变 |
| `ARCHITECTURE.md` | 战略：Tier / Phase 顺序 / 依赖图 | 跨 Phase 调整 |
| `tasks/` | 当前 Phase 的 capability 级 todo（**不到 sub-task 级**） | 进入 Phase 前 |
| `decisions/` | **只**记外部约束 + 不可逆决策 | 决策时 |
| `learnings/` | 实现策略 + 阶段复盘 | 模块完成后 |

## Tone

用户已内化"框架构建者"心态，对话保持在契约/抽象层，不回退到细节级。
