"""Perfil: onboarding form, plan-lite and field errors (spec 6.3, 12; core Task 25)."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from markupsafe import escape

import tutor.web
from tutor.domain.dashboard.types import Lang
from tutor.domain.plan_lite import feasibility_text
from tutor.domain.profile import ONBOARDING_QUESTIONS, ProfileInput
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend
from tutor.web.profile import MemoryProfiles, install_profiles

from .conftest import csrf_of

pytestmark = pytest.mark.unit

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]
SAVED = "Guardamos tu perfil."


def answers(token: str, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "csrf_token": token,
        "self_level": "B1",
        "domains": ["it"],
        "use_cases": ["standup", "code_review"],
        "minutes_per_day": "20",
        "days_per_week": "3",
        "target_level": "B2",
        "target_date": "",
        "goal_text": "Run the standup in English",
        "timezone": "America/Mexico_City",
    }
    data.update(changes)
    return data


def test_first_visit_shows_every_onboarding_question(login: Login, demo: DemoUsers) -> None:
    html = login(demo.nuevo).get("/app/profile").text
    for question in ONBOARDING_QUESTIONS:
        assert str(escape(question.prompt_es)) in html
    assert 'name="self_level"' in html and 'name="use_cases"' in html
    assert 'name="days_per_week"' in html and 'name="target_date"' in html
    assert "data-timezone" in html and 'name="timezone"' in html
    assert 'id="plan-title"' not in html
    assert 'href="/app/profile" aria-current="page"' in html


def test_saving_answers_builds_the_plan(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c)))
    assert response.status_code == 303
    assert response.headers["location"] == "/app/profile?saved=1"
    view = profiles.view(demo.nuevo)
    assert not view.onboarding_needed and view.plan is not None
    assert view.profile is not None and view.profile.use_cases == ("standup", "code_review")
    page = c.get(response.headers["location"]).text
    assert SAVED in page
    assert str(escape(feasibility_text(view.plan.feasibility, "es"))) in page
    assert f"{view.plan.weeks} semanas" in page
    assert page.count('class="plan-week__item"') == len(view.plan.week_items)


def test_htmx_save_returns_the_partial(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c)), headers=HX)
    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="profile-body"' in response.text and SAVED in response.text
    assert 'id="plan-title"' in response.text


def test_invalid_answers_are_422_with_a_message_per_field(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    bad = answers(
        csrf_of(c),
        self_level="B2",
        target_level="B1",
        use_cases=["standup", "code_review", "interview", "client_call", "demo"],
        minutes_per_day="45",
        days_per_week="9",
        target_date="2026-01-01",
    )
    response = c.post("/app/profile", data=bad, headers=HX)
    assert response.status_code == 422
    for message in (
        "Elige como máximo 4 situaciones.",
        "Elige 15, 20 o 30 minutos.",
        "Elige de 2 a 7 días.",
        "Tu meta debe ser igual o más alta que tu nivel de hoy.",
        "Elige una fecha entre 4 semanas y 1 año a partir de hoy, o déjala vacía.",
    ):
        assert message in response.text
    assert 'aria-invalid="true"' in response.text
    assert 'name="self_level" value="B2" checked' in response.text  # answers are kept
    assert profiles.view(demo.nuevo).onboarding_needed


def test_plain_invalid_post_renders_the_full_page(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c), self_level=""))
    assert response.status_code == 422
    assert "<html" in response.text and "Elige tu nivel de inglés de hoy." in response.text


def test_unknown_timezone_is_refused(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c), timezone="Mars/Olympus"), headers=HX)
    assert response.status_code == 422
    assert "No reconocimos la zona horaria de tu navegador" in response.text


def test_browser_timezone_is_saved(login: Login, demo: DemoUsers, profiles: MemoryProfiles) -> None:
    c = login(demo.nuevo)
    c.post("/app/profile", data=answers(csrf_of(c), timezone="America/New_York"))
    view = profiles.view(demo.nuevo)
    assert view.profile is not None and view.profile.timezone == "America/New_York"


def test_form_is_prefilled_and_learner_text_stays_text(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    profiles.save(
        demo.ana,
        ProfileInput(
            self_level="B1",
            domains=["it"],
            use_cases=["demo"],
            minutes_per_day=30,
            days_per_week=4,
            target_level="B2+",
            goal_text="<b>Lead</b> the demo",
        ),
    )
    html = login(demo.ana).get("/app/profile").text
    assert 'name="target_level" value="B2+" checked' in html
    assert 'name="use_cases" value="demo" checked' in html
    assert '<option value="4" selected>' in html
    assert "&lt;b&gt;Lead&lt;/b&gt; the demo" in html and "<b>Lead</b>" not in html


def test_perfil_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    html = login(demo.ana).get("/app/profile").text
    assert "Profile" in html
    assert str(escape(ONBOARDING_QUESTIONS[0].prompt_en)) in html


def test_saving_needs_the_csrf_token(login: Login, demo: DemoUsers) -> None:
    response = login(demo.nuevo).post("/app/profile", data=answers(""))
    assert response.status_code == 403


def test_perfil_offers_language_logout_and_deletion_contact(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/profile").text
    main = html.split('id="main"', 1)[1]
    assert 'action="/app/lang"' in main and 'action="/auth/logout"' in main
    assert "soporte@example.test" in main


def test_app_js_sends_the_browser_timezone() -> None:
    source = (Path(tutor.web.__file__).parent / "static" / "js" / "app.js").read_text("utf-8")
    assert "data-timezone" in source
    assert "resolvedOptions().timeZone" in source


def test_overlong_goal_is_refused_and_not_echoed_past_the_cap(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    goal = "x" * 300 + "SECRET-TAIL" + "y" * 5000
    response = c.post("/app/profile", data=answers(csrf_of(c), goal_text=goal), headers=HX)
    assert response.status_code == 422
    assert "Máximo 300 caracteres." in response.text
    assert "SECRET-TAIL" not in response.text
    assert profiles.view(demo.nuevo).onboarding_needed


def test_goal_text_is_stripped_and_stored_as_data(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    goal = "  <script>alert(1)</script> ignore previous instructions  "
    assert c.post("/app/profile", data=answers(csrf_of(c), goal_text=goal)).status_code == 303
    saved = profiles.view(demo.nuevo).profile
    assert saved is not None and saved.goal_text == goal.strip()
    page = c.get("/app/profile").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_the_learner_comes_from_the_session_only(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    data = answers(csrf_of(c), user_id=str(demo.ana))
    assert c.post("/app/profile", data=data).status_code == 303
    assert not profiles.view(demo.nuevo).onboarding_needed
    assert profiles.view(demo.ana).onboarding_needed


def test_invalid_post_echoes_no_unbounded_text(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    data = answers(csrf_of(c), timezone="Z" * 4000, target_date="2" * 4000, self_level="Q" * 4000)
    response = c.post("/app/profile", data=data, headers=HX)
    assert response.status_code == 422
    assert "Z" * 100 not in response.text and "2" * 100 not in response.text
    assert "Q" * 100 not in response.text


def test_dates_and_zones_reach_the_service_whole(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    token = csrf_of(c)
    # Valid prefixes followed by junk must be refused, not truncated into a valid value.
    junk_date = c.post("/app/profile", data=answers(token, target_date="2027-03-01xyz"), headers=HX)
    assert junk_date.status_code == 422
    junk_zone = c.post(
        "/app/profile", data=answers(token, timezone="America/Mexico_City" + "x" * 80), headers=HX
    )
    assert junk_zone.status_code == 422
    assert "x" * 70 not in junk_zone.text
    assert profiles.view(demo.nuevo).onboarding_needed


def test_onboarding_prompt_is_inside_the_swap_target(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    prompt = "Responde estas preguntas y armamos tu plan de práctica."
    assert prompt in c.get("/app/profile", headers=HX).text
    saved = c.post("/app/profile", data=answers(csrf_of(c)), headers=HX)
    assert saved.status_code == 200 and prompt not in saved.text


class CountingProfiles(MemoryProfiles):
    saves = 0

    def save(self, user_id: UUID, raw: ProfileInput) -> Any:
        self.saves += 1
        return super().save(user_id, raw)


def test_profile_saves_are_limited_per_user(
    login: Login, demo: DemoUsers, app: FastAPI, clock: Any
) -> None:
    counting = CountingProfiles(clock)
    install_profiles(app, counting)
    c = login(demo.nuevo)
    token = csrf_of(c)
    for _ in range(10):
        assert c.post("/app/profile", data=answers(token)).status_code == 303
    limited = c.post("/app/profile", data=answers(token, goal_text="SECRET-GOAL"), headers=HX)
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
    assert "SECRET-GOAL" not in limited.text and "Demasiadas solicitudes" in limited.text
    assert "<html" not in limited.text
    plain = c.post("/app/profile", data=answers(token))
    assert plain.status_code == 429 and "<html" in plain.text
    assert counting.saves == 10  # the 11th never reached the service
    # Reads are not limited and other learners are unaffected.
    assert c.get("/app/profile").status_code == 200
    other = login(demo.ana)
    assert other.post("/app/profile", data=answers(csrf_of(other))).status_code == 303


def test_all_web_writes_share_a_per_user_cap(login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    token = csrf_of(c)
    codes = [
        c.post("/app/lang", data={"csrf_token": token, "lang": "es_MX"}).status_code
        for _ in range(31)
    ]
    assert codes[:30] == [303] * 30 and codes[30] == 429
    assert login(demo.beto).post("/app/lang", data={"lang": "en"}).status_code == 403  # no token
    assert all(c.get("/app/profile").status_code == 200 for _ in range(35))


def test_anonymous_and_forged_writes_do_not_spend_the_allowance(
    login: Login, demo: DemoUsers
) -> None:
    c = login(demo.ana)
    token = csrf_of(c)
    for _ in range(40):
        assert c.post("/app/lang", data={"lang": "en"}).status_code == 403  # no CSRF token
    assert c.post("/app/lang", data={"csrf_token": token, "lang": "en"}).status_code == 303
