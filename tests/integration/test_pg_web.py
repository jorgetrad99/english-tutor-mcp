"""PgWebBackend on db-test through the non-superuser login role (ruling S2): every port method
v0 pages call, seeded through the core services."""

import zoneinfo
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from web_pg_support import BASE, WEB_CONFIG, csrf_token, google_login, pg_settings, pg_web_app

from tutor.app import PeriodicPurge, build_app
from tutor.db.tables import audit_log, glossary_items, users, web_sessions
from tutor.db.uow import scoped_connection
from tutor.domain.dashboard import types as dash
from tutor.domain.dashboard.billing import banners_for
from tutor.domain.dashboard.home import local_today
from tutor.domain.glossary import IncomingItem
from tutor.domain.profile import ProfileInput
from tutor.domain.text import count_words
from tutor.domain.validation import Evidence, ReportedError
from tutor.services.context import Services
from tutor.services.glossary import save_glossary
from tutor.services.lesson import start_lesson
from tutor.services.ports import IdentityResolver, UowFactory
from tutor.services.profile import get_profile, save_profile
from tutor.services.session_end import end_session
from tutor.services.views import LessonStart, StartLessonRequest
from tutor.web.deps import get_deps
from tutor.web.memory import FakeGoogle, FixedClock
from tutor.web.pg import FREE, PgWebBackend
from tutor.web.ports import UNCAPPED, GoogleIdentity, WebSession
from tutor.web.sessions import ANONYMOUS_LIFETIME, COOKIE, hash_token

pytestmark = pytest.mark.integration

TODAY = date(2026, 10, 14)  # NOW is 15:00 UTC, 09:00 in Mexico City
IDLE, ABSOLUTE = timedelta(days=14), timedelta(days=30)
TURNS = (
    "Yesterday I goed to the standup and I explained the login bug to the whole team.",
    "Then I went to the review and we talked about the payments API for a long time.",
    "Tomorrow I will keep you posted about the trade-off and the rollback plan we chose.",
)
ERROR = ReportedError(said="I goed", correct="I went", category="grammar")
PROFILE = ProfileInput(
    self_level="B1",
    domains=["it"],
    use_cases=["standup", "code_review"],
    minutes_per_day=20,
    days_per_week=3,
    target_level="B2",
)


def _evidence(
    chunks_used: Sequence[str],
    *,
    turns: Sequence[str] = TURNS,
    errors: Sequence[ReportedError] = (ERROR,),
) -> Evidence:
    return Evidence(
        user_turns=tuple(turns),
        errors=tuple(errors),
        chunks_used=tuple(chunks_used),
        task_result="achieved",
        hints_given=1,
        cefr_level="B1",
        cefr_confidence="medium",
        cefr_evidence=("Explains a blocker.",),
        confidence_1_5=4,
        assistant_words_estimate=None,
    )


def _raw(ev: Evidence) -> dict[str, Any]:
    """The JSON the MCP layer stores as sessions.raw_evidence."""
    return {
        "user_turns": list(ev.user_turns),
        "errors": [
            {"said": e.said, "correct": e.correct, "category": e.category} for e in ev.errors
        ],
        "chunks_used": list(ev.chunks_used),
        "task_result": ev.task_result,
        "hints_given": ev.hints_given,
        "cefr_estimate": {
            "speaking": ev.cefr_level,
            "confidence": ev.cefr_confidence,
            "evidence": list(ev.cefr_evidence),
        },
        "confidence_1_5": ev.confidence_1_5,
        "assistant_words_estimate": ev.assistant_words_estimate,
    }


def _item(text: str) -> IncomingItem:
    return IncomingItem(
        kind="chunk",
        text=text,
        meaning="Undo a release.",
        context_sentence=f"We had to {text} on Friday.",
        domain="it",
    )


def _events(engine: Engine, uid: UUID) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(select(audit_log.c.event).where(audit_log.c.user_id == uid))
        return sorted(rows.scalars())


def _session_rows(engine: Engine) -> dict[str, UUID | None]:
    """Every web session as the owner sees it: token hash -> user id."""
    with engine.connect() as conn:
        rows = conn.execute(select(web_sessions.c.token_hash, web_sessions.c.user_id))
        return {r.token_hash: r.user_id for r in rows}


@pytest.fixture
def clock(now: datetime) -> FixedClock:
    return FixedClock(now)


@pytest.fixture
def svc(uow_factory: UowFactory, clock: FixedClock) -> Services:
    return Services(
        uow=uow_factory, clock=clock, valid_timezones=frozenset(zoneinfo.available_timezones())
    )


@pytest.fixture
def backend(login_engine: Engine, clock: FixedClock) -> PgWebBackend:
    return PgWebBackend(login_engine, clock)


def _lesson(svc: Services, clock: FixedClock, user_id: UUID) -> LessonStart:
    """One closed 12-minute voice lesson (2 phrases used, one error taken up later) and three
    glossary saves: confirmed, provisional and declined."""
    lesson = start_lesson(svc, user_id, StartLessonRequest(mode="voice"))
    save_glossary(svc, user_id, lesson.session_id, "confirmed", [_item("roll back")])
    save_glossary(svc, user_id, lesson.session_id, "provisional", [_item("heads-up")])
    save_glossary(svc, user_id, lesson.session_id, "declined", [_item("blocker")])
    clock.advance(timedelta(minutes=12))
    ev = _evidence([c.id for c in lesson.item.chunks][:2])
    end_session(svc, user_id, lesson.session_id, ev, _raw(ev))
    return lesson


@pytest.fixture
def seeded(svc: Services, clock: FixedClock, user_id: UUID) -> LessonStart:
    """User A: onboarded, one closed lesson today."""
    save_profile(svc, user_id, PROFILE)
    return _lesson(svc, clock, user_id)


# --- UserDirectory -----------------------------------------------------------


def test_sign_in_creates_a_user_with_defaults_and_audits(
    engine: Engine, backend: PgWebBackend, now: datetime
) -> None:
    ident = GoogleIdentity("g-nora", "nora@example.com", "Nora", True)
    first = backend.sign_in(ident, now)
    again = backend.sign_in(ident, now)
    assert again.id == first.id
    assert (first.display_name, first.role, first.lang) == (
        "Nora",
        dash.Role.LEARNER,
        dash.Lang.ES_MX,
    )
    assert (first.timezone, first.reduce_motion, first.deletion_requested_at) == (
        "America/Mexico_City",
        False,
        None,
    )
    assert _events(engine, first.id) == ["user_created", "web_login", "web_login"]
    with engine.connect() as conn:
        metas = conn.execute(select(audit_log.c.meta).where(audit_log.c.user_id == first.id))
        assert "nora" not in str(list(metas.scalars())).lower()  # ids and enums only


def test_sign_in_matches_by_sub_never_by_email(backend: PgWebBackend, now: datetime) -> None:
    a = backend.sign_in(GoogleIdentity("g-1", "same@example.com", "One", True), now)
    b = backend.sign_in(GoogleIdentity("g-2", "same@example.com", "Two", True), now)
    assert a.id != b.id


def test_sign_in_finds_the_user_mcp_created_and_fills_its_name(
    engine: Engine, backend: PgWebBackend, identity: IdentityResolver, now: datetime
) -> None:
    created = identity.resolve("sub-mcp", None, None, now)
    user = backend.sign_in(GoogleIdentity("sub-mcp", "mia@example.com", "Mia", True), now)
    assert user.id == created.id
    assert user.display_name == "Mia"
    assert _events(engine, user.id) == ["web_login"]


def test_sign_in_of_a_deletion_pending_user_records_no_login(
    engine: Engine, backend: PgWebBackend, now: datetime
) -> None:
    user = backend.sign_in(GoogleIdentity("g-del", "del@example.com", "Del", True), now)
    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user.id).values(deletion_requested_at=now))
    again = backend.sign_in(GoogleIdentity("g-del", "del@example.com", "Del", True), now)
    assert again.deletion_requested_at == now
    assert _events(engine, user.id) == ["user_created", "web_login"]


def test_find_user(backend: PgWebBackend, user_id: UUID, other_user_id: UUID) -> None:
    found = backend.find_user(user_id)
    assert found is not None and found.id == user_id
    assert backend.find_user(uuid4()) is None


# --- WebSessionStore ---------------------------------------------------------


def _anon(token_hash: str, at: datetime) -> WebSession:
    return WebSession(
        token_hash=token_hash,
        user_id=None,
        csrf_token="csrf-" + token_hash[:4],
        created_at=at,
        last_seen_at=at,
        data={"_state_google_x": {"data": {"redirect_uri": f"{BASE}/auth/callback"}}},
    )


def test_web_sessions_create_load_touch_and_end(
    backend: PgWebBackend, user_id: UUID, other_user_id: UUID, now: datetime
) -> None:
    anon = _anon("a" * 64, now)
    backend.create_session(anon)
    assert backend.load_session("a" * 64) == anon
    assert backend.load_session("b" * 64) is None
    with pytest.raises(ValueError, match="exists"):
        backend.create_session(replace(anon, user_id=user_id))  # insert only, never replaces
    later = now + timedelta(minutes=6)
    assert backend.touch_session("a" * 64, later, {"k": 1})
    assert backend.load_session("a" * 64) == replace(anon, last_seen_at=later, data={"k": 1})
    mine = WebSession("d" * 64, user_id, "csrf-d", now, now, {})
    backend.create_session(mine)
    assert backend.touch_session("d" * 64, later, {"lang": "en"})  # a user's row, by its hash
    assert backend.load_session("d" * 64) == replace(mine, last_seen_at=later, data={"lang": "en"})
    backend.create_session(WebSession("c" * 64, other_user_id, "csrf-c", now, now, {}))
    backend.delete_user_sessions(user_id)
    assert backend.load_session("d" * 64) is None
    assert backend.load_session("c" * 64) is not None
    assert backend.load_session("a" * 64) is not None  # anonymous rows belong to no user
    backend.delete_session("c" * 64)
    assert backend.load_session("c" * 64) is None


def test_touch_never_revives_a_deleted_session(
    engine: Engine, backend: PgWebBackend, user_id: UUID, now: datetime
) -> None:
    backend.create_session(WebSession("e" * 64, user_id, "csrf-e", now, now, {}))
    backend.create_session(_anon("f" * 64, now))
    backend.delete_session("e" * 64)
    backend.delete_session("f" * 64)
    assert not backend.touch_session("e" * 64, now, {"x": 1})
    assert not backend.touch_session("f" * 64, now, {"x": 1})
    assert _session_rows(engine) == {}


def test_a_cookie_hash_alone_reaches_no_other_session(
    login_engine: Engine, backend: PgWebBackend, user_id: UUID, now: datetime
) -> None:
    backend.create_session(WebSession("g" * 64, user_id, "csrf-g", now, now, {}))
    backend.create_session(_anon("h" * 64, now))
    with scoped_connection(login_engine, web_session="h" * 64) as conn:
        seen = conn.execute(select(web_sessions.c.token_hash)).scalars().all()
    assert seen == ["h" * 64]


def test_purge_deletes_only_expired_sessions(
    engine: Engine, backend: PgWebBackend, user_id: UUID
) -> None:
    real_now = datetime.now(UTC)  # the database clock bounds the purge cut-off
    stale_anon = real_now - ANONYMOUS_LIFETIME - timedelta(minutes=1)
    backend.create_session(_anon("1" * 64, stale_anon))
    backend.create_session(_anon("2" * 64, real_now - timedelta(minutes=2)))
    idle = real_now - IDLE - timedelta(hours=1)
    backend.create_session(WebSession("3" * 64, user_id, "c3", idle, idle, {}))
    old = real_now - ABSOLUTE - timedelta(hours=1)
    backend.create_session(WebSession("4" * 64, user_id, "c4", old, real_now, {}))
    recent = real_now - timedelta(days=2)
    backend.create_session(WebSession("5" * 64, user_id, "c5", recent, real_now, {}))
    deleted = backend.purge_expired(
        real_now, idle=IDLE, absolute=ABSOLUTE, anonymous=ANONYMOUS_LIFETIME
    )
    assert deleted == 3
    assert set(_session_rows(engine)) == {"2" * 64, "5" * 64}


def test_a_two_day_idle_session_survives_the_purge(
    engine: Engine, backend: PgWebBackend, user_id: UUID
) -> None:
    real_now = datetime.now(UTC)
    two_days = real_now - timedelta(days=2)
    backend.create_session(WebSession("a" * 63 + "1", user_id, "ca", two_days, two_days, {}))
    assert (
        backend.purge_expired(real_now, idle=IDLE, absolute=ABSOLUTE, anonymous=ANONYMOUS_LIFETIME)
        == 0
    )
    assert set(_session_rows(engine)) == {"a" * 63 + "1"}


def test_purge_cannot_end_live_sessions(
    engine: Engine, login_engine: Engine, backend: PgWebBackend, user_id: UUID
) -> None:
    real_now = datetime.now(UTC)
    backend.create_session(WebSession("6" * 64, user_id, "c6", real_now, real_now, {}))
    backend.create_session(_anon("7" * 64, real_now))
    future = real_now + timedelta(days=400)  # clamped to the database clock
    assert (
        backend.purge_expired(future, idle=IDLE, absolute=ABSOLUTE, anonymous=ANONYMOUS_LIFETIME)
        == 0
    )
    with pytest.raises(ValueError, match="differ"):
        backend.purge_expired(
            real_now, idle=timedelta(0), absolute=ABSOLUTE, anonymous=ANONYMOUS_LIFETIME
        )
    # The caller cannot pass lifetimes any more: the old four-argument signature is gone.
    with pytest.raises(DBAPIError), scoped_connection(login_engine) as conn:
        conn.execute(
            text(
                "SELECT public.purge_expired_web_sessions(now(), interval '0',"
                " interval '30 days', interval '0')"
            )
        )
    assert set(_session_rows(engine)) == {"6" * 64, "7" * 64}


def test_create_session_maps_only_a_duplicate_token_to_session_exists(
    backend: PgWebBackend, now: datetime
) -> None:
    from sqlalchemy.exc import IntegrityError

    backend.create_session(_anon("i" * 64, now))
    with pytest.raises(ValueError, match="session exists"):
        backend.create_session(_anon("i" * 64, now))
    with pytest.raises(IntegrityError):  # a foreign key violation is not "session exists"
        backend.create_session(WebSession("j" * 64, uuid4(), "cj", now, now, {}))


# --- DashboardReader ---------------------------------------------------------


def test_home_of_a_new_learner_is_empty(backend: PgWebBackend, user_id: UUID) -> None:
    home = backend.home(user_id, TODAY)
    assert (home.has_connected, home.has_plan, home.today) == (False, False, None)
    assert (home.planned_days, home.session_marks, home.stamps) == ((), (), ())
    assert (home.streak, home.reviews_due, home.provisional_items) == (0, 0, 0)
    assert (home.last_session, home.latest_report, home.newest_closed_session_id) == (
        None,
        None,
        None,
    )
    assert home.week_start == date(2026, 10, 12)


def test_home_after_one_lesson(
    backend: PgWebBackend, seeded: LessonStart, svc: Services, user_id: UUID
) -> None:
    home = backend.home(user_id, TODAY)
    plan = get_profile(svc, user_id).plan
    assert plan is not None and plan.next_item is not None
    title = seeded.item.can_do_es
    assert (home.has_connected, home.has_plan, home.streak) == (True, True, 1)
    assert home.today is not None
    assert home.today.title == plan.next_item.can_do_es
    assert home.today.skill.value == plan.next_item.skill
    assert home.planned_days == (dash.PlannedDay(TODAY, title),)
    assert home.session_marks == (dash.SessionMark(TODAY, True, dash.SessionStatus.CLOSED),)
    chunks = seeded.item.chunks
    assert {s.text: s.used for s in home.stamps} == {c.text: i < 2 for i, c in enumerate(chunks)}
    last = home.last_session
    assert last is not None and last.id == seeded.session_id
    assert (last.label, last.mode, last.duration_min) == (title, dash.Mode.VOICE, 12)
    words = sum(count_words(t) for t in TURNS)
    assert last.words_per_min == pytest.approx(round(words / 12, 1))  # metrics keep 1 decimal
    assert (home.reviews_due, home.provisional_items) == (0, 1)
    assert home.newest_closed_session_id == seeded.session_id
    assert home.last_celebrated_session_id is None
    backend.mark_celebrated(user_id, seeded.session_id)
    assert backend.home(user_id, TODAY).last_celebrated_session_id == seeded.session_id


def test_mark_celebrated_ignores_a_session_of_another_user(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID
) -> None:
    backend.mark_celebrated(other_user_id, seeded.session_id)  # user A's session id
    with scoped_connection(backend._engine, user_id=other_user_id) as conn:
        stored = conn.execute(select(users.c.last_celebrated_session_id)).scalar_one()
    assert stored is None
    assert backend.home(user_id, TODAY).last_celebrated_session_id is None


def test_home_today_is_the_next_item_when_nothing_closed_today(
    backend: PgWebBackend, svc: Services, user_id: UUID
) -> None:
    save_profile(svc, user_id, PROFILE)
    plan = get_profile(svc, user_id).plan
    assert plan is not None and plan.next_item is not None
    home = backend.home(user_id, TODAY)
    assert home.planned_days == (dash.PlannedDay(TODAY, plan.next_item.can_do_es),)
    assert home.has_plan and not home.has_connected


def test_home_uses_the_learners_zone_for_today_week_and_streak(
    backend: PgWebBackend, svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    # Sunday 15:00 UTC is already Monday 05:00 in Kiritimati (UTC+14): a new local week.
    clock.now = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
    save_profile(svc, user_id, replace(PROFILE, timezone="Pacific/Kiritimati"))
    lesson = _lesson(svc, clock, user_id)
    user = backend.find_user(user_id)
    assert user is not None and user.timezone == "Pacific/Kiritimati"
    today = local_today(clock(), user.timezone)  # what the route passes (views.today_for)
    assert today == date(2026, 10, 19)
    home = backend.home(user_id, today)
    assert home.week_start == today
    assert home.session_marks == (dash.SessionMark(today, True, dash.SessionStatus.CLOSED),)
    assert home.planned_days == (dash.PlannedDay(today, lesson.item.can_do_es),)
    assert home.streak == 1
    assert backend.usage(user_id, today).sessions_this_week == 1


def test_an_unknown_stored_zone_falls_back_to_utc_everywhere(
    engine: Engine, backend: PgWebBackend, seeded: LessonStart, clock: FixedClock, user_id: UUID
) -> None:
    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(timezone="Mars/Base"))
    user = backend.find_user(user_id)
    assert user is not None
    today = local_today(clock(), user.timezone)
    home = backend.home(user_id, today)
    assert home.streak == 1
    assert [m.day for m in home.session_marks] == [today]


def test_usage_counts_without_caps(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID
) -> None:
    usage = backend.usage(user_id, TODAY)
    assert usage == dash.FreeUsage(1, UNCAPPED, 2, UNCAPPED, date(2026, 10, 19))
    assert banners_for(FREE, usage, TODAY) == ()


def test_sessions_filter_and_page(
    backend: PgWebBackend,
    seeded: LessonStart,
    svc: Services,
    clock: FixedClock,
    user_id: UUID,
) -> None:
    clock.advance(timedelta(minutes=1))
    short = start_lesson(svc, user_id, StartLessonRequest(mode="text"))
    clock.advance(timedelta(minutes=1))
    ev = _evidence([], turns=("Short answer only.",), errors=())
    end_session(svc, user_id, short.session_id, ev, _raw(ev))
    first = backend.sessions(user_id, dash.SessionFilter(), 1, 1)
    assert [s.id for s in first.items] == [short.session_id]
    assert (first.items[0].status, first.items[0].mode, first.has_next) == (
        dash.SessionStatus.INCOMPLETE,
        dash.Mode.TEXT,
        True,
    )
    second = backend.sessions(user_id, dash.SessionFilter(), 2, 1)
    assert ([s.id for s in second.items], second.has_next) == ([seeded.session_id], False)
    voice = backend.sessions(user_id, dash.SessionFilter(mode=dash.Mode.VOICE), 1, 20)
    assert [s.id for s in voice.items] == [seeded.session_id]
    closed = backend.sessions(user_id, dash.SessionFilter(status=dash.SessionStatus.CLOSED), 1, 20)
    assert [s.task_result for s in closed.items] == [dash.TaskResult.ACHIEVED]


def test_session_detail(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID
) -> None:
    detail = backend.session_detail(user_id, seeded.session_id)
    assert detail is not None
    assert detail.errors == (dash.ErrorView("I goed", "I went", dash.ErrorCategory.GRAMMAR, True),)
    texts = [c.text for c in seeded.item.chunks]
    assert set(detail.chunks_offered) == set(texts)
    assert set(detail.chunks_used) == set(texts[:2])
    assert detail.cefr == dash.CefrOpinion("B1", dash.Confidence.MEDIUM, ("Explains a blocker.",))
    assert (detail.hints_given, detail.confidence_1_5, detail.user_turns) == (1, 4, TURNS)
    assert backend.session_detail(other_user_id, seeded.session_id) is None
    assert backend.session_detail(user_id, uuid4()) is None


def test_glossary_hides_declined_and_uses_local_dates(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID
) -> None:
    rows = backend.glossary(user_id, dash.GlossaryFilter(), TODAY)
    assert {r.text: (r.status, r.due_on, r.expires_on) for r in rows} == {
        "roll back": (dash.GlossaryStatus.CONFIRMED, date(2026, 10, 15), None),
        "heads-up": (dash.GlossaryStatus.PROVISIONAL, None, date(2026, 10, 21)),
    }
    only = backend.glossary(
        user_id, dash.GlossaryFilter(status=dash.GlossaryStatus.PROVISIONAL), TODAY
    )
    assert [r.text for r in only] == ["heads-up"]
    assert backend.glossary_domains(user_id) == ("it",)
    assert backend.glossary(other_user_id, dash.GlossaryFilter(), TODAY) == ()
    assert backend.glossary_domains(other_user_id) == ()


def test_update_glossary_text_is_scoped_to_the_owner(
    engine: Engine,
    backend: PgWebBackend,
    seeded: LessonStart,
    user_id: UUID,
    other_user_id: UUID,
) -> None:
    target = next(
        r for r in backend.glossary(user_id, dash.GlossaryFilter(), TODAY) if r.text == "roll back"
    )
    updated = backend.update_glossary_text(
        user_id, target.id, "<img src=x onerror=alert(1)>", "We had to roll back on Friday."
    )
    assert updated is not None
    assert (updated.meaning, updated.due_on) == ("<img src=x onerror=alert(1)>", target.due_on)
    assert backend.update_glossary_text(other_user_id, target.id, "x", "y") is None
    with engine.connect() as conn:
        declined = conn.execute(
            select(glossary_items.c.id).where(glossary_items.c.text == "blocker")
        ).scalar_one()
    assert backend.update_glossary_text(user_id, declined, "x", "y") is None


def test_has_any_session(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID
) -> None:
    assert backend.has_any_session(user_id)
    assert not backend.has_any_session(other_user_id)


def test_account_preferences_and_install_prompt(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID, now: datetime
) -> None:
    account = backend.account(user_id)
    assert account.profile == dash.Profile(("it",), 20, 3, "B2", None)
    assert account.prefs == dash.Preferences(dash.Lang.ES_MX, False, True, True)
    assert (account.clients, account.subscription, account.install_prompt_dismissed) == (
        (),
        FREE,
        False,
    )
    assert backend.account(other_user_id).profile is None
    backend.set_preferences(user_id, dash.Preferences(dash.Lang.EN, True, False, False))
    backend.dismiss_install_prompt(user_id, now)
    after = backend.account(user_id)
    assert after.prefs == dash.Preferences(dash.Lang.EN, True, False, False)
    assert after.install_prompt_dismissed
    user = backend.find_user(user_id)
    assert user is not None and (user.lang, user.reduce_motion) == (dash.Lang.EN, True)


def test_postponed_methods_raise(backend: PgWebBackend, user_id: UUID, now: datetime) -> None:
    for call in (
        lambda: backend.plan(user_id),
        lambda: backend.progress(user_id),
        lambda: backend.reports(user_id),
        lambda: backend.revoke_client(user_id, "c"),
        lambda: backend.export_data(user_id),
        lambda: backend.request_deletion(user_id, now),
    ):
        with pytest.raises(NotImplementedError):
            call()


# --- HTTP on Postgres ----------------------------------------------------------


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def web(login_engine: Engine, google: FakeGoogle) -> Iterator[TestClient]:
    app = pg_web_app(login_engine, google)

    @app.get("/app/in-flight")
    def in_flight(request: Request) -> PlainTextResponse:
        """Logout from another tab lands while this request is still running."""
        get_deps(request).sessions.delete_session(hash_token(request.cookies[COOKIE]))
        request.session["changed"] = True  # forces the middleware to write the session back
        return PlainTextResponse("ok")

    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        yield client


def test_login_csrf_and_logout_through_the_session_store(
    engine: Engine, web: TestClient, google: FakeGoogle
) -> None:
    target = google_login(web, google, GoogleIdentity("g-web", "web@example.com", "Wen", True))
    assert target == "/app/"
    token = web.cookies.get(COOKIE)
    assert token
    rows = _session_rows(engine)
    assert list(rows) == [hash_token(token)]  # the anonymous OAuth session was replaced
    uid = rows[hash_token(token)]
    assert uid is not None
    dismissed = web.post(
        "/app/install/dismiss", data={"csrf_token": csrf_token(engine, web), "back": "/app/"}
    )
    assert dismissed.status_code == 303
    out = web.post("/auth/logout", data={"csrf_token": csrf_token(engine, web)})
    assert out.status_code == 303
    with engine.connect() as conn:
        dismissed_at = conn.execute(
            select(users.c.install_prompt_dismissed_at).where(users.c.id == uid)
        ).scalar_one()
        left = conn.execute(select(func.count()).select_from(web_sessions)).scalar_one()
    assert dismissed_at is not None
    assert left == 0
    assert _events(engine, uid) == ["user_created", "web_login"]


def test_a_session_deleted_in_flight_is_not_revived(
    engine: Engine, web: TestClient, google: FakeGoogle
) -> None:
    google_login(web, google, GoogleIdentity("g-fly", "fly@example.com", "Fly", True))
    assert len(_session_rows(engine)) == 1
    response = web.get("/app/in-flight")
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert _session_rows(engine) == {}


def test_a_deletion_pending_user_gets_the_deletion_page_and_no_session(
    engine: Engine, web: TestClient, google: FakeGoogle, backend: PgWebBackend, now: datetime
) -> None:
    user = backend.sign_in(GoogleIdentity("g-gone", "gone@example.com", "Gone", True), now)
    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user.id).values(deletion_requested_at=now))
    google.next_identity = GoogleIdentity("g-gone", "gone@example.com", "Gone", True)
    start = web.get("/auth/google?next=/app/")
    callback = web.get(start.headers["location"].replace(BASE, ""))
    assert callback.status_code == 200
    assert "Max-Age=0" in callback.headers["set-cookie"]
    assert _session_rows(engine) == {}
    assert _events(engine, user.id) == ["user_created", "web_login"]


def test_build_app_installs_the_scheduled_purge_and_its_job_purges_through_the_login_role(
    engine: Engine, login_engine: Engine, backend: PgWebBackend, user_id: UUID, tmp_path: Path
) -> None:
    settings = replace(pg_settings(), oauth_storage_dir=tmp_path / "oauth")
    app = build_app(settings, engine=login_engine, web_config=WEB_CONFIG)
    assert isinstance(app.mcp_app, PeriodicPurge)
    old = datetime.now(UTC) - ABSOLUTE - timedelta(days=1)
    backend.create_session(WebSession("8" * 64, user_id, "c8", old, old, {}))
    assert app.mcp_app.job() == 1  # the scheduled job, through the login role
    assert _session_rows(engine) == {}
