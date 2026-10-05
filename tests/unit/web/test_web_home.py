import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.home import build_week_trail
from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import FixedClock, MemoryBackend, empty_home
from tutor.web.routes.home import trail_view

from .conftest import TODAY

Login = Callable[[UUID], TestClient]


def test_inicio_shows_a_normal_week(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    html = login(demo.ana).get("/app/").text
    assert "Pedir aclaraciones en una revisión de código" in html
    assert 'data-copy="start my lesson"' in html
    assert html.count('class="trail__label') == 7
    assert 'data-count-to="4"' in html  # streak
    assert 'class="stamp stamp--used"' in html and "on track" in html
    last = backend.home(demo.ana, TODAY).last_session
    assert last is not None and f'href="/app/sessions/{last.id}"' in html
    assert "repasos esperan en tu próxima lección" in html
    assert "vt-today" in html and "vt-last-session" in html
    assert "minutos hablando" not in html  # Free: no weekly report preview


def test_annual_sees_the_weekly_report_preview(login: Login, demo: DemoUsers) -> None:
    html = login(demo.beto).get("/app/").text
    assert "minutos hablando" in html and 'data-count-to="45"' in html


def test_first_day_shows_connect_steps(login: Login, demo: DemoUsers) -> None:
    html = login(demo.nuevo).get("/app/").text
    assert "Conecta tu tutor" in html and 'hx-trigger="every 10s"' in html
    assert "trail__label" not in html


def test_connected_without_plan_asks_for_the_diagnostic(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    backend.homes[demo.nuevo] = replace(empty_home(TODAY), has_connected=True)
    html = login(demo.nuevo).get("/app/").text
    assert "Haz tu diagnóstico en el chat" in html


def test_celebrates_a_new_session_once(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    assert "data-celebrate" in c.get("/app/").text
    assert "data-celebrate" not in c.get("/app/").text
    assert backend.celebrated[demo.ana] == backend.homes[demo.ana].newest_closed_session_id


def test_today_follows_the_learner_timezone(
    login: Login, demo: DemoUsers, backend: MemoryBackend, clock: FixedClock
) -> None:
    clock.now = datetime(2027, 1, 13, 3, 30, tzinfo=UTC)  # 21:30 on Jan 12 in Mexico City
    html = login(demo.ana).get("/app/").text
    assert re.search(r'data-day="2027-01-12"[^>]*aria-current="date"', html)
    backend.users[demo.ana] = replace(backend.users[demo.ana], timezone="UTC")
    html_utc = login(demo.ana).get("/app/").text
    assert re.search(r'data-day="2027-01-13"[^>]*aria-current="date"', html_utc)


def test_inicio_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Your week" in login(demo.ana).get("/app/").text


def test_trail_view_walks_up_to_today(backend: MemoryBackend, demo: DemoUsers) -> None:
    home = backend.home(demo.ana, TODAY)
    view = trail_view(build_week_trail(home.week_start, home.planned_days, (), TODAY))
    assert len(view["nodes"]) == 7
    assert view["walked"] == "M20 70 L66 46"  # Monday and Tuesday (today)
    assert view["path"].startswith("M20 70 L66 46 L113 60")
