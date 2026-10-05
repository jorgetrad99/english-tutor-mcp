"""end_session evidence validation, spec 11.1 steps 3-6 (requirements section 11)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tutor.domain.lesson import TaskResult
from tutor.domain.levels import LEVEL_VALUE, CefrLevel
from tutor.domain.text import count_words, find_turn, normalize

Category = Literal["grammar", "lexis", "word_order", "register", "other"]
Confidence = Literal["low", "medium", "high"]
SessionOutcome = Literal["closed", "incomplete"]

MIN_USER_WORDS = 30
LOW_TRUST_SHARE = 0.5
CEFR_EXCLUDE_DELTA = 1.0


@dataclass(frozen=True, slots=True)
class ReportedError:
    said: str
    correct: str
    category: Category


@dataclass(frozen=True, slots=True)
class Evidence:
    user_turns: tuple[str, ...]
    errors: tuple[ReportedError, ...]
    chunks_used: tuple[str, ...]
    task_result: TaskResult
    hints_given: int
    cefr_level: CefrLevel
    cefr_confidence: Confidence
    cefr_evidence: tuple[str, ...]
    confidence_1_5: int
    assistant_words_estimate: int | None


@dataclass(frozen=True, slots=True)
class ValidError:
    said: str
    correct: str
    correct_norm: str
    category: Category
    turn_index: int


@dataclass(frozen=True, slots=True)
class ValidatedEvidence:
    status: SessionOutcome
    low_trust: bool
    user_words: int
    turns: int
    errors: tuple[ValidError, ...]
    errors_reported: int
    errors_rejected: int
    chunks_used: tuple[str, ...]
    chunks_rejected: int
    cefr_excluded: bool


def _validate_errors(
    errors: Sequence[ReportedError], turns: Sequence[str]
) -> tuple[tuple[ValidError, ...], int]:
    valid: list[ValidError] = []
    rejected = 0
    for err in errors:
        index = find_turn(err.said, turns)
        if index is None:
            rejected += 1
            continue
        valid.append(
            ValidError(
                said=err.said,
                correct=err.correct,
                correct_norm=normalize(err.correct),
                category=err.category,
                turn_index=index,
            )
        )
    return tuple(valid), rejected


def _validate_chunks(used: Sequence[str], offered: Sequence[str]) -> tuple[tuple[str, ...], int]:
    allowed = set(offered)
    kept: list[str] = []
    rejected = 0
    for chunk_id in dict.fromkeys(used):  # dedupe, first occurrence order
        if chunk_id in allowed:
            kept.append(chunk_id)
        else:
            rejected += 1
    return tuple(kept), rejected


def validate_evidence(
    ev: Evidence,
    chunks_offered: Sequence[str],
    *,
    previous_cefr: CefrLevel | None,
    self_level: CefrLevel,
) -> ValidatedEvidence:
    """Drop unverifiable errors and chunks, decide status, low_trust and cefr_excluded."""
    turns = len(ev.user_turns)
    user_words = sum(count_words(turn) for turn in ev.user_turns)
    valid, errors_rejected = _validate_errors(ev.errors, ev.user_turns)
    reported = len(ev.errors)
    low_trust = reported > 0 and errors_rejected / reported > LOW_TRUST_SHARE
    chunks_used, chunks_rejected = _validate_chunks(ev.chunks_used, chunks_offered)
    reference = previous_cefr if previous_cefr is not None else self_level
    delta = abs(LEVEL_VALUE[ev.cefr_level] - LEVEL_VALUE[reference])
    return ValidatedEvidence(
        status="incomplete" if turns == 0 or user_words < MIN_USER_WORDS else "closed",
        low_trust=low_trust,
        user_words=user_words,
        turns=turns,
        errors=() if low_trust else valid,  # requirements 11: low_trust discards its errors
        errors_reported=reported,
        errors_rejected=errors_rejected,
        chunks_used=chunks_used,
        chunks_rejected=chunks_rejected,
        cefr_excluded=delta >= CEFR_EXCLUDE_DELTA,
    )
