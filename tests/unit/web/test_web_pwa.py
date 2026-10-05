import re
import struct
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tutor.web
from tutor.domain.dashboard.types import Lang
from tutor.web.assets import AssetManifest
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend
from tutor.web.routes.pwa import precache_list

from .conftest import csrf_of

ICONS = Path(tutor.web.__file__).parent / "static" / "icons"


def test_manifest_fields(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert (
        manifest["display"],
        manifest["start_url"],
        manifest["scope"],
        manifest["theme_color"],
        manifest["background_color"],
        manifest["lang"],
        manifest["short_name"],
    ) == ("standalone", "/app/?source=pwa", "/", "#11402c", "#e3f4ea", "es-MX", "Tutor")
    purposes = {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
    assert purposes == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("icon-192.png", (192, 192)),
        ("icon-512.png", (512, 512)),
        ("icon-maskable-512.png", (512, 512)),
        ("apple-touch-icon.png", (180, 180)),
        ("splash-750x1334.png", (750, 1334)),
        ("splash-1170x2532.png", (1170, 2532)),
        ("splash-1179x2556.png", (1179, 2556)),
        ("splash-1290x2796.png", (1290, 2796)),
    ],
)
def test_icons_are_pngs_of_the_right_size(name: str, size: tuple[int, int]) -> None:
    data = (ICONS / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == size


def test_service_worker_embeds_version_and_never_cache_list(
    client: TestClient, app: FastAPI
) -> None:
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    body = response.text
    assert f'"{app.state.assets.version}"' in body
    for prefix in ("/app/", "/api/", "/billing/", "/admin/", "/auth/", "/webhooks/"):
        assert f'"{prefix}"' in body
    assert '"/offline"' in body and app.state.assets.url("css/app.css") in body
    for needle in ("skipWaiting", "clients.claim", 'credentials: "omit"', "HX-Request"):
        assert needle in body


def test_precache_list_takes_shell_assets_only(tmp_path: Path) -> None:
    for rel in (
        "css/app.css",
        "js/app.js",
        "js/VENDORED.md",
        "fonts/baloo2-latin.woff2",
        "fonts/OFL-Baloo2.txt",
        "icons/sprite.svg",
        "icons/icon-192.png",
        "icons/apple-touch-icon.png",
        "icons/splash-750x1334.png",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())
    urls = precache_list(AssetManifest(tmp_path))
    assert [u.split("?")[0] for u in urls] == [
        "/static/css/app.css",
        "/static/fonts/baloo2-latin.woff2",
        "/static/icons/icon-192.png",
        "/static/icons/sprite.svg",
        "/static/js/app.js",
        "/offline",
    ]
    assert all("?v=" in u for u in urls[:-1])


def test_offline_page_renders_without_login(client: TestClient) -> None:
    response = client.get("/offline")
    assert response.status_code == 200
    assert "Tu progreso está a salvo en el servidor" in response.text


def test_offline_page_never_contains_personal_data(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    page = login(demo.ana).get("/offline").text
    assert "Ana" not in page
    assert 'name="csrf-token"' not in page and "X-CSRF-Token" not in page


def test_base_links_manifest_and_ios_assets(client: TestClient) -> None:
    page = client.get("/login").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    assert 'rel="apple-touch-icon"' in page
    assert page.count('rel="apple-touch-startup-image"') == 4
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in page


def test_dismiss_install_requires_csrf_and_is_remembered(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    assert c.post("/app/install/dismiss", data={"back": "/app/"}).status_code == 403
    response = c.post(
        "/app/install/dismiss", data={"csrf_token": csrf_of(c), "back": "/app/glossary"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/app/glossary"
    assert demo.ana in backend.install_dismissed


def test_dismiss_install_over_htmx_returns_empty_fragment(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/install/dismiss", data={"csrf_token": csrf_of(c)}, headers={"hx-request": "true"}
    )
    assert response.status_code == 200 and response.text == ""


def test_dismiss_install_redirect_ignores_foreign_back(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/install/dismiss", data={"csrf_token": csrf_of(c), "back": "https://evil.example"}
    )
    assert response.headers["location"] == "/app/"


def _install_partial(app: FastAPI, dismissed: bool, lang: Lang = Lang.ES_MX) -> str:
    template = app.state.views.envs[lang].get_template("partials/install.html")
    return template.render(install_dismissed=dismissed, **{"csrf_token": "tok", "path": "/app/"})


def test_install_partial_offers_both_paths_until_dismissed(app: FastAPI) -> None:
    html = _install_partial(app, dismissed=False)
    assert "data-install-card" in html and "data-install" in html and "data-ios-install" in html
    assert re.search(r"<section[^>]*data-install-card[^>]*hidden", html)
    assert "Agregar a pantalla de inicio" in html
    assert _install_partial(app, dismissed=True).strip() == ""
    assert "Add to Home Screen" in _install_partial(app, dismissed=False, lang=Lang.EN)


def test_install_styles_exist() -> None:
    css = (Path(tutor.web.__file__).parent / "static" / "css" / "app.css").read_text("utf-8")
    assert ".install-card" in css and ".ios-step" in css


def test_service_worker_never_stores_personal_responses(client: TestClient, app: FastAPI) -> None:
    body = client.get("/sw.js").text
    # Only the install-time precache writes; there is no runtime put/add of fetched responses.
    assert "cache.put" not in body and ".put(" not in body
    assert body.count("addAll(") == 1 and ".add(" not in body
    # Navigations are network-only with the offline page as the fallback.
    assert 'request.mode === "navigate"' in body and 'caches.match("/offline")' in body
    # Only /static/ responses are ever served from the cache.
    assert 'url.pathname.startsWith("/static/")' in body
    for prefix in ("/app/", "/auth/"):
        assert prefix in body.split("NEVER_CACHE =", 1)[1].split("\n", 1)[0]


def test_precache_list_has_no_authenticated_or_auth_paths(app: FastAPI) -> None:
    urls = precache_list(app.state.assets)
    assert urls[-1] == "/offline"
    for url in urls:
        path = url.split("?")[0]
        assert path == "/offline" or path.startswith("/static/"), url
        assert not path.startswith(("/app/", "/auth/", "/api/", "/billing/", "/admin/")), url


def test_offline_response_sets_no_cookie_and_is_not_personalised(
    login: Callable[[UUID], TestClient], demo: DemoUsers, client: TestClient
) -> None:
    anonymous = client.get("/offline")
    assert "set-cookie" not in anonymous.headers
    logged_in = login(demo.ana).get("/offline")
    assert logged_in.text == anonymous.text
