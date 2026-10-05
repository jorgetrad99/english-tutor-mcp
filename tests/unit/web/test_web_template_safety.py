"""Template and code rules that keep the CSP strict and learner text inert (spec 9.6).
Task 29 extends this file; do not duplicate these checks elsewhere."""

import re
from pathlib import Path

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
}
JS_TEMPLATE_RULES = ("safe filter",)


def template_violations() -> list[str]:
    found: list[str] = []
    for path in sorted(TEMPLATES.rglob("*")):
        if path.suffix not in {".html", ".js"}:
            continue
        rules = (
            TEMPLATE_RULES
            if path.suffix == ".html"
            else {k: TEMPLATE_RULES[k] for k in JS_TEMPLATE_RULES}
        )
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ALLOW in line:
                continue
            for name, pattern in rules.items():
                if pattern.search(line):
                    found.append(f"{path.relative_to(TEMPLATES)}:{number}: {name}: {line.strip()}")
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
