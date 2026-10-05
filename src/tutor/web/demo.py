"""Demo data for tests, screenshots and `just dashboard-demo`. Fictional people only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

from tutor.domain.dashboard.home import week_start
from tutor.domain.dashboard.types import (
    CefrOpinion,
    Checkpoint,
    Client,
    Confidence,
    ConnectedClient,
    ErrorCategory,
    ErrorView,
    GlossaryKind,
    GlossaryRow,
    GlossaryStatus,
    HomeData,
    ItemStatus,
    Lang,
    Mode,
    PlanItemView,
    PlannedDay,
    PlanPage,
    PlanVersion,
    Profile,
    ProgressData,
    ReportNumbers,
    ReportsPage,
    Role,
    Series,
    SessionDetail,
    SessionMark,
    SessionStatus,
    SessionSummary,
    Setting,
    SettingType,
    Skill,
    Stamp,
    Subscription,
    SubStatus,
    TaskResult,
    Tier,
    TodayItem,
    User,
    WeeklyReport,
)
from tutor.web.memory import MemoryBackend

TZ = "America/Mexico_City"


@dataclass(frozen=True)
class DemoUsers:
    ana: UUID
    beto: UUID
    nuevo: UUID
    admin: UUID


def _at(day: date, hour: int = 15) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


def _session(day: date, label: str, mode: Mode, wpm: float, status: SessionStatus) -> SessionDetail:
    summary = SessionSummary(
        id=uuid4(),
        started_at=_at(day),
        mode=mode,
        client=Client.CLAUDE,
        label=label,
        duration_min=16,
        words_per_min=wpm,
        task_result=TaskResult.ACHIEVED if status is SessionStatus.CLOSED else None,
        status=status,
        low_trust=False,
    )
    return SessionDetail(
        summary=summary,
        errors=(
            ErrorView(
                "I have went to the meeting", "I went to the meeting", ErrorCategory.GRAMMAR, True
            ),
            ErrorView("we must to deploy", "we must deploy", ErrorCategory.GRAMMAR, False),
        ),
        hints_given=1,
        chunks_offered=("on track", "a quick heads-up", "trade-off"),
        chunks_used=("on track",),
        cefr=CefrOpinion("B1+", Confidence.MEDIUM, ("I went to the meeting", "we are on track")),
        confidence_1_5=3,
        user_turns=(
            "Yesterday I have went to the meeting with the client.",
            "We are on track for the release, but we must to deploy on Friday.",
        ),
    )


def _plan(today: date) -> PlanPage:
    def item(week: int, order: int, key: str, title: str, status: ItemStatus) -> PlanItemView:
        return PlanItemView(week, order, key, title, Skill.SPEAKING, "it", "Daily standup", status)

    v1 = (
        item(
            1, 1, "s1:it:speaking", "Dar una actualización de estado en el standup", ItemStatus.DONE
        ),
        item(1, 2, "s2:it:speaking", "Explicar un bloqueo técnico", ItemStatus.PENDING),
        item(
            2,
            1,
            "s3:it:speaking",
            "Pedir aclaraciones en una revisión de código",
            ItemStatus.PENDING,
        ),
    )
    v2 = (
        v1[0],
        item(
            1,
            2,
            "s3:it:speaking",
            "Pedir aclaraciones en una revisión de código",
            ItemStatus.PENDING,
        ),
        item(2, 1, "s2:it:speaking", "Explicar un bloqueo técnico", ItemStatus.PENDING),
        item(2, 2, "s4:it:speaking", "Presentar una demo al cliente", ItemStatus.PENDING),
    )
    return PlanPage(
        current_week_no=1,
        rationale="Tu diagnóstico mostró que te cuesta hablar de bloqueos; empezamos por ahí.",
        realism_message=None,
        versions=(
            PlanVersion(2, _at(today - timedelta(days=2)), v2),
            PlanVersion(1, _at(today - timedelta(days=9)), v1),
        ),
    )


def _progress(today: date) -> ProgressData:
    days = tuple(today - timedelta(days=3 * i) for i in range(9, -1, -1))
    weeks = tuple(week_start(today) - timedelta(weeks=i) for i in range(3, -1, -1))
    return ProgressData(
        sessions_counted=10,
        wpm_voice=Series(days, (18, 19, None, 21, 22, 22, 24, 23, 25, 27), 25),
        wpm_text=Series(days, (9, 10, 10, 11, 11, None, 12, 12, 13, 13), 12),
        errors_per_100w=Series(days, (6.2, 5.8, 5.9, 5.1, 4.8, 4.4, 4.1, 3.9, 3.4, 3.1)),
        activation_rate=Series(days, (0.2, 0.25, 0.3, 0.3, 0.33, 0.35, 0.38, 0.4, 0.41, 0.42), 0.4),
        minutes_spoken=Series(weeks, (32, 41, 45, 30)),
        sessions_done=Series(weeks, (3, 4, 4, 2)),
        sessions_planned=Series(weeks, (4, 4, 4, 4)),
        cefr_trend=Series(days, (3.3, 3.4, 3.4, 3.5, 3.5, 3.5, 3.6, 3.6, 3.6, 3.7)),
        cefr_excluded=(days[4],),
        confidence_trend=Series(days, (2, 2, 3, 3, 3, 3, 3, 4, 3, 4)),
        recurring_stopped=("I went to the meeting", "we must deploy"),
        parked_leeches=("since three years",),
        checkpoint=Checkpoint(today - timedelta(days=20), "B1+", "B1+"),
    )


def _glossary(today: date) -> list[GlossaryRow]:
    def row(text: str, kind: GlossaryKind, meaning: str, ctx: str, **kw: object) -> GlossaryRow:
        base: dict[str, object] = {
            "id": uuid4(),
            "kind": kind,
            "text": text,
            "meaning": meaning,
            "context_sentence": ctx,
            "domain": "it",
            "status": GlossaryStatus.CONFIRMED,
            "due_on": None,
            "leech": False,
            "expires_on": None,
        }
        base.update(kw)
        return GlossaryRow(**base)  # type: ignore[arg-type]

    return [
        row(
            "on track", GlossaryKind.CHUNK, "en tiempo", "We are on track for Friday.", due_on=today
        ),
        row(
            "a quick heads-up",
            GlossaryKind.CHUNK,
            "un aviso rápido",
            "Just a quick heads-up: the build is red.",
            due_on=today,
        ),
        row(
            "trade-off",
            GlossaryKind.TERM,
            "compromiso",
            "The trade-off is speed versus safety.",
            due_on=today + timedelta(days=3),
        ),
        row(
            "I went to the meeting",
            GlossaryKind.CORRECTION,
            "",
            "Yesterday I went to the meeting.",
            due_on=today + timedelta(days=1),
            leech=True,
        ),
        row(
            "follow up",
            GlossaryKind.CHUNK,
            "dar seguimiento",
            "I'll follow up with QA.",
            domain="business",
        ),
        row(
            "blocker",
            GlossaryKind.TERM,
            "impedimento",
            "My blocker is the staging DB.",
            status=GlossaryStatus.PROVISIONAL,
            expires_on=today + timedelta(days=5),
        ),
        row(
            "since three years",
            GlossaryKind.CORRECTION,
            "",
            "I have worked here for three years.",
            status=GlossaryStatus.ARCHIVED,
        ),
    ]


def _settings(now: datetime) -> dict[str, Setting]:
    rows = (
        ("free_sessions_per_week", "3", SettingType.INT, "Sesiones por semana en Free"),
        ("free_glossary_items", "50", SettingType.INT, "Elementos de glosario en Free"),
        ("review_cap_per_session", "8", SettingType.INT, "Repasos por sesión"),
        ("fsrs_retention_target", "0.85", SettingType.FLOAT, "Retención objetivo FSRS"),
        ("weekly_report_enabled", "true", SettingType.BOOL, "Enviar reporte semanal"),
    )
    return {k: Setting(k, v, t, d, now) for k, v, t, d in rows}


def seed_demo(backend: MemoryBackend, today: date) -> DemoUsers:
    monday = week_start(today)
    now = _at(today, 12)

    ana = User(uuid4(), "Ana", Role.LEARNER, Lang.ES_MX, TZ, False)
    beto = User(uuid4(), "Beto", Role.LEARNER, Lang.ES_MX, TZ, False)
    nuevo = User(uuid4(), "Nuevo", Role.LEARNER, Lang.ES_MX, TZ, False)
    admin = User(uuid4(), "Admin", Role.ADMIN, Lang.ES_MX, TZ, False)
    for user in (ana, beto, nuevo, admin):
        backend.add_user(user, google_sub=f"demo-{user.display_name.lower()}")

    for user in (ana, beto):
        sessions = [
            _session(
                monday,
                "Dar una actualización de estado en el standup",
                Mode.VOICE,
                27,
                SessionStatus.CLOSED,
            ),
            _session(
                monday - timedelta(days=3),
                "Explicar un bloqueo técnico",
                Mode.TEXT,
                13,
                SessionStatus.CLOSED,
            ),
            _session(
                monday - timedelta(days=5),
                "prepare me for tomorrow's demo",
                Mode.VOICE,
                19,
                SessionStatus.INCOMPLETE,
            ),
        ]
        backend.session_rows[user.id] = sessions
        planned = (
            PlannedDay(monday, "Dar una actualización de estado en el standup"),
            PlannedDay(monday + timedelta(days=1), "Pedir aclaraciones en una revisión de código"),
            PlannedDay(monday + timedelta(days=3), "Explicar un bloqueo técnico"),
            PlannedDay(monday + timedelta(days=4), "Presentar una demo al cliente"),
        )
        report = ReportNumbers(monday - timedelta(days=7), 3, 4, 45, 2, 3)
        backend.homes[user.id] = HomeData(
            has_connected=True,
            has_plan=True,
            today=TodayItem(
                "Pedir aclaraciones en una revisión de código",
                "Tu compañero dejó 12 comentarios en tu PR",
                Skill.SPEAKING,
                "it",
            ),
            week_start=monday,
            planned_days=planned,
            session_marks=(SessionMark(monday, True, SessionStatus.CLOSED),),
            streak=4,
            stamps=(
                Stamp("on track", True),
                Stamp("a quick heads-up", False),
                Stamp("trade-off", False),
            ),
            last_session=sessions[0].summary,
            reviews_due=5,
            provisional_items=1,
            latest_report=report if user is beto else None,
            newest_closed_session_id=sessions[0].summary.id,
            last_celebrated_session_id=None,
        )
        backend.sessions_this_week[user.id] = 2
        backend.plans[user.id] = _plan(today)
        backend.progresses[user.id] = _progress(today)
        backend.glossaries[user.id] = _glossary(today)
        backend.profiles[user.id] = Profile(
            ("it", "business"), 15, 4, "B2", date(today.year, 6, 30)
        )
        backend.clients[user.id] = [ConnectedClient("claude-web", "Claude", now)]

    backend.subs[beto.id] = Subscription(
        Tier.ANNUAL,
        SubStatus.ACTIVE,
        backend.price_id,
        backend.price_cents,
        period_end=today + timedelta(days=10),
        subscription_ref="sub_demo",
        last_event_at=now - timedelta(days=355),
    )
    backend.customers[beto.id] = "cus_demo_beto"
    backend.report_pages[beto.id] = ReportsPage(
        weekly=(
            WeeklyReport(
                ReportNumbers(monday - timedelta(days=7), 3, 4, 45, 2, 3),
                "Pedir aclaraciones sin disculparte de más",
                "since three years",
                "Usaste «on track» sin ayuda tres veces",
            ),
            WeeklyReport(
                ReportNumbers(monday - timedelta(days=14), 4, 4, 52, 1, 2),
                "Bloqueos técnicos en el standup",
                None,
                "Primera semana completa",
            ),
        ),
        checkpoints=(Checkpoint(today - timedelta(days=20), "B1+", "B1+"),),
    )
    backend.settings_rows.update(_settings(now))
    return DemoUsers(ana.id, beto.id, nuevo.id, admin.id)
