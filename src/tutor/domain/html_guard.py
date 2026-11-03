"""Template HTML guard (subjects spec 4.4, 15.5): a deny list over the standard-library parser.

Hygiene only: the sandbox (`Content-Security-Policy: sandbox allow-scripts`, `<iframe sandbox>`)
is the security boundary (spec 10). Problems name the tag or attribute, never the content.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

# UTF-8 bytes; leaves room for JSON escaping inside the 64 KB request body (BodySizeGuard).
# Migration 0006 checks the same number on exercise_templates.bytes.
MAX_TEMPLATE_BYTES = 40_000

# The one empty element a template must contain: the server puts the parameters (as JSON) in it
# and its bridge script right after it (spec 15.5). The parser must see this exact start tag as
# the one tutor-params element, at the offset of the marker text (security review P3).
MARKER_START_TAG = '<script type="application/json" id="tutor-params">'
TEMPLATE_MARKER = MARKER_START_TAG + "</script>"
_RESERVED_IDS = frozenset({"tutor-params", "tutor-bridge"})

HtmlProblemCode = Literal[
    "too_large",
    "not_document",
    "tag_forbidden",
    "attr_forbidden",
    "url_forbidden",
    "css_forbidden",
    "marker_missing",
]


@dataclass(frozen=True, slots=True)
class HtmlProblem:
    code: HtmlProblemCode
    detail: str


_FORBIDDEN_TAGS = frozenset(
    {"iframe", "frame", "frameset", "object", "embed", "base", "link", "form", "applet"}
)
_URL_ATTRS = frozenset(
    {"src", "href", "srcset", "poster", "data", "action", "formaction", "xlink:href"}
)
_CSS_TOKENS = ("url(", "@import", "expression(")
_ATTR_TOKENS = ("url(", "@import")
_DOCUMENT_START = re.compile(r"\s*(?:<!doctype\s+html\b|<html\b)", re.IGNORECASE)
_SAFE_NAME = re.compile(r"[a-z][a-z0-9:_.-]{0,31}")
_WHITESPACE = re.compile(r"\s+")
_BOM = chr(0xFEFF)


def _safe(name: str, fallback: str) -> str:
    return name if _SAFE_NAME.fullmatch(name) else fallback


def _has_css_token(value: str) -> bool:
    compact = _WHITESPACE.sub("", value).lower()
    return any(token in compact for token in _CSS_TOKENS)


class _Guard(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[HtmlProblem] = []
        self._style: list[str] | None = None
        self.reserved_ids: list[str] = []  # tutor-params / tutor-bridge elements, in order
        self.params_tags: list[tuple[str, int]] = []  # (start tag text, offset) of tutor-params
        self._line_starts = [0] + [i + 1 for i, ch in enumerate(html) if ch == "\n"]

    def _offset(self) -> int:
        line, column = self.getpos()  # the parser counts lines by "\n", from 1
        return self._line_starts[line - 1] + column

    def _add(self, code: HtmlProblemCode, detail: str) -> None:
        problem = HtmlProblem(code, detail)
        if problem not in self.problems:
            self.problems.append(problem)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ids = [v for n, v in attrs if n == "id" and v in _RESERVED_IDS]
        self.reserved_ids += ids
        if "tutor-params" in ids:
            self.params_tags.append((self.get_starttag_text() or "", self._offset()))
        if tag in _FORBIDDEN_TAGS:
            self._add("tag_forbidden", tag)
            return
        if tag == "meta" and any(name == "http-equiv" for name, _ in attrs):
            self._add("tag_forbidden", "meta[http-equiv]")
            return
        element = _safe(tag, "element")
        for name, value in attrs:
            self._check_attr(element, name, value or "")
        if tag == "style":
            self._style = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._flush_style()

    def handle_data(self, data: str) -> None:
        if self._style is not None:
            self._style.append(data)

    def close(self) -> None:
        super().close()
        if self._style is not None:
            self._style.append(self.rawdata)  # an unterminated <style> keeps its tail here
        self._flush_style()

    def _flush_style(self) -> None:
        if self._style is None:
            return
        css = "".join(self._style)
        self._style = None
        if _has_css_token(css):
            self._add("css_forbidden", "style")

    def _check_attr(self, element: str, name: str, value: str) -> None:
        detail = f"{element}[{_safe(name, 'attribute')}]"
        stripped = value.strip()
        if name == "style":
            if _has_css_token(stripped):
                self._add("css_forbidden", detail)
            return
        if stripped.lower().startswith("data:"):
            self._add("url_forbidden", detail)
            return
        if name in _URL_ATTRS:
            if stripped.startswith("#") or (name == "href" and not stripped):
                return
            self._add("url_forbidden", detail)
            return
        compact = _WHITESPACE.sub("", stripped).lower()
        if any(token in compact for token in _ATTR_TOKENS):
            self._add("attr_forbidden", detail)


def check_html(html: str) -> tuple[HtmlProblem, ...]:
    """Every rule of spec 4.4 the document breaks, in document order, without duplicates.

    An oversized document is reported alone. `too_large` and `not_document` carry `html` and
    `doctype`; tag problems carry the tag (`meta[http-equiv]` for a meta refresh); attribute and
    inline-style problems carry `tag[attr]`; a `<style>` element carries `style`.
    """
    return _check(html)[0]


def _check(html: str) -> tuple[tuple[HtmlProblem, ...], _Guard | None]:
    if len(html.encode("utf-8", "surrogatepass")) > MAX_TEMPLATE_BYTES:
        return (HtmlProblem("too_large", "html"),), None
    guard = _Guard(html)
    if not _DOCUMENT_START.match(html.lstrip(_BOM)):
        guard.problems.append(HtmlProblem("not_document", "doctype"))
    guard.feed(html)
    guard.close()
    return tuple(guard.problems), guard


def check_template_html(html: str) -> tuple[HtmlProblem, ...]:
    """`check_html` plus the marker rule (spec 15.5): exactly one empty
    `<script type="application/json" id="tutor-params"></script>` and no other element with id
    `tutor-params` or `tutor-bridge`; otherwise `marker_missing` (detail `tutor-params`).

    The marker text must also be the element the parser sees: the one tutor-params start tag
    is exactly `MARKER_START_TAG` and starts where the marker text starts, so a marker inside
    a script string, an attribute or a comment cannot stand in for it (compose_document fills
    the first marker text)."""
    problems, guard = _check(html)
    if guard is None:
        return problems
    bound = guard.params_tags == [(MARKER_START_TAG, html.find(TEMPLATE_MARKER))]
    if (
        html.count(TEMPLATE_MARKER) != 1
        or tuple(guard.reserved_ids) != ("tutor-params",)
        or not bound
    ):
        return (*problems, HtmlProblem("marker_missing", "tutor-params"))
    return problems
