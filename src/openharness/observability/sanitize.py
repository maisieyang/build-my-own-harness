"""Sanitize processor + call-site helpers — P3-T5.5b.

Per Three-Axis 轴 2 (Hybrid 策略 D + structlog processor 全局拦截):

- **Processor 层 (defense-in-depth)**:无差别地拦截 — key 在
  :data:`SENSITIVE_KEYS` 里 → ``<redacted>``;value 匹配
  :data:`TOKEN_PATTERNS` → ``<redacted-token>``。即使 dev 忘了 sanitize 也兜底。
- **Call-site helper (语义化)**::func:`sanitize_path` /
  :func:`sanitize_command` —— call site 知道字段语义,显式调用。Processor
  看到 ``key="path"`` 不能假设 value 是路径(URL path / JSON path / ...),
  所以这层不能自动化。

设计取舍:

- **不展开 Pydantic BaseModel** — call site 想 log Pydantic 对象,
  自己传 ``model.model_dump()``。Processor 装作不认识,让 renderer ``str()``
  it(会漏 — 此风险 docstring 警示给 call-site)。
- **整段 redact 而非局部** — 命中 token pattern 就把整个 value 替换为
  ``<redacted-token>``,而不是只替换匹配段。简单 + 无歧义。
- **Dict key 不 sanitize** — key 是 schema,不是 PII;只 redact value。
- **Path / command helper 不进 processor** — 这两个是 call-site 显式调用,
  ``5c/5d`` 写 log 时 ``input={"path": sanitize_path(p, cwd), ...}``。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from structlog.typing import EventDict, WrappedLogger


REDACTED = "<redacted>"
REDACTED_TOKEN = "<redacted-token>"


# Lowercase canonical forms. ``_is_sensitive_key`` normalizes lookups:
# case-insensitive + treat ``-`` and ``_`` as equivalent (so ``API-Key`` /
# ``api_key`` / ``Api-Key`` all hit).
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        # Credentials (generic)
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        # OAuth / session tokens
        "access_token",
        "accesstoken",
        "refresh_token",
        "refreshtoken",
        # HTTP auth
        "authorization",
        "auth",
        "credentials",
        "credential",
        # Session
        "session",
        "sessionid",
        "session_id",
        "cookie",
        "cookies",
        # AWS
        "aws_secret_access_key",
        "aws_access_key_id",
        # Private key material
        "private_key",
        "privatekey",
    }
)


# Known token shapes. First-match-wins; on hit the entire string value is
# replaced with ``<redacted-token>`` regardless of surrounding text.
# Patterns ordered by how often they show up in practice.
TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI / Anthropic-style API keys
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),  # GitHub PAT (classic)
    re.compile(r"github_pat_[a-zA-Z0-9_]{82}"),  # GitHub PAT (fine-grained)
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS Access Key ID
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),  # Google API key
    re.compile(r"eyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+"),  # JWT
)


def _is_sensitive_key(key: str) -> bool:
    """Case-insensitive + hyphen/underscore-insensitive lookup.

    ``API-Key`` / ``api_key`` / ``apiKey`` all normalize to ``api_key``.
    """
    normalized = key.lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS


def _redact_token_in_string(value: str) -> str:
    """Return ``REDACTED_TOKEN`` if any known token pattern appears in
    ``value``;otherwise return ``value`` unchanged."""
    for pattern in TOKEN_PATTERNS:
        if pattern.search(value):
            return REDACTED_TOKEN
    return value


def _sanitize_value(value: Any) -> Any:
    """Recursively sanitize a structlog event-dict value.

    - dict → redact sensitive keys,recurse into other values
    - list / tuple → recurse element-wise (list is returned,tuple lost — tests
      don't depend on tuple identity)
    - str → token-pattern scan
    - other (int / float / bool / None / BaseModel / Path / ...)
      → pass through unchanged

    BaseModel etc. are not automatically expanded — see module docstring
    for the rationale.
    """
    if isinstance(value, dict):
        return _sanitize_dict(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _redact_token_in_string(value)
    return value


def _sanitize_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Sanitize every entry of a dict. Sensitive keys → ``<redacted>``;
    other values recurse through :func:`_sanitize_value`."""
    return {k: REDACTED if _is_sensitive_key(str(k)) else _sanitize_value(v) for k, v in d.items()}


def sanitize_processor(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """structlog processor — inserted between ``TimeStamper`` and the
    renderer in :func:`openharness.observability.logging.configure_logging`.

    The signature follows the structlog processor protocol; ``logger`` and
    ``method_name`` are unused at this layer. Returns a new event dict
    rather than mutating in place (avoids surprising side effects on
    callers that re-use dict literals).
    """
    del logger, method_name
    return _sanitize_dict(dict(event_dict))


# --------------------------------------------------------------------------- #
# Call-site helpers (semantic field sanitizers)                               #
# --------------------------------------------------------------------------- #


def sanitize_path(path: str | Path, cwd: Path) -> str:
    """Return a cwd-relative path string if ``path`` is inside ``cwd``;
    otherwise ``<redacted>``.

    Used at call sites to log path values without leaking absolute paths
    that encode user identity (``/Users/yangxiyue/...``) or project
    structure (``/home/work/cbs-hkmo/...``).

    Symlinks + ``..`` are resolved via ``Path.resolve(strict=False)`` so
    non-existent target paths (e.g., Write creating a new file) are still
    handled.
    """
    from pathlib import Path as _Path  # local to keep top-level lean

    try:
        return str(_Path(path).resolve(strict=False).relative_to(cwd.resolve(strict=False)))
    except ValueError:
        return REDACTED


def sanitize_command(command: str) -> str:
    """Reduce a shell command to ``<first_token> (len=N)``.

    Bash commands can hide credentials in flags
    (``curl -H "Authorization: Bearer xxx"``) or environment-prefix
    syntax (``API_KEY=sk-... cmd``). Logging only the first token + total
    length keeps debug signal (which program ran, was it suspicious-long)
    without spilling args.

    Empty / whitespace-only command → ``"(empty)"``.
    """
    if not command.strip():
        return "(empty)"
    first_token = command.split(None, 1)[0]
    # Even the first token can leak — e.g., ``API_KEY=sk-... python ...``
    # makes the env-var-prefix itself the first token. Run the token-pattern
    # redactor over it too so embedded creds get caught.
    safe_first = _redact_token_in_string(first_token)
    return f"{safe_first} (len={len(command)})"
