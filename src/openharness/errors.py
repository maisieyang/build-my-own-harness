"""OpenHarness exception hierarchy — cross-cutting root + sub-class branches.

Per ``decisions/08-phase-3-boundary.md`` D13.4 (RPC 配套 5 — Error Taxonomy):

::

    OpenHarnessError                   # root, this file
    ├── OpenHarnessApiError            # API layer (api/errors.py)
    │   ├── AuthenticationFailure
    │   ├── RateLimitFailure
    │   └── RequestFailure
    ├── LoopError                      # P3-T2.2b
    │   └── LoopLimitExceeded          # engine/errors.py
    ├── ToolError                      # P3-T2.2c (placeholder for P3-T3)
    ├── PermissionError                # P3-T2.2c (placeholder for P3-T3)
    └── HookError                      # P3-T2.2c (placeholder for P3-T4)

The classes here are **placeholders for the cross-cutting branches**;
P3-T3 / P3-T4 fill in fields and call sites when those capabilities land.

Why a single root: lets ``cli.py`` write ``except OpenHarnessError`` as a
catch-all without falling back to bare ``except Exception`` (which would
also swallow programming bugs we want to propagate to the runner).

Why a single file (not ``errors/`` dir): the entire taxonomy fits in one
short module and reads more cleanly as a unit. Phase 4-6 may add 1-2
more classes; revisit dir-split only at ≥10 classes.

Sub-unit 2a ships ONLY the root. The four branch classes land in 2b/2c.
"""

from __future__ import annotations


class OpenHarnessError(Exception):
    """Root of the OpenHarness exception hierarchy.

    Inherits ``Exception`` (not ``BaseException``) so ``KeyboardInterrupt``
    and ``SystemExit`` keep propagating — a project-wide handler must not
    swallow them.

    Concrete branches add fields as needed (e.g., ``OpenHarnessApiError``
    carries ``status_code``; ``LoopLimitExceeded`` carries ``max_turns``).
    The root itself carries only the message — anything richer belongs in
    a subclass.
    """
