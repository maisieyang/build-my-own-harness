"""Engine — the agent loop and its per-query collaborators.

P2-T1 (this scaffold): :class:`QueryContext` carries the immutable per-query
collaborators; :mod:`messages` exposes pure helpers that build conversation
history; :func:`run_query` exists as a typed stub. The loop body itself
lands in P2-T4.

Public API is consolidated here in P2-T1 sub-unit 1d (re-exports below).
"""

from __future__ import annotations
