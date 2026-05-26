"""Tests for :func:`select_relevant_memories` — P10-T3.3a.

D28.7 spec compliance + tiebreaker / edge cases. The single
load-bearing rule the boundary doc names is **zero-token-hits
exclusion** — multiple tests pin it from different angles so
future "let me default to most-recent N when no match" patches
fail loudly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from openharness.memory.model import Memory, MemoryScope, MemoryType
from openharness.memory.relevance import select_relevant_memories

_UTC = timezone.utc
_NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=_UTC)


def _build_memory(
    *,
    name: str = "x",
    description: str = "d",
    body: str = "b",
    type_: MemoryType = MemoryType.PROJECT,
    importance: float = 0.5,
    use_count: int = 0,
    tags: tuple[str, ...] = (),
    updated_at: datetime | None = None,
    disabled: bool = False,
    id_: str = "01HXXXXXXXX",
) -> Memory:
    """Minimal-valid Memory + per-test overrides."""
    return Memory(
        id=id_,
        name=name,
        description=description,
        type=type_,
        scope=MemoryScope.PRIVATE,
        created_at=_NOW,
        updated_at=updated_at if updated_at is not None else _NOW,
        body=body,
        signature="a" * 64,
        source_path=Path("/tmp/x.md"),
        tags=tags,
        importance=importance,
        use_count=use_count,
        disabled=disabled,
    )


class TestSelectRelevantMemoriesGates:
    def test_empty_memories_returns_empty(self) -> None:
        assert select_relevant_memories("query", [], now=_NOW) == []

    def test_empty_query_returns_empty(self) -> None:
        m = _build_memory(name="anything")
        assert select_relevant_memories("", [m], now=_NOW) == []

    def test_whitespace_only_query_returns_empty(self) -> None:
        m = _build_memory(name="anything")
        assert select_relevant_memories("   \n  ", [m], now=_NOW) == []

    def test_zero_hits_excluded(self) -> None:
        # D28.7 LOAD-BEARING RULE. If this test fails, relevance has
        # silently degenerated into "most recent N" and the whole point
        # of scoring is broken.
        m = _build_memory(name="alpha", description="beta", body="gamma")
        assert select_relevant_memories("zeta", [m], now=_NOW) == []

    def test_disabled_excluded_regardless_of_high_score(self) -> None:
        # Even a perfect match must not surface if disabled=True
        # (soft-delete contract).
        m = _build_memory(
            name="stripe",
            description="stripe SDK reference",
            body="stripe payment refund",
            importance=1.0,
            disabled=True,
        )
        assert select_relevant_memories("stripe", [m], now=_NOW) == []


class TestSelectRelevantMemoriesMatching:
    def test_meta_hit_in_name(self) -> None:
        m = _build_memory(name="stripe-sdk-version")
        result = select_relevant_memories("how to use stripe", [m], now=_NOW)
        assert result == [m]

    def test_meta_hit_in_description(self) -> None:
        m = _build_memory(name="payments", description="Stripe integration notes")
        result = select_relevant_memories("stripe", [m], now=_NOW)
        assert result == [m]

    def test_meta_hit_in_tags(self) -> None:
        m = _build_memory(name="payments", tags=("stripe", "billing"))
        result = select_relevant_memories("stripe", [m], now=_NOW)
        assert result == [m]

    def test_body_hit_alone_sufficient(self) -> None:
        m = _build_memory(name="random", description="random", body="The Stripe API requires v8.")
        result = select_relevant_memories("stripe", [m], now=_NOW)
        assert result == [m]

    def test_query_case_insensitive(self) -> None:
        m = _build_memory(name="stripe-sdk")
        assert select_relevant_memories("STRIPE", [m], now=_NOW) == [m]
        assert select_relevant_memories("Stripe", [m], now=_NOW) == [m]

    def test_han_characters_match(self) -> None:
        # Python \w is Unicode-aware → Han characters tokenize as words.
        # Important for any non-English projects using memory.
        # ``name`` itself is ASCII-only (NAME_PATTERN regex), but
        # description / body / tags accept any text — relevance must
        # tokenize all of them via Unicode.
        m = _build_memory(
            name="payment-bug",
            description="退款 race condition",
            body="支付 流程 涉及 Stripe webhook",
            tags=("支付", "stripe"),
        )
        result = select_relevant_memories("退款 流程", [m], now=_NOW)
        assert result == [m]


class TestSelectRelevantMemoriesScoring:
    def test_meta_hit_beats_body_hit(self) -> None:
        # meta weight 2.0 vs body weight 1.0 — a single meta-hit beats
        # a single body-hit (everything else equal).
        m_meta = _build_memory(name="stripe-mem", body="unrelated body")
        m_body = _build_memory(name="other", body="stripe in body only")
        result = select_relevant_memories("stripe", [m_meta, m_body], now=_NOW)
        assert result[0] is m_meta

    def test_higher_importance_wins_at_tied_hits(self) -> None:
        m_low = _build_memory(name="stripe-a", importance=0.0)
        m_high = _build_memory(name="stripe-b", importance=1.0)
        result = select_relevant_memories("stripe", [m_low, m_high], now=_NOW)
        assert result[0] is m_high

    def test_use_count_capped_at_5(self) -> None:
        # A 100-times-used memory must NOT permanently dominate a
        # fresh one — popularity feedback loop prevention. With both
        # at use_count >= 5, the scoring tie is broken by updated_at.
        m_capped = _build_memory(name="stripe-a", use_count=5, updated_at=_NOW)
        m_overflow = _build_memory(
            name="stripe-b",
            use_count=100,
            updated_at=_NOW - timedelta(days=60),
        )
        result = select_relevant_memories("stripe", [m_capped, m_overflow], now=_NOW)
        # Both score equally on use_count contribution (both >=5 capped).
        # m_capped has fresher updated_at → tiebreaker → wins.
        # m_overflow also lost recency_boost (>30d old).
        assert result[0] is m_capped

    def test_recency_boost_within_14_days(self) -> None:
        m_fresh = _build_memory(name="stripe-a", updated_at=_NOW - timedelta(days=7))
        m_old = _build_memory(name="stripe-b", updated_at=_NOW - timedelta(days=60))
        result = select_relevant_memories("stripe", [m_fresh, m_old], now=_NOW)
        assert result[0] is m_fresh

    def test_recency_boost_step_function(self) -> None:
        # 7d → 0.3 boost; 20d → 0.1 boost; 60d → 0. Pin all three.
        m_7d = _build_memory(name="stripe-a", updated_at=_NOW - timedelta(days=7))
        m_20d = _build_memory(name="stripe-b", updated_at=_NOW - timedelta(days=20))
        m_60d = _build_memory(name="stripe-c", updated_at=_NOW - timedelta(days=60))
        result = select_relevant_memories("stripe", [m_60d, m_20d, m_7d], now=_NOW)
        # 7d wins (boost 0.3), 20d next (boost 0.1), 60d last (boost 0)
        assert result == [m_7d, m_20d, m_60d]


class TestSelectRelevantMemoriesOrdering:
    def test_max_results_caps_output(self) -> None:
        memories = [_build_memory(name=f"stripe-{i}", id_=f"01HM{i:08d}") for i in range(10)]
        result = select_relevant_memories("stripe", memories, max_results=3, now=_NOW)
        assert len(result) == 3

    def test_tiebreaker_is_updated_at_desc(self) -> None:
        # All else equal, more-recent updated_at wins.
        m_old = _build_memory(name="stripe-a", updated_at=_NOW - timedelta(days=100))
        m_new = _build_memory(name="stripe-b", updated_at=_NOW - timedelta(days=50))
        result = select_relevant_memories("stripe", [m_old, m_new], now=_NOW)
        # m_new isn't fresher enough to trigger a different recency
        # bucket (both >30d) → equal score → updated_at tiebreaker.
        # m_new wins because it's newer.
        assert result[0] is m_new

    def test_higher_scoring_comes_first(self) -> None:
        # Memory with 2 meta-hits should rank above one with 1 meta-hit.
        m_one = _build_memory(name="stripe")
        m_two = _build_memory(name="stripe-payment", description="stripe related")
        result = select_relevant_memories("stripe payment", [m_one, m_two], now=_NOW)
        assert result[0] is m_two


class TestNowDefault:
    def test_now_defaults_to_current_utc(self) -> None:
        # When called without explicit now, fall back to datetime.now(UTC).
        # Just verify no exception and the call returns; we don't pin
        # exact behavior because system time varies.
        m = _build_memory(name="stripe", updated_at=datetime.now(_UTC))
        result = select_relevant_memories("stripe", [m])
        assert result == [m]
