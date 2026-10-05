import gzip
import re
from pathlib import Path

import tutor.web

WEB = Path(tutor.web.__file__).parent
STATIC = WEB / "static"
TEMPLATES = WEB / "templates"

VOCABULARY = """
shell sidebar sidebar__nav sidebar__foot nav-link nav-link--active tabbar tabbar__link
tabbar__link--active tabbar__more tabbar__menu brand brand--light main public-head public-main
public-foot page-head page-head__actions grid card card--wide card--narrow card__title card__foot
banner banner--warn banner--info chip chip--free chip--annual chip--ok chip--warn chip--muted btn
btn--primary btn--ghost btn--small btn--block btn--danger copy meter meter__track meter__fill
meter-link kpi kpi__value kpi__label kpi__target trail trail__path trail__node trail__node--done
trail__node--today trail__node--upcoming trail__node--missed trail__node--rest trail__extra streak
stamps stamp stamp--used table-wrap table filters empty muted prose form-row form-error inline-form
field-error chart chart__line chart__target chart__dot as-table said correct diff diff--added
diff--removed diff--moved skeleton error-box skip-link sr-only icon steps code-copy celebrate
""".split()  # noqa: SIM905  (kept as prose so the vocabulary stays diffable)
EXTRA_CLASSES = [
    "trail__progress",
    "trail__label",
    "table--stack",
    "row--saved",
    "celebrate__leaf",
    "celebrate__leaf--a",
    "celebrate__leaf--b",
    "celebrate__leaf--c",
    "vt-today",
    "vt-last-session",
    "htmx-indicator",
]
NAV_ICONS = {"home", "plan", "progress", "sessions", "glossary", "reports", "account"}
REQUIRED_ICONS = NAV_ICONS | {
    "more",
    "copy",
    "check",
    "install",
    "share",
    "connect",
    "settings",
    "add",
    "close",
}


def css() -> str:
    return (STATIC / "css" / "app.css").read_text(encoding="utf-8")


def test_css_is_within_budget() -> None:
    assert len(gzip.compress(css().encode("utf-8"), 9)) <= 30 * 1024


def test_every_vocabulary_class_is_styled() -> None:
    text = css()
    missing = [
        name
        for name in VOCABULARY + EXTRA_CLASSES
        if not re.search(rf"\.{re.escape(name)}(?![\w-])", text)
    ]
    assert missing == []


def test_two_font_families_within_budget_with_licenses() -> None:
    fonts = sorted((STATIC / "fonts").glob("*.woff2"))
    assert [f.name for f in fonts] == ["baloo2-latin.woff2", "publicsans-latin.woff2"]
    assert sum(f.stat().st_size for f in fonts) <= 120 * 1024
    families = set(re.findall(r'@font-face\s*\{[^}]*font-family:\s*"([^"]+)"', css()))
    assert families == {"Baloo 2", "Public Sans"}
    for license_file in ("OFL-Baloo2.txt", "OFL-PublicSans.txt"):
        assert (
            "SIL OPEN FONT LICENSE"
            in (STATIC / "fonts" / license_file).read_text(encoding="utf-8").upper()
        )


def test_every_token_has_a_dark_value() -> None:
    dark = css().split("@media (prefers-color-scheme: dark)", 1)[1]
    for token in (
        "--paper",
        "--card",
        "--ink",
        "--ink-muted",
        "--line",
        "--leaf-strong",
        "--primary",
        "--on-primary",
        "--danger",
        "--on-danger",
        "--warn-bg",
        "--focus",
        "--sidebar",
    ):
        assert f"{token}:" in dark, token


def test_reduced_motion_is_honoured_both_ways() -> None:
    text = css()
    assert "@media (prefers-reduced-motion: reduce)" in text
    assert "body[data-reduce-motion]" in text
    assert "@view-transition" in text


def test_only_cheap_properties_are_animated() -> None:
    keyframes = re.findall(r"@keyframes\s+[\w-]+\s*\{(.*?)\}\s*\}", css(), flags=re.S)
    assert keyframes
    for body in keyframes:
        props = set(re.findall(r"([a-z-]+)\s*:", body))
        assert props <= {"transform", "opacity", "stroke-dashoffset"}, props


def _sprite_ids() -> set[str]:
    sprite = (STATIC / "icons" / "sprite.svg").read_text(encoding="utf-8")
    return set(re.findall(r'<symbol id="([\w-]+)"', sprite))


def test_sprite_has_every_icon_templates_use() -> None:
    used: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        used |= set(re.findall(r"sprite\.svg'\) \}\}#([\w-]+)", path.read_text(encoding="utf-8")))
    assert used | REQUIRED_ICONS <= _sprite_ids()
