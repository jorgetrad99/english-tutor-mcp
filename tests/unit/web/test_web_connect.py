from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]


def test_connect_page_shows_address_command_and_first_phrase(login: Login, demo: DemoUsers) -> None:
    page = login(demo.nuevo).get("/app/connect")
    assert page.status_code == 200
    assert 'data-copy="https://localhost/mcp"' in page.text
    assert "claude mcp add --transport http tutor https://localhost/mcp" in page.text
    assert 'data-copy="start my lesson"' in page.text
    assert 'href="https://claude.ai"' in page.text


def test_new_user_waits_with_polling(login: Login, demo: DemoUsers) -> None:
    page = login(demo.nuevo).get("/app/connect")
    assert 'hx-get="/app/connect/status?n=1"' in page.text
    assert 'hx-trigger="every 10s"' in page.text
    assert "Esperando tu primera sesión…" in page.text


def test_connected_user_gets_a_link_and_no_polling(login: Login, demo: DemoUsers) -> None:
    page = login(demo.ana).get("/app/connect")
    assert "Tu tutor ya registró tu primera sesión." in page.text
    assert "every 10s" not in page.text


def test_status_partial_counts_and_stops_after_the_limit(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    partial = c.get("/app/connect/status?n=5", headers=HX)
    assert partial.status_code == 200 and "<html" not in partial.text
    assert 'hx-get="/app/connect/status?n=6"' in partial.text
    done = c.get("/app/connect/status?n=60", headers=HX)
    assert "every 10s" not in done.text and "Revisar de nuevo" in done.text
    assert "Revisar de nuevo" in c.get("/app/connect/status?n=999999", headers=HX).text
    assert 'status?n=1"' in c.get("/app/connect/status?n=-3", headers=HX).text


def test_status_without_htmx_redirects_to_the_page(login: Login, demo: DemoUsers) -> None:
    response = login(demo.nuevo).get("/app/connect/status?n=2")
    assert response.status_code == 303 and response.headers["location"] == "/app/connect"


def test_status_flips_when_the_first_session_arrives(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.nuevo)
    backend.session_rows[demo.nuevo] = backend.session_rows[demo.ana][:1]
    assert "Ir a Inicio" in c.get("/app/connect/status?n=3", headers=HX).text


def test_connect_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.nuevo] = replace(backend.users[demo.nuevo], lang=Lang.EN)
    assert "Connect your tutor" in login(demo.nuevo).get("/app/connect").text
