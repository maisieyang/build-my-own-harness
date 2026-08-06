"""Engine — the agent loop and its per-query collaborators.

P2-T1 (this scaffold) ships:

- :class:`QueryContext` — immutable per-query collaborator bundle (sub-unit 1a).
- :mod:`openharness.engine.messages` — pure helpers for building conversation
  history (sub-unit 1b). Loop-internal: import from the submodule directly.
- :func:`run_query` — async-generator entrypoint (sub-unit 1c). Body is a
  ``NotImplementedError`` stub; P2-T4 fills it.

Public API:

    from openharness.engine import QueryContext, run_query
"""

from __future__ import annotations

from openharness.engine.context import QueryContext
from openharness.engine.query import extract_authorization_context, run_query

__all__ = [
    "QueryContext",
    "extract_authorization_context",
    "run_query",
]
