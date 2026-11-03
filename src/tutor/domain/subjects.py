"""Subjects: validation and the onboarding questions (subjects spec 3.1, 3.2)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, TypeGuard

from tutor.domain.errors import FieldError

Preference = Literal["practice_first", "theory_first", "balanced"]
ExerciseKind = Literal["quiz", "ear", "exercise"]
SubjectStatus = Literal["active", "paused", "archived"]

PREFERENCES: tuple[Preference, ...] = ("practice_first", "theory_first", "balanced")
EXERCISE_KINDS: tuple[ExerciseKind, ...] = ("quiz", "ear", "exercise")
SUBJECT_STATUSES: tuple[SubjectStatus, ...] = ("active", "paused", "archived")
DEFAULT_EXERCISE_KINDS: tuple[ExerciseKind, ...] = ("quiz", "exercise")
DEFAULT_STATUS: SubjectStatus = "active"

NAME_MAX = 80
GOAL_TEXT_MAX = 300
CONTEXT_TEXT_MAX = 600
MINUTES_MIN = 10
MINUTES_MAX = 120
SESSIONS_MIN = 1
SESSIONS_MAX = 7
MIN_EXERCISE_KINDS = 1
MAX_EXERCISE_KINDS = len(EXERCISE_KINDS)

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Subject:
    name: str
    goal_text: str | None
    context_text: str | None
    preference: Preference
    exercise_kinds: tuple[ExerciseKind, ...]
    minutes_per_session: int
    sessions_per_week: int
    target_date: date | None
    status: SubjectStatus


@dataclass(frozen=True, slots=True)
class SubjectInput:
    """Answers as they arrive from outside; `exercise_kinds` empty means the default,
    `status` None means active."""

    name: str
    goal_text: str | None
    context_text: str | None
    preference: str
    exercise_kinds: Sequence[str]
    minutes_per_session: int
    sessions_per_week: int
    target_date: date | None
    status: str | None


def _is_preference(value: str) -> TypeGuard[Preference]:
    return value in PREFERENCES


def _is_exercise_kind(value: str) -> TypeGuard[ExerciseKind]:
    return value in EXERCISE_KINDS


def _is_status(value: str) -> TypeGuard[SubjectStatus]:
    return value in SUBJECT_STATUSES


def collapse_name(value: str) -> str:
    """Strip and collapse inner whitespace; for names and titles, never for free text."""
    return _SPACES.sub(" ", value).strip()


def _optional_text(
    value: str | None, limit: int, field: str, errors: list[FieldError]
) -> str | None:
    text = (value or "").strip()
    if len(text) > limit:
        errors.append(FieldError(field, "too_long"))
        return None
    return text or None


def _exercise_kinds(values: Sequence[str], errors: list[FieldError]) -> tuple[ExerciseKind, ...]:
    if not values:
        return DEFAULT_EXERCISE_KINDS
    if len(set(values)) != len(values) or not all(_is_exercise_kind(v) for v in values):
        errors.append(FieldError("exercise_kinds", "invalid_choice"))
        return ()
    if not MIN_EXERCISE_KINDS <= len(values) <= MAX_EXERCISE_KINDS:
        errors.append(FieldError("exercise_kinds", "too_many"))
        return ()
    return tuple(k for k in EXERCISE_KINDS if k in values)


def validate_subject(data: SubjectInput, today: date) -> Subject | tuple[FieldError, ...]:
    """Validate a subject; return it, or every field error in field order (ruling 13)."""
    errors: list[FieldError] = []
    name = collapse_name(data.name)
    if not name:
        errors.append(FieldError("name", "required"))
    elif len(name) > NAME_MAX:
        errors.append(FieldError("name", "too_long"))
    goal_text = _optional_text(data.goal_text, GOAL_TEXT_MAX, "goal_text", errors)
    context_text = _optional_text(data.context_text, CONTEXT_TEXT_MAX, "context_text", errors)
    preference_value = data.preference.strip()
    preference = preference_value if _is_preference(preference_value) else None
    if preference is None:
        errors.append(
            FieldError("preference", "required" if not preference_value else "invalid_choice")
        )
    exercise_kinds = _exercise_kinds(data.exercise_kinds, errors)
    if not MINUTES_MIN <= data.minutes_per_session <= MINUTES_MAX:
        errors.append(FieldError("minutes_per_session", "out_of_range"))
    if not SESSIONS_MIN <= data.sessions_per_week <= SESSIONS_MAX:
        errors.append(FieldError("sessions_per_week", "out_of_range"))
    if data.target_date is not None and data.target_date < today:
        errors.append(FieldError("target_date", "invalid"))
    status_value = DEFAULT_STATUS if data.status is None else data.status.strip()
    status = status_value if _is_status(status_value) else None
    if status is None:
        errors.append(FieldError("status", "required" if not status_value else "invalid_choice"))
    if errors or preference is None or status is None:
        return tuple(errors)
    return Subject(
        name=name,
        goal_text=goal_text,
        context_text=context_text,
        preference=preference,
        exercise_kinds=exercise_kinds,
        minutes_per_session=data.minutes_per_session,
        sessions_per_week=data.sessions_per_week,
        target_date=data.target_date,
        status=status,
    )


@dataclass(frozen=True, slots=True)
class QuestionOption:
    value: str
    label_es: str
    label_en: str
    description_es: str
    description_en: str


@dataclass(frozen=True, slots=True)
class SubjectQuestion:
    """One onboarding question for a subject. `options` are the allowed values of `fields[0]`
    when the field is an enum; free text, numbers and dates are described in the prompt."""

    id: str
    fields: tuple[str, ...]
    prompt_es: str
    prompt_en: str
    options: tuple[QuestionOption, ...] | None
    optional: bool


_PREFERENCE_OPTIONS: tuple[QuestionOption, ...] = (
    QuestionOption(
        "practice_first",
        "Practicar primero",
        "Practice first",
        "Empiezas haciendo; la teoría llega cuando la necesitas.",
        "You start by doing; the theory comes when you need it.",
    ),
    QuestionOption(
        "theory_first",
        "Entender la teoría primero",
        "Theory first",
        "Primero entiendes el porqué y luego practicas.",
        "You understand the why first, then you practice.",
    ),
    QuestionOption(
        "balanced",
        "Mitad y mitad",
        "Half and half",
        "Teoría y práctica en cada sesión.",
        "Theory and practice in every session.",
    ),
)

_KIND_OPTIONS: tuple[QuestionOption, ...] = (
    QuestionOption(
        "quiz",
        "Preguntas de teoría",
        "Theory questions",
        "Preguntas cortas sobre lo que dicen tus notas.",
        "Short questions about what your notes say.",
    ),
    QuestionOption(
        "ear",
        "Ejercicios de oído (música)",
        "Ear training (music)",
        "Identificar notas e intervalos que el servidor genera.",
        "Identify notes and intervals the server generates.",
    ),
    QuestionOption(
        "exercise",
        "Ejercicios del curso",
        "Exercises from the course",
        "Practicas un ejercicio de tus notas y reportas cómo te fue.",
        "You practice an exercise from your notes and report how it went.",
    ),
)

SUBJECT_QUESTIONS: tuple[SubjectQuestion, ...] = (
    SubjectQuestion(
        id="name",
        fields=("name",),
        prompt_es="¿Qué quieres aprender? Un nombre corto.",
        prompt_en="What do you want to learn? A short name.",
        options=None,
        optional=False,
    ),
    SubjectQuestion(
        id="goal",
        fields=("goal_text",),
        prompt_es="¿Para qué? Con tus palabras. (opcional)",
        prompt_en="What for? In your own words. (optional)",
        options=None,
        optional=True,
    ),
    SubjectQuestion(
        id="context",
        fields=("context_text",),
        prompt_es=(
            "¿En qué punto estás hoy? Nivel, experiencia, curso o material que sigues. (opcional)"
        ),
        prompt_en=(
            "Where are you today? Level, experience, course or material you follow. (optional)"
        ),
        options=None,
        optional=True,
    ),
    SubjectQuestion(
        id="preference",
        fields=("preference",),
        prompt_es=(
            "¿Cómo prefieres avanzar? Practicar primero, entender la teoría primero, "
            "o mitad y mitad."
        ),
        prompt_en=(
            "How do you prefer to move forward? Practice first, understand the theory first, "
            "or half and half."
        ),
        options=_PREFERENCE_OPTIONS,
        optional=False,
    ),
    SubjectQuestion(
        id="kinds",
        fields=("exercise_kinds",),
        prompt_es=(
            "¿Qué tipo de práctica quieres? Preguntas de teoría, ejercicios de oído (música), "
            "ejercicios del curso. Elige 1 a 3."
        ),
        prompt_en=(
            "What kind of practice do you want? Theory questions, ear training (music), "
            "exercises from the course. Pick 1 to 3."
        ),
        options=_KIND_OPTIONS,
        optional=False,
    ),
    SubjectQuestion(
        id="time",
        fields=("minutes_per_session", "sessions_per_week"),
        prompt_es=(
            "¿Cuántos minutos por sesión (de 10 a 120) y cuántas sesiones por semana (de 1 a 7)?"
        ),
        prompt_en="How many minutes per session (10 to 120) and how many sessions a week (1 to 7)?",
        options=None,
        optional=False,
    ),
    SubjectQuestion(
        id="target",
        fields=("target_date",),
        prompt_es="¿Tienes una fecha meta? (opcional)",
        prompt_en="Do you have a target date? (optional)",
        options=None,
        optional=True,
    ),
)
