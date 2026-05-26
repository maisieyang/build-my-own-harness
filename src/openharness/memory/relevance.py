"""Relevance scoring for memory injection — P10-T3.3a.

Per ``decisions/25-phase-10-boundary.md`` D28.7: turn a passive memory
pile into a query-relevant top-N. The load-bearing rule is **zero-
token-hits exclusion** — a memory whose name/description/tags/body
share NO tokens with the user's query is excluded entirely, not
demoted. Without this rule, relevance degrades into "N most-recent
memories on every turn" which defeats the point.

Scoring formula::

    score = meta_hits   * 2.0
          + body_hits   * 1.0
          + importance  * 0.4
          + min(use_count, 5) * 0.1
          + recency_boost

where ``recency_boost = 0.3`` if updated within 14 days,
``0.1`` within 30 days, else ``0.0``. ``use_count`` is capped at 5
to prevent popularity feedback loops (a 100-times-used memory
shouldn't permanently dominate a brand-new highly-relevant one).

Sort key: ``(-score, -updated_at)`` — score descending, then
modified-time descending as tiebreaker.

Tokenization: ``re.findall(r"\\w+", text.lower())``. Python's
``\\w`` is Unicode-aware, so Han characters (``支付``, ``退款``)
count as word tokens alongside ASCII.

P11-T6-6b (D29.9): minimum stopword set of ~22 ASCII function
words is subtracted from the QUERY (not memory tokens). Reasons:

- Phase 10 T6 surfaced a false positive ("what is the weather
  today" matched a Stripe-refund memory via shared ``the``).
- Memory bodies are written by the author and often deliberately
  short — stripping function words from THEIR tokens would lose
  signal (e.g. a memory body "use the AWS CLI" should still match
  the "AWS" query token). So stopwords only apply on the query side.
- Han characters / non-ASCII tokens are never in the stopword set;
  ``这个`` / ``是`` etc. survive.

Surface threshold also tightens: ``meta_hits >= 1 OR body_hits >= 2``
(D29.9). A single body hit alone — the precise failure mode that
surfaced the Stripe regression — no longer qualifies.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from openharness.memory.model import Memory


# Unicode-aware word boundary. ``\w`` matches ASCII letters/digits/_
# AND Han characters AND most non-ASCII letters via the Unicode
# default category set. ``re.UNICODE`` is the default in Python 3 but
# we set it explicitly so the intent is documented.
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)

_RECENCY_BOOST_14_DAYS = 0.3
_RECENCY_BOOST_30_DAYS = 0.1
_USE_COUNT_CAP = 5

# P11-T6-6b (D29.9): minimum English stopword set subtracted from
# the QUERY tokens before scoring. Verbatim from boundary doc — do
# not silently grow this list without a regression case justifying
# the addition (each new stopword is a small false-negative risk).
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "of",
        "to",
        "for",
        "with",
        "on",
        "in",
        "at",
        "by",
        "and",
        "or",
        "but",
        "this",
        "that",
        "these",
        "those",
    }
)


def _tokenize(text: str) -> set[str]:
    """Lowercase + word-split into a set for O(1) intersection."""
    return set(_TOKEN_PATTERN.findall(text.lower()))


def _meta_text(memory: Memory) -> str:
    """Concatenate name + description + tags for meta-hit counting.

    Each meta token weighs 2x a body token (D28.7) because the
    memory's identity (name) and short summary (description) are
    higher-signal than incidental body words. Tags are written
    deliberately by the author, so they count as meta.
    """
    return " ".join((memory.name, memory.description, " ".join(memory.tags)))


def _recency_boost(updated_at: datetime, now: datetime) -> float:
    """Step function: 0.3 within 14d, 0.1 within 30d, else 0.0.

    Small relative to meta-hit weight (2.0) so a precisely-named
    6-month-old memory still beats a fresh-but-only-incidentally-
    related one. The boost matters most as a tiebreaker among
    equally-relevant memories.
    """
    age = now - updated_at
    if age <= timedelta(days=14):
        return _RECENCY_BOOST_14_DAYS
    if age <= timedelta(days=30):
        return _RECENCY_BOOST_30_DAYS
    return 0.0


def select_relevant_memories(
    query: str,
    memories: Iterable[Memory],
    *,
    max_results: int = 5,
    now: datetime | None = None,
) -> list[Memory]:
    """Return up to ``max_results`` memories most relevant to ``query``.

    Four exclusion gates (in order):

    1. ``query`` tokenizes to an empty set after stopword removal →
       return ``[]`` (no signal to match against). Phase 11 D29.9
       adds the stopword subtraction; Phase 10 only checked raw
       tokens.
    2. ``memory.disabled is True`` → exclude regardless of score
       (soft-deleted memories must not surface).
    3. ``meta_hits == 0 and body_hits == 0`` → exclude (D28.7
       load-bearing rule; without this, "no match" silently
       degenerates into "most recent N").
    4. ``meta_hits == 0 and body_hits < 2`` → exclude (D29.9
       tightened threshold). A single body-only hit is the noisy
       pattern that surfaced Phase 10 T6's "the" false positive.

    ``now`` defaults to ``datetime.now(timezone.utc)``; tests inject a
    fixed value to make recency-boost behavior deterministic.

    Returns a fresh list — callers are free to mutate. Phase 10's
    consumer (prompt assembly in P10-T4) iterates the result to call
    :func:`openharness.memory.usage.mark_memory_used` on each picked
    memory immediately before injection.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # P11-T6-6b (D29.9): subtract stopwords from QUERY tokens only.
    # Memory tokens stay intact so deliberately-short bodies like
    # "use the AWS CLI" still match an "AWS" query.
    query_tokens = _tokenize(query) - _STOPWORDS
    if not query_tokens:
        return []

    scored: list[tuple[float, datetime, Memory]] = []
    for memory in memories:
        if memory.disabled:
            continue
        meta_tokens = _tokenize(_meta_text(memory))
        body_tokens = _tokenize(memory.body)
        meta_hits = len(query_tokens & meta_tokens)
        body_hits = len(query_tokens & body_tokens)
        # D28.7 load-bearing: zero-hits exclusion
        if meta_hits == 0 and body_hits == 0:
            continue
        # P11-T6-6b D29.9: body-only single-hit no longer surfaces
        # (raises the bar for incidental body-token matches).
        if meta_hits == 0 and body_hits < 2:
            continue
        score = (
            meta_hits * 2.0
            + body_hits * 1.0
            + memory.importance * 0.4
            + min(memory.use_count, _USE_COUNT_CAP) * 0.1
            + _recency_boost(memory.updated_at, now)
        )
        scored.append((score, memory.updated_at, memory))

    # Sort: score desc, then updated_at desc as tiebreaker.
    # ``datetime.timestamp()`` requires tz-aware; parse_memory enforces
    # this so any Memory in the pipeline has tzinfo.
    scored.sort(key=lambda x: (-x[0], -x[1].timestamp()))
    return [memory for (_, _, memory) in scored[:max_results]]
