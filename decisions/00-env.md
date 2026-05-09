# Decision 00 — Environment Setup Pitfall (git proxy ↔ shell proxy)

- **Date**: 2026-05-09(P3-T1.1f 补记;实际事件发生在 Phase 1 P1-T1)
- **Phase / Module**: Phase 1 P1-T1 早期 / 环境层
- **Status**: Documented(post-hoc record per learnings/01 §3)

## Context

国内开发场景下,通过代理访问 GitHub 是日常基础设施。Phase 1 P1-T1 期间踩过
一个**和代码无关的环境陷阱**:`pre-commit` 第一次安装时拉 hook 仓库失败,
错误 message 显示 connection issue,但浏览器和 terminal 其它工具都能正常上网。

学习记录(`learnings/01-scaffolding.md` §3)已记下这条,但当时建议"应在
`decisions/00-env.md` 里专门留档"——本文件落实这条建议。

## 现象

```
$ pre-commit install
$ git commit -m "..."
[INFO] Initializing environment for https://github.com/astral-sh/ruff-pre-commit.
An unexpected error has occurred: CalledProcessError: command: ('git', 'fetch', ...)
fatal: unable to access 'https://github.com/...': Failed to connect to ... port 7890
```

陷阱点:**端口号是旧的(7890)**,但**当前代理 app 监听的是新端口(6152)**。
用户其它工具(浏览器 / `curl` / `pip`)都能上网,**唯独 git 不行**。

## 根因

- Terminal env vars(`HTTPS_PROXY` / `HTTP_PROXY`)指向当前代理 app 端口
- `git config http.proxy` / `https.proxy` 指向旧端口
- **git 优先读 git config,不读 env vars** —— 即使 shell 环境是对的,git 仍走错端口
- `pre-commit` 通过 `git fetch` 拉 hook 仓库,**走的是同一个 git 配置**,所以
  pre-commit 也连不上

## 永久 mitigation

每次切换代理 app(Clash → Mihomo → V2Ray ...)或代理升级换端口时,**同步更新
git proxy 配置**:

```bash
git config --global http.proxy http://127.0.0.1:<port>
git config --global https.proxy http://127.0.0.1:<port>
```

或者**完全去掉 git proxy 配置**,让 git 跟随 shell env vars(取决于代理 app 是
否支持透明代理):

```bash
git config --global --unset http.proxy
git config --global --unset https.proxy
```

## Sanity check

新 clone 仓库 / 新 dev 机器搭建时,first-run 校验:

```bash
git config --get http.proxy        # 看 git 走的是什么端口
echo $HTTPS_PROXY                   # 看 shell 走的是什么端口
# 两个值如果不一致 → 触发上面的陷阱
```

## 关联

- learnings/01-scaffolding.md §3 (Phase 1 P1-T1 retro)
- 任何"pre-commit 第一次安装失败 / git fetch 不通 / 但浏览器正常"的现象
- 未来任何"网络通但 git 走不通"的诡异现象,先 check git config

## Consequences

- README troubleshooting 章节(P3-T1.1f 配套)将引用此文件
- 未来 dev onboarding 时 first-run 校验包含这条
