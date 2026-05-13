"""Observability subsystem — P3-T5.

Implements D13.6 (RPC 配套 4) — structlog-based structured logging with:

- 8 dispatch-lifecycle log points (5c/5d): ``turn_start`` / ``tool_dispatch``
  / ``tool_complete`` / ``permission_denied`` / ``retry`` / ``hook_invoke``
  / ``hook_failed`` / ``loop_limit_exceeded``
- 三层 ID 贯穿(run_id / turn_id / tool_use_id),为未来接 OTel /
  LangSmith exporter 留位
- Sanitize processor (5b) for redacting credentials + tokens before
  records leave the process

Sub-units land incrementally:

- 5a (this commit):``configure_logging`` + ``get_logger`` + contextvar binders
- 5b:``sanitize`` processor (SENSITIVE_KEYS + token patterns + path sanitizer)
- 5c:log calls in ``engine/query.py``
- 5d:log calls in ``api/retry.py`` + ``permissions/tier_based.py`` + ``hooks/executor.py``
- 5e:CLI ``--log-level`` / ``--log-format`` flags
- 5f:end-to-end smoke with JSON pipeline through ``jq``

Public API:

    from openharness.observability import (
        configure_logging,
        get_logger,
        bind_run,
        bind_turn,
        new_run_id,
    )
"""

from __future__ import annotations

from openharness.observability.context import bind_run, bind_turn, new_run_id
from openharness.observability.logging import configure_logging, get_logger

__all__ = [
    "bind_run",
    "bind_turn",
    "configure_logging",
    "get_logger",
    "new_run_id",
]
