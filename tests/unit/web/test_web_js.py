import gzip
import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

import tutor.web

JS = Path(tutor.web.__file__).parent / "static" / "js"


def test_total_javascript_is_within_budget() -> None:
    total = sum(len(gzip.compress(p.read_bytes(), 9)) for p in JS.glob("*.js"))
    assert total <= 50 * 1024


def test_vendored_files_match_their_recorded_hashes() -> None:
    rows = re.findall(
        r"^\|\s*(js/[\w.-]+)\s*\|[^|]*\|\s*([\d.]+)\s*\|[^|]*\|\s*([0-9a-f]{64})\s*\|",
        (JS / "VENDORED.md").read_text(encoding="utf-8"),
        flags=re.M,
    )
    assert ("js/htmx.min.js", "2.0.11") in {(f, v) for f, v, _ in rows}
    for rel, _version, digest in rows:
        data = (JS.parent / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, rel


def test_app_js_builds_no_code_or_markup_from_strings() -> None:
    source = (JS / "app.js").read_text(encoding="utf-8")
    for forbidden in (
        "eval(",
        "new Function",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
    ):
        assert forbidden not in source, forbidden


def test_app_js_respects_both_reduced_motion_signals() -> None:
    source = (JS / "app.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in source
    assert "reduceMotion" in source


def test_app_js_implements_every_hook() -> None:
    source = (JS / "app.js").read_text(encoding="utf-8")
    for hook in (
        "data-count-to",
        "data-draw",
        "data-pop",
        "data-strike",
        "data-grow",
        "data-celebrate",
        "data-copy",
        "data-install",
        "data-ios-install",
        "htmx:afterSettle",
        "serviceWorker",
        "beforeinstallprompt",
    ):
        assert hook in source, hook


def test_scripts_load_only_from_static_and_htmx_first(client: TestClient) -> None:
    page = client.get("/login").text
    tags = re.findall(r"<script\b([^>]*)>", page)
    sources = [re.search(r'src="([^"]+)"', attrs) for attrs in tags]
    assert all(m and m.group(1).startswith("/static/") for m in sources)
    assert page.index("js/htmx.min.js") < page.index("js/app.js")


def test_live_region_carries_translated_copy_labels(client: TestClient) -> None:
    assert 'data-copied="Copiado"' in client.get("/login").text
    assert 'data-copied="Copied"' in client.get("/login?lang=en").text


def test_htmx_is_the_audited_2_0_11_build() -> None:
    data = (JS / "htmx.min.js").read_bytes()
    assert len(data) == 52182
    assert (
        hashlib.sha256(data).hexdigest()
        == "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"
    )
