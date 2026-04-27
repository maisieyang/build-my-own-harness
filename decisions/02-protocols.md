# Decision 02 — Protocols & Data Models

- **Date**: 2026-04-27
- **Phase / Module**: Phase 1 / Module 2
- **Status**: Decided

## Context

Wire-level 数据类型是 harness 内部所有"流"的最底层——会被 API client、engine、tool 系统、UI、压缩、持久化所有模块依赖。错的选择会渗透到每一个后续模块。

## Decisions

### D2.1 — Schema 实现：**Pydantic v2**
- **Why**: Anthropic SDK / OpenAI SDK / FastAPI 都用；类型校验 + JSON schema 生成 + 序列化一体；Rust 核心性能好；mypy plugin 兼容（`pydantic.mypy`）
- **Trade-off**: 启动有 ~10-30ms 成本；接受。msgspec 性能优势在我们规模下感受不到。
- **配置**: `model_config = ConfigDict(extra="forbid", validate_assignment=True)` 严格模式

### D2.2 — ContentBlock 结构：**Discriminated Union by `type` field**
- **Why**:
  - 与 Anthropic API wire format **1:1**——API 返回的 JSON 直接 `model_validate` 出对象
  - `isinstance(block, TextBlock)` 让 mypy 类型 narrow
  - TS 出身的 mental model 直接复用（TS discriminated union 同款）
- **关键模式**:
  ```python
  ContentBlock = Annotated[
      TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
      Field(discriminator="type"),
  ]
  ```
- **拒绝的方案**:
  - Class hierarchy（LangChain 风格）— 数据耦合行为，扩展时改基类摩擦大
  - 单一类 + optional 字段 — 失去类型保证，错误使用不会被 mypy catch

### D2.3 — 可变 vs 不可变：**Mutable**
- **Why**: 流式累加 text delta 时需要修改最后一个 block；frozen 在每次 delta 都 `model_copy` 反而是性能/复杂度负担
- **Trade-off**: 不靠 frozen 提供并发安全；asyncio 单线程模型下没问题

### D2.4 — ID 策略：**与 Anthropic API 一致**
- **Why**: 只 `tool_use` 有 `id`；`tool_result` 用 `tool_use_id` 关联；`text` / `image` 无 id
- **拒绝**: 给所有 block 加 id——harness 内部不需要引用 text block

### D2.5 — 模块组织：**子模块拆分**
```
src/openharness/protocols/
├── __init__.py        # re-export 顶层 API（短 import 路径）
├── content.py         # ContentBlock 各类型 + 别名
├── messages.py        # ConversationMessage
├── usage.py           # UsageSnapshot
├── requests.py        # ApiMessageRequest
└── stream_events.py   # ApiStreamEvent
```
- **Why**: ~300 行类型代码放一个文件累；和 SPEC 的 `engine/messages.py` / `engine/stream_events.py` 模式一致
- `__init__.py` re-export 让 `from openharness.protocols import ContentBlock` 仍然短

## What this excludes

- 不做 `BaseMessage` 类层级（LangChain 风格）— 与 wire format 不 1:1
- 不做 frozen models — mutable 实用
- 不做 `id` on text blocks — wire format 不需要
- 不引入 msgspec / attrs — Pydantic v2 生态最优
- 不做 OpenAI 格式的并行类型层级 — 由 API 适配器层做格式转换（Module 3 关心）

## Revisit Triggers

- Pydantic v2 → msgspec：流式 hot path 出现性能瓶颈（profile 证明 > 5% time in serialization）
- Mutable → Frozen：出现"被意外修改"的并发 bug
- 子模块 → 单文件：protocols 总行数 < 200 可以扁平化
