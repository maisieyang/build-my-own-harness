"""Tests for the L6 intake queue — ``services/autopilot.py``.

Card model + deterministic scoring + atomic queue read/write. MVP only
implements the ``"manual"`` source kind (scanner sources are a follow-up);
the schema itself is source-kind-agnostic so adding a new source later
doesn't require a redesign.

Scoring/enqueue take an explicit ``now: datetime`` parameter rather than
calling ``datetime.now()`` internally, so tests stay deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from openharness.services.autopilot import (
    Card,
    enqueue_card,
    load_queue,
    pick_next_card,
    save_queue,
    score_card,
)

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestEnqueueCard:
    def test_enqueue_creates_a_queued_card(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        card = enqueue_card(
            queue_path,
            goal="fix the flaky test",
            verify=["pytest -q"],
            max_iter=3,
            source_ref="manual-1",
            now=_NOW,
        )
        assert card.goal == "fix the flaky test"
        assert card.verify == ["pytest -q"]
        assert card.max_iter == 3
        assert card.status == "queued"
        assert card.source_kind == "manual"
        assert card.id

    def test_enqueue_persists_to_disk(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        enqueue_card(
            queue_path,
            goal="fix the flaky test",
            verify=["pytest -q"],
            max_iter=3,
            source_ref="manual-1",
            now=_NOW,
        )
        cards = load_queue(queue_path)
        assert len(cards) == 1
        assert cards[0].goal == "fix the flaky test"

    def test_duplicate_source_ref_does_not_double_enqueue(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        first = enqueue_card(
            queue_path,
            goal="fix the flaky test",
            verify=["pytest -q"],
            max_iter=3,
            source_ref="manual-1",
            now=_NOW,
        )
        second = enqueue_card(
            queue_path,
            goal="a different goal text",
            verify=["pytest -q"],
            max_iter=3,
            source_ref="manual-1",
            now=_NOW,
        )
        assert second.id == first.id
        assert len(load_queue(queue_path)) == 1


class TestScoreCard:
    def test_fresher_card_scores_higher_than_older_card(self) -> None:
        fresh = Card(
            id="a",
            source_kind="manual",
            source_ref="a",
            goal="g",
            verify=[],
            max_iter=1,
            labels=(),
            status="queued",
            created_at=_NOW,
        )
        old = Card(
            id="b",
            source_kind="manual",
            source_ref="b",
            goal="g",
            verify=[],
            max_iter=1,
            labels=(),
            status="queued",
            created_at=_NOW - timedelta(hours=48),
        )
        fresh_score, _ = score_card(fresh, now=_NOW)
        old_score, _ = score_card(old, now=_NOW)
        assert fresh_score > old_score

    def test_urgent_label_boosts_score(self) -> None:
        plain = Card(
            id="a",
            source_kind="manual",
            source_ref="a",
            goal="g",
            verify=[],
            max_iter=1,
            labels=(),
            status="queued",
            created_at=_NOW,
        )
        urgent = Card(
            id="b",
            source_kind="manual",
            source_ref="b",
            goal="g",
            verify=[],
            max_iter=1,
            labels=("urgent",),
            status="queued",
            created_at=_NOW,
        )
        plain_score, _ = score_card(plain, now=_NOW)
        urgent_score, _ = score_card(urgent, now=_NOW)
        assert urgent_score > plain_score

    def test_score_reasons_are_explainable(self) -> None:
        card = Card(
            id="a",
            source_kind="manual",
            source_ref="a",
            goal="g",
            verify=[],
            max_iter=1,
            labels=("urgent",),
            status="queued",
            created_at=_NOW,
        )
        _, reasons = score_card(card, now=_NOW)
        assert len(reasons) > 0
        assert any("urgent" in r for r in reasons)


class TestPickNextCard:
    def test_picks_highest_scoring_queued_card(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        enqueue_card(
            queue_path,
            goal="low priority",
            verify=[],
            max_iter=1,
            source_ref="a",
            now=_NOW - timedelta(hours=100),
        )
        urgent = enqueue_card(
            queue_path,
            goal="urgent fix",
            verify=[],
            max_iter=1,
            source_ref="b",
            now=_NOW,
            labels=("urgent",),
        )
        cards = load_queue(queue_path)
        picked = pick_next_card(cards, now=_NOW)
        assert picked is not None
        assert picked.id == urgent.id

    def test_skips_non_queued_cards(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        card = enqueue_card(queue_path, goal="g", verify=[], max_iter=1, source_ref="a", now=_NOW)
        cards = load_queue(queue_path)
        cards[0] = Card(
            id=card.id,
            source_kind=card.source_kind,
            source_ref=card.source_ref,
            goal=card.goal,
            verify=card.verify,
            max_iter=card.max_iter,
            labels=card.labels,
            status="completed",
            created_at=card.created_at,
        )
        assert pick_next_card(cards, now=_NOW) is None

    def test_empty_queue_returns_none(self) -> None:
        assert pick_next_card([], now=_NOW) is None


class TestQueuePersistence:
    def test_save_then_load_round_trips(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        card = Card(
            id="a",
            source_kind="manual",
            source_ref="a",
            goal="g",
            verify=["pytest -q"],
            max_iter=2,
            labels=("urgent",),
            status="queued",
            created_at=_NOW,
        )
        save_queue(queue_path, [card])
        loaded = load_queue(queue_path)
        assert len(loaded) == 1
        assert loaded[0].goal == "g"
        assert loaded[0].verify == ["pytest -q"]
        assert loaded[0].labels == ("urgent",)

    def test_load_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        assert load_queue(tmp_path / "does-not-exist.json") == []

    def test_malformed_json_raises_clear_error_not_a_traceback(self, tmp_path: Path) -> None:
        """Review finding: a hand-edited/corrupted queue.json must fail with
        a clear, actionable message, not a raw JSONDecodeError/KeyError."""
        queue_path = tmp_path / "queue.json"
        queue_path.write_text("{not valid json,", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed"):
            load_queue(queue_path)

    def test_missing_field_raises_clear_error(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        queue_path.write_text(json.dumps([{"id": "a", "goal": "g"}]), encoding="utf-8")
        with pytest.raises(ValueError, match="malformed"):
            load_queue(queue_path)


class TestEnqueueDedupRespectsStatus:
    """Review finding: dedup on source_ref alone lets a stale failed/completed
    card silently block re-enqueueing a genuinely new attempt."""

    def test_reenqueue_after_failure_creates_a_fresh_queued_card(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.json"
        first = enqueue_card(
            queue_path, goal="g", verify=["pytest -q"], max_iter=1, source_ref="x", now=_NOW
        )
        cards = load_queue(queue_path)
        cards = [c if c.id != first.id else _with_status(c, "failed") for c in cards]
        save_queue(queue_path, cards)

        second = enqueue_card(
            queue_path,
            goal="g retry",
            verify=["pytest -q"],
            max_iter=1,
            source_ref="x",
            now=_NOW + timedelta(hours=1),
        )

        assert second.id != first.id
        assert second.status == "queued"
        all_cards = load_queue(queue_path)
        assert len(all_cards) == 2

    def test_reenqueue_while_still_queued_returns_same_card(self, tmp_path: Path) -> None:
        """Unchanged behavior: a still-in-flight (queued/running) card is
        NOT duplicated -- only failed/completed cards get a fresh attempt."""
        first = enqueue_card(
            tmp_path / "queue.json",
            goal="g",
            verify=["pytest -q"],
            max_iter=1,
            source_ref="x",
            now=_NOW,
        )
        second = enqueue_card(
            tmp_path / "queue.json",
            goal="g different text",
            verify=["pytest -q"],
            max_iter=1,
            source_ref="x",
            now=_NOW,
        )
        assert second.id == first.id


def _with_status(card: Card, status: str) -> Card:
    return Card(
        id=card.id,
        source_kind=card.source_kind,
        source_ref=card.source_ref,
        goal=card.goal,
        verify=card.verify,
        max_iter=card.max_iter,
        labels=card.labels,
        status=status,
        created_at=card.created_at,
    )


class TestConcurrentEnqueueDoesNotLoseCards:
    """Review finding: unlocked read-modify-write could silently drop a
    concurrently-enqueued card via last-write-wins."""

    def test_sequential_enqueues_via_shared_helper_both_persist(self, tmp_path: Path) -> None:
        """A locked read-modify-write must serialize concurrent mutations
        rather than losing one to a last-write-wins race. This test proves
        the outcome property (both cards end up persisted) that a race
        would violate -- it doesn't need to fabricate real thread
        interleaving to be a meaningful regression lock."""
        queue_path = tmp_path / "queue.json"
        import threading

        results: list[Card] = []
        errors: list[BaseException] = []

        def _enqueue(ref: str) -> None:
            try:
                card = enqueue_card(
                    queue_path,
                    goal=f"goal-{ref}",
                    verify=["pytest -q"],
                    max_iter=1,
                    source_ref=ref,
                    now=_NOW,
                )
                results.append(card)
            except BaseException as exc:  # pragma: no cover - defensive
                errors.append(exc)

        threads = [threading.Thread(target=_enqueue, args=(f"ref-{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        cards = load_queue(queue_path)
        assert len(cards) == 8
        assert {c.source_ref for c in cards} == {f"ref-{i}" for i in range(8)}
