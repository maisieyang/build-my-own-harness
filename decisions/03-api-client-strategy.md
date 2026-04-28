# Decision 03 — API Client Strategy

- **Date**: 2026-04-28
- **Phase / Module**: Phase 1 / Module 3 (P1-T3)
- **Status**: Decided

## Context

P1-T3 builds the API client — the layer that takes our `ApiMessageRequest` and
emits `AsyncIterator[ApiStreamEvent]`. The big strategic choice is **which
Provider to target first**, because that decides:

- Which SDK we depend on
- How heavy the wire-format translation layer is
- How early our anti-corruption layer (`protocols/`) gets stress-tested
- What `oh ask "hi"` actually talks to

## The Pivot

Original assumption (carried implicitly from REFERENCE.md / OpenHarness):
**Anthropic-native first**. Reason: protocols/ is Anthropic-shape, so the
client would be a thin wrapper.

New decision: **Qwen via DashScope (OpenAI-compatible) first**. Reason: real
user constraints (China-based dev, network stability, cost), and the
side-benefit of stress-testing the anti-corruption design from day one.

## Decisions

### D3.1 — First client target: **Qwen via DashScope**

- Endpoint: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- SDK: `openai` Python SDK (DashScope is OpenAI API-compatible)
- Models: `qwen-max` (default), `qwen-plus`, `qwen-turbo`
- **Why**:
  - Network stable from China (no proxy required)
  - Cost low enough for iterative development
  - OpenAI-compatible → mature SDK ecosystem
  - **Stress-tests anti-corruption layer** the moment it ships
- **Trade-off**: Translation layer (Anthropic-shape ↔ OpenAI-shape) is now
  Day-1 work, not Phase-5+ work. Net positive: validates protocols/ design earlier.

### D3.2 — Client interface: **Protocol-first**

```python
# In src/openharness/api/_protocol.py (new module)
class SupportsStreamingMessages(Protocol):
    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        ...
```

Concrete implementation: `OpenAICompatibleApiClient`. Future siblings (e.g.,
`AnthropicApiClient` if/when added) implement the same Protocol — the engine
stays Provider-neutral.

- **Why**:
  - Matches Claude Code / OpenHarness pattern (`SupportsStreamingMessages`)
  - Matches Codex pattern (trait-based DI)
  - Easy to swap for tests via `FakeApiClient` (D3.3)
  - Multi-provider future is built into the design, not bolted on

### D3.3 — Mock boundary: **at our Protocol layer**

Tests use `FakeApiClient` (a hand-rolled implementation of
`SupportsStreamingMessages`) — not a mocked `httpx` client, not a mocked
`openai` SDK client.

- **Why**:
  - HTTP-level mocks are brittle (SSE wire format details leak into tests)
  - SDK-level mocks duplicate SDK internals (we'd be testing the SDK, not us)
  - Protocol-level mocks are clean: tests control the events `FakeApiClient`
    emits, no wire-format reasoning needed
- **What this means for `OpenAICompatibleApiClient` itself**: we still write
  unit tests for it that mock the `openai` SDK at the SDK boundary — but
  callers of the Protocol (engine, CLI) never see SDK details

### D3.4 — Auth: **env var only** (`DASHSCOPE_API_KEY`)

For Phase 1 only. No config files, no keyring, no OAuth.

- **Why**:
  - Simplest possible thing that works
  - Claude Code's earliest versions also started here
  - Keyring / config files are Phase 4+ concerns (when we have multiple
    profiles to manage)
- **Future**: Phase 4 may add `~/.config/openharness/auth.toml`; Phase 5+
  may add keyring integration; OAuth (Anthropic / Copilot style) is
  out of scope for the foreseeable future

### D3.5 — Retry visibility: **explicit `ApiRetryEvent` in the stream**

Already designed in `protocols/stream_events.py` (sub-unit 2f). The client
emits `ApiRetryEvent` before each retry attempt; downstream UIs render
"retrying (attempt 2/3)..." rather than appearing stalled.

- **Why**:
  - Matches Claude Code's design (REFERENCE.md §4.2)
  - Honest UX — users see what's happening
  - Already costs us nothing (the event type exists)

## Translation challenges (informational, will be solved during P1-T3 implementation)

The 7 wire-format mismatches between Anthropic-shape (our `protocols/`) and
OpenAI-shape (Qwen API) that the translation layer must handle:

| # | Anthropic shape (ours) | OpenAI shape (Qwen) | Difficulty |
|---|--------------------|------------------|---------|
| 1 | `content: list[TextBlock]` only | `content: str` directly OK | 🟢 simple |
| 2 | `content: list[TextBlock, ...]` multi-block | OpenAI also list-of-typed-objects | 🟢 simple |
| 3 | `ImageBlock` with base64 source | `{type: "image_url", image_url: {...}}` | 🟡 field renaming |
| 4 | `ToolSpec` description | `{type: "function", function: {name, description, parameters}}` | 🟡 wrap one layer |
| 5 | Assistant message containing `ToolUseBlock` | Assistant message with top-level `tool_calls: [...]` | 🔴 structural diff |
| 6 | User message containing `ToolResultBlock` | Separate `{role: "tool", tool_call_id, content}` message | 🔴 structural diff |
| 7 | `stop_reason: "end_turn"/"tool_use"/"max_tokens"/"stop_sequence"` | `finish_reason: "stop"/"tool_calls"/"length"/"content_filter"` | 🟡 mapping table |

#5 and #6 are the real complexity — OpenAI splits what Anthropic combines.
The translation must serialize/deserialize correctly, and the unit tests must
catch any drift.

## What this decision excludes

- **Anthropic-native client** deferred to Phase 5+ (or never, if Qwen +
  OpenAI-compatible covers all our needs)
- **Multi-provider auto-detection** (REFERENCE.md has 23-Provider registry).
  We do **one** Provider in Phase 1; "auto-detection" is a complexity that
  earns its keep only with 5+ providers
- **OAuth flows** (Copilot DeviceCode / Anthropic Browser) — out of scope
- **Local model providers** (Ollama / vLLM) — Phase 6+ candidate

## Revisit Triggers

| Trigger | What to do |
|---------|----------|
| Translation layer exceeds **200 lines** of code | Stop — write `decisions/04-protocol-revision.md` and consider whether `protocols/` itself should pivot to a more neutral shape |
| OpenAI-shape forces a change in `protocols/` (e.g., new field, restructured ToolUseBlock) | Stop — write `decisions/04-protocol-revision.md`, update protocols/, update tests |
| Qwen rate limits / quotas block dev progress | Add OpenAI cloud (`OPENAI_API_KEY`) as the second target — same `OpenAICompatibleApiClient`, just different `base_url` |
| Phase 5 wants Anthropic features unique to native API (extended thinking / prompt caching) | Add `AnthropicApiClient` as a sibling Protocol implementation; keep `OpenAICompatibleApiClient` for everything else |
| 翻译层 bug 数 > 我们 protocols 层的 bug 数 | 反向信号——可能是 protocols 设计有问题 |

## Connection to existing decisions

- Builds on **D2.2** (discriminated union by `type`): the translation layer
  uses `isinstance(block, ToolUseBlock)` etc. for routing during translation
- Builds on **2f's ApiStreamEvent design**: D3.5 (visible retry) only works
  because we already chose 3 abstract events vs SDK's 7+
- **First real test** of the anti-corruption boundary that capability-ladder
  §8 describes
