# Learnings — Phase 14 (Web tools + anti-substitution system prompt)

> Phase 14 起止 / 2026-06-02(单日 session,接 v0.2.0 ship 后约 12
> 天的 dogfood + 4 个 patch commit 后开启)
> 5 capabilities (boundary+plan + T1…T4) / **6 commits**(本 retro 是
> 第 6 commit;5 个执行 commit + 1 retro commit)
> 2,818 行净增(src + tests + docs + boundary doc + plan)
> Tests 1982(v0.2.0)→ **~2068**(+86) / ruff + mypy --strict src
> clean / cov ≥95% held
>
> 本文件回答两个问题:
>
> 1. **dogfood-driven phase 跟 abstraction-driven phase 的形状有什么
>    不同?** —— Phase 1-13 大多是"内部抽象催生下一个 phase"
>    (Phase 7a → 7c,Phase 11 substrate → 12/13 consumer),Phase 14
>    第一次是"真用户跑出来的 bug 推动新 feature"。
> 2. **第一次 net-new tool ship 自 Phase 6 (`SpawnAgent`) 以来,
>    BaseTool / ToolRegistry / 系统提示词集成层的 contract 还
>    fit 吗?** —— 7 个 phase 跨度后 BaseTool[InputT] + execute() →
>    ToolResult 这套契约接 WebSearch + WebFetch 是不是顺滑?

---

## 1. 数据点

| 维度 | Phase 12(snapshot + resume) | Phase 13(rotation + CLI + focus state) | **Phase 14(web tools + system prompt guard)** |
|---|---|---|---|
| Capability | 6 | 4 | **5**(boundary+plan + T1-T4 + 本 retro) |
| 生产代码净增 | 899 | 971 | **~1,250**(2 个 tool 文件 + cli.py 改动 + settings 新模型 + prompts 加 paragraph) |
| 测试净增 | +108 | +85 | **+86**(T1: 25, T2: 20, T3: 32, T4: 9) |
| 新模块 / 包 | 1 file(`snapshot.py`) | 1 file(`focus_state.py`) | **2 files**(`tools/web_search.py` + `tools/web_fetch.py`) |
| 新 Settings 字段 | 1 nested | 2 nested | **1 nested**(`web: WebSettings`,6 个 leaf 字段) |
| 新 CLI flag | 2 | 1 | **1**(`--enable-web/--no-enable-web`,mirror plugins/memory) |
| 新 CLI 子命令 | 0 | 3 | **0** |
| 新 dep | 0 | 0 | **2**(`markdownify` + 显式 `beautifulsoup4`) |
| 新 tool 数 | 0 | 0 | **2**(WebSearch + WebFetch) |
| **保护层 zero-diff** | 11/11 ⭐⭐⭐ | 11/11 ⭐⭐⭐ | **10/11**(prompts/ 1 commit = boundary 已声明的 web_enabled 新 section)|
| **6 个现有 tool 零修改** | (没追踪)| (没追踪)| ✓ **6/6**(Read/Write/Edit/Bash/Grep/Agent byte-identical) |
| **services/ 零修改** | 0(snapshot 不调用 summarize) | 0 ⭐⭐⭐ | **0**(web tools 自己用 httpx,不走 summarize)|
| 时间 | <1 天 | <1 天 | **<1 天**(包括 dogfood 暴露的 4 个 patch + 这 5 个 phase commit) |

**关键观察**:Phase 14 是**第一次 dogfood-driven**(用户跑 oh chat 撞 bug 触发 phase boundary),而**不是** abstraction-driven(Phase 7a/11/12 那种"上一个 phase 留下抽象,这一个 phase 验证它")。Phase shape 不一样:

- abstraction-driven:retro 末尾"如果有第 N+1 个 consumer,substrate 不动"作为预测,下一个 phase 验证它。
- dogfood-driven:用户场景"我想做 LLM research" → 跑 oh chat → 现象(LLM Grep 本地文件 + 编造 findings) → 根因(无 web 工具 + 无 system prompt 防御) → 修复(2-pronged:加工具 + 加 prompt)。

两种 phase shape 都合法,但**测试方式不一样** —— abstraction-driven phase 的"成功"由 invariant(substrate 零修改)定义;dogfood-driven phase 的"成功"由 prove-it 实验(用户重跑同一个 prompt,看行为是否修复)定义。Phase 14 的 prove-it 在 T5 retro 后做(本 commit 之后)。

---

## 2. 每个 task 的 takeaway

| Task | Commit | 一句话总结 |
|---|---|---|
| **boundary doc + plan** | 174f0e6 | D29.1–D29.7 + 用户 ratify 的 2 个真正开放决策(Tavily as provider + markdownify as new dep),其他 5 个决策 mirror 已有 pattern(opt-in flag / nested Settings / 错误为 ToolResult / 系统提示词 additive kwarg)。预声明 prompts/ 是唯一会被改的保护目录(boundary invariant T14-6) |
| **P14-T1 — Provider Protocol + Tavily impl + WebSettings**(74f56ee) | ~650 行(其中 ~450 行 tests)。`WebSearchProvider` Protocol(3 行函数签名) + `TavilySearchProvider` impl(httpx POST + 4 类错误分支) + 4 个 `WebSearchProviderError` 特化类 + `WebSettings` 6 字段 nested model + 25 单元测试用 `httpx.MockTransport` 验证 happy + 401/403/429/4xx/5xx/timeout/network。**关键设计**:`transport: AsyncBaseTransport \| None = None` 作 test-injection seam,production code 路径 `None` → httpx 默认 transport,tests 注入 MockTransport;比 monkeypatching 干净 |
| **P14-T2 — WebSearch tool**(4ce91ec) | ~307 行(157 src + 150 tests)。`WebSearch(BaseTool[WebSearchInput])`:`is_read_only=True`,Description 显式 chain 到 WebFetch(D29.1 workflow rationale)。**D29.7 实现**:每个 `WebSearchProviderError` 特化在 execute() catch,转 `ToolResult(is_error=True)` + category hint。markdown 输出 `_format_results_as_markdown` 拼数字列表 + 占位符防 missing fields 时崩。20 新测试 |
| **P14-T3 — WebFetch tool**(03aa174) | ~569 行(300 src + 270 tests)。`WebFetch(BaseTool[WebFetchInput])`:`HttpUrl` 输入(自动 reject ftp/file/javascript),httpx streaming GET + body size cap + bs4 chrome strip + markdownify。⭐ **踩坑**:boundary doc D29.5 写"markdownify 的 strip=[...] 移除 chrome tags",**落地时发现 markdownify 的 strip kwarg 只移除标签外壳、保留 inner text** —— `<script>alert(1)</script>` 变成字面 "alert(1)" 留在输出。pivot 到 bs4 `decompose()` 预处理。代价:+30 行实现 + bs4 从 transitive dep 提升为显式 dep。retro §3.2 详述 |
| **P14-T4 — CLI flag + system prompt + conditional registration**(da91a52) | ~322 行(120 src 改动 + 200 tests)。THE bug fix 落地在 `build_system_prompt(web_enabled=...)`:**三态 kwarg**(None 保 byte-identity / True 加 "## Web Access" 正向引导 / False 加 "## No Internet Access" 反 substitution)。`_maybe_register_web_tools` helper 隔离条件注册,no-op when OFF + typer.Exit when ON-no-key + register when ON-keyed。`_run_ask` + `_run_chat` 各加 `enable_web_override` 参数 + 解析 + 4 个 `build_system_prompt()` call sites 全加 `web_enabled=enable_web`。9 新测试(5 prompt + 4 cli helper) |
| **P14-T5 — invariant verification + retro**(本 commit)| Protected 目录 10/11 zero-diff(prompts/ 1 commit = T4 加的 web_enabled section,boundary doc T14-6 已预声明);6 个现有 tool **6/6 byte-identical**;4 个 services/(summarize / snapshot / session_memory / focus_state)**4/4 byte-identical**。2068 测试通过。CHANGELOG `[Unreleased]` sketches v0.3.0 |

---

## 3. Framework-level 主题 — Phase 14 真正学到的

### 3.1 ⭐⭐⭐ Dogfood-driven phase shape 跟 abstraction-driven 不一样

Phase 1-13 大多数 phase 的 trigger 是**抽象自洽性**:Phase 7a 写 Protocol → 7c 验证(12% 代码 ship 第二个 impl);Phase 11 写 `summarize()` substrate → 12/13 跨 phase 验证 N=7 consumer。Phase boundary doc 的 "Triggering observation" 是 "上一个 phase retro §X 留下的问题"。

Phase 14 第一次,trigger 是真用户跑 oh chat 的 transcript:

```
>>> 我想要做一个调研，关于LLM 的最新的进展
[LLM 开始 Grep + Read 本地无关文件 + 编造 "Claude 4 (2M HAC)" 等假具体]
```

这种 trigger 的 phase 有几个明显不同的 shape:

| 维度 | abstraction-driven(Phase 7a/11/12/13) | dogfood-driven(Phase 14) |
|---|---|---|
| Trigger 来源 | 上一个 phase 的 invariant 预测 | 真用户行为 |
| Boundary doc 写法 | "上一个 retro 提到 X,本 phase 验证 X 在 N+1 consumer 仍然 hold" | "用户 transcript 显示 Y,本 phase 找根因 + 修" |
| 验收方式 | invariant verification(substrate 零修改)| prove-it 实验(用户重跑同 prompt,看行为变化) |
| Retro 末尾预测 | "如果第 N+2 个 consumer 落地,substrate 还能不动" | "如果有新 dogfood 暴露 X,下一个 phase 修 X" |
| 心理感受 | "复利在兑现" | "我的产品在出问题" |

第二种感受其实更重要 —— Phase 1-13 验证了**框架抽象**的复利,Phase 14 第一次验证**产品 UX** 的反馈循环。后者是项目能继续活下去的关键(没人用的项目不需要 retro)。

**Phase 15+ 预测**:dogfood 会继续暴露其他 UX bug。比如 cli.py 工作区里你并行开的 Phase 15(`rich-live` / `_stream_render.py` 修改)就是另一个 dogfood-driven phase(预测:基于 v0.2.0 流式渲染体验的某个 gap)。Phase 14 的 retro 给后续 dogfood-driven phase 提供了一个 template。

### 3.2 ⭐⭐ Boundary doc 假设错了一次,T3 落地时验证 + pivot

D29.5 boundary doc 说:

> markdownify 已经做 HTML → markdown 转换很好,它的 ``strip=[...]`` kwarg
> 在转换期间移除 chrome tags。先做 BeautifulSoup 预处理重复工作。

落地 T3 时发现这是错的:markdownify 的 `strip=['script', 'style', ...]` 只移除 wrapping tag,保留 inner text:

```python
markdownify("<script>alert(1)</script>", strip=['script'])
# → "alert(1)"  ← 字面文本留下来了!
```

代价 + 修复:
- ~30 行新增 `_strip_chrome` 函数用 bs4 `find_all(tag).decompose()` 删整个子树
- bs4 从 transitive dep(markdownify 自带)提升为显式 dep
- T3 commit message 把这个 pivot 写清楚:`feat(tools): P14-T3 WebFetch tool — httpx GET + bs4 chrome strip + markdownify`(标题就放出来"bs4 chrome strip")
- Boundary doc 文字也回头改,说明 bs4 是必要的

**框架洞察**:boundary doc 写"用 X 库的 Y 功能"时,**没真正跑过 Y** 就开始写。这是 Phase 14 第一次踩,但应该是常态而不是异常 —— 抽象层面的"应该 work"跟真跑一次的"实际 work"有缝隙,boundary doc 不可能消除这个缝隙,只能在 retro 里诚实记录。

**判断 framework**:
| boundary doc 的假设错了 → 应该 |
|---|
| Pivot + retro 标注假设的错点(本 case) |
| 假装没错 + 硬上 → 产生别的 bug 或 silent quality drop |
| reopen boundary doc + 重新 ratify(只有结构性错误才走这步) |

第一类是常态。第二类是 anti-pattern。第三类只有大调整时才用。

### 3.3 ⭐⭐ 三态 kwarg(None/True/False)为 byte-identity 让路

P14-T4 的 `build_system_prompt(web_enabled: bool | None = None)` 是个 unusual API choice —— 函数明显是 ON/OFF 二元语义,为什么留个第三态 None?

答案:**byte-identity 保护**。`build_system_prompt` 是 P5b/5c/5d/10/11 一直 additive 扩展的 hot function,有 233+ caller tests。如果新 kwarg 默认 `False`,那所有 default-omitted call sites 都会突然加一个 "## No Internet Access" section,233 个 byte-snapshot 测试**全炸**。

三态设计:
- `None`(default,callers without explicit web concept): 不加 section → byte-identical 到 Phase 13 → 233+ tests 不动
- `True`: 加 "## Web Access" 正向引导
- `False`: 加 "## No Internet Access" 反 substitution

CLI 层永远显式 `True` 或 `False`(从 `enable_web` 决议),用户体验里没有 None 状态。但 unit test 默认参数自动是 None,继续走 byte-identity 分支。

**这是 Phase 10 D28.6 "additive kwarg" 模式的延续**,但 D28.6 是单 kwarg `None vs value`,Phase 14 是 `None vs True vs False`。tri-state 更精细,代价是 API 描述更难解释(retro §3.1 docstring 我花 30 字解释)。

**判断 framework**:additive kwarg 设计的"中性"状态:
| kwarg 类型 | 中性 = | 用例 |
|---|---|---|
| Optional value(`X \| None`) | `None`(无该信息) | claude_md_content, memory_manifest |
| Optional behavior toggle(`bool`) | `False`(关闭) | enable_X |
| Optional 3-way disposition | `None`(不表态) | web_enabled (P14) |

第三类是 phase 14 才出现的。出现条件:**新行为有 ON/OFF 语义,但默认 ON/OFF 任一都破坏 byte-identity**。这种情况留个 None 做"我不参与这个决策"出口。

### 3.4 ⭐ 系统提示词 vs 工具:两个修都需要,不能省

Boundary doc D29.6 + plan T4 都说 "system-prompt guard 是 the bug fix,tool registration 是 enabling feature"。retro 时回头想这个分法,觉得它最重要:

| 用户场景 | 没 system prompt guard | 有 system prompt guard,但没工具 | 工具 + system prompt 都有 |
|---|---|---|---|
| 用户 `oh ask "latest LLM news"` | LLM Grep 本地文件 + 编造 ❌ | LLM 说"我没法上网" ✓ | LLM 调 WebSearch + WebFetch + 答 ✓ |
| 用户 `oh ask "summarize this codebase"` | LLM Grep + 答 ✓(本来就该 grep) | LLM 仍 Grep + 答 ✓(对本地任务) | 同左 ✓ |

System prompt guard 单独修部分解决问题(LLM 不再 hallucinate),但用户的实际需求("我想做研究")没满足。工具单独修但没 prompt guard:user 不开 --enable-web 时仍有 confabulation 风险。

**两个修一起 ship 是必须的,而不是 "纵深防御" 那种 nice-to-have**。这是 Phase 14 我对 "两层修复" 概念的细化理解。

**反例**:如果只 ship system prompt guard、不 ship tools(用户跑 --enable-web 也没用),用户的反应会是 "harness 还是不让我研究 → 我换别的产品"。如果只 ship tools、不 ship guard,用户**第一次跑** `oh ask "..."` 没加 `--enable-web` 时仍踩老 bug。**两端同时修才是产品级修复**。

### 3.5 ⭐ Provider abstraction 没有 N=2 consumer,值不值?

D29.2 写 `WebSearchProvider` Protocol + `TavilySearchProvider` impl。Protocol 抽象的成本:1 个 Protocol class + 1 个 dataclass(`WebSearchResult`)+ 4 个 Error subclass + 抽象层 ~50 LoC。

Phase 7a Protocol 的 ROI 是 Phase 7c 验证(12% LoC ship 第二个 impl)。但 Phase 14 ship 时没有 N=2 consumer —— Brave / Serper 都是 Phase 15+ 候选。

值不值?**值** —— 三个理由:

1. **测试套件直接受益**:Protocol 让 `_StubProvider`(50 LoC)就能完全替代 Tavily 测试。如果没 Protocol,要么 mock httpx(我的实际做法,在 TavilySearchProvider 内部),要么测试拉真 API key。Protocol + 双 impl 让单元 + 集成两种测试都 trivial。
2. **swap 一次的 LoC 已经被 7c 数据证明便宜**:不是猜,是数据。
3. **Tavily quota 到期或 service shutdown 时**:1 个 file 替换 → swap to Brave 的 emergency exit。商业 SaaS 抽象层不是 over-engineering,是 risk hedge。

但 Phase 14 也学到:**Protocol 抽象的 ROI 不能只看 N=2 验证那一次**。Protocol 给 testing + future-proofing 的当下价值,跟 "第二个 impl 来时省 12% LoC" 是两笔不同的账。

---

## 4. Python-specific 经验

### 4.1 `httpx.MockTransport` 作 test-injection seam

```python
class TavilySearchProvider:
    def __init__(self, ..., transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport  # production = None, test = MockTransport

    async def search(self, ...):
        async with httpx.AsyncClient(transport=self._transport) as client:
            ...
```

替代 monkeypatching httpx。测试代码:

```python
def handler(request): return httpx.Response(200, json={...})
provider = TavilySearchProvider(api_key="...", transport=httpx.MockTransport(handler))
```

干净。Production 永远 None。这个 pattern Phase 14 验证后,后续任何 httpx-based tool / client(WebFetch / 未来 BraveProvider / 等等)都可以同款 inject。

### 4.2 `bs4.BeautifulSoup.decompose()` 删整个子树

```python
soup = BeautifulSoup(html, "html.parser")
for tag_name in ["script", "style", "nav", ...]:
    for element in soup.find_all(tag_name):
        element.decompose()  # 删掉 tag + 所有 children
```

`.decompose()` 跟 `.unwrap()`(只剥 tag,留 children)+ `.extract()`(detach 返回)区分。chrome stripping 永远要 `.decompose()`。

### 4.3 Pydantic `HttpUrl` 自动 reject 非 http/https scheme

```python
class WebFetchInput(BaseModel):
    url: HttpUrl  # ftp://, file://, javascript: 全部 ValidationError
```

不用写 custom validator。Pydantic 2.x 的 `HttpUrl` 严格,默认拒 file URI(安全 +1)。

---

## 5. 如果重做我会改什么

### 5.1 boundary doc 应该先 spike markdownify 5 分钟再写 D29.5

D29.5 假设 markdownify 的 `strip=[...]` 删 chrome,这是只读 README 的猜测。如果我在 boundary doc 阶段花 5 分钟跑过 `markdownify("<script>alert(1)</script>", strip=["script"])`,会立刻发现 strip 不删 inner text,boundary doc 就能直接写 "用 bs4 预处理 + markdownify 转换",T3 不用 pivot。

**判断 framework**:boundary doc 里**任何 "用 X 库做 Y" 的具体技术声明**,应该至少有一行已跑代码的 evidence。retro §3.2 的"假设错了"是可以避免的成本。

### 5.2 Phase 14 应该 cut v0.3.0 release

v0.2.0 ship 是 5 月 28 日,Phase 14 ship 是 6 月 2 日(本 retro 落地时间)。这是 ~5 天的 feature 新增 + 5 天的 dogfood patch 收集。应该 cut **v0.3.0** —— 但本 retro 不做这事,因为:
- T5 acceptance 只说"sketch v0.3.0 CHANGELOG entry",不说"tag + push"
- tag + push 是 user-initiated action(per CLAUDE.md)

**predicted CHANGELOG entry for v0.3.0**(append to `[Unreleased]`,本 commit 包含):
- Added: WebSearch + WebFetch tools (`--enable-web` opt-in)
- Added: TavilySearchProvider (Brave / Serper 候选 deferred)
- Added: 三态 anti-substitution system prompt(默认 OFF 加 paragraph,ON 加正向引导)
- Added: 4 个 v0.2.0 patch fix(JSON heuristic / readline / max_tokens default / chat REPL ApiError survival)
- Added: `markdownify` + `beautifulsoup4` 显式 dep

### 5.3 应该在 boundary doc 阶段列 retro 预期答案

Phase 14 boundary doc 列了"predicted retro questions",但不是预期答案 —— 只是"我要在 retro 回答什么"。落地后回顾,这种"问题列表"是有用的(防止 retro 漏掉关键 topic),但**更有用的会是"我预期的答案"**。如果落地前预期答案跟落地后实际答案不一致,这是最有价值的 retro signal —— "我之前的认知错在哪"。

Phase 15+ 应该试试这个改良。

---

## 6. 还未做的(Phase 15+ candidates)

| 候选 | 来自 | 触发条件 |
|---|---|---|
| WebFetch caching by URL | D29.6 deferred | 测得"同 URL 多次 fetch" 频率 > 阈值 |
| Brave / Serper provider | D29.2 deferred | Tavily quota 到期 或 用户偏好 |
| PDF rendering in WebFetch | D29.6 deferred | dogfood 撞 PDF 链接占比 > 阈值 |
| JS-rendered pages via Playwright | D29.6 deferred | "现代 web 大量 SPA" 数据成立后 |
| Prompt-injection quarantine for fetched content | risks §3.1 | 一次 PI 攻击事件后 |
| Tavily SearchDepth advanced 模式 | sub-decision deferred | 用户反馈"搜索结果不够深入" |
| `oh web fetch <url>` CLI subcommand | D29.6 deferred (out of scope) | "用户想直接 fetch 不 LLM 调度" |
| Rich Live 流式渲染升级 | 工作区上你并行开的 Phase 15 | 已经在做 |

---

## 7. Phase 14 close-out gates checklist(T5 验收)

- [x] T5 acceptance 全部 hit
- [x] `pytest tests/` 全 GREEN(2068 测试)
- [x] ruff check + ruff format 全 clean
- [x] mypy --strict src/ clean(105 source files)
- [x] **保护层 invariant**:10/11 zero diff;prompts/ 唯一例外 = T4 加的 `web_enabled` section,boundary doc T14-6 已预声明
- [x] 6 existing tools(Read/Write/Edit/Bash/Grep/Agent)byte-identical
- [x] 4 services/(summarize / snapshot / session_memory / focus_state)byte-identical
- [x] boundary doc D29.1-D29.7 全部 locked
- [x] CHANGELOG `[Unreleased]` v0.3.0 sketch 落地
- [x] 本 retro 写成

Phase 14 close-out ✓ done。
