# SWE-bench Lite 失败分类学

> 官方判定（`official-verdicts.json`）× 每题过程数据（`records.jsonl`）的 join。
> 回答立项时的核心问题：**resolved 之外的失败，多少是 harness 的锅、多少是模型的锅。**
> 模型 qwen3.7-max（thinking off），harness openharness-0.4.0，官方 SWE-bench harness
> 在自建 ECS 上评测（托管服务故障，见 RUNLOG 节点 11–12）。

## 顶层数字

| | 数 | 占比 |
|---|---|---|
| **resolved** | **170 / 300** | **56.7%**（全 300）/ 60.1%（可评测 283）/ 63.7%（已评测 267）|
| unresolved | 97 | |
| 未评测（matplotlib env 构建失败） | 17 | 基础设施问题，非模型/harness |
| 空 patch（模型未产出） | 16 | 计入 unresolved |

对照锚点：Lite 榜首 Opus 4.6 = 62.7%（顶级闭源 + 顶级 harness）。本项目 solo + 中档
定位，距榜首 ~6 个点。qwen3.7-max 无公开 Lite 分（其 Verified = 80.4%，近 Opus 4.6）。

## 核心发现一：失败 100% 归模型，harness 硬失败为零

97 个 unresolved 的本地过程状态归因：

| 本地状态 | 数 | 归因 |
|---|---|---|
| `completed`（harness 跑通、模型产出 patch、但代码错） | 86 | **模型能力失败** |
| `invalid-envelope`（撞 40 轮上限、未收敛） | 11 | **模型收敛失败** |
| harness 崩溃 / 权限误拦 / 上下文丢失 / 循环卡死 | **0** | — |

**结论：failure 里没有一例是 harness 缺陷。** 89% 是模型写出自信但错误的代码，11% 是
模型在轮次预算内没收敛（且 RUNLOG 节点 2/9 已证 20→40 加轮次救不回这些惯犯——是模型
边界不是预算不足）。这是"harness 不是玩具、且没有拖模型后腿"的直接实证：**天花板由
模型画，不由 harness 画。**

## 核心发现二：能力边界按任务领域系统性分布

已评测题的 resolved 率（与 harness 无关，随领域变化）：

| 仓库 | resolved/评测 | 率 |
|---|---|---|
| psf/requests | 6/6 | 100% |
| django | 77/109 | 70.6% |
| scikit-learn | 15/23 | 65.2% |
| pytest | 11/17 | 64.7% |
| sympy | 41/67 | 61.2% |
| sphinx / astropy / pylint | ~ | 50% |
| matplotlib | 2/6 | 33.3% |
| pallets/flask | 0/2 | 0% |

从 100% 到 0% 的跨度由**任务领域**驱动（Web 框架 CRUD 类高、符号数学/绘图库低），
harness 对所有仓库同构——又一条瓶颈在模型侧的证据。

## 核心发现三：对错题的过程指纹几乎重合

- resolved 题 turn 中位数 = **11**
- unresolved（completed）题 turn 中位数 = **13**

模型在做错的题上并没有"明显挣扎更久"——它常常自信地、以相近的轮数交付一个错误的
patch。这说明**模型缺乏对自身正确性的校准**，而非"想做对但资源不够"。对 harness 的
启示：单纯的自我评估不可信（印证 loop-runtime 的设计前提——完成门用外部 verify，
不用模型自评）；真正的杠杆在**给模型一个能自查的验证信号**（sandbox 里跑测试），
而非加轮次或加上下文。

## 数据出处

- `official-verdicts.json` — 170/97 逐题 resolved 判定（自建 ECS 官方 harness）
- `records.jsonl` — 每题 status/turns/tokens/duration（跑批时产出）
- `predictions.jsonl` — 300 题 model_patch（提交物）
- 完整战役过程见 `RUNLOG.md`
