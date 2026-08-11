"""``configure_logging`` + ``get_logger`` — structlog bootstrap for P3-T5.

Per Three-Axis 轴 1:framework observability 走 module 内联 structlog
(hook 系统留作 user-level 接入点);轴 3:log 全走 ``stderr``,``stdout``
保持 LLM text response 干净。

This module ships **only the wire format** — the 8 actual log call points
land in 5c/5d (engine / api retry / permissions / hooks). The processor
chain ordering matters:

1. ``merge_contextvars`` first — pulls ``run_id`` / ``turn_id`` bound by
   :mod:`openharness.observability.context` into every record.
2. ``add_log_level`` — adds the canonical ``level`` field.
3. ``TimeStamper`` — ISO-8601 ``timestamp``;tests parse this so render
   order matters.
4. ``sanitize_processor`` — redacts known-sensitive keys + token patterns
   before either renderer sees the event (5b).
5. Renderer — :class:`structlog.dev.ConsoleRenderer` (human) or
   :class:`structlog.processors.JSONRenderer` (machine / JSONL pipe).

The renderer choice is exclusively about output **format**. Both go to
the same handler (stderr); ``--log-format`` is a Phase 3 CLI flag added
in 5e.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Literal

import structlog

from openharness.observability.sanitize import sanitize_processor

if TYPE_CHECKING:
    from typing import TextIO

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["console", "json"]


# Third-party libraries that emit at INFO level into the stdlib root logger.
# Their records bypass structlog's JSONRenderer (they're raw stdlib LogRecords,
# not structlog event dicts), so they show up as plain-text lines on stderr —
# breaking JSONL purity for ``jq`` / OTel exporters. Silenced to WARNING so
# normal non-interactive runs emit only our structlog JSON.
#
# Trade-off: ``--log-level DEBUG`` will not show httpx/openai internals.
# Users debugging those should set the levels manually:
#   logging.getLogger("httpx").setLevel(logging.DEBUG)
# *after* configure_logging.
_NOISY_THIRD_PARTY_LOGGERS: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "openai",
    "urllib3",
)


def configure_logging(
    *,
    level: LogLevel = "WARNING",
    format: LogFormat = "console",
    stream: TextIO | None = None,
) -> None:
    """Configure structlog + stdlib logging once.

    Idempotent within a process — calling twice replaces the handler set
    on the root logger (so tests can re-configure between runs).

    Args:
        level: minimum level to emit. ``WARNING`` is the default per
            ``decisions/08`` D13.6: don't pollute terminal in normal use;
            users opt in with ``--log-level INFO`` / ``DEBUG``.
        format: ``"console"`` for human reading(rich-ish, no color in non-TTY),
            ``"json"`` for one-JSON-per-line (jq-friendly, OTel/LangSmith
            exporter-friendly).
        stream: where to write. ``None`` → ``sys.stderr`` (production).
            Tests inject :class:`io.StringIO`.
    """
    # 0. Clear structlog's lazy-bind cache. ``cache_logger_on_first_use=True``
    #    means BoundLoggers are bound to the active configuration at first
    #    access. Without ``reset_defaults()``, a re-configure (CLI flag at
    #    runtime, test reconfigure between cases) silently no-ops for any
    #    already-cached logger — root.handlers gets the new stream but the
    #    cached logger keeps writing to the old one. ``reset_defaults()``
    #    drops the cache so the next ``get_logger()`` rebinds.
    structlog.reset_defaults()

    # 1. Configure stdlib logging — structlog rides on top of it so users
    #    can :meth:`logging.getLogger("openharness").addHandler(...)` to
    #    forward records anywhere (file / syslog / etc.).
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Silence noisy third-party loggers — their plain-text records would
    # otherwise leak into the JSONL stream on stderr (see module constant).
    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # 2. Configure structlog processor chain.
    renderer: structlog.types.Processor
    if format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        # ``colors`` default is auto-detected (off in non-TTY) so tests
        # don't get ANSI escape codes interleaved with assertions.
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            sanitize_processor,  # 5b — runs before renderer so JSON / console both sanitized
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        # ``cache_logger_on_first_use=False`` — module-level ``logger =
        # get_logger("engine")`` in callers (engine / api / permissions /
        # hooks) is evaluated at import time. With cache=True, each such
        # logger would bind processors ONCE at first ``.info()`` call and
        # never re-bind. CLI / test re-configures (5e flag, fixture
        # reconfigure) need re-binding — set cache=False so every call
        # picks up the current global processor chain. Cost: a small
        # per-call function dispatch; negligible vs. JSON serialization.
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger.

    All call sites should pass a short module name (``"engine"`` /
    ``"api"`` / ``"permissions"`` / ``"hooks"``) so JSONL consumers can
    filter by ``logger`` field downstream.
    """
    return structlog.stdlib.get_logger(name or "openharness")
