# Learnings 04 — CLI 实现 (P1-T4)

> Phase 1 capability "oh ask 跑通流式输出 + 错误人话提示 + 集成测试 gated"
> 完成。这份文档承载三件事：实现策略落地（原 decisions/05 D5.5 / D5.6 / D5.8
> 移过来的）、验证结果、可复用经验。

## 实现策略

### 1. 流式渲染 — append-only `print(end="", flush=True)`

实现：`src/openharness/_stream_render.py:render_stream`

- `ApiTextDeltaEvent` → stdout，不换行、flush
- `ApiRetryEvent` → stderr，不污染 stdout（`oh ask | tee out.txt` 干净）
- `ApiMessageCompleteEvent` → 末尾换行（仅当 emit 过 text 时；空响应不留 stray 空行）

替代方案 Rich `Live` markdown 重渲被排除：每个 delta 重渲整段会闪烁 / 复杂 /
终端 resize+scrollback edge cases 多。Markdown 重渲、`--output json` / 完整
Print mode 留到 Tier 1。

### 2. 错误 UX — 按异常类型差异化 hint

实现：`src/openharness/cli.py:ask` 5 个 except blocks

- `pydantic.ValidationError` → "Configuration error" + `OPENHARNESS_API_KEY` 提示
- `AuthenticationFailure` → "Authentication failed (HTTP 401)" + key 提示
- `RateLimitFailure` → "Rate-limited after retries (HTTP 429)" + 等待重试提示
- `RequestFailure` → "Request failed (HTTP <status>)"
- `OpenHarnessApiError`（catch-all）→ "API error: <msg>"

所有错误 → exit 1、stderr、不显 traceback（`--debug` 留到 Tier 1）。

### 3. Phase 1 范围 — 仅 streaming text

无 `--output json` / 无 markdown 重渲 / 无 `--debug` flag。这些是 Tier 1 才考虑的。

## 验证

- 测试：`tests/cli/test_cli.py`（happy path 5 + error UX 4 + arg validation 2 = 11）
  + `test_render.py`（4）+ `test_smoke.py`（1）+ `test_integration.py`（1，marker gated）
- coverage：`_stream_render.py` 97% / `cli.py` 89% / 整体 92.83%
- mypy strict / ruff / ruff format 全 clean

## 可复用经验

### 教训 1: seam 注入 over monkeypatch SDK

`cli.py` 暴露 `_load_settings` / `_build_client` 两个 module-level seam，
测试用 `monkeypatch.setattr(cli_module, "_build_client", lambda _: stub)` 替换。
这比 monkeypatch 进 `AsyncOpenAI` 内部干净——测试不知道底层 SDK 长什么样。

未来加 frontend（HTTP server / TUI）时同一个 pattern 可复用：暴露窄的
module-level seam，frontend 实现注入。

### 教训 2: conftest.py 的 marker-aware carve-out

`tests/cli/conftest.py` 用 autouse fixture 清理 `OPENHARNESS_*` env，但通过
`request.keywords` 让 `@pytest.mark.integration` 的测试**保留**真实 env：

```python
@pytest.fixture(autouse=True)
def _clean_openharness_env(request, monkeypatch):
    if "integration" in request.keywords:
        return  # carve-out: 集成测试需要真 env
    for var in ("OPENHARNESS_API_KEY", "OPENHARNESS_BASE_URL", "OPENHARNESS_MODEL"):
        monkeypatch.delenv(var, raising=False)
```

否则集成测试会被 fixture 反向破坏。这个 pattern 是处理"unit + integration
共用 env"的标准答案。

### 教训 3: --max-tokens 是 spec 之外的 emergent UX

原 capability 描述只有 `prompt + --model`，build 时多加了 `--max-tokens`
（防止"hi"被截断）。这是合理的 emerge——按新工作流，做完后 review 决定保留并
写进 spec，而不是 build 前就预先钉死。

### 教训 4: append-only 比闪屏 markdown 渲染更"Unix"

Append-only 能和 pipe 组合（`oh ask | tee` / `oh ask | jq`）。Rich `Live`
渲染会破坏管道，因为它写 ANSI escape codes 重渲屏幕。Phase 1 选 append-only
最强的理由其实不是"简单"，是"composable with pipes"——这才是 Unix 哲学的内核。

## Open

- 未来 `--debug` flag（显示 traceback）：Tier 1 加
- 未来 `--output json/text`：Tier 1 加
- 未来 `--no-stream`：Tier 1 决定要不要
