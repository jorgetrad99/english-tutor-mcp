from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang, SessionStatus
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


def test_a_page_past_the_last_offers_the_way_back(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/sessions?page=99").text
    assert "Ninguna sesión coincide con esta página." in html
    assert 'href="/app/sessions?page=1"' in html
    assert "Aún no tienes sesiones" not in html


def test_first_day_learner_past_page_one_is_not_told_it_is_their_first_day(
    login: Login, demo: DemoUsers
) -> None:
    html = login(demo.nuevo).get("/app/sessions?page=3").text
    assert "Aún no tienes sesiones" not in html and 'href="/app/sessions?page=1"' in html
    assert "Aún no tienes sesiones" in login(demo.nuevo).get("/app/sessions").text


def test_status_filter_accepts_only_closed_and_incomplete(login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    closed = c.get("/app/sessions?status=closed").text
    assert 'chip chip--ok">Completa<' in closed and "Incompleta<" not in closed.split("<table")[1]
    assert '<option value="closed" selected>' in closed
    for raw in ("open", "bogus"):
        html = c.get(f"/app/sessions?status={raw}").text
        assert "selected>Completa" not in html and '<option value="closed" selected>' not in html
        assert "Explicar un bloqueo técnico" in html


def test_detail_hides_the_level_opinion_when_excluded_or_incomplete(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    # Final review M7: an estimate the server excluded, or one from an incomplete lesson,
    # is not shown as the model's opinion.
    detail = backend.session_rows[demo.ana][0]
    assert detail.cefr is not None
    session_id = detail.summary.id
    backend.cefr_excluded.add(session_id)
    assert backend.session_detail(demo.ana, session_id).cefr is None  # type: ignore[union-attr]
    html = login(demo.ana).get(f"/app/sessions/{session_id}").text
    assert "opinión del modelo" not in html
    backend.cefr_excluded.clear()
    incomplete = replace(detail.summary, status=SessionStatus.INCOMPLETE)
    backend.session_rows[demo.ana][0] = replace(detail, summary=incomplete)
    assert backend.session_detail(demo.ana, session_id).cefr is None  # type: ignore[union-attr]
