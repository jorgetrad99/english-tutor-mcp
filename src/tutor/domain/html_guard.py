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
# and its bridge script right after it (spec 15.5). It must be the first content of <head>
# (amendment A51), so no parser has to decide whether a browser would see it.
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

# What may precede the marker (amendment A51): each part optional, in this order, with ASCII
# whitespace only around them: one BOM, `<!doctype html>`, `<html>` with plain attributes,
# `<head>` without attributes, `<meta charset=utf-8>`. No comment, text or other tag, so the
# marker is the first content of the head however the rest of the document parses. Written by
# hand rather than with html.parser, whose quirks differ from browsers and between Python
# patch releases (security pass P2, P3). re.ASCII keeps IGNORECASE from folding non-ASCII
# letters (such as U+017F, long s) into ASCII ones.
_WS = r"[\t\n\f\r ]"
_ATTR_VALUE = r"""(?:"[^"<>\x00]*"|'[^'<>\x00]*'|[^\t\n\f\r "'=<>`\x00]+)"""
_HTML_ATTR = rf"{_WS}+[a-z][a-z0-9-]*(?:{_WS}*={_WS}*{_ATTR_VALUE})?"
_CHARSET = r"""(?:"utf-8"|'utf-8'|utf-8)"""  # another charset could re-decode the page
_MARKER_PREFIX = re.compile(
    "".join(
        (
            rf"\A{_BOM}?{_WS}*",
            rf"(?:<!doctype html>{_WS}*)?",
            rf"(?:<html(?:{_HTML_ATTR})*{_WS}*>{_WS}*)?",
            rf"(?:<head>{_WS}*)?",
            rf"(?:<meta{_WS}+charset{_WS}*={_WS}*{_CHARSET}{_WS}*/?>{_WS}*)?",
        )
    ),
    re.ASCII | re.IGNORECASE,
)


def _safe(name: str, fallback: str) -> str:
    return name if _SAFE_NAME.fullmatch(name) else fallback


def _has_css_token(value: str) -> bool:
    compact = _WHITESPACE.sub("", value).lower()
    return any(token in compact for token in _CSS_TOKENS)


def _marker_comes_first(html: str) -> bool:
    """True when the marker text sits right after an allowed prefix (`_MARKER_PREFIX`)."""
    prefix = _MARKER_PREFIX.match(html)
    return prefix is not None and html.startswith(TEMPLATE_MARKER, prefix.end())


class _Guard(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[HtmlProblem] = []
        self._style: list[str] | None = None
        self.reserved_ids: list[str] = []  # tutor-params / tutor-bridge elements, in order

    def _add(self, code: HtmlProblemCode, detail: str) -> None:
        problem = HtmlProblem(code, detail)
        if problem not in self.problems:
            self.problems.append(problem)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.reserved_ids += [v for n, v in attrs if n == "id" and v in _RESERVED_IDS]
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

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag == "style":
            self._flush_style()  # the parser saw no content

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
    guard = _Guard()
    if not _DOCUMENT_START.match(html.lstrip(_BOM)):
        guard.problems.append(HtmlProblem("not_document", "doctype"))
    guard.feed(html)
    guard.close()
    return tuple(guard.problems), guard


def check_template_html(html: str) -> tuple[HtmlProblem, ...]:
    """`check_html` plus the marker rule (spec 15.5, amendment A51): exactly one empty
    `<script type="application/json" id="tutor-params"></script>`, as the first content of the
    head, and no other element with id `tutor-params` or `tutor-bridge`; otherwise
    `marker_missing` (detail `tutor-params`).

    Only `<!doctype html>`, `<html ...>`, `<head>` and `<meta charset="utf-8">` may precede the
    marker, in that order and with ASCII whitespace between (`_MARKER_PREFIX`). A browser then
    builds the marker as the head's first element before anything that could hide or swallow
    it, and compose_document fills that same first marker text."""
    problems, guard = _check(html)
    if guard is None:
        return problems
    if (
        not _marker_comes_first(html)
        or html.count(TEMPLATE_MARKER) != 1
        or tuple(guard.reserved_ids) != ("tutor-params",)
    ):
        return (*problems, HtmlProblem("marker_missing", "tutor-params"))
    return problems
