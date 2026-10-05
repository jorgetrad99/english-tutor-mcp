"""Postgres adapters for the dashboard ports (core loop v0 spec section 12; plan Task 23).

Every query runs inside `tutor.db.uow.scoped_connection` as tutor_app, so row-level security
applies to the web exactly as to MCP. Learner data is read and written with `app.user_id`
(which also takes the per-user advisory lock); a web session is reached by `app.web_session`
(the cookie token's SHA-256), which the session middleware knows before it knows the user.
No method uses an owner connection; the cross-user purge of expired web sessions goes
through the SECURITY DEFINER function `purge_expired_web_sessions` (migration 0005's version).

Every method blocks: callers run them in a worker thread, never on the event loop.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.errors import UniqueViolation
from sqlalchemy import (
    Connection,
    Engine,
    Select,
    delete,
    exists,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from tutor.db.repos.people import PgAuditRepo
from tutor.db.tables import (
    glossary_items,
    profiles,
    review_states,
    session_errors,
    session_metrics,
    sessions,
    track_chunks,
    track_items,
    users,
    web_sessions,
)
from tutor.db.uow import PgIdentity, PgUnitOfWork, scoped_connection
from tutor.domain.dashboard import types as dash
from tutor.domain.dashboard.glossary import filter_glossary
from tutor.domain.dashboard.home import local_today, week_start
from tutor.domain.text import find_turn
from tutor.services.context import current_streak
from tutor.services.ports import PlanItemRow
from tutor.settings import Settings
from tutor.web.config import WebConfig
from tutor.web.google import GoogleOidcLogin
from tutor.web.ports import UNCAPPED, Clock, GoogleIdentity, WebDeps, WebSession

FREE = dash.Subscription(dash.Tier.FREE, dash.SubStatus.NONE, price_id="", price_cents=0)

# Lifetimes baked into purge_expired_web_sessions (migration 0005); WebConfig must match.
PURGE_IDLE = timedelta(days=14)
PURGE_ABSOLUTE = timedelta(days=30)
PURGE_ANONYMOUS = timedelta(minutes=10)
_SESSION_PK = "web_sessions_pkey"  # the token_hash primary key (a constraint name)
_PURGE_TIMEOUT = text("SET LOCAL statement_timeout = '5s'")
_PURGE = text("SELECT public.purge_expired_web_sessions(:now)")

_SESSION_COLUMNS = (
    sessions.c.id,
    sessions.c.plan_item_id,
    sessions.c.track_item_id,
    sessions.c.prep_text,
    sessions.c.mode,
    sessions.c.client,
    sessions.c.started_at,
    sessions.c.ended_at,
    sessions.c.status,
    sessions.c.low_trust,
    sessions.c.task_result,
    sessions.c.hints_given,
    sessions.c.cefr_estimate_speaking,
    sessions.c.cefr_confidence,
    sessions.c.cefr_excluded,
    sessions.c.confidence_1_5,
    sessions.c.chunks_offered,
    sessions.c.raw_evidence,
    session_metrics.c.duration_min,
    session_metrics.c.user_words_per_min,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _zone(name: str) -> ZoneInfo:
    """The learner's zone; an unknown name is UTC, as in domain.dashboard.home.local_today."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    """UTC instant of 00:00 on `day` in the learner's zone (DST-aware)."""
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


def _user(m: RowMapping) -> dash.User:
    return dash.User(
        id=m["id"],
        display_name=m["display_name"],
        role=dash.Role(m["role"]),
        lang=dash.Lang(m["lang"]),
        timezone=m["timezone"],
        reduce_motion=m["reduce_motion"],
        deletion_requested_at=m["deletion_requested_at"],
    )


def _me(conn: Connection, user_id: UUID) -> RowMapping:
    return conn.execute(select(users).where(users.c.id == user_id)).mappings().one()


def _track(conn: Connection) -> dict[str, RowMapping]:
    return {r["id"]: r for r in conn.execute(select(track_items)).mappings().all()}


def _title(item: RowMapping | None, track_item_id: str, lang: str) -> str:
    """The can-do statement in the learner's UI language (seed data, not learner text)."""
    if item is None:
        return track_item_id
    return str(item["can_do_en"] if lang == dash.Lang.EN.value else item["can_do_es"])


def _chunk_texts(conn: Connection, ids: Sequence[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = conn.execute(
        select(track_chunks.c.id, track_chunks.c.text).where(track_chunks.c.id.in_(list(ids)))
    ).all()
    return {str(r.id): str(r.text) for r in rows}


def _valid_used(raw: Mapping[str, Any] | None, offered: Sequence[str]) -> frozenset[str]:
    """Spec 11.1 step 4: a reported chunk id counts only if the session offered it."""
    reported = (raw or {}).get("chunks_used") or ()
    return frozenset(c for c in reported if isinstance(c, str)) & frozenset(offered)


def _client(value: str) -> dash.Client:
    # The dashboard has no "unknown" client; v0 sessions come from Claude connectors.
    known = {c.value for c in dash.Client}
    return dash.Client(value) if value in known else dash.Client.CLAUDE


def _session_query(user_id: UUID) -> Select[Any]:
    return (
        select(*_SESSION_COLUMNS)
        .select_from(
            sessions.outerjoin(session_metrics, session_metrics.c.session_id == sessions.c.id)
        )
        .where(sessions.c.user_id == user_id)
    )


def _summary(m: RowMapping, track: Mapping[str, RowMapping], lang: str) -> dash.SessionSummary:
    duration = m["duration_min"]
    return dash.SessionSummary(
        id=m["id"],
        started_at=m["started_at"],
        mode=dash.Mode(m["mode"]),
        client=_client(m["client"]),
        label=m["prep_text"] or _title(track.get(m["track_item_id"]), m["track_item_id"], lang),
        duration_min=round(duration) if duration is not None else 0,
        words_per_min=m["user_words_per_min"],
        task_result=dash.TaskResult(m["task_result"]) if m["task_result"] else None,
        status=dash.SessionStatus(m["status"]),
        low_trust=m["low_trust"],
    )


def _glossary_query(user_id: UUID) -> Select[Any]:
    """Every glossary row of the user except declined ones (plan ruling 10)."""
    return (
        select(
            glossary_items.c.id,
            glossary_items.c.kind,
            glossary_items.c.text,
            glossary_items.c.meaning,
            glossary_items.c.context_sentence,
            glossary_items.c.domain,
            glossary_items.c.status,
            glossary_items.c.leech,
            glossary_items.c.provisional_expires_at,
            review_states.c.due_at,
        )
        .select_from(
            glossary_items.outerjoin(
                review_states, review_states.c.glossary_item_id == glossary_items.c.id
            )
        )
        .where(glossary_items.c.user_id == user_id, glossary_items.c.status != "declined")
    )


def _glossary_row(m: RowMapping, tz_name: str) -> dash.GlossaryRow:
    status = dash.GlossaryStatus(m["status"])
    provisional = status is dash.GlossaryStatus.PROVISIONAL
    due = m["due_at"]
    expires = m["provisional_expires_at"]
    return dash.GlossaryRow(
        id=m["id"],
        kind=dash.GlossaryKind(m["kind"]),
        text=m["text"],
        meaning=m["meaning"],
        context_sentence=m["context_sentence"],
        domain=m["domain"],
        status=status,
        due_on=None if provisional or due is None else local_today(due, tz_name),
        leech=m["leech"],
        expires_on=local_today(expires, tz_name) if provisional and expires is not None else None,
    )


def _planned_days(
    closed: Sequence[RowMapping],
    track: Mapping[str, RowMapping],
    lang: str,
    tz_name: str,
    today: date,
    next_item: PlanItemRow | None,
) -> tuple[dash.PlannedDay, ...]:
    """Ruling (Task 23): only days with a closed on-plan session, plus today when an item is
    pending. Plan-lite has no calendar, so no other date is invented."""
    days: dict[date, str] = {}
    for m in closed:
        if m["plan_item_id"] is not None:
            day = local_today(m["started_at"], tz_name)
            days.setdefault(day, _title(track.get(m["track_item_id"]), m["track_item_id"], lang))
    if next_item is not None:
        item_id = next_item.track_item_id
        days.setdefault(today, _title(track.get(item_id), item_id, lang))
    return tuple(dash.PlannedDay(day, title) for day, title in sorted(days.items()))


def _web_session(m: RowMapping) -> WebSession:
    return WebSession(
        token_hash=m["token_hash"],
        user_id=m["user_id"],
        csrf_token=m["csrf_token"],
        created_at=m["created_at"],
        last_seen_at=m["last_seen_at"],
        data=dict(m["data"]),
    )


class PgWebBackend:
    """UserDirectory, WebSessionStore, DashboardReader, GlossaryEditor and AccountService."""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
        self._identity = PgIdentity(engine)

    # --- UserDirectory -----------------------------------------------------

    def sign_in(self, identity: GoogleIdentity, now: datetime) -> dash.User:
        """Find or create by Google sub through PgIdentity (app.google_sub policy branch; never
        by email); audit user_created and web_login with ids only. A user who asked for
        deletion gets no web_login (the callback shows the deletion page)."""
        resolved = self._identity.resolve(identity.sub, identity.email, identity.name or None, now)
        with scoped_connection(self._engine, user_id=resolved.id) as conn:
            row = _me(conn, resolved.id)
            fill: dict[str, Any] = {}
            if not row["display_name"]:
                fill["display_name"] = (identity.name or identity.email.split("@", 1)[0])[:200]
            if row["email"] is None:
                fill["email"] = identity.email
            if fill:
                conn.execute(update(users).where(users.c.id == resolved.id).values(**fill))
                row = _me(conn, resolved.id)
            audit = PgAuditRepo(conn, resolved.id)
            if resolved.created:
                audit.record("user_created", {"via": "web"}, now)
            if row["deletion_requested_at"] is None:
                audit.record("web_login", {}, now)
        return _user(row)

    def find_user(self, user_id: UUID) -> dash.User | None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            row = conn.execute(select(users).where(users.c.id == user_id)).mappings().one_or_none()
        return None if row is None else _user(row)

    # --- WebSessionStore ---------------------------------------------------

    def load_session(self, token_hash: str) -> WebSession | None:
        with scoped_connection(self._engine, web_session=token_hash) as conn:
            m = (
                conn.execute(select(web_sessions).where(web_sessions.c.token_hash == token_hash))
                .mappings()
                .one_or_none()
            )
        return None if m is None else _web_session(m)

    def create_session(self, session: WebSession) -> None:
        """Insert only. A pre-login row has user_id NULL and is reachable by its hash alone;
        a user's row is inserted with that user's scope (policy web_sessions_holder, 0002)."""
        try:
            with scoped_connection(
                self._engine, user_id=session.user_id, web_session=session.token_hash
            ) as conn:
                conn.execute(
                    insert(web_sessions).values(
                        token_hash=session.token_hash,
                        user_id=session.user_id,
                        csrf_token=session.csrf_token,
                        data=dict(session.data),
                        created_at=session.created_at,
                        last_seen_at=session.last_seen_at,
                    )
                )
        except IntegrityError as exc:
            orig = exc.orig
            if isinstance(orig, UniqueViolation) and orig.diag.constraint_name == _SESSION_PK:
                raise ValueError("session exists") from None  # no driver text past this point
            raise

    def touch_session(self, token_hash: str, last_seen_at: datetime, data: dict[str, Any]) -> bool:
        """`UPDATE ... WHERE token_hash` only, never an upsert: a row deleted meanwhile (logout
        in another tab, purge) stays deleted and the caller drops the cookie."""
        with scoped_connection(self._engine, web_session=token_hash) as conn:
            holder = conn.execute(
                select(web_sessions.c.user_id).where(web_sessions.c.token_hash == token_hash)
            ).one_or_none()
        if holder is None:
            return False
        owner: UUID | None = holder.user_id
        # A user's row passes the policy's WITH CHECK only in that user's scope.
        with scoped_connection(self._engine, user_id=owner, web_session=token_hash) as conn:
            result = conn.execute(
                update(web_sessions)
                .where(
                    web_sessions.c.token_hash == token_hash,
                    web_sessions.c.user_id.is_not_distinct_from(owner),
                )
                .values(last_seen_at=last_seen_at, data=dict(data))
            )
            return result.rowcount == 1

    def delete_session(self, token_hash: str) -> None:
        with scoped_connection(self._engine, web_session=token_hash) as conn:
            conn.execute(delete(web_sessions).where(web_sessions.c.token_hash == token_hash))

    def delete_user_sessions(self, user_id: UUID) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(delete(web_sessions).where(web_sessions.c.user_id == user_id))

    def purge_expired(
        self, now: datetime, *, idle: timedelta, absolute: timedelta, anonymous: timedelta
    ) -> int:
        """Delete every expired web session; returns how many. RLS hides other people's rows
        from tutor_app, so this calls migration 0005's function. It takes only the clock and
        applies its own lifetimes, so the arguments here must equal them."""
        if (idle, absolute, anonymous) != (PURGE_IDLE, PURGE_ABSOLUTE, PURGE_ANONYMOUS):
            raise ValueError("web session lifetimes differ from the database function")
        with scoped_connection(self._engine) as conn:
            conn.execute(_PURGE_TIMEOUT)  # transaction-local: a stuck purge cannot hold a worker
            return int(conn.execute(_PURGE, {"now": now}).scalar_one())

    # --- DashboardReader ---------------------------------------------------

    def home(self, user_id: UUID, today: date) -> dash.HomeData:
        """`today` is the learner's local date from the same users.timezone (views.today_for);
        this transaction reads that zone once and uses it for the week, marks and streak."""
        now = self._clock()
        start = week_start(today)
        with scoped_connection(self._engine, user_id=user_id) as conn:
            me = _me(conn, user_id)
            tz, lang = str(me["timezone"]), str(me["lang"])
            zone = _zone(tz)
            track = _track(conn)
            uow = PgUnitOfWork(conn, user_id)
            plan = uow.plans.active()
            pending = sorted(
                (i for i in (plan.items if plan is not None else ()) if i.status == "pending"),
                key=lambda i: (i.week_no, i.order_no),
            )
            next_item = pending[0] if pending else None
            week = (
                conn.execute(
                    _session_query(user_id)
                    .where(
                        sessions.c.started_at >= _midnight(start, zone),
                        sessions.c.started_at < _midnight(start + timedelta(days=7), zone),
                    )
                    .order_by(sessions.c.started_at)
                )
                .mappings()
                .all()
            )
            latest = (
                conn.execute(
                    _session_query(user_id).order_by(sessions.c.started_at.desc()).limit(1)
                )
                .mappings()
                .one_or_none()
            )
            newest_closed = conn.execute(
                select(sessions.c.id)
                .where(sessions.c.user_id == user_id, sessions.c.status == "closed")
                .order_by(sessions.c.ended_at.desc().nulls_last())
                .limit(1)
            ).scalar_one_or_none()
            streak = current_streak(uow, now, zone)
            reviews_due = uow.glossary.count_due(now)
            provisional = uow.glossary.count_provisional()
            closed = [m for m in week if m["status"] == "closed"]
            offered: list[str] = []
            used: set[str] = set()
            for m in closed:
                offered += [c for c in m["chunks_offered"] if c not in offered]
                used |= _valid_used(m["raw_evidence"], m["chunks_offered"])
            texts = _chunk_texts(conn, offered)
        today_item = None
        item = track.get(next_item.track_item_id) if next_item is not None else None
        if next_item is not None and item is not None:
            today_item = dash.TodayItem(
                title=_title(item, next_item.track_item_id, lang),
                scenario_hint=item["scenario_hint"],
                skill=dash.Skill(item["skill"]),
                domain=item["domain"],
            )
        return dash.HomeData(
            has_connected=latest is not None or me["mcp_first_seen_at"] is not None,
            has_plan=plan is not None,
            today=today_item,
            week_start=start,
            planned_days=_planned_days(closed, track, lang, tz, today, next_item),
            session_marks=tuple(
                dash.SessionMark(
                    local_today(m["started_at"], tz),
                    m["plan_item_id"] is not None,
                    dash.SessionStatus(m["status"]),
                )
                for m in week
            ),
            streak=streak,
            stamps=tuple(dash.Stamp(texts.get(c, c), c in used) for c in offered),
            last_session=_summary(latest, track, lang) if latest is not None else None,
            reviews_due=reviews_due,
            provisional_items=provisional,
            latest_report=None,
            newest_closed_session_id=newest_closed,
            last_celebrated_session_id=me["last_celebrated_session_id"],
        )

    def usage(self, user_id: UUID, today: date) -> dash.FreeUsage:
        """Real counts, no caps (ruling: Free counters, Task 23)."""
        start = week_start(today)
        with scoped_connection(self._engine, user_id=user_id) as conn:
            zone = _zone(str(_me(conn, user_id)["timezone"]))
            started = conn.execute(
                select(func.count())
                .select_from(sessions)
                .where(
                    sessions.c.user_id == user_id,
                    sessions.c.started_at >= _midnight(start, zone),
                )
            ).scalar_one()
            items = conn.execute(
                select(func.count())
                .select_from(glossary_items)
                .where(glossary_items.c.user_id == user_id, glossary_items.c.status != "declined")
            ).scalar_one()
        return dash.FreeUsage(int(started), UNCAPPED, int(items), UNCAPPED, start + timedelta(7))

    def plan(self, user_id: UUID) -> dash.PlanPage | None:
        raise NotImplementedError("v0 has no Plan page (plan ruling 11); Perfil shows plan-lite")

    def progress(self, user_id: UUID) -> dash.ProgressData:
        raise NotImplementedError("v0 has no Progreso page (spec 3.2)")

    def sessions(
        self, user_id: UUID, f: dash.SessionFilter, page: int, per_page: int
    ) -> dash.SessionPage:
        query = _session_query(user_id)
        if f.mode is not None:
            query = query.where(sessions.c.mode == f.mode.value)
        if f.status is not None:
            query = query.where(sessions.c.status == f.status.value)
        query = (
            query.order_by(sessions.c.started_at.desc(), sessions.c.id)
            .offset((page - 1) * per_page)
            .limit(per_page + 1)
        )
        with scoped_connection(self._engine, user_id=user_id) as conn:
            lang = str(_me(conn, user_id)["lang"])
            track = _track(conn)
            rows = conn.execute(query).mappings().all()
        items = tuple(_summary(m, track, lang) for m in rows[:per_page])
        return dash.SessionPage(items, page, len(rows) > per_page)

    def session_detail(self, user_id: UUID, session_id: UUID) -> dash.SessionDetail | None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            m = (
                conn.execute(_session_query(user_id).where(sessions.c.id == session_id))
                .mappings()
                .one_or_none()
            )
            if m is None:
                return None
            lang = str(_me(conn, user_id)["lang"])
            track = _track(conn)
            errors = conn.execute(
                select(
                    session_errors.c.said,
                    session_errors.c.correct,
                    session_errors.c.category,
                    session_errors.c.turn_index,
                )
                .where(
                    session_errors.c.session_id == session_id,
                    session_errors.c.user_id == user_id,
                )
                .order_by(session_errors.c.turn_index, session_errors.c.said)
            ).all()
            offered = [str(c) for c in m["chunks_offered"]]
            texts = _chunk_texts(conn, offered)
        raw: Mapping[str, Any] = m["raw_evidence"] or {}
        turns = tuple(t for t in (raw.get("user_turns") or ()) if isinstance(t, str))
        used = _valid_used(raw, offered)
        cefr = None
        shown = m["status"] == "closed" and not m["cefr_excluded"]  # never an excluded estimate
        if shown and m["cefr_estimate_speaking"] is not None:
            estimate = raw.get("cefr_estimate") or {}
            evidence = tuple(str(e) for e in (estimate.get("evidence") or ()))
            confidence = dash.Confidence(m["cefr_confidence"] or "low")
            cefr = dash.CefrOpinion(m["cefr_estimate_speaking"], confidence, evidence)
        return dash.SessionDetail(
            summary=_summary(m, track, lang),
            errors=tuple(
                dash.ErrorView(
                    said=e.said,
                    correct=e.correct,
                    category=dash.ErrorCategory(e.category),
                    taken_up=find_turn(e.correct, turns, after=e.turn_index) is not None,
                )
                for e in errors
            ),
            hints_given=m["hints_given"] or 0,
            chunks_offered=tuple(texts.get(c, c) for c in offered),
            chunks_used=tuple(texts.get(c, c) for c in offered if c in used),
            cefr=cefr,
            confidence_1_5=m["confidence_1_5"],
            user_turns=turns,
        )

    def glossary(
        self, user_id: UUID, f: dash.GlossaryFilter, today: date
    ) -> tuple[dash.GlossaryRow, ...]:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            tz = str(_me(conn, user_id)["timezone"])
            rows = conn.execute(_glossary_query(user_id)).mappings().all()
        return filter_glossary([_glossary_row(m, tz) for m in rows], f, today)

    def glossary_domains(self, user_id: UUID) -> tuple[str, ...]:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            domains = conn.execute(
                select(glossary_items.c.domain)
                .where(glossary_items.c.user_id == user_id, glossary_items.c.status != "declined")
                .distinct()
                .order_by(glossary_items.c.domain)
            ).scalars()
            return tuple(str(d) for d in domains)

    def reports(self, user_id: UUID) -> dash.ReportsPage:
        raise NotImplementedError("v0 has no Reportes page (spec 3.2)")

    def account(self, user_id: UUID) -> dash.AccountData:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            me = _me(conn, user_id)
            p = (
                conn.execute(select(profiles).where(profiles.c.user_id == user_id))
                .mappings()
                .one_or_none()
            )
        user = _user(me)
        profile = None
        if p is not None:
            profile = dash.Profile(
                domains=tuple(p["domains"]),
                minutes_per_day=p["minutes_per_day"],
                days_per_week=p["days_per_week"],
                target_level=p["target_level"],
                target_date=p["target_date"],
            )
        return dash.AccountData(
            user=user,
            profile=profile,
            prefs=dash.Preferences(
                user.lang, user.reduce_motion, me["email_weekly"], me["email_reminders"]
            ),
            clients=(),
            subscription=FREE,
            install_prompt_dismissed=me["install_prompt_dismissed_at"] is not None,
        )

    def has_connected(self, user_id: UUID) -> bool:
        """A tool call set users.mcp_first_seen_at, or the learner has any session."""
        with scoped_connection(self._engine, user_id=user_id) as conn:
            if _me(conn, user_id)["mcp_first_seen_at"] is not None:
                return True
            found = conn.execute(
                select(sessions.c.id).where(sessions.c.user_id == user_id).limit(1)
            ).scalar_one_or_none()
        return found is not None

    # --- GlossaryEditor ----------------------------------------------------

    def update_glossary_text(
        self, user_id: UUID, item_id: UUID, meaning: str, context_sentence: str
    ) -> dash.GlossaryRow | None:
        now = self._clock()
        with scoped_connection(self._engine, user_id=user_id) as conn:
            changed = conn.execute(
                update(glossary_items)
                .where(
                    glossary_items.c.id == item_id,
                    glossary_items.c.user_id == user_id,
                    glossary_items.c.status != "declined",
                )
                .values(meaning=meaning, context_sentence=context_sentence, updated_at=now)
                .returning(glossary_items.c.id)
            ).scalar_one_or_none()
            if changed is None:
                return None
            tz = str(_me(conn, user_id)["timezone"])
            row = (
                conn.execute(_glossary_query(user_id).where(glossary_items.c.id == item_id))
                .mappings()
                .one()
            )
        return _glossary_row(row, tz)

    # --- AccountService ----------------------------------------------------

    def set_preferences(self, user_id: UUID, prefs: dash.Preferences) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(
                    lang=prefs.lang.value,
                    reduce_motion=prefs.reduce_motion,
                    email_weekly=prefs.email_weekly,
                    email_reminders=prefs.email_reminders,
                )
            )

    def revoke_client(self, user_id: UUID, client_id: str) -> bool:
        raise NotImplementedError("v0 lists no MCP clients (plan ruling 3)")

    def export_data(self, user_id: UUID) -> dict[str, Any]:
        raise NotImplementedError("v0: export is by request to the author (spec 3.2)")

    def request_deletion(self, user_id: UUID, now: datetime) -> None:
        raise NotImplementedError("v0: deletion is by request to the author (spec 13)")

    def dismiss_install_prompt(self, user_id: UUID, now: datetime) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(
                update(users)
                .where(users.c.id == user_id, users.c.install_prompt_dismissed_at.is_(None))
                .values(install_prompt_dismissed_at=now)
            )

    def mark_celebrated(self, user_id: UUID, session_id: UUID) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(
                update(users)
                .where(
                    users.c.id == user_id,
                    exists().where(sessions.c.id == session_id, sessions.c.user_id == user_id),
                )
                .values(last_celebrated_session_id=session_id)
            )


class V0Subscriptions:
    """SubscriptionStore for v0: everyone is Free and nothing is billed (spec 12). Billing
    writes (link_customer, apply_event) raise: no v0 route reaches them (plan ruling 11)."""

    def subscription(self, user_id: UUID) -> dash.Subscription:
        return FREE

    def customer_id(self, user_id: UUID) -> str | None:
        return None

    def user_for_customer(self, customer_id: str) -> UUID | None:
        return None

    def link_customer(self, user_id: UUID, customer_id: str) -> None:
        raise NotImplementedError("v0 has no billing (spec 3.2)")

    def apply_event(self, user_id: UUID, event: dash.BillingEvent) -> bool:
        raise NotImplementedError("v0 has no billing (spec 3.2)")


class BillingDisabled(RuntimeError):
    """Raised by every DisabledBilling method; no v0 route reaches billing."""


class DisabledBilling:
    """BillingGateway for v0: raises if anything calls it (spec 12)."""

    def checkout_url(
        self,
        *,
        user_id: UUID,
        customer_id: str | None,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        raise BillingDisabled("checkout")

    def portal_url(self, *, customer_id: str, return_url: str) -> str:
        raise BillingDisabled("portal")

    def parse_event(self, payload: bytes, signature: str) -> dash.BillingEvent | None:
        raise BillingDisabled("webhook")


class V0Settings:
    """SettingsStore for v0: no admin settings exist (no /admin, plan ruling 11)."""

    def list_settings(self) -> tuple[dash.Setting, ...]:
        return ()

    def update_setting(
        self, key: str, value: str, actor_id: UUID, now: datetime
    ) -> dash.Setting | None:
        return None


def pg_web_deps(engine: Engine, settings: Settings, config: WebConfig) -> WebDeps:
    """Production wiring of the dashboard ports for v0."""
    if config.test_login:
        raise ValueError("test login needs the in-memory backend; never wire it to Postgres")
    backend = PgWebBackend(engine, _utc_now)
    return WebDeps(
        users=backend,
        sessions=backend,
        reader=backend,
        glossary=backend,
        account=backend,
        settings=V0Settings(),
        subscriptions=V0Subscriptions(),
        billing=DisabledBilling(),
        google=GoogleOidcLogin(settings.google_client_id, settings.google_client_secret),
        clock=_utc_now,
    )
