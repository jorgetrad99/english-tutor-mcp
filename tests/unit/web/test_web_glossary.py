from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import GlossaryRow, Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

from .conftest import csrf_of

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]


def row_of(backend: MemoryBackend, user_id: UUID, text: str) -> GlossaryRow:
    return next(r for r in backend.glossaries[user_id] if r.text == text)


def test_page_lists_items_labels_and_free_counter(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/glossary").text
    assert "7 de 50 elementos" in html
    assert html.count('<tr id="g-') == 7
    assert "Por confirmar hasta el" in html  # provisional item
    assert "Te cuesta" in html  # leech
    assert "En pausa" in html  # archived
    assert "Toca repasar" in html  # due today


def test_annual_has_no_counter(login: Login, demo: DemoUsers) -> None:
    assert "de 50 elementos" not in login(demo.beto).get("/app/glossary").text


def test_filters_return_the_table_partial_with_matching_export_link(
    login: Login, demo: DemoUsers
) -> None:
    response = login(demo.ana).get("/app/glossary?kind=term", headers=HX)
    assert "<html" not in response.text and 'id="glossary-table"' in response.text
    assert "trade-off" in response.text and "blocker" in response.text
    assert "on track" not in response.text
    assert 'href="/app/glossary.csv?kind=term"' in response.text


def test_search_ignores_accents(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/glossary?q=rapido", headers=HX)
    assert "a quick heads-up" in response.text and "trade-off" not in response.text


def test_edit_form_partial(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    response = login(demo.ana).get(f"/app/glossary/{row.id}/edit", headers=HX)
    assert response.status_code == 200 and "<html" not in response.text
    assert 'name="meaning"' in response.text and 'value="compromiso"' in response.text
    assert "data-esc-cancel" in response.text


def test_save_updates_the_row_and_announces(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    response = c.post(
        f"/app/glossary/{row.id}",
        data={
            "csrf_token": csrf_of(c),
            "meaning": "  concesi\N{LATIN SMALL LETTER O WITH ACUTE}n ",
            "context_sentence": "The trade-off is cost.",
        },
        headers=HX,
    )
    assert response.status_code == 200
    assert "concesi\N{LATIN SMALL LETTER O WITH ACUTE}n" in response.text
    assert 'hx-swap-oob="true"' in response.text and "Guardado" in response.text
    assert (
        row_of(backend, demo.ana, "trade-off").meaning
        == "concesi\N{LATIN SMALL LETTER O WITH ACUTE}n"
    )


def test_plain_form_post_redirects(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    response = c.post(
        f"/app/glossary/{row.id}",
        data={"csrf_token": csrf_of(c), "meaning": "x", "context_sentence": "Fine."},
    )
    assert response.status_code == 303 and response.headers["location"] == "/app/glossary"


def test_invalid_edit_is_422_and_changes_nothing(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    response = c.post(
        f"/app/glossary/{row.id}",
        data={"csrf_token": csrf_of(c), "meaning": "x" * 201, "context_sentence": "   "},
        headers=HX,
    )
    assert response.status_code == 422
    assert "M\N{LATIN SMALL LETTER A WITH ACUTE}ximo 200 caracteres." in response.text
    assert (
        "Escribe la oraci\N{LATIN SMALL LETTER O WITH ACUTE}n "
        "donde apareci\N{LATIN SMALL LETTER O WITH ACUTE} la frase." in response.text
    )
    assert 'aria-invalid="true"' in response.text
    assert row_of(backend, demo.ana, "trade-off").meaning == "compromiso"


def test_save_without_csrf_is_403(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    response = login(demo.ana).post(
        f"/app/glossary/{row.id}", data={"meaning": "x", "context_sentence": "y"}
    )
    assert response.status_code == 403


def test_other_users_items_are_404(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    beto_row = backend.glossaries[demo.beto][0]
    c = login(demo.ana)
    assert c.get(f"/app/glossary/{beto_row.id}/edit", headers=HX).status_code == 404
    assert c.get(f"/app/glossary/{beto_row.id}/row", headers=HX).status_code == 404
    response = c.post(
        f"/app/glossary/{beto_row.id}",
        data={"csrf_token": csrf_of(c), "meaning": "x", "context_sentence": "y"},
    )
    assert response.status_code == 404
    assert backend.glossaries[demo.beto][0].meaning == beto_row.meaning
    assert c.get("/app/glossary/zzz/edit").status_code == 404


def test_html_in_an_edit_stays_text(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    payload = "<img src=x onerror=alert(1)>"
    response = c.post(
        f"/app/glossary/{row.id}",
        data={"csrf_token": csrf_of(c), "meaning": payload, "context_sentence": "Fine."},
        headers=HX,
    )
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
    assert "<img src=x" not in response.text
    page = c.get("/app/glossary").text
    assert "&lt;img src=x onerror=alert(1)&gt;" in page and "<img src=x" not in page


def test_csv_export_is_a_filtered_attachment(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/glossary.csv?kind=term")
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="glosario.csv"'
    assert response.text.startswith("\N{ZERO WIDTH NO-BREAK SPACE}")
    assert len(response.text.strip().splitlines()) == 3  # header + trade-off + blocker


def test_empty_glossary(login: Login, demo: DemoUsers) -> None:
    assert (
        "Tu glosario est\N{LATIN SMALL LETTER A WITH ACUTE} vac\N{LATIN SMALL LETTER I WITH ACUTE}o"
        in login(demo.nuevo).get("/app/glossary").text
    )


def test_glossary_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Search" in login(demo.ana).get("/app/glossary").text
