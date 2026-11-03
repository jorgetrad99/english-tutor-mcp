"""start_practice, next_exercise, show_exercise, record_result and end_practice, and the plan
status (subjects spec 6, 15.4.1, 15.8, 16.4; slice B plan rulings 28-36 and 63).

One unit of work per call. The server draws, times and grades every showing: the client sends
an answer, never a duration, a grade or a timestamp, and receives the prompt parameters and the
stored draw only. Audit events carry ids, enums and counts; answers and template text are data
and are never logged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from tutor.domain.metrics import streak_days
from tutor.domain.practice import (
    ITEM_COUNT_MIN,
    SELF_RATING_MAX,
    SELF_RATING_MIN,
    Candidate,
    ItemSource,
    KindResult,
    SessionSummary,
    SummaryItem,
    answer_to_json,
    avoid_for,
    capacities,
    draw_items,
    draw_showing,
    grade_showing,
    item_count,
    parse_showing_answer,
    plan_mix,
    schedulable,
    summarise,
)
from tutor.domain.practice_plan import PlanInputs, plan_inputs_changed, plan_status
from tutor.domain.subjects import EXERCISE_KINDS, Subject
from tutor.domain.templates import (
    Draw,
    Graded,
    compose_document,
    encode_payload,
    public_payload,
    render_text,
)
from tutor.services.context import Services, local_date, user_zone
from tutor.services.errors import ServiceError, field_error
from tutor.services.ports import (
    InstanceRow,
    PracticeItemRow,
    PracticeSessionRow,
    TemplateRow,
    UnitOfWork,
)
from tutor.services.subject_views import (
    GradedResult,
    NextItem,
    PlanStatus,
    PracticeItemView,
    PracticeStart,
    PracticeSummary,
    ShownExercise,
)
from tutor.services.template_files import bridge

StartMode = Literal["app", "text"]
START_MODES: tuple[StartMode, ...] = ("app", "text")


# --- plans (rulings 28, 29) ---------------------------------------------------------------------


def plan_inputs(subject: Subject) -> PlanInputs:
    return PlanInputs(
        minutes_per_session=subject.minutes_per_session,
        sessions_per_week=subject.sessions_per_week,
        target_date=subject.target_date,
    )


def sync_plan(uow: UnitOfWork, subject_id: UUID, subject: Subject, now: datetime) -> bool:
    """Create the subject's first plan, or a new version when its time inputs changed; True
    when a plan was created. The new version starts today and counts from zero (ruling 29)."""
    inputs = plan_inputs(subject)
    active = uow.practice.active_plan(subject_id)
    if active is not None and not plan_inputs_changed(active.inputs, inputs):
        return False
    rationale: dict[str, Any] = (
        {"reason": "created"}
        if active is None
        else {"reason": "inputs_changed", "previous_version": active.version}
    )
    plan = uow.practice.create_plan(
        subject_id, inputs, local_date(now, user_zone(uow)), now, rationale
    )
    uow.audit.record(
        "practice_plan_created",
        {
            "subject_id": str(subject_id),
            "plan_id": str(plan.id),
            "version": plan.version,
            "reason": rationale["reason"],
        },
        now,
    )
    return True


def plan_status_of(uow: UnitOfWork, subject_id: UUID, today: date) -> PlanStatus | None:
    """The active plan's status at `today` (spec 6.1), or None without a plan."""
    plan = uow.practice.active_plan(subject_id)
    if plan is None:
        return None
    status = plan_status(
        plan.inputs,
        version=plan.version,
        start_date=plan.start_date,
        completed=uow.practice.completed_count(plan.id),
        today=today,
    )
    return PlanStatus(
        version=status.version,
        start_date=status.start_date,
        total_sessions=status.total_sessions,
        expected_sessions=status.expected,
        completed_sessions=status.completed,
        missed=status.missed,
        projected_end=status.projected_end,
        delay_days=status.delay_days,
        adherence=status.adherence,
    )


# --- shared helpers -----------------------------------------------------------------------------


def _replace_open(uow: UnitOfWork, now: datetime) -> None:
    """Mark the user's open practice session incomplete (ruling 30)."""
    current = uow.practice.open_session()
    if current is None:
        return
    uow.practice.mark_incomplete(current.id, now)
    uow.audit.record(
        "practice_incomplete",
        {"session_id": str(current.id), "subject_id": str(current.subject_id)},
        now,
    )


def _instance_and_template(uow: UnitOfWork, instance_id: UUID) -> tuple[InstanceRow, TemplateRow]:
    instance = uow.instances.get(instance_id)
    template = uow.templates.get(instance.template_id) if instance is not None else None
    if instance is None or template is None:  # pragma: no cover - items reference stored rows
        raise ServiceError("not_found", ("item_id",))
    return instance, template


def _view(
    uow: UnitOfWork,
    session: PracticeSessionRow,
    item_id: UUID,
    seq: int,
    instance_id: UUID,
    shown: Draw,
) -> PracticeItemView:
    """The showing's text and, outside text mode, its composed document: prompt parameters and
    the stored draw only (spec 15.5, 15.7)."""
    instance, template = _instance_and_template(uow, instance_id)
    spec = template.spec
    document = None
    if session.mode != "text" and template.html is not None:
        payload = public_payload(instance.id, spec, instance.params, shown)
        document = compose_document(template.html, encode_payload(payload), bridge("artifact"))
    return PracticeItemView(
        item_id=item_id,
        session_id=session.id,
        seq=seq,
        instance_id=instance.id,
        practice_kind=spec.practice_kind,
        grading_kind=spec.grading_kind,
        needs_view=spec.needs_view,
        document=document,
        text=render_text(spec, instance.params, shown, instance.prompt_text),
    )


def _row_view(
    uow: UnitOfWork, session: PracticeSessionRow, row: PracticeItemRow
) -> PracticeItemView:
    return _view(uow, session, row.id, row.seq, row.instance_id, row.draw)


def _session(uow: UnitOfWork, session_id: UUID) -> PracticeSessionRow:
    session = uow.practice.session(session_id)
    if session is None:
        raise ServiceError("not_found", ("session_id",))
    return session


def _open(uow: UnitOfWork, session_id: UUID) -> PracticeSessionRow:
    session = _session(uow, session_id)
    if session.status != "open":
        raise ServiceError("session_closed", ("session_id",))
    return session


# --- start_practice -----------------------------------------------------------------------------


def start_practice(
    svc: Services, user_id: UUID, subject_id: UUID, mode: StartMode = "app"
) -> PracticeStart:
    """Close any open practice session as incomplete, draw the session's showings of the
    subject's instances (ruling 31, amended) and hand out the first one. A subject with nothing
    to practise in its kinds is `validation_failed` on `subject_id`; one whose items all need
    the view cannot start in text mode (`validation_failed` on `mode`)."""
    if mode not in START_MODES:
        raise ServiceError("validation_failed", ("mode",))
    now = svc.clock()
    with svc.uow(user_id) as uow:
        row = uow.subjects.get(subject_id)
        if row is None:
            raise ServiceError("not_found", ("subject_id",))
        subject = row.subject
        candidates = uow.practice.candidates(subject_id)
        if not schedulable(candidates, subject.exercise_kinds, text_mode=False):
            raise ServiceError("validation_failed", ("subject_id",))
        pool = schedulable(candidates, subject.exercise_kinds, text_mode=mode == "text")
        if not pool:
            raise ServiceError("validation_failed", ("mode",))
        _replace_open(uow, now)
        mix = plan_mix(
            subject.preference, capacities(pool), item_count(subject.minutes_per_session)
        )
        session_id = uuid4()
        items = draw_items(session_id, mix, pool, [uuid4() for _ in range(mix.total)])
        plan = uow.practice.active_plan(subject_id)
        session = uow.practice.create_session(
            session_id,
            subject_id=subject_id,
            plan_id=plan.id if plan is not None else None,
            mode=mode,
            items=items,
            now=now,
        )
        first = None
        if items:
            uow.practice.mark_shown(items[0].item_id, now)
            head = items[0]
            first = _view(uow, session, head.item_id, head.seq, head.instance_id, head.draw)
        uow.audit.record(
            "practice_started",
            {
                "session_id": str(session_id),
                "subject_id": str(subject_id),
                "practice_mode": mode,
                "item_count": len(items),
                "short": mix.short,
            },
            now,
        )
    return PracticeStart(
        session_id=session_id, mode=mode, item_count=len(items), short=mix.short, first=first
    )


# --- next_exercise ------------------------------------------------------------------------------


def next_exercise(svc: Services, user_id: UUID, session_id: UUID) -> NextItem:
    """The first unanswered showing by seq, marked shown now if it never was (the first moment
    wins, ruling 63), or `done` when every showing is answered."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        session = _open(uow, session_id)
        rows = uow.practice.items(session_id)
        answered = sum(1 for r in rows if r.answered_at is not None)
        pending = next((r for r in rows if r.answered_at is None), None)
        if pending is None:
            return NextItem(session_id, done=True, item=None, answered=answered, remaining=0)
        uow.practice.mark_shown(pending.id, now)
        view = _row_view(uow, session, pending)
    return NextItem(
        session_id, done=False, item=view, answered=answered, remaining=len(rows) - answered
    )


# --- show_exercise ------------------------------------------------------------------------------


def show_exercise(
    svc: Services, user_id: UUID, subject_id: UUID, instance_id: UUID
) -> ShownExercise:
    """Append one showing of a chosen exercise instance to the learner's open practice session
    of this subject, or open a `class` session for it (spec 15.9); the showing is shown now
    and its draw avoids the pool entries this session already asked."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        instance = uow.instances.get(instance_id)
        if instance is None or instance.subject_id != subject_id:
            raise ServiceError("not_found", ("instance_id",))
        template = uow.templates.get(instance.template_id)
        if template is None or instance.purpose != "exercise" or instance.status != "active":
            raise ServiceError("validation_failed", ("instance_id",))
        session = uow.practice.open_session()
        if session is None or session.subject_id != subject_id:
            _replace_open(uow, now)
            plan = uow.practice.active_plan(subject_id)
            session = uow.practice.create_session(
                uuid4(),
                subject_id=subject_id,
                plan_id=plan.id if plan is not None else None,
                mode="class",
                items=(),
                now=now,
            )
            uow.audit.record(
                "practice_started",
                {
                    "session_id": str(session.id),
                    "subject_id": str(subject_id),
                    "practice_mode": "class",
                    "item_count": 0,
                    "short": False,
                },
                now,
            )
        rows = uow.practice.items(session.id)
        spec, params = template.spec, instance.params
        prior = [r.draw for r in rows if r.instance_id == instance_id]
        candidate = Candidate(instance.id, spec, params, last_shown_at=None)
        item = draw_showing(
            candidate,
            uuid4(),
            seq=1 + max((r.seq for r in rows), default=0),
            avoid=avoid_for(spec, params, prior),
        )
        stored = uow.practice.add_item(session.id, item, now)
        view = _row_view(uow, session, stored)
    return ShownExercise(session_id=session.id, mode=session.mode, item=view)


# --- record_result ------------------------------------------------------------------------------


def _graded_result(session_id: UUID, item_id: UUID, existing: bool, graded: Graded) -> GradedResult:
    return GradedResult(
        session_id=session_id,
        item_id=item_id,
        outcome="existing" if existing else "new",
        correct=graded.correct,
        score=graded.score,
        reveal=dict(graded.reveal),
    )


def _stored_grade(instance: InstanceRow, template: TemplateRow, row: PracticeItemRow) -> Graded:
    """The stored grade of an answered showing, with its reveal recomputed from the stored
    answer and draw (deterministic; the grade itself is never recomputed)."""
    answer = parse_showing_answer(
        template.spec, instance.params, row.draw, row.answer or {}, from_view=True
    )
    reveal: Mapping[str, object] = {}
    if not isinstance(answer, tuple):
        regraded = grade_showing(template.spec, instance.params, row.draw, answer)
        if isinstance(regraded, Graded):
            reveal = regraded.reveal
    return Graded(row.correct, row.score or 0, reveal)


def record_result(
    svc: Services,
    user_id: UUID,
    session_id: UUID,
    item_id: UUID,
    answer: Mapping[str, object],
    *,
    source: ItemSource = "chat",
) -> GradedResult:
    """Grade one showing on the server against its instance and its stored draw (spec 15.8).
    The first answer wins: a repeat returns the stored result as `existing`. A showing never
    handed out is `validation_failed` on `item_id` (ruling 63). A choice from chat is answered
    with its printed `option_number`; `option_index` only from the view (`source` app)."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        session = _session(uow, session_id)
        row = uow.practice.item(item_id)
        if row is None or row.session_id != session_id:
            raise ServiceError("not_found", ("item_id",))
        instance, template = _instance_and_template(uow, row.instance_id)
        if row.answered_at is not None:
            return _graded_result(session_id, item_id, True, _stored_grade(instance, template, row))
        if session.status != "open":
            raise ServiceError("session_closed", ("session_id",))
        if row.shown_at is None:
            raise ServiceError("validation_failed", ("item_id",))
        spec, params = template.spec, instance.params
        parsed = parse_showing_answer(spec, params, row.draw, answer, from_view=source == "app")
        if isinstance(parsed, tuple):
            raise field_error("validation_failed", parsed)
        graded = grade_showing(spec, params, row.draw, parsed)
        if isinstance(graded, tuple):
            raise field_error("validation_failed", graded)
        stored = uow.practice.record(
            item_id,
            answer=answer_to_json(parsed),
            correct=graded.correct,
            score=graded.score,
            source=source,
            now=now,
        )
        if stored is None:  # pragma: no cover - answered between the read and the write
            current = uow.practice.item(item_id) or row
            return _graded_result(
                session_id, item_id, True, _stored_grade(instance, template, current)
            )
    return _graded_result(session_id, item_id, False, graded)


# --- end_practice -------------------------------------------------------------------------------


def _summary_json(summary: SessionSummary, short: bool) -> dict[str, Any]:
    return {
        "items": summary.items,
        "answered": summary.answered,
        "unanswered": summary.unanswered,
        "minutes": summary.minutes,
        "short": short,
        "by_kind": [
            {
                "practice_kind": k.practice_kind,
                "items": k.items,
                "answered": k.answered,
                "correct": k.correct,
                "mean_score": k.mean_score,
            }
            for k in summary.by_kind
        ],
    }


def _kind_results(data: Sequence[Mapping[str, Any]]) -> tuple[KindResult, ...]:
    results = []
    for entry in data:
        kind = next((k for k in EXERCISE_KINDS if k == entry.get("practice_kind")), None)
        if kind is None:  # pragma: no cover - the server wrote this JSON
            continue
        results.append(
            KindResult(
                practice_kind=kind,
                items=int(entry["items"]),
                answered=int(entry["answered"]),
                correct=int(entry["correct"]),
                mean_score=entry["mean_score"],
            )
        )
    return tuple(results)


def end_practice(
    svc: Services, user_id: UUID, session_id: UUID, self_rating: int | None = None
) -> PracticeSummary:
    """Close the session with its computed summary (ruling 36: unanswered showings are counted,
    the session is `closed`) and record `practice_ended`; idempotent, a closed session returns
    its stored summary. The result carries the plan status and the subject's current streak."""
    if self_rating is not None and not SELF_RATING_MIN <= self_rating <= SELF_RATING_MAX:
        raise ServiceError("validation_failed", ("self_rating",))
    now = svc.clock()
    with svc.uow(user_id) as uow:
        session = _session(uow, session_id)
        if session.status == "incomplete":
            raise ServiceError("session_closed", ("session_id",))
        if session.status == "open":
            rows = uow.practice.items(session_id)
            kinds = {
                r.instance_id: _instance_and_template(uow, r.instance_id)[1].spec.practice_kind
                for r in rows
            }
            summary = summarise(
                [SummaryItem(kinds[r.instance_id], r.correct, r.score) for r in rows],
                session.started_at,
                now,
            )
            short = session.mode != "class" and session.item_count < ITEM_COUNT_MIN
            uow.practice.close(
                session_id, summary=_summary_json(summary, short), self_rating=self_rating, now=now
            )
            uow.audit.record(
                "practice_ended",
                {
                    "session_id": str(session_id),
                    "subject_id": str(session.subject_id),
                    "items": summary.items,
                    "answered": summary.answered,
                    "minutes": summary.minutes,
                },
                now,
            )
            session = _session(uow, session_id)
        stored: Mapping[str, Any] = session.summary or {}
        tz = user_zone(uow)
        status = plan_status_of(uow, session.subject_id, local_date(now, tz))
        days = uow.progress.practice_days(session.subject_id, uow.users.timezone())
        streak = streak_days(days, now, tz)
    return PracticeSummary(
        session_id=session_id,
        items=int(stored.get("items", 0)),
        answered=int(stored.get("answered", 0)),
        unanswered=int(stored.get("unanswered", 0)),
        by_kind=_kind_results(stored.get("by_kind", [])),
        minutes=int(stored.get("minutes", 0)),
        short=bool(stored.get("short", False)),
        self_rating=session.self_rating,
        plan_status=status,
        streak_days=streak,
    )
