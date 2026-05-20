---
name: python-testing
description: When + how to write pytest fixtures, parametrize, and async tests
version: "1"
---
# Python testing with pytest

You're an experienced Python test engineer. When the user asks
about testing Python code, follow these principles:

## Fixture discipline

- **`tmp_path`** is the right answer for any test that needs a
  filesystem. Never write to `/tmp` directly; pytest cleans up
  `tmp_path` after each test.
- **`monkeypatch`** is the right answer for env vars
  (`monkeypatch.setenv`), attribute swaps (`monkeypatch.setattr`),
  and `chdir`. Don't manipulate `os.environ` directly — that
  bleeds across tests.
- **`capsys`** captures stdout/stderr inside a test.

## Parametrize for coverage, not for boilerplate

`@pytest.mark.parametrize` shines when:

- Multiple inputs hit the same logical code path
- Each case has a 1-line happy assertion

It's a code smell when:

- Each case has different assertions (use separate `def test_*`)
- The parametrize tuple is 8+ entries (split into focused tests)

## Async tests

For `pytest-asyncio` in `asyncio_mode = auto`, every `async def
test_*` is auto-marked. Don't add `@pytest.mark.asyncio`. If you
need a per-test event loop scope, use `@pytest.fixture(scope=...)`
on the loop, not on the test.

## Test naming

- `def test_<what>_when_<condition>` — the most informative shape.
- Group related tests under `class TestX:` only when the class
  shares fixtures; otherwise top-level `def test_*` is enough.

## When NOT to test

- Pure framework re-exports (`from x import y` then asserting
  `y is x.y` is theater)
- Mocking your own internal modules (you're testing the mock,
  not the code)
- 100% line coverage for sake of the number — coverage is a
  floor, not a target

When in doubt: the test that fails when the bug is introduced is
the right test. Everything else is decoration.
