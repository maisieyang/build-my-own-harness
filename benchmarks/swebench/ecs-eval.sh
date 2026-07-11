#!/usr/bin/env bash
# SWE-bench Lite 官方评测 — 阿里云 ECS 一键脚本 (RUNLOG 节点 12)
#
# 背景: 官方托管评测服务故障 (sb-cli issues #25-#28), Modal gRPC 不可达,
# 本地 Mac 磁盘不足 -- 在一台 x86 ECS 上自跑官方 harness。
#
# 机器规格 (香港/新加坡区, 按量付费, 跑完即销毁):
#   - ecs.c7.2xlarge (8 vCPU / 16 GiB) 或同级
#   - Ubuntu 22.04 x86_64
#   - 200 GiB ESSD 系统盘
#   - 分配公网 IP
#
# 用法: 把本脚本整个贴进 ECS 终端 (root), 或:
#   scp 本脚本 + predictions.jsonl 上去后 bash ecs-eval.sh
#
# 产出: /root/eval/reports/ 下的官方报告 JSON (resolved 名单 + 分数)

set -euxo pipefail

# ---- 1. Docker ----
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl start docker

# ---- 2. Python 环境 (uv) ----
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

mkdir -p /root/eval && cd /root/eval

# ---- 3. predictions ----
# 首选: 从仓库拉 (predictions.jsonl 已入库)
if [ ! -f predictions.jsonl ]; then
  git clone --depth 1 https://github.com/maisieyang/build-my-own-harness.git repo
  cp repo/benchmarks/swebench/out/predictions.jsonl .
fi
python3 -c "import json;rows=[json.loads(l) for l in open('predictions.jsonl') if l.strip()];assert len(rows)==300, len(rows);print('predictions OK: 300 rows')"

# ---- 4. 官方评测 harness ----
# --max_workers 6: 8 vCPU 的 75%
# 默认 namespace 从 Docker Hub 拉预构建镜像 (x86, 香港区直连无碍);
# 拉不到的实例会本地构建。
uv run --with swebench python -m swebench.harness.run_evaluation \
  --dataset_name SWE-bench/SWE-bench_Lite \
  --split test \
  --predictions_path predictions.jsonl \
  --run_id oh-lite-v1 \
  --max_workers 6 \
  --cache_level env \
  --report_dir /root/eval/reports \
  2>&1 | tee /root/eval/eval.log

# ---- 5. 摘要 ----
echo "==== REPORT FILES ===="
ls -la /root/eval/reports/ || true
ls -la ./*.json || true
echo "==== 完成后把 reports/ 下的 json 传回本机即可 ===="
