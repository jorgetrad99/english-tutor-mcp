from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]


def test_list_is_newest_first_with_status_chips(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/sessions").text
    assert html.index("Dar una actualización de estado en el standup") < html.index(
        "Explicar un bloqueo técnico"
    )
    assert 'chip chip--muted">Incompleta<' in html
    assert 'chip chip--ok">Completa<' in html


def test_filter_by_mode_returns_the_table_partial(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/sessions?mode=text", headers=HX)
    assert response.status_code == 200 and "<html" not in response.text
    assert 'id="sessions-table"' in response.text
    assert "Explicar un bloqueo técnico" in response.text
    assert "standup" not in response.text


def test_bad_filters_are_ignored(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/sessions?mode=fax&status=x&page=abc")
    assert response.status_code == 200
    assert "Explicar un bloqueo técnico" in response.text


def test_pagination_keeps_filters(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    rows = backend.session_rows[demo.ana]
    rows.extend(replace(d, summary=replace(d.summary, id=uuid4())) for d in rows * 10)
    c = login(demo.ana)
    assert "/app/sessions?mode=voice&amp;page=2" in c.get("/app/sessions?mode=voice").text
    assert "/app/sessions?mode=voice&amp;page=1" in c.get("/app/sessions?mode=voice&page=2").text


def test_detail_shows_corrections_opinion_and_turns(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    detail = backend.session_rows[demo.ana][0]
    html = login(demo.ana).get(f"/app/sessions/{detail.summary.id}").text
    assert "I have went to the meeting" in html and "I went to the meeting" in html
    assert "data-strike" in html
    assert "opinión del modelo" in html and "B1+" in html
    assert "we are on track" in html  # evidence phrase
    assert "3 de 5" in html
    assert "Yesterday I have went to the meeting with the client." in html


def test_incomplete_session_says_it_does_not_count(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    incomplete = backend.session_rows[demo.ana][2]
    html = login(demo.ana).get(f"/app/sessions/{incomplete.summary.id}").text
    assert "Sesión incompleta" in html


def test_other_users_or_malformed_ids_are_404(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    beto_session = backend.session_rows[demo.beto][0].summary.id
    c = login(demo.ana)
    assert c.get(f"/app/sessions/{beto_session}").status_code == 404
    assert c.get("/app/sessions/not-a-uuid").status_code == 404


def test_user_turns_are_escaped(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    detail = backend.session_rows[demo.ana][0]
    backend.session_rows[demo.ana][0] = replace(detail, user_turns=("<script>alert(1)</script>",))
    html = login(demo.ana).get(f"/app/sessions/{detail.summary.id}").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html


def test_first_day_has_an_empty_state(login: Login, demo: DemoUsers) -> None:
    assert "Aún no tienes sesiones" in login(demo.nuevo).get("/app/sessions").text


def test_sessions_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Voice" in login(demo.ana).get("/app/sessions").text
