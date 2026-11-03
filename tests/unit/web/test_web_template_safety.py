"""Template and code rules that keep the CSP strict and learner text inert (spec 9.6).
Task 29 extends this file; do not duplicate these checks elsewhere."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import tutor.web

WEB = Path(tutor.web.__file__).parent
TEMPLATES = WEB / "templates"
ALLOW = "safe: static"  # a line carrying this comment may mark a named constant safe

TEMPLATE_RULES: dict[str, re.Pattern[str]] = {
    "inline style attribute": re.compile(r"\sstyle\s*="),
    "style element": re.compile(r"<style\b", re.IGNORECASE),
    "inline script": re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE),
    "hx-on attribute": re.compile(r"\bhx-on"),
    "event handler attribute": re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE),
    "js: in hx-vals": re.compile(r"hx-vals\s*=\s*[\"']js:"),
    "safe filter": re.compile(r"\|\s*safe\b"),
    "srcdoc attribute": re.compile(r"\bsrcdoc\b", re.IGNORECASE),
}
JS_TEMPLATE_RULES = ("safe filter",)

# The one frame in the templates (spec 9 and 10; slice D ruling 53 as amended by the controller:
# the src is a signed URL on the template origin). Its only allowance is this file and this
# exact attribute set: sandbox "allow-scripts" alone, no srcdoc, no allow-same-origin.
IFRAME = re.compile(r"<iframe\b", re.IGNORECASE)
FRAME_PARTIAL = "partials/instance_frame.html"
FRAME_ATTRS = [
    ("sandbox", "allow-scripts"),
    ("referrerpolicy", "no-referrer"),
    ("loading", "lazy"),
    ("src", "{{ frame_src }}"),
    ("title", "{{ prompt_text }}"),
    ("class", "instance-frame instance-frame--{{ purpose }}"),
]


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.iframes: list[list[tuple[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "iframe":
            self.iframes.append(attrs)


def iframe_violations(name: str, text: str) -> list[str]:
    """An `<iframe>` anywhere but the frame partial, or the partial's frame not exactly
    FRAME_ATTRS (in order, no duplicates, nothing else), or more than one frame there."""
    lines = [n for n, line in enumerate(text.splitlines(), 1) if IFRAME.search(line)]
    if not lines:
        return []
    if name != FRAME_PARTIAL:
        return [f"{name}:{n}: iframe element" for n in lines]
    parser = _Tags()
    parser.feed(text)
    parser.close()
    if len(lines) != 1 or len(parser.iframes) != 1:
        return [f"{name}: iframe element: exactly one frame allowed"]
    if parser.iframes[0] != FRAME_ATTRS:
        return [f"{name}:{lines[0]}: iframe element: attributes must be exactly {FRAME_ATTRS}"]
    return []


def file_violations(name: str, text: str) -> list[str]:
    """Every rule broken by one template file (`name` relative to the templates folder)."""
    rules = (
        TEMPLATE_RULES
        if name.endswith(".html")
        else {k: TEMPLATE_RULES[k] for k in JS_TEMPLATE_RULES}
    )
    found: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for rule, pattern in rules.items():
            if pattern.search(line):
                found.append(f"{name}:{number}: {rule}: {line.strip()}")
    if name.endswith(".html"):
        found.extend(iframe_violations(name, text))
    return found


def template_violations() -> list[str]:
    found: list[str] = []
    for path in sorted(TEMPLATES.rglob("*")):
        if path.suffix not in {".html", ".js"}:
            continue
        name = path.relative_to(TEMPLATES).as_posix()
        found.extend(file_violations(name, path.read_text(encoding="utf-8")))
    return found


def python_violations() -> list[str]:
    found: list[str] = []
    for path in sorted(WEB.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "Markup(" in line and ALLOW not in line:
                found.append(f"{path.relative_to(WEB)}:{number}: {line.strip()}")
    return found


def test_templates_have_no_inline_code_or_unsafe_output() -> None:
    assert template_violations() == []


def test_python_never_marks_text_safe() -> None:
    assert python_violations() == []


# --- the iframe element rule -----------------------------------------------------------------

GOOD_FRAME = (
    '<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" loading="lazy" '
    'src="{{ frame_src }}" title="{{ prompt_text }}" '
    'class="instance-frame instance-frame--{{ purpose }}"></iframe>\n'
    '<p class="instance-prompt">{{ prompt_text }}</p>\n'
)


def test_the_frame_partial_is_the_one_allowed_frame_with_exactly_its_attributes() -> None:
    real = (TEMPLATES / FRAME_PARTIAL).read_text(encoding="utf-8")
    assert real == GOOD_FRAME
    assert iframe_violations(FRAME_PARTIAL, real) == []
    assert file_violations(FRAME_PARTIAL, real) == []


def test_an_iframe_in_any_other_template_is_refused() -> None:
    for name in ("pages/note.html", "partials/other.html", "layouts/app.html"):
        assert file_violations(name, GOOD_FRAME)
    assert file_violations("pages/note.html", "<div>\n  <IFRAME src=x></IFRAME>\n</div>\n")


@pytest.mark.parametrize(
    "bad",
    [
        GOOD_FRAME.replace('sandbox="allow-scripts"', 'sandbox="allow-scripts allow-same-origin"'),
        GOOD_FRAME.replace('sandbox="allow-scripts"', 'sandbox="allow-scripts allow-popups"'),
        GOOD_FRAME.replace('sandbox="allow-scripts"', 'sandbox=""'),
        GOOD_FRAME.replace('sandbox="allow-scripts" ', ""),
        GOOD_FRAME.replace('loading="lazy"', 'loading="lazy" srcdoc="&lt;p&gt;"'),
        GOOD_FRAME.replace('loading="lazy"', 'loading="lazy" sandbox="allow-scripts"'),
        GOOD_FRAME.replace('loading="lazy"', 'loading="lazy" allow="camera"'),
        GOOD_FRAME.replace('referrerpolicy="no-referrer"', 'referrerpolicy="origin"'),
        GOOD_FRAME.replace('src="{{ frame_src }}"', 'src="/app/instances/{{ id }}/raw"'),
        GOOD_FRAME + GOOD_FRAME,
    ],
)
def test_the_frame_partial_with_another_attribute_set_is_refused(bad: str) -> None:
    assert iframe_violations(FRAME_PARTIAL, bad)


def test_srcdoc_is_refused_in_every_template() -> None:
    assert file_violations("pages/note.html", '<div srcdoc="x"></div>\n')
