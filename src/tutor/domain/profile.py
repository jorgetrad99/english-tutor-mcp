"""Learner profile: validation, plan-relevant changes and onboarding questions (spec 6.1-6.4)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, TypeGuard

from tutor.domain.levels import LEVEL_VALUE, CefrLevel, is_level

UseCase = Literal[
    "standup",
    "code_review",
    "interview",
    "client_call",
    "demo",
    "incident",
    "one_on_one",
    "async_writing",
]
Domain = Literal["it"]

USE_CASES: tuple[UseCase, ...] = (
    "standup",
    "code_review",
    "interview",
    "client_call",
    "demo",
    "incident",
    "one_on_one",
    "async_writing",
)
DOMAINS: tuple[Domain, ...] = ("it",)
MINUTES_CHOICES: tuple[int, ...] = (15, 20, 30)
MIN_DAYS_PER_WEEK = 2
MAX_DAYS_PER_WEEK = 7
MIN_USE_CASES = 1
MAX_USE_CASES = 4
TARGET_MIN_DAYS = 28
TARGET_MAX_DAYS = 364
GOAL_TEXT_MAX = 300
DEFAULT_TIMEZONE = "America/Mexico_City"

ProfileErrorCode = Literal[
    "required",
    "invalid_choice",
    "too_few",
    "too_many",
    "out_of_range",
    "below_current_level",
    "too_long",
    "invalid_timezone",
]


@dataclass(frozen=True, slots=True)
class Profile:
    self_level: CefrLevel
    domains: tuple[Domain, ...]
    use_cases: tuple[UseCase, ...]
    minutes_per_day: int
    days_per_week: int
    target_level: CefrLevel
    target_date: date | None
    goal_text: str | None
    timezone: str


@dataclass(frozen=True, slots=True)
class ProfileInput:
    self_level: str
    domains: Sequence[str]
    use_cases: Sequence[str]
    minutes_per_day: int
    days_per_week: int
    target_level: str
    target_date: date | None = None
    goal_text: str | None = None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    code: ProfileErrorCode


def _is_use_case(value: str) -> TypeGuard[UseCase]:
    return value in USE_CASES


def _is_domain(value: str) -> TypeGuard[Domain]:
    return value in DOMAINS


def _level(value: str, field: str, errors: list[FieldError]) -> CefrLevel | None:
    if not value.strip():
        errors.append(FieldError(field, "required"))
        return None
    if not is_level(value):
        errors.append(FieldError(field, "invalid_choice"))
        return None
    return value


def _domains(values: Sequence[str], errors: list[FieldError]) -> tuple[Domain, ...]:
    if not values:
        errors.append(FieldError("domains", "required"))
        return ()
    if len(set(values)) != len(values) or not all(_is_domain(v) for v in values):
        errors.append(FieldError("domains", "invalid_choice"))
        return ()
    return tuple(d for d in DOMAINS if d in values)


def _use_cases(values: Sequence[str], errors: list[FieldError]) -> tuple[UseCase, ...]:
    if len(values) < MIN_USE_CASES:
        errors.append(FieldError("use_cases", "too_few"))
        return ()
    if len(set(values)) != len(values) or not all(_is_use_case(v) for v in values):
        errors.append(FieldError("use_cases", "invalid_choice"))
        return ()
    if len(values) > MAX_USE_CASES:
        errors.append(FieldError("use_cases", "too_many"))
        return ()
    return tuple(u for u in USE_CASES if u in values)


def _goal_text(value: str | None, errors: list[FieldError]) -> str | None:
    text = (value or "").strip()
    if len(text) > GOAL_TEXT_MAX:
        errors.append(FieldError("goal_text", "too_long"))
        return None
    return text or None


def validate_profile(
    raw: ProfileInput,
    today: date,
    *,
    valid_timezones: frozenset[str],
    current_timezone: str = DEFAULT_TIMEZONE,
) -> Profile | tuple[FieldError, ...]:
    """Validate onboarding answers; return a Profile or every field error, in field order."""
    errors: list[FieldError] = []
    self_level = _level(raw.self_level, "self_level", errors)
    domains = _domains(raw.domains, errors)
    use_cases = _use_cases(raw.use_cases, errors)
    if raw.minutes_per_day not in MINUTES_CHOICES:
        errors.append(FieldError("minutes_per_day", "invalid_choice"))
    if not MIN_DAYS_PER_WEEK <= raw.days_per_week <= MAX_DAYS_PER_WEEK:
        errors.append(FieldError("days_per_week", "out_of_range"))
    target_level = _level(raw.target_level, "target_level", errors)
    if (
        self_level is not None
        and target_level is not None
        and LEVEL_VALUE[target_level] < LEVEL_VALUE[self_level]
    ):
        errors.append(FieldError("target_level", "below_current_level"))
    if raw.target_date is not None and not (
        TARGET_MIN_DAYS <= (raw.target_date - today).days <= TARGET_MAX_DAYS
    ):
        errors.append(FieldError("target_date", "out_of_range"))
    goal_text = _goal_text(raw.goal_text, errors)
    timezone = (raw.timezone or "").strip() or current_timezone
    if timezone not in valid_timezones:
        errors.append(FieldError("timezone", "invalid_timezone"))
    if errors or self_level is None or target_level is None:
        return tuple(errors)
    return Profile(
        self_level=self_level,
        domains=domains,
        use_cases=use_cases,
        minutes_per_day=raw.minutes_per_day,
        days_per_week=raw.days_per_week,
        target_level=target_level,
        target_date=raw.target_date,
        goal_text=goal_text,
        timezone=timezone,
    )


def plan_inputs_changed(old: Profile | None, new: Profile) -> bool:
    """True when the plan must be regenerated: no old profile or a plan input differs."""
    if old is None:
        return True
    return (
        old.self_level,
        old.domains,
        old.use_cases,
        old.minutes_per_day,
        old.days_per_week,
        old.target_level,
        old.target_date,
    ) != (
        new.self_level,
        new.domains,
        new.use_cases,
        new.minutes_per_day,
        new.days_per_week,
        new.target_level,
        new.target_date,
    )


@dataclass(frozen=True, slots=True)
class OnboardingOption:
    value: str
    label_en: str
    label_es: str
    description_en: str
    description_es: str


@dataclass(frozen=True, slots=True)
class OnboardingQuestion:
    """One onboarding question. `options` are the allowed values of `fields[0]`; any other
    field is described in the prompt (free date, free text or a numeric range)."""

    id: str
    fields: tuple[str, ...]
    prompt_en: str
    prompt_es: str
    options: tuple[OnboardingOption, ...]
    multi: bool
    min_choices: int
    max_choices: int


def _level_options() -> tuple[OnboardingOption, ...]:
    return (
        OnboardingOption(
            "B1",
            "B1, intermediate",
            "B1, intermedio",
            "You follow a standup and say what you did, but you often stop to find words.",
            "Entiendes un standup y dices qué hiciste, pero seguido te detienes a buscar palabras.",
        ),
        OnboardingOption(
            "B1+",
            "B1+, strong intermediate",
            "B1+, intermedio alto",
            "Routine meetings are fine; explaining something new or unexpected is still hard.",
            "Te defiendes en juntas de rutina; explicar algo nuevo o inesperado todavía te cuesta.",
        ),
        OnboardingOption(
            "B2",
            "B2, upper intermediate",
            "B2, intermedio avanzado",
            "You explain technical ideas and give opinions, with some mistakes and pauses.",
            "Explicas ideas técnicas y das tu opinión, con algunos errores y pausas.",
        ),
        OnboardingOption(
            "B2+",
            "B2+, strong upper intermediate",
            "B2+, intermedio avanzado alto",
            "You discuss and disagree with ease; you want to sound more natural and precise.",
            "Discutes y das tu desacuerdo con soltura; quieres sonar más natural y preciso.",
        ),
        OnboardingOption(
            "C1",
            "C1, advanced",
            "C1, avanzado",
            "You work in English without effort; you want polish for interviews and clients.",
            "Trabajas en inglés sin esfuerzo; quieres pulirlo para entrevistas y clientes.",
        ),
    )


_USE_CASE_OPTIONS: tuple[OnboardingOption, ...] = (
    OnboardingOption(
        "standup",
        "Daily standup",
        "Daily standup",
        "Short daily updates: what you did, what is next, what blocks you.",
        "Actualizaciones diarias cortas: qué hiciste, qué sigue y qué te bloquea.",
    ),
    OnboardingOption(
        "code_review",
        "Code review",
        "Revisión de código",
        "Comments on pull requests, in writing or on a call.",
        "Comentarios en pull requests, por escrito o en una llamada.",
    ),
    OnboardingOption(
        "interview",
        "Job interview",
        "Entrevista de trabajo",
        "Talking about your experience and discussing an offer.",
        "Hablar de tu experiencia y negociar una oferta.",
    ),
    OnboardingOption(
        "client_call",
        "Client call",
        "Llamada con cliente",
        "Calls with clients or stakeholders: requirements, status and changes.",
        "Llamadas con clientes o stakeholders: requerimientos, avances y cambios.",
    ),
    OnboardingOption(
        "demo",
        "Demo or presentation",
        "Demo o presentación",
        "Showing your work and answering questions about it.",
        "Mostrar tu trabajo y responder preguntas sobre él.",
    ),
    OnboardingOption(
        "incident",
        "Incident or outage",
        "Incidente o caída",
        "Explaining what broke, asking for help and giving updates under pressure.",
        "Explicar qué falló, pedir ayuda y dar avances bajo presión.",
    ),
    OnboardingOption(
        "one_on_one",
        "One-on-one",
        "One-on-one con tu líder",
        "Feedback, goals and career talks with your manager.",
        "Feedback, metas y pláticas de carrera con tu líder.",
    ),
    OnboardingOption(
        "async_writing",
        "Async writing",
        "Escritura asíncrona",
        "Slack messages, emails, tickets and pull request descriptions.",
        "Mensajes de Slack, correos, tickets y descripciones de pull requests.",
    ),
)

_MINUTES_OPTIONS: tuple[OnboardingOption, ...] = (
    OnboardingOption(
        "15",
        "15 minutes a day",
        "15 minutos al día",
        "A short daily habit.",
        "Un hábito diario corto.",
    ),
    OnboardingOption(
        "20",
        "20 minutes a day",
        "20 minutos al día",
        "A full lesson.",
        "Una lección completa.",
    ),
    OnboardingOption(
        "30",
        "30 minutes a day",
        "30 minutos al día",
        "A full lesson with extra practice.",
        "Una lección completa con práctica extra.",
    ),
)

ONBOARDING_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="level",
        fields=("self_level",),
        prompt_en="How would you describe your English today?",
        prompt_es="¿Cómo describirías tu inglés hoy?",
        options=_level_options(),
        multi=False,
        min_choices=1,
        max_choices=1,
    ),
    OnboardingQuestion(
        id="field",
        fields=("domains",),
        prompt_en="What field do you work in?",
        prompt_es="¿En qué área trabajas?",
        options=(
            OnboardingOption(
                "it",
                "Software and IT",
                "Software y TI",
                "Developers, QA, DevOps, data and other IT roles.",
                "Desarrollo, QA, DevOps, datos y otros roles de TI.",
            ),
        ),
        multi=True,
        min_choices=1,
        max_choices=len(DOMAINS),
    ),
    OnboardingQuestion(
        id="use_cases",
        fields=("use_cases",),
        prompt_en="Where do you need English at work? Pick 1 to 4.",
        prompt_es="¿En qué situaciones del trabajo necesitas inglés? Elige de 1 a 4.",
        options=_USE_CASE_OPTIONS,
        multi=True,
        min_choices=MIN_USE_CASES,
        max_choices=MAX_USE_CASES,
    ),
    OnboardingQuestion(
        id="time",
        fields=("minutes_per_day", "days_per_week"),
        prompt_en=(
            "How many minutes a day (15, 20 or 30) and how many days a week (2 to 7) "
            "can you practice?"
        ),
        prompt_es=(
            "¿Cuántos minutos al día (15, 20 o 30) y cuántos días a la semana (de 2 a 7) "
            "puedes practicar?"
        ),
        options=_MINUTES_OPTIONS,
        multi=False,
        min_choices=1,
        max_choices=1,
    ),
    OnboardingQuestion(
        id="goal",
        fields=("target_level", "target_date", "goal_text"),
        prompt_en=(
            "What level do you want to reach (your current level or higher)? Optionally, by "
            "when (4 weeks to 1 year from today) and why, in one sentence."
        ),
        prompt_es=(
            "¿Qué nivel quieres alcanzar (tu nivel actual o uno más alto)? Si quieres, dinos "
            "para cuándo (de 4 semanas a 1 año a partir de hoy) y por qué, en una frase."
        ),
        options=_level_options(),
        multi=False,
        min_choices=1,
        max_choices=1,
    ),
)
