"""Perfil: the onboarding answers and the current plan-lite (core loop v0 spec 6.3 and 12)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import Lang, User
from tutor.domain.plan_lite import feasibility_text
from tutor.domain.profile import (
    DEFAULT_TIMEZONE,
    GOAL_TEXT_MAX,
    ONBOARDING_QUESTIONS,
    TARGET_MAX_DAYS,
    TARGET_MIN_DAYS,
    ProfileInput,
    validate_profile,
)
from tutor.services.errors import ServiceError
from tutor.services.views import ProfileView
from tutor.web.deps import APP_ROUTER_DEPS, current_user
from tutor.web.profile import PROFILE_PATH, get_profiles, iana_zones
from tutor.web.views import is_htmx, render, request_lang, today_for

router = APIRouter(dependencies=APP_ROUTER_DEPS)
DAYS_CHOICES = tuple(range(2, 8))
QUESTIONS = {q.id: q for q in ONBOARDING_QUESTIONS}

# (field, ProfileErrorCode) -> message id; the template maps each id to an es-MX msgid.
MESSAGE_FOR: dict[tuple[str, str], str] = {
    ("self_level", "required"): "level",
    ("self_level", "invalid_choice"): "level",
    ("domains", "required"): "field",
    ("domains", "invalid_choice"): "field",
    ("use_cases", "too_few"): "use_cases_few",
    ("use_cases", "too_many"): "use_cases_many",
    ("use_cases", "invalid_choice"): "use_cases_choice",
    ("minutes_per_day", "invalid_choice"): "minutes",
    ("days_per_week", "out_of_range"): "days",
    ("target_level", "required"): "target_choice",
    ("target_level", "invalid_choice"): "target_choice",
    ("target_level", "below_current_level"): "target_below",
    ("target_date", "out_of_range"): "date",
    ("goal_text", "too_long"): "goal_long",
    ("timezone", "invalid_timezone"): "timezone",
}
FIELD_FALLBACK: dict[str, str] = {
    "self_level": "level",
    "domains": "field",
    "use_cases": "use_cases_choice",
    "minutes_per_day": "minutes",
    "days_per_week": "days",
    "target_level": "target_choice",
    "target_date": "date",
    "goal_text": "goal_long",
    "timezone": "timezone",
}


def _int(raw: str) -> int:
    text = raw.strip()
    return int(text) if text.isdecimal() and len(text) <= 3 else -1


def _date(raw: str) -> date | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return date.min  # fails the 28-364 day rule, so the field shows its own message


_TZ_ECHO_MAX = 64  # longest IANA name is well below this
_DATE_ECHO_MAX = 10  # YYYY-MM-DD


def _form_values(
    *,
    self_level: str,
    domains: list[str] | None,
    use_cases: list[str] | None,
    minutes_per_day: str,
    days_per_week: str,
    target_level: str,
    target_date: str,
    goal_text: str,
    timezone: str,
) -> dict[str, Any]:
    """The submitted answers as the page redisplays them: learner text stays text and nothing is
    echoed past its cap (the service still receives goal_text whole, so it can refuse it)."""
    return {
        "self_level": self_level.strip(),
        "domains": list(domains or []),
        "use_cases": list(use_cases or []),
        "minutes_per_day": minutes_per_day.strip(),
        "days_per_week": days_per_week.strip(),
        "target_level": target_level.strip(),
        "target_date": target_date.strip()[:_DATE_ECHO_MAX],
        "goal_text": goal_text.strip(),
        "timezone": timezone.strip()[:_TZ_ECHO_MAX],
    }


def _saved_values(view: ProfileView) -> dict[str, Any]:
    p = view.profile
    if p is None:
        return {
            "self_level": "",
            "domains": ["it"],
            "use_cases": [],
            "minutes_per_day": "",
            "days_per_week": "",
            "target_level": "",
            "target_date": "",
            "goal_text": "",
            "timezone": DEFAULT_TIMEZONE,
        }
    return {
        "self_level": p.self_level,
        "domains": list(p.domains),
        "use_cases": list(p.use_cases),
        "minutes_per_day": str(p.minutes_per_day),
        "days_per_week": str(p.days_per_week),
        "target_level": p.target_level,
        "target_date": p.target_date.isoformat() if p.target_date else "",
        "goal_text": p.goal_text or "",
        "timezone": p.timezone,
    }


def _to_input(values: Mapping[str, Any]) -> ProfileInput:
    return ProfileInput(
        self_level=values["self_level"].strip(),
        domains=list(values["domains"]),
        use_cases=list(values["use_cases"]),
        minutes_per_day=_int(values["minutes_per_day"]),
        days_per_week=_int(values["days_per_week"]),
        target_level=values["target_level"].strip(),
        target_date=_date(values["target_date"]),
        goal_text=values["goal_text"] or None,
        timezone=values["timezone"].strip() or None,
    )


def _messages(
    raw: ProfileInput, fields: tuple[str, ...], today: date, user: User
) -> dict[str, str]:
    """Message ids per field. Validity was decided by save_profile; validate_profile (the same
    domain rule) is called only to learn each field's error code (plan ruling, Task 25)."""
    checked = validate_profile(
        raw, today, valid_timezones=iana_zones(), current_timezone=user.timezone
    )
    errors: dict[str, str] = {}
    if isinstance(checked, tuple):
        for error in checked:
            errors.setdefault(error.field, MESSAGE_FOR.get((error.field, error.code), "field"))
    for name in fields:
        errors.setdefault(name, FIELD_FALLBACK.get(name, "field"))
    return errors


def _context(
    request: Request,
    view: ProfileView,
    values: Mapping[str, Any],
    errors: Mapping[str, str],
    *,
    saved: bool,
) -> dict[str, Any]:
    today = today_for(request)
    plan = view.plan
    lang: Literal["en", "es"] = "en" if request_lang(request) is Lang.EN else "es"
    shown = {**values, "goal_text": values["goal_text"][:GOAL_TEXT_MAX]}
    return {
        "view": view,
        "plan": plan,
        "q": QUESTIONS,
        "values": shown,
        "errors": errors,
        "saved": saved,
        "days_choices": DAYS_CHOICES,
        "date_min": (today + timedelta(days=TARGET_MIN_DAYS)).isoformat(),
        "date_max": (today + timedelta(days=TARGET_MAX_DAYS)).isoformat(),
        "goal_max": GOAL_TEXT_MAX,
        "feasibility": feasibility_text(plan.feasibility, lang) if plan is not None else None,
    }


@router.get(PROFILE_PATH, response_class=HTMLResponse)
def profile_page(request: Request, user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
    view = get_profiles(request).view(user.id)
    saved = request.query_params.get("saved") == "1"
    ctx = _context(request, view, _saved_values(view), {}, saved=saved)
    template = "partials/profile_body.html" if is_htmx(request) else "pages/profile.html"
    return render(request, template, ctx)


@router.post(PROFILE_PATH)
def profile_save(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    self_level: Annotated[str, Form()] = "",
    domains: Annotated[list[str] | None, Form()] = None,
    use_cases: Annotated[list[str] | None, Form()] = None,
    minutes_per_day: Annotated[str, Form()] = "",
    days_per_week: Annotated[str, Form()] = "",
    target_level: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
    goal_text: Annotated[str, Form()] = "",
    timezone: Annotated[str, Form()] = "",
) -> Response:
    """Plain `def`: the service call blocks on the database, so FastAPI runs it in a worker
    thread. The user comes from the session only; no field can name another learner."""
    profiles = get_profiles(request)
    values = _form_values(
        self_level=self_level,
        domains=domains,
        use_cases=use_cases,
        minutes_per_day=minutes_per_day,
        days_per_week=days_per_week,
        target_level=target_level,
        target_date=target_date,
        goal_text=goal_text,
        timezone=timezone,
    )
    raw = _to_input(values)
    try:
        profiles.save(user.id, raw)
    except ServiceError as exc:
        if exc.code != "validation_failed":
            raise
        errors = _messages(raw, exc.fields, today_for(request), user)
        ctx = _context(request, profiles.view(user.id), values, errors, saved=False)
        template = "partials/profile_body.html" if is_htmx(request) else "pages/profile.html"
        return render(request, template, ctx, status_code=422)
    if not is_htmx(request):
        return RedirectResponse(f"{PROFILE_PATH}?saved=1", status_code=303)
    view = profiles.view(user.id)
    ctx = _context(request, view, _saved_values(view), {}, saved=True)
    return render(request, "partials/profile_body.html", ctx)
