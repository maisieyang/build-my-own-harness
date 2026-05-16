"""Execution substrate abstraction — Phase 7a.

The harness's **substrate-aware** layer:``BashTool.execute`` no longer
directly calls ``asyncio.create_subprocess_shell``. Instead it delegates
to ``ctx.execution_env.run_command(...)`` where ``execution_env`` is
any :class:`ExecutionEnvironment`(``HostExecution`` by default, ``SandboxExecution``
when Phase 7b lands).

Per ``decisions/15-phase-7-boundary.md``:

- D17.1: Protocol with single ``run_command(command, cwd, timeout)``
  → :class:`ProcessResult`(``output`` merged + ``exit_code`` +
  ``timed_out``)
- D17.2: :class:`HostExecution` wraps current Bash behavior
  identically — behavior parity is the load-bearing assertion
- D17.3: ``QueryContext.execution_env`` field defaults to the
  ``HostExecution`` singleton;``ToolExecutionContext.execution_env``
  is additive(populated by the engine from the QueryContext)
- D17.4: only ``BashTool`` consumes ``execution_env``;Read / Write /
  Edit / Grep / MCP / Skills / SubAgent ignore the field
- D17.5: real Docker substrate(``SandboxExecution``)defers to
  Phase 7b

Cross-cutting invariant — this is the **fourth tenant test** of Phase
3's abstractions. Permissions / hooks / engine dispatch / observability /
mcp / compaction / skills / commands / protocols all stay zero-diff.

Public API::

    from openharness.execution import (
        ExecutionEnvironment,
        HostExecution,
        ProcessResult,
    )

The default singleton ``_HOST_EXECUTION`` is intentionally module-private
— callers construct their own ``HostExecution()`` if they want to inject
a fresh instance(or just use the QueryContext default which points to
the singleton).
"""

from __future__ import annotations

from openharness.execution.base import ExecutionEnvironment, ProcessResult
from openharness.execution.host import HostExecution

__all__ = [
    "ExecutionEnvironment",
    "HostExecution",
    "ProcessResult",
]
