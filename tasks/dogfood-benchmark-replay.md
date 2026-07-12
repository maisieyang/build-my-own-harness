# Dogfood 设计 — benchmark 事故手动复现包

> Created 2026-07-12 · 上游:`benchmarks/swebench/RUNLOG.md`(12 节点)。
> 动机:战役事故大多发生在自动批跑里,作者只看到汇报;按三拍协议把
> 可复现的挑出来做成手动实验,补齐体感。体例同
> `learnings/dogfood-2026-07-12.md`:题目(命令)/ 预期 / 对应节点。
> 跑完的结果与分析写入 learnings 新档(实验记录与设计分离)。

## 可复现(5 个实验)

### R1 — 配置源漂移(节点 1)

**此刻就活在本机**:`~/.openharness/.env` = `deepseek-v4-flash`,项目
`.env` = `qwen3.7-max`。`.env` 按 **cwd** 解析——换个目录,配置就漂。

```bash
cd /tmp && oh ask -p "reply one word: hi" --output-format json --log-level INFO 2>&1 | grep -o "model=[a-z0-9.-]*" | head -1
cd /Users/yangxiyue/2026/aa/harness/build-my-own-harness && oh ask -p "reply one word: hi" --output-format json --log-level INFO 2>&1 | grep -o "model=[a-z0-9.-]*" | head -1
```

**预期**:两行 `model=` 不同(/tmp 下漂到用户级 deepseek,repo 下是项目级
qwen)。/tmp 那次可能直接报错(用户级 key/endpoint 组合是否有效未知)——
报错本身也是漂移的证据。
**原事故**:首次冒烟署名 `+default`,实跑的模型根本不是以为的那个;
benchmark 修复 = `_pin_config` 显式注入子进程 env。

### R2 — 思考模式伏击(节点 8)

```bash
time OPENHARNESS_EXTRA_BODY='{"enable_thinking": false}' oh ask -p "用一句话说明 off-by-one 错误" --output-format json
time OPENHARNESS_EXTRA_BODY='{"enable_thinking": true}'  oh ask -p "用一句话说明 off-by-one 错误" --output-format json
```

**预期**:对照 wall time 与 envelope 里的 `usage.output_tokens`——战役中
判别实验的读数是 1.4s/22 tokens vs 48s/2559 tokens(34× 延迟、100× 输出)。
**原事故**:provider 中途翻转默认,整批 timeout 且 stderr 零警告;教训 =
云端模型的默认行为是可被单方面改掉的实验变量,行为开关必须显式钉死。

### R3 — 轮次顶死亡 + 兑现的旋钮(节点 2)

```bash
oh ask -p "逐个读取 src/openharness/engine/ 下每个 py 文件,统计各文件行数,最后汇总一个表" --output-format json --max-turns 2
# 预期死于轮次顶后,再:
oh ask -p "同上题" --output-format json --max-turns 20
```

**预期**:第一条 `LoopLimitExceeded`,错误信息指名 `--max-turns`——这个
旋钮在战役前**并不存在**(错误信息承诺了未实现的 flag,harness 真 bug);
第二条正常完成。
**原事故**:astropy-14182 撞 20 轮顶死亡;astropy-6938 以 19 轮贴顶擦过。

### R4 — TLS 分层封锁(节点 11)

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('https://api.swebench.com', timeout=10).status)"
curl -sS -o /dev/null -w "curl: HTTP %{http_code}\n" --max-time 10 https://api.swebench.com
```

**预期**:python SSL EOF 报错,curl 200——同一台机、同一个域名,按客户端
TLS 指纹分层放行/掐断。
**原事故**:sb-cli(python 栈)提交全灭,读源码复刻 API 后 curl 版
300/300 accepted;教训 = 官方工具不可用 ≠ 官方服务不可用。

### R5 — 修复循环全链 + 断点续跑(Track B / 节点 6-7 的机制底座)

```bash
oh ask -p "在当前目录说明 README 的结构" --verify "test -f /tmp/never-exists-file" --max-iter 2 --output-format json
# 记下 emitted json 里 run.run_id,然后:
oh run show <run_id>
oh ask -p "在当前目录说明 README 的结构" --resume-run <run_id> --verify "test -f /tmp/never-exists-file" --max-iter 3 --output-format json
```

**预期**:①verify 命中不可能条件,2 轮全败,`attempts: 2`;②journal 事件
链完整可读;③`--resume-run` 从 attempt 3 继续而非从 1 重来(prompt 必须
与原 goal 逐字一致,不一致会 fail-closed 拒绝——顺便体感这个防呆)。
**原事故映射**:这套 journal/resume 正是 300 题批次断 4 次却零损失续跑、
以及事后归因的机制底座。

## 不可手动复现(透明列出,免得以为漏了)

| 节点 | 事故 | 不可复现原因 | 沉降 |
|---|---|---|---|
| 5/10 | 欠费雪崩 + 免费探活端点掩盖计费状态 | 不能故意把账户烧穿 | 教训已入 RUNLOG:预检必须含一次真实计费调用 |
| 3 | DashScope 流中断不触发重试 | 需网络故障注入 | 排队项 D2(api 层顺手修) |
| 12 | 同 run_id 重跑覆盖汇总报告 | 需 ECS 环境 | 教训已入 RUNLOG:逐题结果是 ground truth,汇总可再生 |
