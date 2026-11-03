import pytest
from tutor.domain.html_guard import (
    ARTIFACT_KINDS,
    ARTIFACT_TITLE_MAX,
    MAX_ARTIFACT_BYTES,
    HtmlProblem,
    check_html,
)

pytestmark = pytest.mark.unit

HEAD = "<head><meta charset='utf-8'><title>Clase 3</title></head>"


def doc(body: str = "", head: str = HEAD, start: str = "<!doctype html>") -> str:
    return f"{start}<html lang='es'>{head}<body>{body}</body></html>"


def test_constants() -> None:
    assert MAX_ARTIFACT_BYTES == 262_144
    assert ARTIFACT_KINDS == ("lesson_view", "exercise_view", "other")
    assert ARTIFACT_TITLE_MAX == 200


# --- Review Focus 4 ----------------------------------------------------------------------------


def test_review_focus_4_iframe_is_rejected() -> None:
    # Review Focus 4
    problems = check_html(doc("<iframe src='https://example.test'></iframe>"))
    assert problems == (HtmlProblem("tag_forbidden", "iframe"),)


def test_review_focus_4_meta_refresh_is_rejected() -> None:
    # Review Focus 4
    head = HEAD + "<meta http-equiv='refresh' content='0;url=https://example.test'>"
    assert check_html(doc(head=head)) == (HtmlProblem("tag_forbidden", "meta[http-equiv]"),)


def test_review_focus_4_absolute_link_is_rejected() -> None:
    # Review Focus 4
    problems = check_html(doc("<a href='https://example.test/x'>x</a>"))
    assert problems == (HtmlProblem("url_forbidden", "a[href]"),)


def test_review_focus_4_data_url_image_is_rejected() -> None:
    # Review Focus 4
    problems = check_html(doc("<img src='data:image/png;base64,AAAA' alt='x'>"))
    assert problems == (HtmlProblem("url_forbidden", "img[src]"),)


def test_review_focus_4_style_attribute_with_url_is_rejected() -> None:
    # Review Focus 4
    problems = check_html(doc("<div style='background: url(https://example.test/a.png)'>x</div>"))
    assert problems == (HtmlProblem("css_forbidden", "div[style]"),)


def test_review_focus_4_inline_script_style_and_fragment_links_are_accepted() -> None:
    # Review Focus 4
    body = (
        "<nav><a href='#intro'>Intro</a> <a href='#ex1'>Ejercicio</a> <a href=''>aquí</a></nav>"
        "<h1 id='intro'>Acorde de séptima</h1>"
        "<button onclick=\"document.getElementById('ex1').hidden = false\">Mostrar</button>"
        "<section id='ex1' hidden><p style='color: #333; margin: 0 auto'>C7 G7</p></section>"
        "<style>body { font-family: sans-serif; } .tab { color: red }</style>"
        "<script>const notas = ['C', 'E', 'G', 'Bb']; console.log(notas.length);</script>"
    )
    assert check_html(doc(body)) == ()


# --- size and document shape ---------------------------------------------------------------


def padded(size: int) -> str:
    shell = doc("{}")
    return shell.format("x" * (size - len(shell.encode()) + 2))


def test_exactly_the_byte_cap_is_accepted_and_one_more_is_too_large() -> None:
    assert len(padded(MAX_ARTIFACT_BYTES).encode()) == MAX_ARTIFACT_BYTES
    assert check_html(padded(MAX_ARTIFACT_BYTES)) == ()
    over = padded(MAX_ARTIFACT_BYTES + 1)
    assert len(over.encode()) == MAX_ARTIFACT_BYTES + 1
    assert check_html(over) == (HtmlProblem("too_large", "html"),)


def test_size_is_measured_in_utf8_bytes() -> None:
    body = "é" * (MAX_ARTIFACT_BYTES // 2)
    assert check_html(doc(body)) == (HtmlProblem("too_large", "html"),)


def test_too_large_short_circuits_other_checks() -> None:
    html = padded(MAX_ARTIFACT_BYTES + 100).replace("<body>", "<body><iframe></iframe>")
    assert check_html(html) == (HtmlProblem("too_large", "html"),)


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   ",
        "hola",
        "<div>hola</div>",
        "<htmlx>",
        "<!-- comment --><!doctype html><html></html>",
        "<?xml version='1.0'?><html></html>",
    ],
)
def test_not_a_document(html: str) -> None:
    assert check_html(html)[:1] == (HtmlProblem("not_document", "doctype"),)


@pytest.mark.parametrize(
    "start",
    [
        "<!doctype html>",
        "<!DOCTYPE HTML>",
        "<!DOCTYPE html PUBLIC '-//W3C//DTD XHTML 1.0 Strict//EN'>",
        "  \n\t<!doctype html>",
        chr(0xFEFF) + "<!doctype html>",
        "",
        "\n",
    ],
)
def test_document_starts(start: str) -> None:
    assert check_html(doc(start=start)) == ()


def test_html_start_without_doctype_is_a_document() -> None:
    assert check_html("<HTML><body>x</body></HTML>") == ()
    assert check_html("<html lang='es'>") == ()


def test_not_document_is_reported_with_the_other_problems() -> None:
    assert check_html("<div><iframe></iframe></div>") == (
        HtmlProblem("not_document", "doctype"),
        HtmlProblem("tag_forbidden", "iframe"),
    )


# --- tags ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag",
    ["iframe", "frame", "frameset", "object", "embed", "base", "link", "form", "applet"],
)
def test_forbidden_tags(tag: str) -> None:
    assert check_html(doc(f"<{tag}></{tag}>")) == (HtmlProblem("tag_forbidden", tag),)
    assert check_html(doc(f"<{tag.upper()}>")) == (HtmlProblem("tag_forbidden", tag),)


def test_self_closing_forbidden_tag_reports_only_the_tag() -> None:
    head = HEAD + "<link rel='stylesheet' href='https://example.test/a.css'/>"
    assert check_html(doc(head=head)) == (HtmlProblem("tag_forbidden", "link"),)


def test_meta_without_http_equiv_is_allowed() -> None:
    head = "<head><meta charset='utf-8'><meta name='viewport' content='width=device-width'></head>"
    assert check_html(doc(head=head)) == ()


def test_meta_http_equiv_is_case_insensitive() -> None:
    head = HEAD + "<META HTTP-EQUIV='Content-Security-Policy' content='default-src *'>"
    assert check_html(doc(head=head)) == (HtmlProblem("tag_forbidden", "meta[http-equiv]"),)


# --- attributes ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "attr", "value"),
    [
        ("img", "src", "https://example.test/a.png"),
        ("img", "src", "/a.png"),
        ("img", "src", ""),
        ("img", "SRC", "a.png"),
        ("a", "href", "javascript:alert(1)"),
        ("a", "href", "relative/page.html"),
        ("img", "srcset", "a.png 1x, b.png 2x"),
        ("video", "poster", "p.png"),
        ("div", "data", "x"),
        ("div", "action", "/x"),
        ("button", "formaction", "/x"),
        ("use", "xlink:href", "https://example.test/icons.svg#a"),
        ("script", "src", "https://example.test/x.js"),
        ("audio", "src", "#not-really-a-fragment-but-fine"),
    ],
)
def test_url_attributes_that_are_not_fragments(tag: str, attr: str, value: str) -> None:
    problems = check_html(doc(f"<{tag} {attr}='{value}'></{tag}>"))
    expected = HtmlProblem("url_forbidden", f"{tag}[{attr.lower()}]")
    if value.startswith("#"):
        assert problems == ()
    else:
        assert problems == (expected,)


@pytest.mark.parametrize(
    "body",
    [
        "<a href=''>x</a>",
        "<a href>x</a>",
        "<a href='#top'>x</a>",
        "<a href=' #top '>x</a>",
        "<img src='#'>",
        "<use xlink:href='#icon'></use>",
        "<button formaction='#f'>x</button>",
        "<div data-note='3' data-x='url'>x</div>",
    ],
)
def test_fragment_and_empty_href_are_allowed(body: str) -> None:
    assert check_html(doc(body)) == ()


@pytest.mark.parametrize(
    "body",
    [
        "<div title='data:text/html,x'>x</div>",
        "<div title='  DATA:text/plain,x'>x</div>",
        "<object data='data:x'></object>",
    ],
)
def test_data_urls_are_rejected_in_any_attribute(body: str) -> None:
    problems = check_html(doc(body))
    assert problems and problems[0].code in ("url_forbidden", "tag_forbidden")


def test_data_url_in_a_plain_attribute_names_the_attribute() -> None:
    assert check_html(doc("<div title='data:text/html,x'>x</div>")) == (
        HtmlProblem("url_forbidden", "div[title]"),
    )


@pytest.mark.parametrize(
    "value",
    ["url(x)", "URL(x)", "a @import x", "&#117;rl(x)", "&#64;import x"],
)
def test_attributes_with_url_or_import_are_rejected(value: str) -> None:
    problems = check_html(doc(f'<div foo="{value}">x</div>'))
    assert problems == (HtmlProblem("attr_forbidden", "div[foo]"),)


# --- css -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "background: url(https://example.test/a.png)",
        "background: URL (a.png)",
        "@import 'x.css'",
        "width: expression(alert(1))",
        "width: EXPRESSION (1)",
        "background: &#117;rl(x)",
    ],
)
def test_style_attribute_css_is_rejected(value: str) -> None:
    assert check_html(doc(f'<div style="{value}">x</div>')) == (
        HtmlProblem("css_forbidden", "div[style]"),
    )


@pytest.mark.parametrize(
    "css",
    [
        "body { background: url(https://example.test/a.png) }",
        "body { background: URL( a.png ) }",
        "@import url('x.css');",
        "@IMPORT 'x.css';",
        ".x { width: expression(alert(1)) }",
        "body { background: u\nrl(a.png) }",
    ],
)
def test_style_element_css_is_rejected(css: str) -> None:
    assert check_html(doc(f"<style>{css}</style>")) == (HtmlProblem("css_forbidden", "style"),)


def test_style_element_css_without_urls_is_accepted() -> None:
    css = "body { color: #123; } .url-like { content: 'url'; } @media (max-width: 600px) {}"
    assert check_html(doc(f"<style>{css}</style>")) == ()


def test_unclosed_style_element_is_still_scanned() -> None:
    html = doc("<style>body { background: url(a.png) }")
    assert check_html(html) == (HtmlProblem("css_forbidden", "style"),)


def test_script_content_is_not_scanned_for_css() -> None:
    assert check_html(doc("<script>const s = 'url(x) @import';</script>")) == ()


# --- details and ordering ---------------------------------------------------------------------


def test_details_never_quote_content() -> None:
    body = (
        "<img src='https://evil.example/secret.png'>"
        "<div foo='url(secret)' style='background: url(secret)'>x</div>"
        "<style>body { background: url(secret) }</style>"
    )
    problems = check_html(doc(body))
    assert len(problems) == 4
    for problem in problems:
        assert "secret" not in problem.detail and "evil" not in problem.detail
        assert len(problem.detail) <= 64


def test_problems_are_in_document_order_without_duplicates() -> None:
    body = (
        "<a href='https://example.test/1'>1</a>"
        "<iframe></iframe>"
        "<a href='https://example.test/2'>2</a>"
        "<iframe></iframe>"
    )
    assert check_html(doc(body)) == (
        HtmlProblem("url_forbidden", "a[href]"),
        HtmlProblem("tag_forbidden", "iframe"),
    )


def test_forbidden_tag_attributes_are_not_reported_separately() -> None:
    body = "<form action='https://example.test' style='background:url(x)'></form>"
    assert check_html(doc(body)) == (HtmlProblem("tag_forbidden", "form"),)


def test_odd_names_are_not_echoed() -> None:
    long_attr = "a" * 40
    problems = check_html(doc(f"<div {long_attr}='url(x)' x$y='url(x)'>x</div>"))
    assert problems == (HtmlProblem("attr_forbidden", "div[attribute]"),)
    problems = check_html(doc("<zz$$ src='https://example.test'></zz$$>"))
    assert problems == (HtmlProblem("url_forbidden", "element[src]"),)


def test_problem_is_frozen() -> None:
    problem = HtmlProblem("tag_forbidden", "iframe")
    with pytest.raises(AttributeError):
        problem.detail = "x"  # type: ignore[misc]
