# Decision 04 — API Client Implementation & Testing Philosophy

- **Date**: 2026-04-28
- **Phase / Module**: Phase 1 / P1-T3 / sub-units 3c.1 + 3c.2
- **Status**: Decided & shipped

## Context

[decisions/03-api-client-strategy.md](./03-api-client-strategy.md) locked
the **strategic** choice: target Qwen via DashScope first, use the `openai`
SDK, build a translation layer, integrate with our retry layer.

This document records the **implementation-level** decisions made during
3c.1 (translation) and 3c.2 (client orchestration). They are durable
enough to deserve their own record because:

- Future Provider implementations (Anthropic-native, Codex-style, etc.)
  should follow the same shape
- The testing philosophy here defines "what is a *good* test" project-wide
- Some choices look small but have non-obvious rationale that future-me
  needs to remember

## The component dependency graph (reference)

```
ApiMessageRequest
   │
   │  to_openai_request (3c.1)
   ▼
openai_kwargs
   │
   │  with_retry (3b)
   ▼
[ AsyncOpenAI.chat.completions.create() ]
   ▲
   │  Exception → _translate_openai_error (3c.2)
   │           → AuthenticationFailure / RateLimitFailure /
   │             RequestFailure (3a)
   ▼
AsyncStream of ChatCompletionChunk
   │
   │  _StreamAssembler.consume() (3c.1)
   ▼
ApiTextDeltaEvent...  → finalize() → ApiMessageCompleteEvent
```

Four sub-units (3a errors / 3b retry / 3c.1 translation / 3c.2 client) compose
exactly here. **Every Provider implementation should compose this way** —
errors and retry are Provider-neutral; translation and client are
Provider-specific but follow the same wiring pattern.

## Decisions

### D4.1 — Error translation: 6 `isinstance` checks, conservative fallback

```python
def _translate_openai_error(exc: Exception) -> OpenHarnessApiError:
    if isinstance(exc, openai.AuthenticationError):       return AuthenticationFailure(..., 401)
    if isinstance(exc, openai.PermissionDeniedError):     return AuthenticationFailure(..., 403)
    if isinstance(exc, openai.RateLimitError):            return RateLimitFailure(..., 429, retry_after=...)
    if isinstance(exc, openai.APIStatusError):            return RequestFailure(..., status_code=exc.status_code)
    if isinstance(exc, openai.APIConnectionError):        return RequestFailure(..., status_code=None)
    return RequestFailure(...)  # Unknown — fallback
```

**Why**:

- **Concrete-first ordering**: `openai.RateLimitError` extends `APIStatusError`,
  so the `isinstance(exc, openai.APIStatusError)` check would catch it if listed
  first. The order from-most-specific-to-most-general is mandatory.
- **Fallback to RequestFailure** on unknown: surfaces unexpected SDK errors
  rather than silently swallowing them. Conservative.
- **Always preserve `__cause__`**: caller does `raise translated from original`
  so debug visibility survives the wrap (tested via `__cause__` chain in 3a).
- **`retry_after` parsed from headers**: Provider's hint beats our backoff math.

### D4.2 — `stream_message` event ordering: retry-first, then stream

```python
async def stream_message(...) -> AsyncIterator[ApiStreamEvent]:
    # 1. Retries (if any) accumulate during establishment
    retry_events: list[ApiRetryEvent] = []
    stream = await with_retry(_establish, on_retry=lambda ...: retry_events.append(...))

    # 2. Yield retry events FIRST (they all happened pre-stream)
    for r in retry_events:
        yield r

    # 3. Then iterate the stream → text deltas
    async for chunk in stream:
        yield from assembler.consume(chunk.model_dump())

    # 4. Finally the terminal event
    yield assembler.finalize()
```

**Why**:

- Retries ALL happen *before* any data flows — buffering them and yielding
  before stream events gives consumers a clean ordering: retries → deltas → complete
- Allows tests to do `assert isinstance(events[0], ApiRetryEvent)` cleanly
- Avoids the complexity of mid-stream retries (which are conceptually different —
  if the stream itself dies mid-flight, restarting would lose position)

### D4.3 — `OpenHarnessApiError` direct reraise (avoid wrap-of-wrap)

```python
async def _establish_stream() -> Any:
    try:
        return await self._sdk.chat.completions.create(**kwargs)
    except OpenHarnessApiError:    # ← reraise our own errors as-is
        raise
    except Exception as exc:
        raise _translate_openai_error(exc) from exc
```

**Why**:

- Without the first `except`, `with_retry` raises `RateLimitFailure`, we catch
  `Exception`, translate AGAIN → wrap-of-wrap with confusing `__cause__` chain
- Pattern: **"my own type? raise; otherwise translate"**. Applies to every
  layer that wraps another layer.

### D4.4 — Constructor: SDK injection over auto-instantiation

```python
class OpenAICompatibleApiClient:
    def __init__(self, *, sdk: AsyncOpenAI, retry_policy=DEFAULT_POLICY): ...
```

NOT:

```python
class OpenAICompatibleApiClient:
    def __init__(self, *, api_key=None, base_url=None, ...): ...   # rejected
```

**Why**:

- **DI-friendly testing**: tests pass `_FakeAsyncOpenAI` directly, no
  monkey-patching `os.environ`, no mocking the SDK constructor
- **CLI controls SDK config**: timeouts, base_url, default headers, proxy —
  all live in the CLI's `AsyncOpenAI(...)` construction, not buried in our class
- **Single responsibility**: client orchestrates the request → translate →
  call → translate pipeline, NOT auth credential management

### D4.5 — Testing philosophy: each test = a product contract

A pattern that emerged in 3c.2 testing and is worth codifying.

Each test name should describe **a product behavior the user can feel**, not
"test some code path". From `test_client.py`:

| Test | Product contract being verified |
|------|-------------------------------|
| `test_authentication_error_not_retried` | Wrong API key → fail fast, do not waste 3 retries |
| `test_rate_limit_then_success_emits_retry_event` | User sees "retrying..." instead of apparent freeze |
| `test_persistent_rate_limit_exhausts_retries` | Honesty: do not retry forever |
| `test_400_error_not_retried` | Client-side bug surfaces immediately, retry does not mask it |
| `test_connection_error` | Network is down → fail fast (status_code=None → not retryable) |

**Anti-pattern to avoid**: tests named `test_translate_openai_error_with_401`
or `test_with_retry_callback_invoked` — those describe code paths, not user-
visible contracts.

**Going forward**: every API / engine / CLI test should be defensible as
"a product behavior our user can feel" — if not, it is probably testing
implementation rather than contract.

### D4.6 — `openai>=1.50,<2.0` as runtime dependency

```toml
dependencies = [
    "pydantic>=2.5",
    "openai>=1.50,<2.0",
]
```

**Why**:

- `openai` SDK works against any OpenAI-compatible endpoint (Qwen via
  DashScope, OpenAI cloud, DeepSeek, Moonshot, SiliconFlow) by configuring
  `base_url` — single dep, multi-Provider
- Lower bound `1.50`: stable async API + structured exception classes
  (AuthenticationError / RateLimitError / APIStatusError / APIConnectionError
  hierarchy)
- Upper bound `<2.0`: opt-in to a major-version review; if 2.0 ships
  breaking changes, write a follow-up decision before bumping
- Transitive deps (httpx, sniffio, distro, jiter) are accepted — they come
  via openai and are well-maintained

## What this excludes

- **Anthropic-native client**: deferred to Phase 5+ (or never, if Qwen +
  OpenAI-compatible covers all needs). When added, it follows the SAME 4-component
  pattern (errors / retry / translation / client) — `_translate_anthropic_error`,
  `to_anthropic_request`, `AnthropicApiClient` — wired the same way.
- **Multi-provider auto-detection** (REFERENCE.md's 23-Provider registry):
  out of scope for the foreseeable future. We ship one Provider at a time.
- **Mid-stream retry**: if the SDK stream dies mid-flight, the error
  surfaces. We do not attempt to resume from where it stopped.
- **Streaming mid-flight retry events**: retry events only emit during
  *establishment*, not during stream iteration.

## Revisit Triggers

| Trigger | Action |
|---------|--------|
| openai SDK ships v2.x with breaking changes | Write `decisions/05-openai-sdk-v2-migration.md` before bumping |
| 4 of 5 next provider implementations duplicate the same translation skeleton | Extract a `BaseOpenAICompatibleClient` and have providers parameterize it |
| `_translate_openai_error` exceeds ~30 lines | Move to `errors_translation.py` and add per-error-type unit tests |
| Mid-stream errors become a real user pain | Add an "ApiStreamErrorEvent" type and reconsider stream-error handling |
| The "retry events first" ordering blocks an integration | Reconsider — but the alternative (interleaving) is significantly more complex |

## Connection to existing decisions

- Builds on **D2.2** (discriminated union by `type`): the assembler in 3c.1
  uses `isinstance` to narrow ContentBlock variants during translation.
- Builds on **D2.3** (mutable models): `_StreamAssembler` mutates state
  across `consume()` calls; immutable would force `_copy()` per chunk.
- Builds on **D2.4** (IDs only on tool_use): `_translate_user_with_tool_results`
  uses `block.tool_use_id` as `tool_call_id` directly.
- Builds on **D3.5** (visible retry via `ApiRetryEvent`): D4.2's event
  ordering choice is downstream of this — we have a typed event for retry,
  so we yield it.
- **First validation of the anti-corruption layer thesis** from
  capability-ladder.md §8: the protocol-side stayed unchanged through the
  entire 3c.1 + 3c.2 implementation. The thesis holds.
