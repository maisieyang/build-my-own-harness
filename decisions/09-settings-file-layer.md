# Decision 09 — Settings File 层暂时保留作为 Phase 5 伏笔

- **Date**: 2026-05-09
- **Phase / Module**: Phase 3 entry / P3-T1.1f
- **Status**: Decided

## Context

P1-T4 设计 `Settings` 时定的优先级是 4 级:**CLI > ENV > File > Default**。
其中 File 层指向 XDG 路径 `~/.config/openharness/config.toml`。

`learnings/phase-1-and-2.md` §3.3 走完 Phase 1 + 2 后判决:

> - **CLI 级**:`--model` / `--max-tokens` / `--auto` / `--dry-run` 都走 override 路径,**实测每条都用过**
> - **ENV 级**:`.env` 文件 + `OPENHARNESS_*` 是默认运行路径,实测主路径
> - **Default 级**:三个默认值都用过
> - **File 级**:**Phase 2 全程零使用,没有任何代码路径或测试触达它**

P3-T1.1f Three-Axis 给的是 **保留 + 写决策锁伏笔** 的判决(对抗 "现在砍掉简化
为 3 级")。本文件正式记录该判决。

## Decision

**Phase 3 不动 File 层**——既不实现 TOML 加载逻辑,也不删除已有配置 stub。
File 层保留作为 **Phase 5 multi-profile** 的伏笔。

## Why

**砍 vs 留 trade-off**:

| 维度 | 砍掉(简化为 3 级) | 保留(现状) |
|---|---|---|
| 当下代码量 | 减一些 stub 代码 | 不变 |
| Phase 5 重构成本 | 多 profile 时需要重新加 File 层 | 0(直接激活) |
| 用户心智 | 优先级少一层,简单 | 多一层,但暂时空着 |
| 文档 | 不需要解释"为什么有 File 层但没用" | 需要(本文件就是) |

**关键判断**:Phase 5 真的会需要 multi-profile —— 用户切换 OpenAI / DeepSeek
/ Qwen / Anthropic 多账号时,**ENV 一套环境变量是不够的**(env 只能存一份当前
profile)。File 层是 multi-profile 的天然容器(`~/.config/openharness/profiles.toml`
存多个 profile,CLI 通过 `oh --profile production` 切换)。

砍掉再加回来 = 一次重构成本(改 Settings 字段、改 CLI flag 串联、改 docs);
保留维护成本接近 0(Settings 现在只是声明 File 层位置,不读取它)。**留着**
是更便宜的选择。

## What "保留" 具体意味着

P3-T1.1f 不做任何代码改动。Settings 当前的 4 级优先级声明保留(`pyproject.toml`
里 pydantic-settings 配置,`config/settings.py` docstring 里描述)。

P3-T1.1f **唯一的动作**就是这个 decision 文件——让"为什么 File 层声明在但
没实现"有 grep-able 答案。未来读代码的人(或 future-self)在 `Settings`
源码里看到 File 层但找不到 `_load_toml()` 调用,**会被引向这里**理解
"故意空着"的原因。

## Phase 5 multi-profile 的预期形态(草稿,不锁定)

```toml
# ~/.config/openharness/profiles.toml

default_profile = "qwen"

[profile.qwen]
api_key = "sk-..."
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
model = "qwen-plus"

[profile.openai]
api_key = "sk-..."
base_url = "https://api.openai.com/v1"
model = "gpt-4o"

[profile.deepseek]
# ...
```

CLI:`oh --profile openai ask "..."`。env vars 仍存在,但**优先级位于 File 层
之下**(env 当作"覆盖某个 profile 的某个字段"的手段,而不是 profile 本身)。

**这一部分 Phase 5 入口的 Three-Axis 再正式拍**——本文件只锁"现在不动,
留位"。

## Reversibility

- 砍掉 File 层:future Phase 4/5 入口可重新评估,代价是改回去
- 实现 File 层:未来任何 Phase 都可加,加的时候本文件升级为"已激活"

## Consequences

- `config/settings.py` 4 级优先级声明保留(无代码改动)
- `pyproject.toml` 任何 pydantic-settings 相关配置保留
- 未来读代码的人见到 File 层不读取,grep `decisions/09` 看到本文件
- Phase 5 boundary 决策 `decisions/<NN>-phase-5-boundary.md` 时引用本文件

## 关联

- `learnings/phase-1-and-2.md` §3.3(retro 判决来源)
- `tasks/phase-3-plan.md` P3-T1.1f acceptance(本决策的 binding spec)
- `decisions/05-cli.md`(P1-T4 当时定 4 级优先级的原始决策)
