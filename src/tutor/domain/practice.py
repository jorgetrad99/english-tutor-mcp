"""Practice sessions: item count, mix, deterministic drawing, grading glue and the session
summary (subjects spec 6.2 amended by 15, 15.4.1, 15.8; slice B plan rulings 31-36 as amended
by slice A Task 4b's "Effect on slice B").

Every practice item is one showing of a stored template instance. Authored instances (`quiz`,
`exercise`) are picked least recently shown first and never twice in one session; a `drill` is
a showing of a pool instance, which may come back within a session with a new draw that avoids
the entries already asked while unused ones remain. The server ships no item banks: the domain
knows practice kinds, grading kinds and draws, never what a cue means.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, TypeGuard
from uuid import UUID

from tutor.domain.errors import FieldError
from tutor.domain.subject_metrics import round_half_up
from tutor.domain.subjects import EXERCISE_KINDS, ExerciseKind, Preference
from tutor.domain.templates import (
    Answer,
    ChoiceAnswer,
    Draw,
    Graded,
    GradingKind,
    NumberAnswer,
    SelfReportAnswer,
    SequenceAnswer,
    TemplateSpec,
    ValueAnswer,
    ValuesAnswer,
    draw,
    grade,
    parse_answer,
    permutation,
    seed_for,
)

PracticeMode = Literal["app", "text", "class"]
PracticeStatus = Literal["open", "closed", "incomplete"]
ItemSource = Literal["app", "chat"]
PRACTICE_MODES: tuple[PracticeMode, ...] = ("app", "text", "class")
PRACTICE_STATUSES: tuple[PracticeStatus, ...] = ("open", "closed", "incomplete")
ITEM_SOURCES: tuple[ItemSource, ...] = ("app", "chat")

ITEM_COUNT_MIN = 4
ITEM_COUNT_MAX = 12
MINUTES_PER_ITEM = 3
SELF_RATING_MIN = 1
SELF_RATING_MAX = 4

# Spec 6.2 shares in percent, in the order that breaks ties; `drill` takes the shares `ear`
# had (slice A Task 4b, "Effect on slice B", ruling 34).
MIX: Mapping[Preference, tuple[tuple[ExerciseKind, int], ...]] = {
    "practice_first": (("exercise", 60), ("quiz", 20), ("drill", 20)),
    "theory_first": (("quiz", 50), ("drill", 30), ("exercise", 20)),
    "balanced": (("quiz", 40), ("drill", 30), ("exercise", 30)),
}


def item_count(minutes_per_session: int) -> int:
    """`clamp(round(minutes / 3), 4, 12)` (ruling 35); a third never ends in .5, so integer
    rounding is exact."""
    rounded = (2 * minutes_per_session + MINUTES_PER_ITEM) // (2 * MINUTES_PER_ITEM)
    return max(ITEM_COUNT_MIN, min(ITEM_COUNT_MAX, rounded))


# --- candidates and the mix ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """An active exercise instance of the subject, with its template's spec, its canonical
    parameters and the last time one of its showings was handed out (None: never)."""

    instance_id: UUID
    spec: TemplateSpec
    params: Mapping[str, object]
    last_shown_at: datetime | None

    @property
    def practice_kind(self) -> ExerciseKind | None:
        return self.spec.practice_kind


def schedulable(
    candidates: Sequence[Candidate], enabled: Collection[ExerciseKind], *, text_mode: bool
) -> tuple[Candidate, ...]:
    """The candidates a session may draw: an enabled practice kind, and in text mode only
    instances whose template does not need the view (ruling 34, amended)."""
    return tuple(
        c
        for c in candidates
        if c.practice_kind is not None
        and c.practice_kind in enabled
        and not (text_mode and c.spec.needs_view)
    )


def capacities(candidates: Sequence[Candidate]) -> dict[ExerciseKind, int | None]:
    """How many items each kind can give one session: authored kinds one per instance, a
    drill without limit (a pool instance can be shown again with a new draw). Kinds with no
    candidate are absent."""
    result: dict[ExerciseKind, int | None] = {}
    for c in candidates:
        kind = c.practice_kind
        if kind is None:
            continue
        if kind == "drill":
            result[kind] = None
        else:
            current = result.get(kind) or 0
            result[kind] = current + 1
    return result


@dataclass(frozen=True, slots=True)
class Mix:
    counts: Mapping[ExerciseKind, int]
    short: bool  # fewer than ITEM_COUNT_MIN items in all (ruling 35)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _split(count: int, shares: Sequence[tuple[ExerciseKind, int]]) -> dict[ExerciseKind, int]:
    """`count` split by share, largest remainders first, ties in share order."""
    whole = sum(share for _, share in shares)
    floors = {kind: count * share // whole for kind, share in shares}
    order = sorted(range(len(shares)), key=lambda i: (-(count * shares[i][1] % whole), i))
    for i in order[: count - sum(floors.values())]:
        floors[shares[i][0]] += 1
    return floors


def plan_mix(
    preference: Preference, capacity: Mapping[ExerciseKind, int | None], count: int
) -> Mix:
    """Items per kind (spec 6.2, ruling 35): the preference's shares over the kinds that have
    items, renormalised; a kind that has fewer items than its share gives the shortfall to the
    others, until the count is reached or every kind is used up."""
    limits = {k: v for k, v in capacity.items() if v is None or v > 0}
    open_shares = [(kind, share) for kind, share in MIX[preference] if kind in limits]
    fixed: dict[ExerciseKind, int] = {}
    remaining = count
    while open_shares and remaining > 0:
        split = _split(remaining, open_shares)
        over = {
            kind: limit
            for kind, n in split.items()
            if (limit := limits[kind]) is not None and n > limit
        }
        if not over:
            fixed.update(split)
            break
        fixed.update(over)
        remaining -= sum(over.values())
        open_shares = [(k, s) for k, s in open_shares if k not in over]
    counts = {kind: fixed[kind] for kind, _ in MIX[preference] if fixed.get(kind, 0) > 0}
    return Mix(counts=counts, short=sum(counts.values()) < ITEM_COUNT_MIN)


# --- drawing ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DrawnItem:
    """One showing to store: its id (whose seed drives the draw), its place in the session, the
    instance, and the stored seed and draw that grading reads back (spec 15.4.1)."""

    item_id: UUID
    seq: int
    instance_id: UUID
    practice_kind: ExerciseKind | None
    grading_kind: GradingKind
    draw_seed: int
    draw: Draw


def _recency(c: Candidate) -> tuple[bool, datetime | None, UUID]:
    return (c.last_shown_at is not None, c.last_shown_at, c.instance_id)


def used_entries(spec: TemplateSpec, shown: Draw) -> frozenset[int]:
    """The pool entries a showing asked: the cue entry of `one` and `one_of_choices` (the
    other options are distractors), every entry of an ordering."""
    if spec.pool_draw in ("one", "one_of_choices") and shown.entries:
        return frozenset({shown.entries[0]})
    if spec.pool_draw == "ordered":
        return frozenset(shown.entries)
    return frozenset()


def _pool_size(spec: TemplateSpec, params: Mapping[str, object]) -> int:
    pools = spec.by_role("pool")
    value = params.get(pools[0].name) if pools else None
    return len(value) if isinstance(value, list) else 0


def avoid_for(
    spec: TemplateSpec, params: Mapping[str, object], prior: Sequence[Draw]
) -> frozenset[int]:
    """The entries the next showing of this pool instance avoids, given the session's earlier
    draws of it in order: what was asked since every entry was last asked (a cycle)."""
    size = _pool_size(spec, params)
    asked: set[int] = set()
    for shown in prior:
        asked |= used_entries(spec, shown)
        if size and len(asked) >= size:
            asked = set()
    return frozenset(asked)


def draw_showing(
    candidate: Candidate, item_id: UUID, *, seq: int, avoid: frozenset[int] = frozenset()
) -> DrawnItem:
    """One showing of an instance, drawn with `seed_for(item_id)`."""
    seed = seed_for(item_id)
    return DrawnItem(
        item_id=item_id,
        seq=seq,
        instance_id=candidate.instance_id,
        practice_kind=candidate.practice_kind,
        grading_kind=candidate.spec.grading_kind,
        draw_seed=seed,
        draw=draw(candidate.spec, candidate.params, seed, avoid),
    )


def draw_items(
    session_id: UUID, mix: Mix, candidates: Sequence[Candidate], item_ids: Sequence[UUID]
) -> tuple[DrawnItem, ...]:
    """The session's showings (ruling 31, amended): for each kind of the mix, authored instances
    least recently shown first, never twice; drills rotate over the pool instances least
    recently shown first. The order of the showings is a permutation seeded by the session id;
    `item_ids[k]` is the id of the showing at seq k + 1, and a drill's draw avoids the entries
    its instance already asked earlier in the session. ValueError when the candidates cannot
    fill the mix or the ids do not match its total."""
    if len(item_ids) != mix.total:
        raise ValueError("one item id is needed per item")
    slots: list[Candidate] = []
    for kind in EXERCISE_KINDS:
        wanted = mix.counts.get(kind, 0)
        if not wanted:
            continue
        pool = sorted((c for c in candidates if c.practice_kind == kind), key=_recency)
        if not pool or (kind != "drill" and len(pool) < wanted):
            raise ValueError("the candidates cannot fill the mix")
        slots += [pool[k % len(pool)] for k in range(wanted)]
    order = permutation(len(slots), seed_for(session_id))
    prior: dict[UUID, list[Draw]] = {}
    items: list[DrawnItem] = []
    for seq, (position, item_id) in enumerate(zip(order, item_ids, strict=True), start=1):
        candidate = slots[position]
        earlier = prior.setdefault(candidate.instance_id, [])
        avoid = avoid_for(candidate.spec, candidate.params, earlier)
        item = draw_showing(candidate, item_id, seq=seq, avoid=avoid)
        earlier.append(item.draw)
        items.append(item)
    return tuple(items)


# --- grading glue -------------------------------------------------------------------------------


def option_count(spec: TemplateSpec, params: Mapping[str, object], shown: Draw) -> int:
    """How many options a choice showing printed: the draw's for a pool, the options
    parameter's for an authored choice."""
    if spec.pool_draw == "one_of_choices":
        return len(shown.order)
    options = params.get(spec.options_param or "")
    return len(options) if isinstance(options, list) else 0


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _choice(
    spec: TemplateSpec,
    params: Mapping[str, object],
    shown: Draw,
    data: Mapping[str, object],
    *,
    from_view: bool,
) -> Answer | tuple[FieldError, ...]:
    has_number, has_index = "option_number" in data, "option_index" in data
    if has_number == has_index:
        return (FieldError("answer", "invalid"),)
    count = option_count(spec, params, shown)
    if has_number:
        field, value = "answer.option_number", data["option_number"]
        if _is_int(value):
            return ChoiceAnswer(value - 1) if 1 <= value <= count else _out(field)
        return (FieldError(field, "invalid"),)
    field, value = "answer.option_index", data["option_index"]
    if from_view and _is_int(value):
        return ChoiceAnswer(value) if 0 <= value < count else _out(field)
    return (FieldError(field, "invalid"),)


def _out(field: str) -> tuple[FieldError, ...]:
    return (FieldError(field, "out_of_range"),)


def parse_showing_answer(
    spec: TemplateSpec,
    params: Mapping[str, object],
    shown: Draw,
    data: Mapping[str, object],
    *,
    from_view: bool,
) -> Answer | tuple[FieldError, ...]:
    """The answer to one showing (spec 15.8). A choice is answered in chat with the 1-based
    `option_number` printed beside the option, which the server converts; a 0-based
    `option_index` is accepted only from the view (slice A plan ruling 36). Both are checked
    against the options this showing printed. Other kinds go to `templates.parse_answer`."""
    if spec.grading_kind == "choice_index" and data.get("answer_kind") == "choice_index":
        return _choice(spec, params, shown, data, from_view=from_view)
    return parse_answer(spec.grading_kind, data)


def grade_showing(
    spec: TemplateSpec, params: Mapping[str, object], shown: Draw, answer: Answer
) -> Graded | tuple[FieldError, ...]:
    """`templates.grade` against the stored instance and the showing's stored draw: never a
    recomputed draw (spec 15.4.1)."""
    return grade(spec, params, shown, answer)


def _number_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def answer_to_json(answer: Answer) -> dict[str, object]:
    """The stored `practice_items.answer`: the answer's shape as plain JSON."""
    if isinstance(answer, ChoiceAnswer):
        return {"answer_kind": "choice_index", "option_index": answer.option_index}
    if isinstance(answer, ValueAnswer):
        return {"answer_kind": "value", "value": answer.value}
    if isinstance(answer, ValuesAnswer):
        return {"answer_kind": "values", "values": list(answer.values)}
    if isinstance(answer, SequenceAnswer):
        return {"answer_kind": "sequence", "values": list(answer.values)}
    if isinstance(answer, NumberAnswer):
        return {"answer_kind": "number", "value": _number_text(answer.value)}
    report: SelfReportAnswer = answer
    return {
        "answer_kind": "self_report",
        "rating": report.rating,
        "tempo_bpm": report.tempo_bpm,
        "minutes": report.minutes,
    }


# --- the session summary ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SummaryItem:
    """One showing as the summary sees it; `score` None means unanswered."""

    practice_kind: ExerciseKind | None
    correct: bool | None
    score: int | None


@dataclass(frozen=True, slots=True)
class KindResult:
    practice_kind: ExerciseKind
    items: int
    answered: int
    correct: int
    mean_score: int | None  # rounded half up, self-report scores included


@dataclass(frozen=True, slots=True)
class SessionSummary:
    items: int
    answered: int
    unanswered: int
    by_kind: tuple[KindResult, ...]  # in EXERCISE_KINDS order, kinds present only
    minutes: int  # whole minutes from start to end, truncated


def summarise(
    items: Sequence[SummaryItem], started_at: datetime, ended_at: datetime
) -> SessionSummary:
    """`end_practice`'s summary (spec 6.2): answered and unanswered showings (ruling 36),
    per kind, and the session's minutes on the server clock."""
    by_kind: list[KindResult] = []
    for kind in EXERCISE_KINDS:
        mine = [i for i in items if i.practice_kind == kind]
        if not mine:
            continue
        scores = [i.score for i in mine if i.score is not None]
        by_kind.append(
            KindResult(
                practice_kind=kind,
                items=len(mine),
                answered=len(scores),
                correct=sum(1 for i in mine if i.correct is True),
                mean_score=round_half_up(sum(scores), len(scores)) if scores else None,
            )
        )
    answered = sum(1 for i in items if i.score is not None)
    return SessionSummary(
        items=len(items),
        answered=answered,
        unanswered=len(items) - answered,
        by_kind=tuple(by_kind),
        minutes=max(0, (ended_at - started_at) // timedelta(minutes=1)),
    )
