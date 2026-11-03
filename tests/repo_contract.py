"""Repository contract: backend-agnostic tests for every port in tutor.services.ports.

Not collected directly (the file name does not start with test_). Subclasses provide the fixtures
`uow_factory`, `identity`, `user_id`, `other_user_id` (two fresh users per test, resolved through
`identity`) and `now` (2026-10-14 15:00 UTC). Data is created only through the ports and the
identity resolver, with seeded track ids, so the same tests run on memory and on Postgres.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from tutor.content import load_track
from tutor.domain.fsrs import FsrsState, new_state
from tutor.domain.glossary import (
    GlossaryAction,
    GlossaryKind,
    IncomingItem,
    InsertItem,
    Promote,
    Reinforce,
    Reject,
    SaveStatus,
    SetStatus,
)
from tutor.domain.lesson import BriefVariant, DueCandidate, RecentResult, TaskResult
from tutor.domain.levels import CefrLevel
from tutor.domain.metrics import SessionMetrics
from tutor.domain.notes import (
    ConceptLinkInput,
    ExerciseInput,
    NoteInput,
    Origin,
    QuizItemInput,
    SourceKind,
    ValidNote,
    ValidNotes,
    ValidSource,
    idempotency_key,
    validate_notes,
)
from tutor.domain.plan_lite import PlannedItem
from tutor.domain.profile import DEFAULT_TIMEZONE, Profile
from tutor.domain.subjects import Subject, SubjectStatus
from tutor.domain.text import normalize
from tutor.domain.track import TrackItem
from tutor.domain.validation import Evidence, ReportedError, SessionOutcome, ValidError
from tutor.services.ports import (
    ArtifactRow,
    GlossaryRowData,
    IdentityResolver,
    NoteRow,
    OpenSessionExists,
    PassageRow,
    ReviewLogRow,
    SessionRow,
    UnitOfWork,
    UowFactory,
)

NEW_YORK = "America/New_York"
MEANING = "What the team says for it."
CONTEXT = "We used it in the standup."
DEFAULT_TURNS = (
    "Yesterday I worked on the login bug and I am blocked on the API keys from the payments team.",
    "Today I'm going to write the tests for the new endpoint and review the pull request from Ana.",
    "It should be done by Thursday if the keys arrive and I'll keep you posted after standup.",
)
RATIONALE: dict[str, Any] = {
    "weeks": 12,
    "hours_available": 12.0,
    "hours_needed": 180,
    "reachable": False,
    "milestone_level": None,
    "message": "confidence_only",
    "inputs": {"days_per_week": 3, "use_cases": ["standup", "code_review"]},
}
RESULT: dict[str, Any] = {
    "status": "closed",
    "streak": 2,
    "metrics": {"user_words": 60, "activation_rate": 0.4},
    "summary_text": "one\ntwo\nthree\nfour",
}
RECENT_ERROR = ValidError(
    said="I have 25 years",
    correct="I am 25 years old",
    correct_norm="i am 25 years old",
    category="grammar",
    turn_index=0,
)
OLD_ERROR = ValidError(
    said="I am agree", correct="I agree", correct_norm="i agree", category="grammar", turn_index=1
)
DROPPED_ERROR = ValidError(
    said="more fast", correct="faster", correct_norm="faster", category="lexis", turn_index=2
)


class ContractAbort(Exception):
    """Raised inside a unit of work to force a rollback."""


def present[T](value: T | None) -> T:
    assert value is not None
    return value


def sample_profile(
    *,
    timezone: str = DEFAULT_TIMEZONE,
    days_per_week: int = 3,
    goal_text: str | None = "Run the standup in English",
) -> Profile:
    return Profile(
        self_level="B1",
        domains=("it",),
        use_cases=("standup", "code_review"),
        minutes_per_day=20,
        days_per_week=days_per_week,
        target_level="B2",
        target_date=date(2027, 1, 15),
        goal_text=goal_text,
        timezone=timezone,
    )


def sample_evidence(
    *,
    turns: Sequence[str] = DEFAULT_TURNS,
    errors: Sequence[ReportedError] = (),
    chunks_used: Sequence[str] = (),
    task_result: TaskResult = "achieved",
    hints_given: int = 0,
    cefr_level: CefrLevel = "B1",
) -> Evidence:
    return Evidence(
        user_turns=tuple(turns),
        errors=tuple(errors),
        chunks_used=tuple(chunks_used),
        task_result=task_result,
        hints_given=hints_given,
        cefr_level=cefr_level,
        cefr_confidence="medium",
        cefr_evidence=("Explains a blocker with the past simple.",),
        confidence_1_5=4,
        assistant_words_estimate=None,
    )


def sample_metrics() -> SessionMetrics:
    return SessionMetrics(
        user_words=60,
        assistant_words_estimate=None,
        user_ratio=None,
        turns=3,
        words_per_turn=20.0,
        duration_min=20.0,
        user_words_per_min=3.0,
        errors_total=1,
        errors_rejected=0,
        errors_by_category={
            "grammar": 1,
            "lexis": 0,
            "word_order": 0,
            "register": 0,
            "other": 0,
        },
        errors_per_100w=1.7,
        recurring_errors=0,
        uptake_count=1,
        chunks_offered=5,
        chunks_used=2,
        chunks_rejected=0,
        activation_rate=0.4,
    )


def incoming(text: str, kind: GlossaryKind = "term") -> IncomingItem:
    return IncomingItem(
        kind=kind, text=text, meaning=MEANING, context_sentence=CONTEXT, domain="it"
    )


def first_item(uow: UnitOfWork) -> TrackItem:
    return uow.track.items("it")[0]


def planned_items(track: Sequence[TrackItem]) -> tuple[PlannedItem, ...]:
    return (
        PlannedItem(week_no=1, order_no=1, track_item_id=track[0].id, variant="base"),
        PlannedItem(week_no=1, order_no=2, track_item_id=track[1].id, variant="base"),
        PlannedItem(week_no=2, order_no=1, track_item_id=track[0].id, variant="complication"),
    )


def start_session(
    uow: UnitOfWork,
    item: TrackItem,
    now: datetime,
    *,
    plan_item_id: UUID | None = None,
    brief_variant: BriefVariant = "base",
) -> SessionRow:
    return uow.sessions.create(
        plan_item_id=plan_item_id,
        track_item_id=item.id,
        prep_text=None,
        mode="voice",
        client="claude",
        brief_variant=brief_variant,
        chunks_offered=[c.id for c in item.chunks],
        now=now,
    )


def finish_session(
    uow: UnitOfWork,
    session_id: UUID,
    ended_at: datetime,
    *,
    status: SessionOutcome = "closed",
    evidence: Evidence | None = None,
    cefr_excluded: bool = False,
) -> None:
    uow.sessions.close(
        session_id,
        status=status,
        low_trust=False,
        ended_at=ended_at,
        evidence=evidence if evidence is not None else sample_evidence(),
        raw_evidence={"user_turns": list(DEFAULT_TURNS)},
        cefr_excluded=cefr_excluded,
        result={"status": status},
    )


def add_closed_session(
    uow: UnitOfWork,
    item: TrackItem,
    started_at: datetime,
    ended_at: datetime,
    *,
    status: SessionOutcome = "closed",
    task_result: TaskResult = "achieved",
    hints_given: int = 0,
    cefr_level: CefrLevel = "B1",
    cefr_excluded: bool = False,
    plan_item_id: UUID | None = None,
) -> SessionRow:
    row = start_session(uow, item, started_at, plan_item_id=plan_item_id)
    evidence = sample_evidence(
        task_result=task_result, hints_given=hints_given, cefr_level=cefr_level
    )
    finish_session(
        uow, row.id, ended_at, status=status, evidence=evidence, cefr_excluded=cefr_excluded
    )
    return present(uow.sessions.get(row.id))


def insert_glossary(
    uow: UnitOfWork,
    session_id: UUID,
    text: str,
    now: datetime,
    *,
    status: SaveStatus = "confirmed",
    kind: GlossaryKind = "term",
    first_due: datetime | None = None,
    expires: datetime | None = None,
) -> GlossaryRowData:
    """Insert through `apply` exactly as `plan_glossary_save` would, then read the row back."""
    item = incoming(text, kind)
    norm = normalize(text)
    if status == "provisional" and expires is None:
        expires = now + timedelta(days=7)
    due = first_due if first_due is not None else now + timedelta(days=1)
    action = InsertItem(
        index=0,
        item=item,
        text_norm=norm,
        status=status,
        provisional_expires_at=expires if status == "provisional" else None,
        first_due=due if status == "confirmed" else None,
    )
    uow.glossary.apply([action], [item], session_id=session_id, now=now)
    return uow.glossary.by_norms([norm])[norm]


def sample_subject(name: str = "Guitar", *, status: SubjectStatus = "active") -> Subject:
    return Subject(
        name=name,
        goal_text="Play at the wedding",
        context_text=None,
        preference="balanced",
        exercise_kinds=("quiz", "exercise"),
        minutes_per_session=30,
        sessions_per_week=3,
        target_date=None,
        status=status,
    )


def valid_source(
    kind: SourceKind = "course", title: str = "Guitar course", url: str | None = None
) -> ValidSource:
    return ValidSource(kind, title, url)


# Search fixtures (plan ruling 2): exact word forms only, no stems, plurals or stop words of the
# query vocabulary anywhere else, so Postgres full text search and the memory token model agree.
FILLER = "Mantener el pulso con calma y paciencia."


def long_paragraph(lead: str) -> str:
    """One paragraph of about 700 characters, so two never merge into one passage."""
    return lead + " " + " ".join([FILLER] * 17)


def valid_note(
    title: str,
    *paragraphs: str,
    lesson_ref: str | None = None,
    position: int | None = None,
    concepts: Sequence[str] = (),
    links: Sequence[ConceptLinkInput] = (),
    exercises: Sequence[ExerciseInput] = (),
    quiz_items: Sequence[QuizItemInput] = (),
) -> ValidNote:
    """A note validated by the domain, its paragraphs separated by blank lines."""
    data = NoteInput(
        title=title,
        lesson_ref=lesson_ref,
        position=position,
        raw_text="\n\n".join(paragraphs),
        concepts=concepts,
        links=links,
        exercises=exercises,
        quiz_items=quiz_items,
    )
    result = validate_notes(None, [data], origin="web")
    assert isinstance(result, ValidNotes)
    return result.notes[0]


def add_note(
    uow: UnitOfWork,
    subject_id: UUID,
    note: ValidNote,
    now: datetime,
    *,
    source_id: UUID | None = None,
    supersedes: UUID | None = None,
    origin: Origin = "mcp",
) -> NoteRow:
    return uow.notes.insert(
        note,
        subject_id=subject_id,
        source_id=source_id,
        origin=origin,
        supersedes=supersedes,
        now=now,
    )


class RepoContract:
    """Every port method, both backends. Subclass it in a module that provides the fixtures."""

    # Unit of work and identity

    def test_uow_exposes_its_user(self, uow_factory: UowFactory, user_id: UUID) -> None:
        with uow_factory(user_id) as uow:
            assert uow.user_id == user_id

    def test_uow_commits_on_clean_exit(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            uow.profiles.upsert(sample_profile(), now)
        with uow_factory(user_id) as uow:
            assert uow.profiles.get() == sample_profile()

    def test_uow_rolls_back_when_the_block_raises(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        # A private exception type: NotImplementedError is a RuntimeError, so a RuntimeError
        # here would swallow Task 16's placeholder repositories instead of letting them xfail.
        with pytest.raises(ContractAbort), uow_factory(user_id) as uow:
            uow.profiles.upsert(sample_profile(timezone=NEW_YORK), now)
            aborted = start_session(uow, first_item(uow), now)
            uow.plans.create(planned_items(uow.track.items("it")), RATIONALE, now)
            insert_glossary(uow, aborted.id, "ship it", now)
            uow.audit.record("profile_saved", {"plan_inputs_changed": True}, now)
            raise ContractAbort
        with uow_factory(user_id) as uow:
            assert uow.profiles.get() is None
            assert uow.users.timezone() == DEFAULT_TIMEZONE
            assert uow.sessions.open_session() is None
            assert uow.sessions.count_started_since(now - timedelta(days=1)) == 0
            assert uow.plans.active() is None
            assert dict(uow.glossary.by_norms(["ship it"])) == {}

    def test_identity_resolves_by_sub_never_by_email(
        self, identity: IdentityResolver, now: datetime
    ) -> None:
        sub = f"contract-{uuid4()}"
        first = identity.resolve(sub, "same@example.com", "First", now)
        again = identity.resolve(sub, "changed@example.com", "Changed", now)
        other = identity.resolve(f"contract-{uuid4()}", "same@example.com", "First", now)
        assert first.created
        assert not again.created
        assert again.id == first.id
        assert other.created
        assert other.id != first.id

    def test_fixture_users_are_distinct(self, user_id: UUID, other_user_id: UUID) -> None:
        assert user_id != other_user_id

    # Users

    def test_user_timezone_defaults_and_is_per_user(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID
    ) -> None:
        with uow_factory(user_id) as uow:
            assert uow.users.timezone() == DEFAULT_TIMEZONE
            uow.users.set_timezone(NEW_YORK)
        with uow_factory(user_id) as uow:
            assert uow.users.timezone() == NEW_YORK
        with uow_factory(other_user_id) as uow:
            assert uow.users.timezone() == DEFAULT_TIMEZONE

    def test_note_mcp_use_is_true_only_the_first_time(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            assert uow.users.note_mcp_use(now) is True
        with uow_factory(user_id) as uow:
            assert uow.users.note_mcp_use(now + timedelta(hours=1)) is False
        with uow_factory(other_user_id) as uow:
            assert uow.users.note_mcp_use(now) is True

    # Track

    def test_track_items_match_the_packaged_track(
        self, uow_factory: UowFactory, user_id: UUID
    ) -> None:
        expected = tuple(
            sorted((t for t in load_track() if t.domain == "it"), key=lambda t: t.order_no)
        )
        with uow_factory(user_id) as uow:
            items = uow.track.items("it")
        assert items == expected
        assert all([c.position for c in t.chunks] == [1, 2, 3, 4, 5] for t in items)

    # Profiles

    def test_profile_upsert_round_trips_and_sets_the_user_timezone(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            assert uow.profiles.get() is None
            uow.profiles.upsert(sample_profile(timezone=NEW_YORK), now)
        with uow_factory(user_id) as uow:
            assert uow.profiles.get() == sample_profile(timezone=NEW_YORK)
            assert uow.users.timezone() == NEW_YORK
            uow.profiles.upsert(sample_profile(days_per_week=5, goal_text=None), now)
        with uow_factory(user_id) as uow:
            assert uow.profiles.get() == sample_profile(days_per_week=5, goal_text=None)
            assert uow.users.timezone() == DEFAULT_TIMEZONE

    def test_profile_timezone_is_the_user_timezone(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            uow.profiles.upsert(sample_profile(), now)
            uow.users.set_timezone(NEW_YORK)
        with uow_factory(user_id) as uow:
            assert present(uow.profiles.get()).timezone == NEW_YORK

    def test_profiles_are_isolated(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            uow.profiles.upsert(sample_profile(timezone=NEW_YORK), now)
        with uow_factory(other_user_id) as uow:
            assert uow.profiles.get() is None
            assert uow.users.timezone() == DEFAULT_TIMEZONE

    # Plans

    def test_plan_create_returns_the_active_plan(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            assert uow.plans.active() is None
            track = uow.track.items("it")
            created = uow.plans.create(planned_items(track), RATIONALE, now)
        assert created.version == 1
        assert created.generated_at == now
        assert dict(created.rationale) == RATIONALE
        assert [
            (i.week_no, i.order_no, i.track_item_id, i.variant, i.status, i.done_session_id)
            for i in created.items
        ] == [
            (1, 1, track[0].id, "base", "pending", None),
            (1, 2, track[1].id, "base", "pending", None),
            (2, 1, track[0].id, "complication", "pending", None),
        ]
        assert len({i.id for i in created.items}) == 3
        with uow_factory(user_id) as uow:
            assert uow.plans.active() == created

    def test_plan_create_supersedes_and_versions_per_user(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        later = now + timedelta(hours=1)
        with uow_factory(user_id) as uow:
            track = uow.track.items("it")
            v1 = uow.plans.create(planned_items(track), RATIONALE, now)
            v2 = uow.plans.create(planned_items(track)[:2], {"weeks": 4}, later)
        assert (v2.version, v2.generated_at, dict(v2.rationale)) == (2, later, {"weeks": 4})
        assert {i.id for i in v1.items}.isdisjoint({i.id for i in v2.items})
        with uow_factory(user_id) as uow:
            assert uow.plans.active() == v2
        with uow_factory(other_user_id) as uow:
            assert uow.plans.active() is None
            assert uow.plans.create(planned_items(track), RATIONALE, now).version == 1
        with uow_factory(user_id) as uow:
            assert uow.plans.active() == v2

    def test_mark_done_works_on_any_plan_version_once(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            track = uow.track.items("it")
            v1 = uow.plans.create(planned_items(track), RATIONALE, now)
            session = start_session(uow, track[0], now, plan_item_id=v1.items[0].id)
            v2 = uow.plans.create(planned_items(track), RATIONALE, now + timedelta(minutes=5))
            assert uow.plans.mark_done(v1.items[0].id, session.id) is True
        with uow_factory(user_id) as uow:
            assert uow.plans.mark_done(v1.items[0].id, session.id) is False
            assert uow.plans.mark_done(uuid4(), session.id) is False
            assert uow.plans.active() == v2
            assert uow.plans.done_base_track_ids() == frozenset({track[0].id})
            assert present(uow.sessions.get(session.id)).plan_item_id == v1.items[0].id

    def test_mark_done_with_another_users_session_raises_lookup_error(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            plan = uow.plans.create(planned_items(uow.track.items("it")), RATIONALE, now)
            session = start_session(uow, first_item(uow), now)
        with uow_factory(other_user_id) as uow, pytest.raises(LookupError):
            uow.plans.mark_done(plan.items[0].id, session.id)
        with uow_factory(user_id) as uow, pytest.raises(LookupError):
            uow.plans.mark_done(plan.items[0].id, uuid4())
        with uow_factory(user_id) as uow:
            assert [i.status for i in present(uow.plans.active()).items] == ["pending"] * 3

    def test_done_base_track_ids_ignore_complications_and_pending_items(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            track = uow.track.items("it")
            plan = uow.plans.create(planned_items(track), RATIONALE, now)
            session = start_session(uow, track[0], now)
            assert uow.plans.done_base_track_ids() == frozenset()
            assert uow.plans.mark_done(plan.items[2].id, session.id) is True
            assert uow.plans.done_base_track_ids() == frozenset()
            assert uow.plans.mark_done(plan.items[1].id, session.id) is True
        with uow_factory(user_id) as uow:
            assert uow.plans.done_base_track_ids() == frozenset({track[1].id})
            item = present(uow.plans.active()).items[1]
            assert (item.status, item.done_session_id) == ("done", session.id)

    def test_plans_are_isolated(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            plan = uow.plans.create(planned_items(uow.track.items("it")), RATIONALE, now)
        with uow_factory(other_user_id) as uow:
            assert uow.plans.active() is None
            mine = start_session(uow, first_item(uow), now)
            assert uow.plans.mark_done(plan.items[0].id, mine.id) is False
            assert uow.plans.done_base_track_ids() == frozenset()
        with uow_factory(user_id) as uow:
            assert [i.status for i in present(uow.plans.active()).items] == ["pending"] * 3

    # Sessions

    def test_session_create_and_get(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            assert uow.sessions.open_session() is None
            created = uow.sessions.create(
                plan_item_id=None,
                track_item_id=item.id,
                prep_text="Outage review with the client tomorrow",
                mode="text",
                client="chatgpt",
                brief_variant="simpler",
                chunks_offered=[c.id for c in item.chunks],
                now=now,
            )
        assert (
            created.track_item_id,
            created.plan_item_id,
            created.prep_text,
            created.mode,
            created.client,
            created.brief_variant,
        ) == (item.id, None, "Outage review with the client tomorrow", "text", "chatgpt", "simpler")
        assert created.chunks_offered == tuple(c.id for c in item.chunks)
        assert (
            created.status,
            created.started_at,
            created.ended_at,
            created.low_trust,
            created.result,
        ) == ("open", now, None, False, None)
        with uow_factory(user_id) as uow:
            assert uow.sessions.get(created.id) == created
            assert uow.sessions.open_session() == created
            assert uow.sessions.get(uuid4()) is None

    def test_session_keeps_its_plan_item(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            track = uow.track.items("it")
            plan = uow.plans.create(planned_items(track), RATIONALE, now)
            session = start_session(uow, track[0], now, plan_item_id=plan.items[0].id)
        with uow_factory(user_id) as uow:
            assert present(uow.sessions.get(session.id)).plan_item_id == plan.items[0].id

    def test_only_one_open_session_per_user(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            first = start_session(uow, first_item(uow), now)
        with pytest.raises(OpenSessionExists), uow_factory(user_id) as uow:
            start_session(uow, first_item(uow), now + timedelta(minutes=1))
        with uow_factory(user_id) as uow:
            assert present(uow.sessions.open_session()).id == first.id
            assert uow.sessions.count_started_since(now - timedelta(days=1)) == 1
        with uow_factory(other_user_id) as uow:
            assert start_session(uow, first_item(uow), now).status == "open"

    def test_mark_incomplete_frees_the_open_slot(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        ended = now + timedelta(minutes=5)
        with uow_factory(user_id) as uow:
            first = start_session(uow, first_item(uow), now)
            uow.sessions.mark_incomplete(first.id, ended)
        with uow_factory(user_id) as uow:
            row = present(uow.sessions.get(first.id))
            assert (row.status, row.ended_at, row.result) == ("incomplete", ended, None)
            assert uow.sessions.open_session() is None
            second = start_session(uow, first_item(uow), now + timedelta(minutes=6))
            uow.sessions.mark_incomplete(first.id, now + timedelta(hours=1))
        with uow_factory(user_id) as uow:
            assert present(uow.sessions.get(first.id)).ended_at == ended
            assert present(uow.sessions.open_session()).id == second.id

    def test_close_stores_the_outcome_and_result(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            closed = start_session(uow, item, now)
            uow.sessions.close(
                closed.id,
                status="closed",
                low_trust=True,
                ended_at=now + timedelta(minutes=20),
                evidence=sample_evidence(),
                raw_evidence={"user_turns": list(DEFAULT_TURNS), "nested": {"ids": [1, 2]}},
                cefr_excluded=True,
                result=RESULT,
            )
        with uow_factory(user_id) as uow:
            row = present(uow.sessions.get(closed.id))
            assert (row.status, row.low_trust, row.ended_at) == (
                "closed",
                True,
                now + timedelta(minutes=20),
            )
            assert row.result == RESULT
            assert uow.sessions.open_session() is None
            short = start_session(uow, item, now + timedelta(hours=1))
            uow.sessions.close(
                short.id,
                status="incomplete",
                low_trust=False,
                ended_at=now + timedelta(hours=1, minutes=5),
                evidence=sample_evidence(turns=("Yes.",)),
                raw_evidence={},
                cefr_excluded=False,
                result={"status": "incomplete"},
            )
        with uow_factory(user_id) as uow:
            row = present(uow.sessions.get(short.id))
            assert (row.status, row.result) == ("incomplete", {"status": "incomplete"})

    def test_count_started_since_counts_every_status(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            replaced = start_session(uow, item, now - timedelta(hours=2))
            uow.sessions.mark_incomplete(replaced.id, now - timedelta(hours=1, minutes=50))
            add_closed_session(uow, item, now - timedelta(hours=1), now - timedelta(minutes=40))
            start_session(uow, item, now)
        with uow_factory(user_id) as uow:
            assert uow.sessions.count_started_since(now - timedelta(hours=2)) == 3
            assert uow.sessions.count_started_since(now - timedelta(hours=2, seconds=-1)) == 2
            assert uow.sessions.count_started_since(now) == 1
            assert uow.sessions.count_started_since(now + timedelta(seconds=1)) == 0

    def test_recent_results_are_closed_sessions_newest_first(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        h = timedelta(hours=1)
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            add_closed_session(uow, item, now - 4 * h, now - 3 * h, task_result="achieved")
            add_closed_session(
                uow, item, now - 3 * h, now - 2 * h, task_result="not_achieved", hints_given=2
            )
            add_closed_session(
                uow, item, now - 2 * h, now - h, status="incomplete", task_result="achieved"
            )
            add_closed_session(
                uow, item, now - h, now - h / 2, task_result="partial", hints_given=1
            )
        with uow_factory(user_id) as uow:
            assert uow.sessions.recent_results(3) == (
                RecentResult(task_result="partial", hints_given=1),
                RecentResult(task_result="not_achieved", hints_given=2),
                RecentResult(task_result="achieved", hints_given=0),
            )
            assert uow.sessions.recent_results(1) == (
                RecentResult(task_result="partial", hints_given=1),
            )

    def test_closed_ended_at_lists_closed_sessions_since_in_ascending_order(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        d = timedelta(days=1)
        m20 = timedelta(minutes=20)
        h = timedelta(hours=1)
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            add_closed_session(uow, item, now - 3 * d - m20, now - 3 * d)
            add_closed_session(uow, item, now - 2 * d - m20, now - 2 * d, status="incomplete")
            add_closed_session(uow, item, now - h - m20, now - h)
        with uow_factory(user_id) as uow:
            assert uow.sessions.closed_ended_at(now - 3 * d) == (now - 3 * d, now - h)
            assert uow.sessions.closed_ended_at(now - 2 * d) == (now - h,)
            assert uow.sessions.closed_ended_at(now) == ()

    def test_previous_cefr_skips_excluded_and_incomplete_sessions(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        h = timedelta(hours=1)
        with uow_factory(user_id) as uow:
            assert uow.sessions.previous_cefr() is None
            item = first_item(uow)
            add_closed_session(uow, item, now - 4 * h, now - 3 * h, cefr_level="B1")
        with uow_factory(user_id) as uow:
            assert uow.sessions.previous_cefr() == "B1"
            add_closed_session(
                uow, item, now - 3 * h, now - 2 * h, cefr_level="C1", cefr_excluded=True
            )
            add_closed_session(
                uow, item, now - 2 * h, now - h, status="incomplete", cefr_level="B2"
            )
        with uow_factory(user_id) as uow:
            assert uow.sessions.previous_cefr() == "B1"
            add_closed_session(uow, item, now - h, now - h / 2, cefr_level="B2")
        with uow_factory(user_id) as uow:
            assert uow.sessions.previous_cefr() == "B2"

    def test_last_done_by_track_uses_closed_sessions(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        h = timedelta(hours=1)
        with uow_factory(user_id) as uow:
            track = uow.track.items("it")
            add_closed_session(uow, track[0], now - 4 * h, now - 3 * h)
            add_closed_session(uow, track[0], now - 2 * h, now - h)
            add_closed_session(uow, track[1], now - h, now - h / 2, status="incomplete")
        with uow_factory(user_id) as uow:
            assert dict(uow.sessions.last_done_by_track()) == {track[0].id: now - h}

    def test_saved_errors_feed_recent_correct_norms(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        d = timedelta(days=1)
        m20 = timedelta(minutes=20)
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            old = add_closed_session(uow, item, now - 40 * d, now - 40 * d + m20)
            uow.sessions.save_errors(old.id, [OLD_ERROR])
            recent = add_closed_session(uow, item, now - 2 * d, now - 2 * d + m20)
            uow.sessions.save_errors(recent.id, [DROPPED_ERROR])
            uow.sessions.save_errors(recent.id, [RECENT_ERROR])  # replaces, never appends
            short = add_closed_session(uow, item, now - d, now - d + m20, status="incomplete")
            uow.sessions.save_errors(short.id, [DROPPED_ERROR])
        with uow_factory(user_id) as uow:
            assert uow.sessions.recent_correct_norms(now - 30 * d, uuid4()) == frozenset(
                {"i am 25 years old"}
            )
            assert uow.sessions.recent_correct_norms(now - 30 * d, recent.id) == frozenset()
            assert uow.sessions.recent_correct_norms(now - 50 * d, uuid4()) == frozenset(
                {"i am 25 years old", "i agree"}
            )

    def test_save_metrics_accepts_a_closed_session_twice(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = add_closed_session(uow, first_item(uow), now - timedelta(minutes=20), now)
            uow.sessions.save_metrics(session.id, sample_metrics())
        with uow_factory(user_id) as uow:
            uow.sessions.save_metrics(session.id, sample_metrics())
            assert present(uow.sessions.get(session.id)).status == "closed"

    def test_sessions_are_isolated(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            done = add_closed_session(uow, item, now - timedelta(hours=2), now - timedelta(hours=1))
            uow.sessions.save_errors(done.id, [RECENT_ERROR])
            live = start_session(uow, item, now)
        with uow_factory(other_user_id) as uow:
            assert uow.sessions.get(live.id) is None
            assert uow.sessions.get(done.id) is None
            assert uow.sessions.open_session() is None
            assert uow.sessions.count_started_since(now - timedelta(days=1)) == 0
            assert uow.sessions.recent_results(3) == ()
            assert uow.sessions.closed_ended_at(now - timedelta(days=1)) == ()
            assert uow.sessions.previous_cefr() is None
            assert dict(uow.sessions.last_done_by_track()) == {}
            assert uow.sessions.recent_correct_norms(now - timedelta(days=1), uuid4()) == (
                frozenset()
            )
            uow.sessions.mark_incomplete(live.id, now)
            finish_session(uow, live.id, now)
            theirs = start_session(uow, item, now)
        with uow_factory(user_id) as uow:
            row = present(uow.sessions.get(live.id))
            assert (row.status, row.ended_at, row.result) == ("open", None, None)
            assert present(uow.sessions.open_session()).id == live.id
        assert theirs.id != live.id

    # Glossary

    def test_insert_confirmed_item_is_scheduled(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        tomorrow = now + timedelta(days=1)
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            row = insert_glossary(
                uow, session.id, "Roll back the deploy", now, kind="chunk", first_due=tomorrow
            )
        assert (row.kind, row.text, row.text_norm, row.meaning, row.context_sentence) == (
            "chunk",
            "Roll back the deploy",
            "roll back the deploy",
            MEANING,
            CONTEXT,
        )
        assert (
            row.domain,
            row.status,
            row.seen_count,
            row.leech,
            row.created_at,
            row.provisional_expires_at,
        ) == ("it", "confirmed", 1, False, now, None)
        with uow_factory(user_id) as uow:
            assert uow.reviews.state(row.id) == new_state(tomorrow)
            assert uow.glossary.due_candidates(now) == ()
            assert uow.glossary.count_due(now) == 0
            assert uow.glossary.due_candidates(tomorrow) == (
                DueCandidate(
                    item_id=row.id, kind="chunk", leech=False, due=tomorrow, last_ratings=()
                ),
            )
            assert uow.glossary.count_due(tomorrow) == 1

    def test_provisional_and_declined_items_are_never_due(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        later = now + timedelta(days=30)
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            maybe = insert_glossary(uow, session.id, "circle back", now, status="provisional")
            dropped = insert_glossary(uow, session.id, "synergy", now, status="declined")
        assert maybe.provisional_expires_at == now + timedelta(days=7)
        assert dropped.provisional_expires_at is None
        with uow_factory(user_id) as uow:
            assert [r.id for r in uow.glossary.provisional(8)] == [maybe.id]
            assert uow.glossary.count_provisional() == 1
            assert uow.reviews.state(maybe.id) is None
            assert uow.reviews.state(dropped.id) is None
            assert uow.glossary.due_candidates(later) == ()
            assert uow.glossary.count_due(later) == 0

    def test_reinforce_updates_kind_counts_leech_and_due(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        moved = now + timedelta(days=2)
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            row = insert_glossary(uow, session.id, "roll back the deploy", now)
            uow.glossary.apply(
                [
                    Reinforce(
                        index=0,
                        item_id=row.id,
                        kind="correction",
                        seen_count=2,
                        leech=True,
                        due=moved,
                    )
                ],
                [incoming("roll back the deploy", "correction")],
                session_id=session.id,
                now=now + timedelta(hours=1),
            )
        with uow_factory(user_id) as uow:
            updated = uow.glossary.get_many([row.id])[row.id]
            assert (
                updated.kind,
                updated.seen_count,
                updated.leech,
                updated.status,
                updated.text,
                updated.meaning,
                updated.created_at,
            ) == ("correction", 2, True, "confirmed", "roll back the deploy", MEANING, now)
            state = present(uow.reviews.state(row.id))
            assert state.due == moved
            assert (state.reps, state.stability) == (0, None)
            assert uow.glossary.due_candidates(moved) == (
                DueCandidate(
                    item_id=row.id, kind="correction", leech=True, due=moved, last_ratings=()
                ),
            )

    def test_promote_confirms_and_schedules(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        tomorrow = now + timedelta(days=1)
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            maybe = insert_glossary(uow, session.id, "keep you posted", now, status="provisional")
            uow.glossary.apply(
                [Promote(index=0, item_id=maybe.id, first_due=tomorrow)],
                [incoming("keep you posted")],
                session_id=session.id,
                now=now + timedelta(hours=1),
            )
        with uow_factory(user_id) as uow:
            row = uow.glossary.get_many([maybe.id])[maybe.id]
            assert (row.status, row.provisional_expires_at) == ("confirmed", None)
            assert uow.reviews.state(maybe.id) == new_state(tomorrow)
            assert uow.glossary.provisional(8) == ()
            assert uow.glossary.count_provisional() == 0

    def test_set_status_moves_between_provisional_and_declined(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            maybe = insert_glossary(uow, session.id, "circle back", now, status="provisional")
            uow.glossary.apply(
                [
                    SetStatus(
                        index=0, item_id=maybe.id, status="declined", provisional_expires_at=None
                    )
                ],
                [incoming("circle back")],
                session_id=session.id,
                now=now + timedelta(hours=1),
            )
        with uow_factory(user_id) as uow:
            row = uow.glossary.get_many([maybe.id])[maybe.id]
            assert (row.status, row.provisional_expires_at) == ("declined", None)
            assert uow.glossary.count_provisional() == 0
            assert uow.reviews.state(maybe.id) is None
            uow.glossary.apply(
                [
                    SetStatus(
                        index=0,
                        item_id=maybe.id,
                        status="provisional",
                        provisional_expires_at=now + timedelta(days=7),
                    )
                ],
                [incoming("circle back")],
                session_id=session.id,
                now=now + timedelta(hours=2),
            )
        with uow_factory(user_id) as uow:
            row = uow.glossary.get_many([maybe.id])[maybe.id]
            assert (row.status, row.provisional_expires_at) == (
                "provisional",
                now + timedelta(days=7),
            )
            assert uow.glossary.count_provisional() == 1

    def test_reject_actions_change_nothing(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            uow.glossary.apply(
                [Reject(index=0, reason="empty")],
                [incoming("  !!  ")],
                session_id=session.id,
                now=now,
            )
        with uow_factory(user_id) as uow:
            assert dict(uow.glossary.by_norms([""])) == {}
            assert uow.glossary.provisional(8) == ()
            assert uow.glossary.count_due(now + timedelta(days=30)) == 0

    def test_by_norms_and_get_many_return_only_requested_items(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            shipped = insert_glossary(uow, session.id, "ship it", now)
            oncall = insert_glossary(uow, session.id, "on-call", now, status="provisional")
        with uow_factory(user_id) as uow:
            assert dict(uow.glossary.by_norms(["ship it", "missing phrase"])) == {
                "ship it": shipped
            }
            assert dict(uow.glossary.by_norms([])) == {}
            assert dict(uow.glossary.get_many([shipped.id, oncall.id, uuid4()])) == {
                shipped.id: shipped,
                oncall.id: oncall,
            }
            assert dict(uow.glossary.get_many([])) == {}

    def test_glossary_is_isolated_and_text_is_unique_per_user(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        later = now + timedelta(days=2)
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            mine = insert_glossary(uow, session.id, "Ship it", now)
        with uow_factory(other_user_id) as uow:
            their_session = start_session(uow, first_item(uow), now)
            theirs = insert_glossary(uow, their_session.id, "ship it!", now, status="provisional")
            assert theirs.id != mine.id
            assert dict(uow.glossary.by_norms(["ship it"])) == {"ship it": theirs}
            assert dict(uow.glossary.get_many([mine.id])) == {}
            assert uow.glossary.due_candidates(later) == ()
            assert uow.glossary.count_provisional() == 1
            assert uow.glossary.purge(now + timedelta(days=400)) == 1
        with uow_factory(user_id) as uow:
            assert dict(uow.glossary.by_norms(["ship it"])) == {"ship it": mine}
            assert uow.glossary.count_provisional() == 0
            assert [c.item_id for c in uow.glossary.due_candidates(later)] == [mine.id]

    def test_provisional_lists_oldest_first_with_a_limit(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        texts = ("alpha phrase", "bravo phrase", "charlie phrase")
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            for n, text in enumerate(texts):
                created = now - timedelta(hours=3 - n)
                insert_glossary(uow, session.id, text, created, status="provisional")
        with uow_factory(user_id) as uow:
            assert [r.text for r in uow.glossary.provisional(2)] == list(texts[:2])
            assert [r.text for r in uow.glossary.provisional(8)] == list(texts)
            assert uow.glossary.count_provisional() == 3

    def test_purge_removes_expired_provisional_and_old_declined_items(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        d = timedelta(days=1)
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now - 100 * d)
            rows: list[tuple[str, datetime, SaveStatus, datetime | None]] = [
                # Expiry is strict: an item expiring exactly now is kept until later.
                ("expired provisional", now - 8 * d, "provisional", now - d),
                ("expires right now", now - 7 * d, "provisional", now),
                ("still provisional", now - d, "provisional", now + d),
                # Declined items age by created_at; exactly 30 days old is kept.
                ("old declined", now - 31 * d, "declined", None),
                ("declined thirty days ago", now - 30 * d, "declined", None),
                ("recent declined", now - 29 * d, "declined", None),
            ]
            for text, created, status, expires in rows:
                insert_glossary(uow, session.id, text, created, status=status, expires=expires)
            insert_glossary(uow, session.id, "old confirmed", now - 100 * d)
        with uow_factory(user_id) as uow:
            assert uow.glossary.purge(now) == 2
        with uow_factory(user_id) as uow:
            kept = uow.glossary.by_norms([r[0] for r in rows] + ["old confirmed"])
            assert set(kept) == {
                "expires right now",
                "still provisional",
                "declined thirty days ago",
                "recent declined",
                "old confirmed",
            }
            assert uow.glossary.purge(now) == 0

    def test_due_candidates_carry_the_last_two_ratings(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        d = timedelta(days=1)
        m5 = timedelta(minutes=5)
        with uow_factory(user_id) as uow:
            item = first_item(uow)
            origin = add_closed_session(uow, item, now - 10 * d, now - 10 * d + 4 * m5)
            row = insert_glossary(
                uow, origin.id, "keep you posted", now - 10 * d, kind="chunk", first_due=now - 5 * d
            )
            before = present(uow.reviews.state(row.id))
            reviewed = []
            for days, rating in ((3, 3), (2, 2), (1, 3)):
                start = now - days * d
                session = add_closed_session(uow, item, start, start + 4 * m5)
                assert uow.reviews.log(session.id, row.id, rating, start + m5, before) is True
                reviewed.append(session)
        with uow_factory(user_id) as uow:
            assert uow.glossary.due_candidates(now) == (
                DueCandidate(
                    item_id=row.id, kind="chunk", leech=False, due=now - 5 * d, last_ratings=(2, 3)
                ),
            )
            uow.reviews.set_log_rating(reviewed[2].id, row.id, 4)
        with uow_factory(user_id) as uow:
            assert uow.glossary.due_candidates(now)[0].last_ratings == (2, 4)

    # Reviews

    def test_review_state_round_trips(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        first = FsrsState(
            stability=3.17,
            difficulty=5.28,
            reps=2,
            lapses=1,
            last_review=now,
            due=now + timedelta(days=4),
        )
        second = FsrsState(
            stability=9.5,
            difficulty=4.75,
            reps=3,
            lapses=1,
            last_review=now + timedelta(days=4),
            due=now + timedelta(days=13),
        )
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            row = insert_glossary(uow, session.id, "blameless postmortem", now)
            uow.reviews.save_state(row.id, first)
        with uow_factory(user_id) as uow:
            assert uow.reviews.state(row.id) == first
            uow.reviews.save_state(row.id, second)
        with uow_factory(user_id) as uow:
            assert uow.reviews.state(row.id) == second
            assert uow.reviews.state(uuid4()) is None

    def test_review_log_is_unique_per_session_and_item(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        m = timedelta(minutes=1)
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            shipped = insert_glossary(uow, session.id, "ship it", now)
            rolled = insert_glossary(uow, session.id, "roll back the deploy", now)
            before_a = present(uow.reviews.state(shipped.id))
            before_b = present(uow.reviews.state(rolled.id))
            assert uow.reviews.log(session.id, shipped.id, 3, now + m, before_a) is True
            assert uow.reviews.log(session.id, shipped.id, 1, now + 2 * m, before_a) is False
            assert uow.reviews.log(session.id, rolled.id, 2, now + 3 * m, before_b) is True
        with uow_factory(user_id) as uow:
            assert uow.reviews.session_logs(session.id) == (
                ReviewLogRow(
                    item_id=shipped.id, rating=3, reviewed_at=now + m, state_before=before_a
                ),
                ReviewLogRow(
                    item_id=rolled.id, rating=2, reviewed_at=now + 3 * m, state_before=before_b
                ),
            )
            assert uow.reviews.session_logs(uuid4()) == ()

    def test_set_log_rating_changes_only_that_log(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            shipped = insert_glossary(uow, session.id, "ship it", now)
            rolled = insert_glossary(uow, session.id, "roll back the deploy", now)
            for row, rating, minutes in ((shipped, 3, 1), (rolled, 2, 2)):
                before = present(uow.reviews.state(row.id))
                uow.reviews.log(
                    session.id, row.id, rating, now + timedelta(minutes=minutes), before
                )
            uow.reviews.set_log_rating(session.id, shipped.id, 4)
        with uow_factory(user_id) as uow:
            logs = uow.reviews.session_logs(session.id)
            assert [(log.item_id, log.rating) for log in logs] == [(shipped.id, 4), (rolled.id, 2)]

    def test_reviews_are_isolated(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            row = insert_glossary(uow, session.id, "ship it", now)
            before = present(uow.reviews.state(row.id))
            uow.reviews.log(session.id, row.id, 3, now, before)
        with uow_factory(other_user_id) as uow:
            assert uow.reviews.state(row.id) is None
            assert uow.reviews.session_logs(session.id) == ()
            uow.reviews.set_log_rating(session.id, row.id, 1)
        with uow_factory(user_id) as uow:
            assert uow.reviews.session_logs(session.id)[0].rating == 3

    def test_create_with_a_plan_item_the_user_does_not_own_raises_lookup_error(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            plan = uow.plans.create(planned_items(uow.track.items("it")), RATIONALE, now)
        for foreign in (plan.items[0].id, uuid4()):
            with pytest.raises(LookupError), uow_factory(other_user_id) as uow:
                start_session(uow, first_item(uow), now, plan_item_id=foreign)
        with uow_factory(other_user_id) as uow:
            assert uow.sessions.open_session() is None
            assert uow.sessions.count_started_since(now - timedelta(days=1)) == 0

    def test_glossary_apply_with_another_users_session_raises_lookup_error(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            foreign = start_session(uow, first_item(uow), now)
        with uow_factory(other_user_id) as uow:
            own = start_session(uow, first_item(uow), now)
            maybe = insert_glossary(uow, own.id, "maybe", now, status="provisional")
            before = uow.glossary.get_many([maybe.id])[maybe.id]
        insert = InsertItem(
            index=0,
            item=incoming("ship it"),
            text_norm=normalize("ship it"),
            status="confirmed",
            provisional_expires_at=None,
            first_due=now,
        )
        attempts: list[GlossaryAction] = [
            insert,
            Promote(index=0, item_id=maybe.id, first_due=now),
            SetStatus(index=0, item_id=maybe.id, status="declined", provisional_expires_at=None),
        ]
        for action in attempts:
            with pytest.raises(LookupError), uow_factory(other_user_id) as uow:
                uow.glossary.apply([action], [incoming("ship it")], session_id=foreign.id, now=now)
        with uow_factory(other_user_id) as uow:
            assert uow.glossary.by_norms([normalize("ship it")]) == {}
            assert uow.glossary.get_many([maybe.id])[maybe.id] == before
            assert uow.reviews.state(maybe.id) is None

    def test_writes_against_another_users_rows_raise_lookup_error(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            row = insert_glossary(uow, session.id, "ship it", now)
            state = present(uow.reviews.state(row.id))
        reinforce = Reinforce(
            index=0, item_id=row.id, kind="correction", seen_count=9, leech=True, due=now
        )
        attempts: list[Callable[[UnitOfWork], object]] = [
            lambda u: u.sessions.save_metrics(session.id, sample_metrics()),
            lambda u: u.sessions.save_errors(session.id, [RECENT_ERROR]),
            lambda u: u.reviews.save_state(row.id, new_state(now)),
            lambda u: u.reviews.log(session.id, row.id, 3, now, state),
            lambda u: u.glossary.apply(
                [reinforce], [incoming("ship it")], session_id=session.id, now=now
            ),
        ]
        for attempt in attempts:
            with pytest.raises(LookupError), uow_factory(other_user_id) as uow:
                attempt(uow)
        with uow_factory(user_id) as uow:
            assert uow.glossary.get_many([row.id])[row.id] == row
            assert uow.reviews.state(row.id) == state
            assert uow.reviews.session_logs(session.id) == ()
            assert uow.sessions.recent_correct_norms(now - timedelta(days=1), uuid4()) == (
                frozenset()
            )

    def test_session_logs_with_equal_timestamps_are_stable_and_complete(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            session = start_session(uow, first_item(uow), now)
            items = [insert_glossary(uow, session.id, t, now) for t in ("ship it", "roll back")]
            for row in items:
                before = present(uow.reviews.state(row.id))
                assert uow.reviews.log(session.id, row.id, 3, now, before) is True
        with uow_factory(user_id) as uow:
            first = uow.reviews.session_logs(session.id)
            second = uow.reviews.session_logs(session.id)
        assert first == second
        assert {log.item_id for log in first} == {r.id for r in items}
        assert all(log.reviewed_at == now for log in first)

    # Audit

    def test_audit_record_accepts_json_meta(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            uow.audit.record(
                "session_closed",
                {"session_id": str(uuid4()), "status": "closed", "low_trust": False, "n": 3},
                now,
            )

    # Subjects

    def test_subject_create_get_and_list_active_first_then_by_name(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            piano = uow.subjects.create(sample_subject("Piano"), now)
            bass = uow.subjects.create(sample_subject("Bass", status="paused"), now)
            drums = uow.subjects.create(sample_subject("Drums"), now)
            alto = uow.subjects.create(sample_subject("Alto sax", status="archived"), now)
        assert (piano.subject, piano.created_at, piano.updated_at) == (
            sample_subject("Piano"),
            now,
            now,
        )
        assert len({piano.id, bass.id, drums.id, alto.id}) == 4
        with uow_factory(user_id) as uow:
            assert uow.subjects.get(piano.id) == piano
            assert uow.subjects.get(uuid4()) is None
            assert [row.id for row in uow.subjects.list()] == [drums.id, piano.id, alto.id, bass.id]

    def test_subject_update_replaces_and_bumps_updated_at(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        later = now + timedelta(hours=1)
        changed = sample_subject("Piano jazz", status="paused")
        with uow_factory(user_id) as uow:
            row = uow.subjects.create(sample_subject("Piano"), now)
        with uow_factory(user_id) as uow:
            assert uow.subjects.update(row.id, changed, later) is True
            assert uow.subjects.update(uuid4(), changed, later) is False
        with uow_factory(user_id) as uow:
            updated = present(uow.subjects.get(row.id))
            assert (updated.subject, updated.created_at, updated.updated_at) == (
                changed,
                now,
                later,
            )
            assert [r.id for r in uow.subjects.list()] == [row.id]

    def test_subjects_are_isolated(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        # Review Focus 5
        with uow_factory(user_id) as uow:
            mine = uow.subjects.create(sample_subject("Piano"), now)
        with uow_factory(other_user_id) as uow:
            assert uow.subjects.list() == ()
            assert uow.subjects.get(mine.id) is None
            assert uow.subjects.update(mine.id, sample_subject("Taken"), now) is False
            with pytest.raises(LookupError):
                uow.notes.upsert_source(mine.id, valid_source(), now)
        with uow_factory(user_id) as uow:
            assert uow.subjects.get(mine.id) == mine

    # Sources

    def test_upsert_source_matches_kind_and_title_within_the_subject(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        later = now + timedelta(hours=1)
        with uow_factory(user_id) as uow:
            guitar = uow.subjects.create(sample_subject("Guitar"), now).id
            piano = uow.subjects.create(sample_subject("Piano"), now).id
            first = uow.notes.upsert_source(guitar, valid_source(), now)
            again = uow.notes.upsert_source(
                guitar, valid_source(url="https://example.test/c"), later
            )
            pdf = uow.notes.upsert_source(guitar, valid_source(kind="pdf"), now)
            theirs = uow.notes.upsert_source(piano, valid_source(), now)
        assert (first.subject_id, first.kind, first.title, first.url, first.created_at) == (
            guitar,
            "course",
            "Guitar course",
            None,
            now,
        )
        assert (again.id, again.url, again.created_at) == (first.id, "https://example.test/c", now)
        assert len({first.id, pdf.id, theirs.id}) == 3
        with uow_factory(user_id) as uow:
            assert uow.notes.source(first.id) == again
            assert uow.notes.source(uuid4()) is None
            # No url in the call keeps the stored one.
            assert uow.notes.upsert_source(guitar, valid_source(), later).url == again.url
        with pytest.raises(LookupError), uow_factory(user_id) as uow:
            uow.notes.upsert_source(uuid4(), valid_source(), now)

    # Notes

    def test_note_insert_stores_text_passages_items_and_concepts(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        link = ConceptLinkInput("Quinto traste", "Acorde de séptima", "prerequisite")
        exercise = ExerciseInput(
            "Cambio C7-G7", "Cambia entre C7 y G7 cada compás.", "technique", 60, 5
        )
        quiz = QuizItemInput(
            "¿Cuántas notas tiene un acorde de séptima?",
            ("Tres", "Cuatro"),
            1,
            "La séptima se suma.",
            "Acorde de séptima",
        )
        note = valid_note(
            "Clase 3",
            long_paragraph("acorde de séptima."),
            long_paragraph("quinto traste."),
            lesson_ref="M2/C3",
            position=3,
            concepts=["Acorde de séptima", "Blues"],
            links=[link],
            exercises=[exercise],
            quiz_items=[quiz],
        )
        assert len(note.passages) == 2
        with uow_factory(user_id) as uow:
            subject_id = uow.subjects.create(sample_subject(), now).id
            source_id = uow.notes.upsert_source(subject_id, valid_source(), now).id
            row = add_note(uow, subject_id, note, now, source_id=source_id)
        assert row == NoteRow(
            id=row.id,
            subject_id=subject_id,
            source_id=source_id,
            title="Clase 3",
            lesson_ref="M2/C3",
            position=3,
            raw_text=note.raw_text,
            raw_had_nul=False,
            clean_text=note.clean_text,
            clean_version=1,
            idempotency_key=idempotency_key(subject_id, source_id, "M2/C3", note.raw_text),
            origin="mcp",
            supersedes_note_id=None,
            created_at=now,
            updated_at=now,
        )
        with uow_factory(user_id) as uow:
            assert uow.notes.get(row.id) == row
            assert uow.notes.get(uuid4()) is None
            assert uow.notes.by_idempotency_key(row.idempotency_key) == row
            assert uow.notes.by_idempotency_key("no such key") is None
            assert uow.notes.passage_count(row.id) == 2
            assert uow.notes.passages(row.id, 1, 10) == tuple(
                PassageRow(seq, text) for seq, text in enumerate(note.passages, start=1)
            )
            [stored] = uow.notes.exercises(row.id)
            assert (
                stored.seq,
                stored.name,
                stored.instructions,
                stored.kind,
                stored.tempo_bpm,
                stored.duration_min,
            ) == (1, "Cambio C7-G7", "Cambia entre C7 y G7 cada compás.", "technique", 60, 5)
            [item] = uow.notes.quiz_items(row.id)
            assert (
                item.seq,
                item.question,
                item.options,
                item.answer_index,
                item.explanation,
                item.concept,
            ) == (
                1,
                quiz.question,
                ("Tres", "Cuatro"),
                1,
                "La séptima se suma.",
                "Acorde de séptima",
            )
            # Link endpoints ("Quinto traste") exist as concepts but are not attached to the note.
            assert [(c.subject_id, c.name, c.name_norm) for c in uow.notes.concepts(row.id)] == [
                (subject_id, "Acorde de séptima", "acorde de septima"),
                (subject_id, "Blues", "blues"),
            ]
            assert uow.notes.artifacts(row.id) == ()

    def test_review_focus_1_same_lesson_twice_then_with_a_typo_fixed(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        # Review Focus 1
        first = valid_note(
            "Clase 3", long_paragraph("acorde de séptima con un tipo."), lesson_ref="M2/C3"
        )
        fixed = valid_note(
            "Clase 3", long_paragraph("acorde de séptima con un typo."), lesson_ref="M2/C3"
        )
        with uow_factory(user_id) as uow:
            subject_id = uow.subjects.create(sample_subject(), now).id
            source_id = uow.notes.upsert_source(subject_id, valid_source(), now).id
            original = add_note(uow, subject_id, first, now, source_id=source_id)
        key = idempotency_key(subject_id, source_id, "M2/C3", first.raw_text)
        with uow_factory(user_id) as uow:
            assert uow.notes.by_idempotency_key(key) == original
            assert uow.notes.latest_by_lesson(subject_id, source_id, "M2/C3") == original
            assert uow.notes.latest_by_lesson(subject_id, None, "M2/C3") is None
            assert uow.notes.latest_by_lesson(subject_id, source_id, "M2/C4") is None
            replacement = add_note(
                uow,
                subject_id,
                fixed,
                now + timedelta(minutes=1),
                source_id=source_id,
                supersedes=original.id,
            )
        assert replacement.supersedes_note_id == original.id
        assert replacement.idempotency_key != original.idempotency_key
        with uow_factory(user_id) as uow:
            assert uow.notes.latest_by_lesson(subject_id, source_id, "M2/C3") == replacement
            assert uow.notes.get(original.id) == original  # the old note stays
            assert [h.note_id for h in uow.notes.search("séptima", subject_id, 10)] == [
                replacement.id,
                original.id,
            ]

    def test_passages_window_is_one_based_and_inclusive(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        note = valid_note("Largo", *(long_paragraph(f"parte {n}.") for n in range(1, 5)))
        assert len(note.passages) == 4
        with uow_factory(user_id) as uow:
            subject_id = uow.subjects.create(sample_subject(), now).id
            row = add_note(uow, subject_id, note, now)
        with uow_factory(user_id) as uow:
            assert uow.notes.passage_count(row.id) == 4
            assert [p.seq for p in uow.notes.passages(row.id, 2, 3)] == [2, 3]
            assert [p.seq for p in uow.notes.passages(row.id, 4, 9)] == [4]
            assert uow.notes.passages(row.id, 5, 6) == ()
            assert uow.notes.passages(row.id, 1, 1) == (PassageRow(1, note.passages[0]),)

    def test_review_focus_5_insert_with_a_foreign_subject_or_source_raises_lookup_error(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        # Review Focus 5
        with uow_factory(other_user_id) as uow:
            theirs = uow.subjects.create(sample_subject("Theirs"), now).id
            their_source = uow.notes.upsert_source(theirs, valid_source(), now).id
            their_note = add_note(
                uow,
                theirs,
                valid_note("Theirs", long_paragraph("ajeno."), lesson_ref="L9"),
                now,
                source_id=their_source,
            )
        with uow_factory(user_id) as uow:
            mine = uow.subjects.create(sample_subject("Mine"), now).id
            other_subject = uow.subjects.create(sample_subject("Other"), now).id
            my_source = uow.notes.upsert_source(mine, valid_source(), now).id
            # Their rows are invisible, not only unwritable.
            assert uow.notes.by_idempotency_key(their_note.idempotency_key) is None
            assert uow.notes.source(their_source) is None
            assert uow.notes.latest_by_lesson(theirs, their_source, "L9") is None
            assert uow.notes.get(their_note.id) is None
        note = valid_note("Intento", long_paragraph("propio."), lesson_ref="L1")
        attempts: list[tuple[UUID, UUID | None, UUID | None]] = [
            (theirs, None, None),  # another user's subject
            (uuid4(), None, None),  # a missing subject
            (mine, their_source, None),  # another user's source
            (other_subject, my_source, None),  # a source of another subject of mine
            (mine, my_source, their_note.id),  # superseding another user's note
            (mine, my_source, uuid4()),  # superseding a missing note
        ]
        for subject_id, source_id, supersedes in attempts:
            with pytest.raises(LookupError), uow_factory(user_id) as uow:
                add_note(uow, subject_id, note, now, source_id=source_id, supersedes=supersedes)
        with uow_factory(user_id) as uow:
            assert uow.notes.search("propio", None, 10) == ()
            for subject_id in (mine, other_subject):
                assert uow.notes.latest_by_lesson(subject_id, None, "L1") is None
                assert uow.notes.latest_by_lesson(subject_id, my_source, "L1") is None
        with uow_factory(other_user_id) as uow:
            assert uow.notes.search("propio", None, 10) == ()
            assert [h.note_id for h in uow.notes.search("ajeno", None, 10)] == [their_note.id]
            assert uow.notes.by_idempotency_key(their_note.idempotency_key) == their_note
            assert uow.notes.latest_by_lesson(theirs, their_source, "L9") == their_note

    def test_concepts_merge_by_name_norm_within_a_subject(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            guitar = uow.subjects.create(sample_subject("Guitar"), now).id
            piano = uow.subjects.create(sample_subject("Piano"), now).id
            a = add_note(
                uow,
                guitar,
                valid_note("A", long_paragraph("uno."), concepts=["Pentatónica menor"]),
                now,
            )
            b = add_note(
                uow,
                guitar,
                valid_note("B", long_paragraph("dos."), concepts=["pentatonica  MENOR", "Blues"]),
                now,
            )
            c = add_note(
                uow,
                piano,
                valid_note("C", long_paragraph("tres."), concepts=["Pentatónica menor"]),
                now,
            )
        with uow_factory(user_id) as uow:
            [ca] = uow.notes.concepts(a.id)
            cb = uow.notes.concepts(b.id)
            [cc] = uow.notes.concepts(c.id)
        assert (ca.subject_id, ca.name, ca.name_norm) == (
            guitar,
            "Pentatónica menor",
            "pentatonica menor",
        )
        assert [x.name for x in cb] == ["Blues", "Pentatónica menor"]  # by name_norm
        assert cb[1].id == ca.id  # same subject, same name_norm: one concept, first spelling
        assert (cc.subject_id, cc.name) == (piano, "Pentatónica menor")
        assert cc.id != ca.id  # another subject: another concept

    # Artifacts

    def test_artifacts_round_trip_and_list_without_html(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        html = "<!doctype html><html><body><p>Canción</p></body></html>"
        later = now + timedelta(minutes=1)
        with uow_factory(user_id) as uow:
            subject_id = uow.subjects.create(sample_subject(), now).id
            note = add_note(uow, subject_id, valid_note("Clase", long_paragraph("texto.")), now)
            first = uow.notes.add_artifact(note.id, "lesson_view", "Vista", html, now)
            second = uow.notes.add_artifact(note.id, "other", "Otra", html + " ", later)
        assert first == ArtifactRow(
            id=first.id,
            note_id=note.id,
            kind="lesson_view",
            title="Vista",
            html=html,
            bytes=len(html.encode("utf-8")),
            created_at=now,
        )
        assert (second.kind, second.bytes, second.created_at) == ("other", first.bytes + 1, later)
        with uow_factory(user_id) as uow:
            assert uow.notes.artifacts(note.id) == (
                replace(first, html=None),
                replace(second, html=None),
            )
            assert uow.notes.artifact(first.id) == first
            assert uow.notes.artifact(uuid4()) is None
        with uow_factory(other_user_id) as uow:
            assert uow.notes.artifact(first.id) is None
            assert uow.notes.artifacts(note.id) == ()
            assert uow.notes.get(note.id) is None
            assert uow.notes.passage_count(note.id) == 0
            assert uow.notes.passages(note.id, 1, 10) == ()
            assert uow.notes.exercises(note.id) == ()
            assert uow.notes.quiz_items(note.id) == ()
            assert uow.notes.concepts(note.id) == ()
            with pytest.raises(LookupError):
                uow.notes.add_artifact(note.id, "other", "Ajena", html, now)
        with uow_factory(user_id) as uow:
            assert len(uow.notes.artifacts(note.id)) == 2

    # Search

    def test_search_fixture_orders_by_matches_depth_recency_and_subject(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        """Twelve notes over two subjects; the expected id order of each query is listed here
        and must hold on both backends (passages matched desc, then updated_at desc)."""
        m = timedelta(minutes=1)
        ids: dict[str, UUID] = {}
        with uow_factory(user_id) as uow:
            guitar = uow.subjects.create(sample_subject("Guitar"), now).id
            piano = uow.subjects.create(sample_subject("Piano"), now).id

            def put(key: str, subject_id: UUID, minute: int, title: str, *leads: str) -> None:
                note = valid_note(title, *(long_paragraph(lead) for lead in leads))
                ids[key] = add_note(uow, subject_id, note, now + minute * m).id

            put(
                "a1",
                guitar,
                1,
                "Acordes",
                "power chord con dos notas.",
                "power chord en el quinto traste.",
            )
            put(
                "a2",
                guitar,
                2,
                "Escalas",
                "escala pentatónica menor.",
                "la pentatónica para improvisar.",
            )
            put(
                "a3",
                guitar,
                3,
                "Dominantes",
                "acorde de séptima dominante.",
                "power chord con distorsión.",
            )
            put("a4", guitar, 4, "Posiciones", "acorde de séptima en el quinto traste.")
            put("a5", guitar, 5, "Afinación", "afinar la guitarra con afinador.")
            put("a6", guitar, 6, "Modos", "modos griegos: jónico y dórico.")
            put("b1", piano, 7, "Octavas", "power chord en el piano con octavas.")
            put("b2", piano, 8, "Dominantes en piano", "acorde de séptima en el piano.")
            put("b3", piano, 9, "Teclas negras", "escala pentatónica en teclas negras.")
            put("b4", piano, 10, "Pedal", "pedal de resonancia y dinámica.")
            put(
                "b5",
                piano,
                11,
                "Arpegios",
                "arpegio de séptima ascendente.",
                "arpegio de séptima descendente.",
            )
            put("b6", piano, 12, "Lectura", "lectura de partitura en clave de fa.")
        expected: list[tuple[str, UUID | None, int, list[str]]] = [
            ("power chord", None, 10, ["a1", "b1", "a3"]),
            ("séptima", None, 10, ["b5", "b2", "a4", "a3"]),
            ("séptima", None, 2, ["b5", "b2"]),
            ("pentatonica", None, 10, ["a2", "b3"]),
            ("quinto traste", None, 10, ["a4", "a1"]),
            ("power chord", piano, 10, ["b1"]),
            ("power chord", guitar, 10, ["a1", "a3"]),
            ("ukelele", None, 10, []),
        ]
        with uow_factory(user_id) as uow:
            for query, subject_id, limit, keys in expected:
                hits = uow.notes.search(query, subject_id, limit)
                assert [h.note_id for h in hits] == [ids[k] for k in keys], query
                assert all(h.concepts == () and h.depth is None and not h.fuzzy for h in hits)
            first, *_ = uow.notes.search("power chord", None, 10)
            assert first.passages == 2
            assert all(h.passages == 1 for h in uow.notes.search("quinto traste", None, 10))
            # Same data, same query, same order.
            assert uow.notes.search("séptima", None, 10) == uow.notes.search("séptima", None, 10)

    def test_search_reaches_notes_through_the_concept_graph(
        self, uow_factory: UowFactory, user_id: UUID, now: datetime
    ) -> None:
        """A `related` link is followed both ways at depth 1 and a `prerequisite` from the
        reached concept at depth 2; a third hop and an incoming prerequisite are not followed."""
        m = timedelta(minutes=1)
        with uow_factory(user_id) as uow:
            subject_id = uow.subjects.create(sample_subject("Drums"), now).id

            def put(minute: int, title: str, concept: str, *links: ConceptLinkInput) -> UUID:
                note = valid_note(
                    title, long_paragraph(f"contenido {title}."), concepts=[concept], links=links
                )
                return add_note(uow, subject_id, note, now + minute * m).id

            seed = put(1, "uno", "Ritmo")
            related = put(2, "dos", "Compás", ConceptLinkInput("Compás", "Ritmo", "related"))
            second = put(
                3, "tres", "Síncopa", ConceptLinkInput("Compás", "Síncopa", "prerequisite")
            )
            put(
                4, "cuatro", "Swing", ConceptLinkInput("Síncopa", "Swing", "prerequisite")
            )  # depth 3
            put(5, "cinco", "Metrónomo", ConceptLinkInput("Metrónomo", "Ritmo", "prerequisite"))
            # A cycle back to the seed must not loop or change depths; this newer note ties
            # with "dos" (one concept, depth 1) and wins on updated_at.
            cycle = put(6, "seis", "Compás", ConceptLinkInput("Ritmo", "Compás", "related"))
        with uow_factory(user_id) as uow:
            hits = uow.notes.search("ritmo", None, 10)
        assert [(h.note_id, h.passages, h.concepts, h.depth, h.fuzzy) for h in hits] == [
            (seed, 0, ("Ritmo",), 0, False),
            (cycle, 0, ("Compás",), 1, False),
            (related, 0, ("Compás",), 1, False),
            (second, 0, ("Síncopa",), 2, False),
        ]

    def test_search_is_scoped_to_the_user(
        self, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
    ) -> None:
        with uow_factory(user_id) as uow:
            subject_id = uow.subjects.create(sample_subject(), now).id
            mine = add_note(uow, subject_id, valid_note("Mía", long_paragraph("power chord.")), now)
        with uow_factory(other_user_id) as uow:
            assert uow.notes.search("power chord", None, 10) == ()
            assert uow.notes.search("power chord", subject_id, 10) == ()
        with uow_factory(user_id) as uow:
            assert [h.note_id for h in uow.notes.search("power chord", None, 10)] == [mine.id]
            assert uow.notes.search("power chord", uuid4(), 10) == ()
