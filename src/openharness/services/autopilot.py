"""L6 intake queue — ``Card`` model + deterministic scoring + atomic queue I/O.

MVP only implements the ``"manual"`` source kind (scanner sources are a
follow-up); the schema itself is source-kind-agnostic so adding a new
source later doesn't require a redesign.

Scoring/enqueue take an explicit ``now: datetime`` parameter rather than
calling ``datetime.now()`` internally, so callers (and their tests) stay
deterministic.

Concurrency: ``enqueue_card`` (and any other read-modify-write) takes an
exclusive ``fcntl`` lock on a sibling ``.lock`` file around the
load-mutate-save cycle, so two processes (e.g. a crontab-fired
``run-next`` racing a manual ``enqueue``) can't silently drop one
another's write via last-write-wins on the atomic whole-file rewrite.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

_BASE_SCORE_BY_SOURCE_KIND: dict[str, int] = {"manual": 50}
_URGENCY_LABEL_BONUS: dict[str, int] = {"urgent": 30, "bug": 20}
_DECAY_PER_HOUR = 1

# Card statuses that mean "still in flight" -- a repeat enqueue_card() call
# for the same source_ref while the existing card is in one of these states
# returns that same card (no duplicate). A "failed"/"completed" card does
# NOT block a fresh attempt (see enqueue_card's dedup logic).
_IN_FLIGHT_STATUSES = frozenset({"queued", "running"})


@dataclass(frozen=True)
class Card:
    """One intake-queue entry — a goal + verify gate awaiting a repair-loop run."""

    id: str
    source_kind: str
    source_ref: str
    goal: str
    verify: list[str]
    max_iter: int
    labels: tuple[str, ...]
    status: str
    created_at: datetime


def score_card(card: Card, *, now: datetime) -> tuple[int, list[str]]:
    """Deterministic priority score — higher runs sooner.

    ``source_kind`` base score, plus a per-label urgency bonus, minus a
    linear-in-hours decay since ``card.created_at``. Never calls
    ``datetime.now()`` — the caller supplies ``now`` so scoring stays
    reproducible in tests.
    """
    reasons: list[str] = []

    base = _BASE_SCORE_BY_SOURCE_KIND.get(card.source_kind, 0)
    reasons.append(f"source_kind={card.source_kind!r} base score {base}")

    bonus = 0
    for label in card.labels:
        label_bonus = _URGENCY_LABEL_BONUS.get(label)
        if label_bonus:
            bonus += label_bonus
            reasons.append(f"label {label!r} urgency bonus +{label_bonus}")

    hours_old = (now - card.created_at).total_seconds() / 3600
    decay = int(hours_old * _DECAY_PER_HOUR)
    reasons.append(f"age {hours_old:.1f}h decay -{decay}")

    return base + bonus - decay, reasons


def _card_from_entry(entry: dict[str, object]) -> Card:
    return Card(
        id=entry["id"],  # type: ignore[arg-type]
        source_kind=entry["source_kind"],  # type: ignore[arg-type]
        source_ref=entry["source_ref"],  # type: ignore[arg-type]
        goal=entry["goal"],  # type: ignore[arg-type]
        verify=list(entry["verify"]),  # type: ignore[call-overload]
        max_iter=entry["max_iter"],  # type: ignore[arg-type]
        labels=tuple(entry["labels"]),  # type: ignore[arg-type]
        status=entry["status"],  # type: ignore[arg-type]
        created_at=datetime.fromisoformat(entry["created_at"]),  # type: ignore[arg-type]
    )


def load_queue(queue_path: Path) -> list[Card]:
    """Read the queue file, or return ``[]`` if it doesn't exist yet.

    Raises ``ValueError`` with a clear, actionable message (not a raw
    ``JSONDecodeError``/``KeyError``) if the file is malformed, truncated,
    or schema-drifted -- it's a plain JSON file a user might hand-edit.
    """
    if not queue_path.exists():
        return []
    try:
        raw = json.loads(queue_path.read_text(encoding="utf-8"))
        return [_card_from_entry(entry) for entry in raw]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"autopilot queue file at {queue_path} is malformed: {exc}. "
            "Fix or delete it manually to continue."
        ) from exc


def save_queue(queue_path: Path, cards: list[Card]) -> None:
    """Atomically rewrite the queue file (same-dir tempfile + ``os.replace``)."""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "id": card.id,
            "source_kind": card.source_kind,
            "source_ref": card.source_ref,
            "goal": card.goal,
            "verify": card.verify,
            "max_iter": card.max_iter,
            "labels": list(card.labels),
            "status": card.status,
            "created_at": card.created_at.isoformat(),
        }
        for card in cards
    ]
    content = json.dumps(payload, indent=2)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=queue_path.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, queue_path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@contextlib.contextmanager
def _queue_lock(queue_path: Path) -> Iterator[None]:
    """Exclusive ``fcntl`` lock on a sibling ``.lock`` file.

    Serializes concurrent read-modify-write cycles (``enqueue_card``, status
    transitions) across processes -- without this, two processes racing
    (e.g. a crontab-fired ``run-next`` vs. a manual ``enqueue``) can silently
    drop one another's write, since ``save_queue``'s atomic rewrite only
    guarantees the FINAL write itself is torn-free, not that the
    read-then-write cycle around it is race-free.
    """
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = queue_path.with_suffix(queue_path.suffix + ".lock")
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def with_queue_lock(queue_path: Path, mutator: Callable[[list[Card]], list[Card]]) -> list[Card]:
    """Load, mutate, and atomically save the queue under an exclusive lock.

    ``mutator`` receives the current card list and returns the new one.
    The single lock held across load→mutate→save is what actually closes
    the race window that ``save_queue``'s atomicity alone doesn't cover.
    """
    with _queue_lock(queue_path):
        cards = load_queue(queue_path)
        cards = mutator(cards)
        save_queue(queue_path, cards)
        return cards


def enqueue_card(
    queue_path: Path,
    *,
    goal: str,
    verify: list[str],
    max_iter: int,
    source_ref: str,
    now: datetime,
    labels: Sequence[str] = (),
    source_kind: str = "manual",
) -> Card:
    """Enqueue a new card, deduplicating on ``source_ref``.

    A repeat ``source_ref`` returns the existing card unchanged ONLY if
    that card is still in flight (``queued``/``running``) -- a prior
    ``failed``/``completed`` card does not block a fresh attempt at the
    same ``source_ref``; a new card is created instead.
    """
    result: list[Card] = []

    def _mutate(cards: list[Card]) -> list[Card]:
        for existing in cards:
            if existing.source_ref == source_ref and existing.status in _IN_FLIGHT_STATUSES:
                result.append(existing)
                return cards

        card = Card(
            id=str(uuid.uuid4()),
            source_kind=source_kind,
            source_ref=source_ref,
            goal=goal,
            verify=list(verify),
            max_iter=max_iter,
            labels=tuple(labels),
            status="queued",
            created_at=now,
        )
        result.append(card)
        return [*cards, card]

    with_queue_lock(queue_path, _mutate)
    return result[0]


def pick_next_card(cards: list[Card], *, now: datetime) -> Card | None:
    """Highest-scoring ``status == "queued"`` card, or ``None`` if none queued."""
    queued = [card for card in cards if card.status == "queued"]
    if not queued:
        return None
    return max(queued, key=lambda card: score_card(card, now=now)[0])
