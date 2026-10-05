"""Postgres-only behaviour of sessions, glossary and reviews (concurrency, idempotency, JSONB)."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from tutor.db.uow import PgUnitOfWork
from tutor.domain.fsrs import FsrsState, new_state, review, state_to_json
from tutor.domain.glossary import IncomingItem, InsertItem, Reinforce
from tutor.domain.metrics import SessionMetrics
from tutor.domain.profile import Profile
from tutor.domain.text import normalize
from tutor.domain.validation import Evidence, ReportedError, ValidError
from tutor.services.context import Services
from tutor.services.ports import (
    OpenSessionExists,
    ReviewLogRow,
    SessionRow,
    UnitOfWork,
    UowFactory,
)
from tutor.services.session_end import end_session
from tutor.services.views import EndSessionResult

pytestmark = pytest.mark.integration

EVIDENCE = Evidence(
    user_turns=(
        "Yesterday I goed to the standup and explained the rollback to the whole team, "
        "and then we talked about the release plan, the monitoring alerts and who would "
        "be on call during the weekend after the deploy.",
    ),
    errors=(ReportedError(said="I goed", correct="I went", category="grammar"),),
    chunks_used=(),
    task_result="achieved",
    hints_given=1,
    cefr_level="B2",
    cefr_confidence="medium",
    cefr_evidence=("Explained a rollback clearly",),
    confidence_1_5=4,
    assistant_words_estimate=None,
)
ERROR = ValidError(
    said="I goed", correct="I went", correct_norm="i went", category="grammar", turn_index=0
)


def _open(uow: UnitOfWork, now: datetime) -> SessionRow:
    item = uow.track.items("it")[0]
    return uow.sessions.create(
        plan_item_id=None,
        track_item_id=item.id,
        prep_text=None,
        mode="voice",
        client="claude",
        brief_variant="base",
        chunks_offered=[c.id for c in item.chunks],
        now=now,
    )


def _metrics(user_words: int) -> SessionMetrics:
    return SessionMetrics(
        user_words=user_words,
        assistant_words_estimate=None,
        user_ratio=None,
        turns=4,
        words_per_turn=user_words / 4,
        duration_min=12.0,
        user_words_per_min=user_words / 12,
        errors_total=1,
        errors_rejected=0,
        errors_by_category={"grammar": 1},
        errors_per_100w=100 / user_words,
        recurring_errors=0,
        uptake_count=1,
        chunks_offered=5,
        chunks_used=2,
        chunks_rejected=0,
        activation_rate=0.4,
    )


def _add_confirmed(
    uow: UnitOfWork, session_id: UUID, text_: str, due: datetime, now: datetime
) -> UUID:
    item = IncomingItem(
        kind="chunk",
        text=text_,
        meaning="undo a release",
        context_sentence=f"We had to {text_} the deploy.",
        domain="it",
    )
    norm = normalize(text_)
    action = InsertItem(
        index=0,
        item=item,
        text_norm=norm,
        status="confirmed",
        provisional_expires_at=None,
        first_due=due,
    )
    uow.glossary.apply([action], [item], session_id=session_id, now=now)
    return uow.glossary.by_norms([norm])[norm].id


def _wait_for_lock_wait(engine: Engine, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            if waiting:
                return True
            conn.rollback()  # pg_stat_activity is a per-transaction snapshot
            time.sleep(0.05)
    return False


def test_two_connections_racing_for_an_open_session_one_wins(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    # Review Focus 5: two start_lesson calls racing; exactly one open session, no 500.
    outcomes: list[object] = []

    def second_caller() -> None:
        try:
            with uow_factory(user_id) as uow:
                outcomes.append(_open(uow, now))
        except Exception as exc:  # recorded and asserted below
            outcomes.append(exc)

    with uow_factory(user_id) as first:
        winner = _open(first, now)
        thread = threading.Thread(target=second_caller)
        thread.start()
        assert _wait_for_lock_wait(engine), "the second insert never waited on the first"
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], OpenSessionExists)
    with uow_factory(user_id) as uow:
        current = uow.sessions.open_session()
        assert current is not None
        assert current.id == winner.id
    with engine.connect() as conn:
        opened = conn.execute(
            text("SELECT count(*) FROM sessions WHERE user_id = :u"), {"u": user_id}
        ).scalar_one()
    assert opened == 1


def test_a_second_open_session_in_one_unit_of_work_keeps_the_transaction_usable(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        first = _open(uow, now)
        with pytest.raises(OpenSessionExists):
            _open(uow, now)
        current = uow.sessions.open_session()
        assert current is not None
        assert current.id == first.id


def test_repeated_close_keeps_one_metrics_row_and_one_error_set(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    # Review Focus 2 (repository level): a second end_session write never duplicates rows.
    ended = now + timedelta(minutes=12)
    with uow_factory(user_id) as uow:
        session = _open(uow, now)
        for user_words in (40, 50):
            uow.sessions.close(
                session.id,
                status="closed",
                low_trust=False,
                ended_at=ended,
                evidence=EVIDENCE,
                raw_evidence={"user_turns": list(EVIDENCE.user_turns)},
                cefr_excluded=False,
                result={"status": "closed"},
            )
            uow.sessions.save_metrics(session.id, _metrics(user_words))
            uow.sessions.save_errors(session.id, [ERROR])
    with engine.connect() as conn:
        metrics = conn.execute(
            text(
                "SELECT user_words, errors_by_category FROM session_metrics WHERE session_id = :s"
            ),
            {"s": session.id},
        ).all()
        errors = conn.execute(
            text("SELECT count(*) FROM session_errors WHERE session_id = :s"), {"s": session.id}
        ).scalar_one()
        row = conn.execute(
            text(
                "SELECT status, ended_at, task_result, hints_given, cefr_estimate_speaking,"
                " confidence_1_5 FROM sessions WHERE id = :s"
            ),
            {"s": session.id},
        ).one()
    assert [tuple(m) for m in metrics] == [(50, {"grammar": 1})]
    assert errors == 1
    assert tuple(row) == ("closed", ended, "achieved", 1, "B2", 4)


def test_session_result_round_trips_as_jsonb(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    result = {
        "status": "closed",
        "metrics": {"user_words": 42, "user_ratio": None, "errors_by_category": {"grammar": 1}},
        "summary_text": "You spoke 42 words in 12 minutes (3.5 per minute).\nStreak: 3 days.",
        "streak": 3,
        "low_trust": False,
    }
    with uow_factory(user_id) as uow:
        session = _open(uow, now)
        uow.sessions.close(
            session.id,
            status="closed",
            low_trust=False,
            ended_at=now + timedelta(minutes=12),
            evidence=EVIDENCE,
            raw_evidence={"user_turns": list(EVIDENCE.user_turns)},
            cefr_excluded=True,
            result=result,
        )
    with uow_factory(user_id) as uow:
        stored = uow.sessions.get(session.id)
    assert stored is not None
    assert stored.result == result
    assert stored.status == "closed"
    assert stored.chunks_offered == session.chunks_offered


def test_fsrs_state_round_trips_through_review_states_and_logs(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    first_due = now + timedelta(days=1)
    reviewed_at = first_due + timedelta(hours=2)
    with uow_factory(user_id) as uow:
        session = _open(uow, now)
        item_id = _add_confirmed(uow, session.id, "roll back", first_due, now)
        initial = uow.reviews.state(item_id)
        assert initial == new_state(first_due)
        assert initial is not None
        after: FsrsState = review(initial, 3, reviewed_at)
        uow.reviews.save_state(item_id, after)
        assert uow.reviews.state(item_id) == after
        assert uow.reviews.log(session.id, item_id, 3, reviewed_at, initial) is True
        assert uow.reviews.log(session.id, item_id, 4, reviewed_at, initial) is False
        assert uow.reviews.session_logs(session.id) == (
            ReviewLogRow(item_id=item_id, rating=3, reviewed_at=reviewed_at, state_before=initial),
        )
    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT state_before FROM review_logs WHERE glossary_item_id = :i"),
            {"i": item_id},
        ).scalar_one()
    assert stored == state_to_json(initial)


def test_last_ratings_keep_the_newest_two_and_follow_rating_upgrades(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        assert isinstance(uow, PgUnitOfWork)
        first = _open(uow, now)
        item_id = _add_confirmed(uow, first.id, "hotfix", now - timedelta(days=1), now)
        state = uow.reviews.state(item_id)
        assert state is not None
        sessions = [first.id]
        for minutes, rating in ((1, 2), (2, 3), (3, 3)):
            if len(sessions) < minutes:
                uow.sessions.mark_incomplete(sessions[-1], now)
                sessions.append(_open(uow, now).id)
            uow.reviews.log(sessions[-1], item_id, rating, now + timedelta(minutes=minutes), state)
        (candidate,) = uow.glossary.due_candidates(now + timedelta(hours=1))
        assert candidate.item_id == item_id
        assert candidate.last_ratings == (3, 3)
        uow.reviews.set_log_rating(sessions[-1], item_id, 4)
        (candidate,) = uow.glossary.due_candidates(now + timedelta(hours=1))
        assert candidate.last_ratings == (3, 4)
        assert uow.reviews.session_logs(sessions[-1])[0].rating == 4


class _LockingSessions:
    """Holds the row lock for a moment after `get`, so a second end_session must wait for it."""

    def __init__(self, inner: Any, locked: threading.Event) -> None:
        self._inner = inner
        self._locked = locked

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def get(self, session_id: UUID) -> SessionRow | None:
        row = cast(SessionRow | None, self._inner.get(session_id))
        self._locked.set()
        time.sleep(0.6)
        return row


def test_two_concurrent_end_sessions_close_once(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    # Review Focus 2: a second end_session waits for the first, then reports already_closed.
    with uow_factory(user_id) as uow:
        uow.profiles.upsert(
            Profile(
                self_level="B1",
                domains=("it",),
                use_cases=("standup",),
                minutes_per_day=20,
                days_per_week=3,
                target_level="B2",
                target_date=date(2027, 3, 1),
                goal_text="Lead the standup",
                timezone="America/Mexico_City",
            ),
            now,
        )
        session = _open(uow, now)
    locked = threading.Event()
    slow_first = [True]

    @contextmanager
    def slow_uow(uid: UUID) -> Iterator[UnitOfWork]:
        with uow_factory(uid) as uow:
            if slow_first[0]:
                slow_first[0] = False
                assert isinstance(uow, PgUnitOfWork)
                uow.sessions = cast(Any, _LockingSessions(uow.sessions, locked))
            yield uow

    ended = now + timedelta(minutes=12)
    first_svc = Services(uow=slow_uow, clock=lambda: ended, valid_timezones=frozenset())
    second_svc = Services(uow=uow_factory, clock=lambda: ended, valid_timezones=frozenset())
    raw = {"user_turns": list(EVIDENCE.user_turns)}
    outcomes: dict[str, EndSessionResult | Exception] = {}

    def run(name: str, svc: Services) -> None:
        try:
            outcomes[name] = end_session(svc, user_id, session.id, EVIDENCE, raw)
        except Exception as exc:  # recorded and asserted below
            outcomes[name] = exc

    first = threading.Thread(target=run, args=("first", first_svc))
    second = threading.Thread(target=run, args=("second", second_svc))
    first.start()
    assert locked.wait(timeout=5)
    second.start()
    assert _wait_for_lock_wait(engine), "the second end_session never waited on the first"
    first.join(timeout=15)
    second.join(timeout=15)
    assert not first.is_alive()
    assert not second.is_alive()
    assert not any(isinstance(o, Exception) for o in outcomes.values()), outcomes
    flags = sorted(o.already_closed for o in outcomes.values() if isinstance(o, EndSessionResult))
    assert flags == [False, True]
    with engine.connect() as conn:
        metrics = conn.execute(
            text("SELECT count(*) FROM session_metrics WHERE session_id = :s"), {"s": session.id}
        ).scalar_one()
        audits = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE event = 'session_closed' AND user_id = :u"),
            {"u": user_id},
        ).scalar_one()
        status, result = conn.execute(
            text("SELECT status, result FROM sessions WHERE id = :s"), {"s": session.id}
        ).one()
    assert (metrics, audits) == (1, 1)
    assert status == "closed"
    assert result is not None


def test_one_users_concurrent_units_of_work_on_different_sessions_do_not_deadlock(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    # The per-user advisory lock serializes units of work, so opposite lock orders never deadlock.
    with uow_factory(user_id) as uow:
        old = _open(uow, now)
        first = _add_confirmed(uow, old.id, "roll back", now, now)
        second = _add_confirmed(uow, old.id, "hotfix", now, now)
        uow.sessions.mark_incomplete(old.id, now)
        current = _open(uow, now)
    errors: list[BaseException] = []

    def review_in_order() -> None:
        try:
            with uow_factory(user_id) as uow:
                state = uow.reviews.state(first)
                assert state is not None
                uow.reviews.save_state(first, state)
                time.sleep(0.5)
                uow.reviews.log(current.id, second, 3, now, state)
                uow.reviews.save_state(second, state)
        except BaseException as exc:
            errors.append(exc)

    def reinforce_in_opposite_order() -> None:
        try:
            time.sleep(0.15)
            with uow_factory(user_id) as uow:
                for item_id in (second, first):
                    uow.glossary.apply(
                        [
                            Reinforce(
                                index=0,
                                item_id=item_id,
                                kind="chunk",
                                seen_count=2,
                                leech=False,
                                due=now,
                            )
                        ],
                        [],
                        session_id=old.id,
                        now=now,
                    )
                    time.sleep(0.4)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=review_in_order),
        threading.Thread(target=reinforce_in_opposite_order),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
