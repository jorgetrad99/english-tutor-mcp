"""v0 navigation and links: only the five v0 pages, nothing postponed (ruling 11, core Task 25)."""

import re
from collections.abc import Callable
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import (
    PlanPage,
    ProgressData,
    ReportsPage,
    Subscription,
    SubStatus,
    Tier,
)
from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers, seed_demo
from tutor.web.memory import FixedClock, MemoryBackend, memory_deps
from tutor.web.ports import UNCAPPED
from tutor.web.profile import MemoryProfiles, install_profiles
from tutor.web.routing import iter_api_routes

from .conftest import BASE, TODAY

pytestmark = pytest.mark.unit

Login = Callable[[UUID], TestClient]
NAV = ["/app/", "/app/profile", "/app/sessions", "/app/glossary", "/app/connect"]
POSTPONED = (
    "/app/plan",
    "/app/progress",
    "/app/reports",
    "/app/account",
    "/billing",
    "/admin",
    "/pricing",
    "/webhooks",
)
LINK = re.compile(r'(?:href|action|hx-get|hx-post)="([^"]+)"')
PARAM = re.compile(r"{(\w+)(?::\w+)?}")


def test_sidebar_and_tab_bar_list_only_the_v0_pages(login: Login, demo: DemoUsers) -> None:
    html = login(demo.admin).get("/app/connect").text
    sidebar = html.split('class="sidebar__nav"', 1)[1].split("</nav>", 1)[0]
    tabbar = html.split('class="tabbar"', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'<a class="nav-link[^"]*" href="([^"]+)"', sidebar) == NAV
    assert re.findall(r'<a class="tabbar__link[^"]*" href="([^"]+)"', tabbar) == NAV
    for gone in ("tabbar__more", "meter-link", "/admin/settings", "chip--free"):
        assert gone not in html


def test_the_current_page_is_marked_in_both_navs(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/connect").text
    assert html.count('href="/app/connect" aria-current="page"') == 2


def test_no_v0_page_links_to_a_postponed_page(
    login: Login, client: TestClient, demo: DemoUsers, backend: MemoryBackend
) -> None:
    for uid in (demo.ana, demo.beto, demo.nuevo, demo.admin):
        backend.subs[uid] = Subscription(Tier.FREE, SubStatus.NONE, "price_29", 2900)
    backend.session_cap = backend.glossary_cap = UNCAPPED
    session_id = backend.session_rows[demo.ana][0].summary.id
    item_id = backend.glossaries[demo.ana][0].id
    pages = [
        "/app/",
        "/app/profile",
        "/app/connect",
        "/app/sessions",
        "/app/glossary",
        f"/app/sessions/{session_id}",
        f"/app/glossary/{item_id}/edit",
    ]
    links: list[str] = []
    for uid in (demo.ana, demo.nuevo):
        c = login(uid)
        links += [link for page in pages for link in LINK.findall(c.get(page).text)]
    for page in ("/login", "/login?lang=en", "/privacy", "/terms", "/offline"):
        links += LINK.findall(client.get(page).text)
    assert [link for link in links if link.startswith(POSTPONED)] == []
    assert 'href="/app/profile">' in login(demo.ana).get("/app/").text  # "Ver plan"


class V0Backend(MemoryBackend):
    """Fails loudly if a v0 route reads a postponed page's data."""

    def plan(self, user_id: UUID) -> PlanPage | None:
        raise AssertionError("v0 routes never read the Plan page")

    def progress(self, user_id: UUID) -> ProgressData:
        raise AssertionError("v0 routes never read Progreso")

    def reports(self, user_id: UUID) -> ReportsPage:
        raise AssertionError("v0 routes never read Reportes")


def test_no_v0_route_reads_plan_progress_or_reports(
    clock: FixedClock, config: WebConfig, profiles: MemoryProfiles
) -> None:
    backend = V0Backend()
    demo = seed_demo(backend, TODAY)
    app: FastAPI = create_app(memory_deps(backend, clock), config)
    install_profiles(app, profiles)
    ids = {
        "session_id": str(backend.session_rows[demo.ana][0].summary.id),
        "item_id": str(backend.glossaries[demo.ana][0].id),
    }
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        assert c.post("/auth/test-login", data={"user_id": str(demo.ana)}).status_code == 303
        checked = 0
        for route in iter_api_routes(app):  # app.routes only holds included-router nodes
            if "GET" not in (route.methods or ()):
                continue
            if not route.path.startswith("/app"):
                continue
            path = PARAM.sub(lambda m: ids[m.group(1)], route.path)
            for headers in ({}, {"hx-request": "true"}):
                assert c.get(path, headers=headers).status_code < 500, path
            checked += 1
    assert checked >= 10


def test_entrar_and_privacy_state_what_is_stored_and_how_to_delete(client: TestClient) -> None:
    login_page = client.get("/login").text
    assert "Tus lecciones pasan por el proveedor de tu asistente" in login_page
    assert "soporte@example.test" in login_page
    privacy = client.get("/privacy").text
    assert "desde Cuenta" not in privacy and "reporte semanal" not in privacy
    assert "lo hacemos a mano" in privacy
    assert "/pricing" not in client.get("/terms").text


def test_terms_promise_nothing_that_does_not_exist_in_v0(client: TestClient) -> None:
    terms = client.get("/terms").text
    for promise in ("reembolso", "Annual", "CFDI"):
        assert promise not in terms
