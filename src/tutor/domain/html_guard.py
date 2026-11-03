"""Artifact HTML guard (subjects spec 4.4): a deny list over the standard-library parser.

Hygiene only: the sandbox (`Content-Security-Policy: sandbox allow-scripts`, `<iframe sandbox>`)
is the security boundary (spec 10). Problems name the tag or attribute, never the content.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

# UTF-8 bytes; leaves room for JSON escaping inside the 64 KB request body (BodySizeGuard).
# The database CHECK (49,152 bytes, migration 0006) is only a backstop.
MAX_ARTIFACT_BYTES = 40_000
ARTIFACT_TITLE_MAX = 200

ArtifactKind = Literal["lesson_view", "exercise_view", "other"]
ARTIFACT_KINDS: tuple[ArtifactKind, ...] = ("lesson_view", "exercise_view", "other")

HtmlProblemCode = Literal[
    "too_large",
    "not_document",
    "tag_forbidden",
    "attr_forbidden",
    "url_forbidden",
    "css_forbidden",
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
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[HtmlProblem] = []
        self._style: list[str] | None = None

    def _add(self, code: HtmlProblemCode, detail: str) -> None:
        problem = HtmlProblem(code, detail)
        if problem not in self.problems:
            self.problems.append(problem)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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
    if len(html.encode("utf-8", "surrogatepass")) > MAX_ARTIFACT_BYTES:
        return (HtmlProblem("too_large", "html"),)
    guard = _Guard()
    if not _DOCUMENT_START.match(html.lstrip(_BOM)):
        guard.problems.append(HtmlProblem("not_document", "doctype"))
    guard.feed(html)
    guard.close()
    return tuple(guard.problems)
