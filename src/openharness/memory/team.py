"""Team-scope secret scanner — P11-T4.4c (D29.10).

When the extraction secondary pass (P11-T4.4d) classifies a memory
as ``scope: team``, run :func:`check_team_memory_secrets` before
writing. A clean record proceeds to ``MemoryStore.add_or_update``
under the team subdirectory; a record with a detected secret is
**silently dropped** with a ``memory_team_secret_blocked`` log
(no user-facing error — extraction failures stay invisible to keep
the main conversation flow uninterrupted).

Regex-only detection (no entropy analysis):

- ``BEGIN PRIVATE KEY`` (PEM blocks)
- AWS access key ``AKIA[0-9A-Z]{16}``
- GitHub tokens ``gh[pousr]_...``
- OpenAI keys ``sk-...``
- Anthropic keys ``sk-ant-...``
- Generic ``(secret|token|api_key|password)\\s*[:=]\\s*['"]?[^'"\\s]{12,}``

Conservative pattern set — entropy detection would catch more but
produce false positives that block legitimate records. The 6
patterns above cover the high-value cases without rejecting normal
text. Phase 12+ may add a pluggable scanner if real demand surfaces.

Private-scope memories do NOT call this — only TEAM scope (D29.10
trust model: private is local-only, team is shared).
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PEM private key", re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    # OpenAI / generic ``sk-`` key — must check AFTER Anthropic ``sk-ant-``
    # so the Anthropic-specific rule fires first on those matches.
    ("OpenAI / generic sk- key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    # Generic ``secret=value`` style: catches inadvertent dumps of
    # ``api_key=ghp_...`` / ``password: hunter2hunter2hunter2`` etc.
    # 12-char value minimum so casual ``token: a`` doesn't trigger.
    (
        "generic secret=value",
        re.compile(
            r"(?i)\b(secret|token|api[_-]?key|password|passwd|auth[_-]?token)"
            r"\s*[:=]\s*['\"]?[^'\"\s]{12,}"
        ),
    ),
]


def check_team_memory_secrets(content: str) -> str | None:
    """Scan ``content`` for the 6 secret patterns. Return ``None`` if
    clean, else a human-readable error string naming the rule label(s)
    that matched.

    Multiple hits → all rule labels concatenated (comma-separated)
    so the log captures the full reason. We never echo the actual
    matched value — that would leak the secret into the log file.
    """
    matched: list[str] = []
    for label, pattern in _PATTERNS:
        if pattern.search(content):
            matched.append(label)
    if not matched:
        return None
    return f"team memory rejected — detected: {', '.join(matched)}"
