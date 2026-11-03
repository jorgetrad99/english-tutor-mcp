import pytest

from tutor.domain.html_guard import (
    MAX_TEMPLATE_BYTES,
    TEMPLATE_MARKER,
    HtmlProblem,
    check_html,
    check_template_html,
)

pytestmark = pytest.mark.unit

HEAD = "<head><meta charset='utf-8'><title>Clase 3</title></head>"


def doc(body: str = "", head: str = HEAD, start: str = "<!doctype html>") -> str:
    return f"{start}<html lang='es'>{head}<body>{body}</body></html>"


def test_constants() -> None:
    assert MAX_TEMPLATE_BYTES == 40_000  # room for JSON escaping in a 64 KB body
    assert TEMPLATE_MARKER == '<script type="application/json" id="tutor-params"></script>'


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
    assert len(padded(MAX_TEMPLATE_BYTES).encode()) == MAX_TEMPLATE_BYTES
    assert check_html(padded(MAX_TEMPLATE_BYTES)) == ()
    over = padded(MAX_TEMPLATE_BYTES + 1)
    assert len(over.encode()) == MAX_TEMPLATE_BYTES + 1
    assert check_html(over) == (HtmlProblem("too_large", "html"),)


def test_size_is_measured_in_utf8_bytes() -> None:
    body = "é" * (MAX_TEMPLATE_BYTES // 2)
    assert check_html(doc(body)) == (HtmlProblem("too_large", "html"),)


def test_too_large_short_circuits_other_checks() -> None:
    html = padded(MAX_TEMPLATE_BYTES + 100).replace("<body>", "<body><iframe></iframe>")
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


# --- the template marker (spec 15.5, amendment A51) --------------------------------------------

# The marker is the first content of <head>; the rest of the document comes after it.
REST = "</head><body><p>x</p><script>go()</script></body></html>"
META = "<meta charset='utf-8'>"  # required right before the marker (amendment A52)
MARKED = f"<!doctype html><html><head>{META}{TEMPLATE_MARKER}{REST}"
MISSING = HtmlProblem("marker_missing", "tutor-params")


def test_template_with_one_marker_passes() -> None:
    assert check_template_html(MARKED) == ()


@pytest.mark.parametrize(
    "html",
    [
        "<!doctype html><html><body><p>no marker</p></body></html>",
        MARKED.replace("<p>x</p>", TEMPLATE_MARKER),  # two markers
        MARKED.replace(TEMPLATE_MARKER, f"<!-- {TEMPLATE_MARKER} -->"),  # only in a comment
        MARKED.replace(  # only inside another script's text
            TEMPLATE_MARKER, "<script>const m = '<div id=\"x\"></div>';</script>"
        ),
        MARKED.replace("<p>x</p>", '<p id="tutor-bridge">x</p>'),  # a reserved id
        MARKED.replace("<p>x</p>", '<div id="tutor-params"></div>'),  # a second params element
        MARKED.replace(
            TEMPLATE_MARKER, '<script type="application/json" id="tutor-params">{}</script>'
        ),  # not empty
    ],
)
def test_marker_rule(html: str) -> None:
    assert check_template_html(html)[-1] == MISSING


# The exact start tag but a different order of attributes, as a real element.
REORDERED = '<script id="tutor-params" type="application/json"></script>'
EXACT_START = '<script type="application/json" id="tutor-params">'


@pytest.mark.parametrize(
    "html",
    [
        # The marker text where a browser sees no element, plus a real reordered element.
        MARKED.replace(
            TEMPLATE_MARKER, f"<script>var m = '{TEMPLATE_MARKER}';</script>{REORDERED}"
        ),
        MARKED.replace(TEMPLATE_MARKER, f"<div title='{TEMPLATE_MARKER}'></div>{REORDERED}"),
        MARKED.replace(TEMPLATE_MARKER, f"<!-- {TEMPLATE_MARKER} -->{REORDERED}"),
        MARKED.replace(TEMPLATE_MARKER, REORDERED),  # reordered alone
        MARKED.replace(TEMPLATE_MARKER, "<script id=tutor-params type=application/json></script>"),
        # The exact start tag as a real element, but not where the marker text is.
        MARKED.replace(TEMPLATE_MARKER, f"<!-- {TEMPLATE_MARKER} -->{EXACT_START} </script>"),
        MARKED.replace(
            TEMPLATE_MARKER, f"<p title='{TEMPLATE_MARKER}'></p>{EXACT_START}\n</script>"
        ),
    ],
    ids=[
        "js-string",
        "attribute",
        "comment",
        "reordered",
        "unquoted",
        "comment-exact",
        "attr-exact",
    ],
)
def test_marker_must_be_the_real_params_element(html: str) -> None:
    # Security review P3: compose_document fills the first marker text, so that text must be
    # the one tutor-params element a browser sees, with exactly the marker's start tag.
    assert check_template_html(html)[-1] == MISSING


def test_marker_position_survives_a_bom_and_crlf_lines() -> None:
    crlf = "\r\n"
    lines = ["<!doctype html>", "<html>", "<head>", META, f"  {TEMPLATE_MARKER}", "</head>"]
    html = chr(0xFEFF) + crlf.join(lines)
    assert check_template_html(html) == ()


def test_marker_problem_comes_after_the_html_problems() -> None:
    html = MARKED.replace("<p>x</p>", "<iframe></iframe>").replace(TEMPLATE_MARKER, "")
    assert check_template_html(html) == (HtmlProblem("tag_forbidden", "iframe"), MISSING)


def test_oversized_template_reports_too_large_alone() -> None:
    body = "x" * (MAX_TEMPLATE_BYTES - len(MARKED) + 2)  # replaces the one "x"
    over = MARKED.replace("<p>x</p>", f"<p>{body}</p>")
    assert len(over.encode()) == MAX_TEMPLATE_BYTES + 1
    assert check_template_html(over) == (HtmlProblem("too_large", "html"),)
    fits = MARKED.replace("<p>x</p>", f"<p>{body[:-1]}</p>")
    assert len(fits.encode()) == MAX_TEMPLATE_BYTES
    assert check_template_html(fits) == ()


# --- the marker must open <head> (security pass, amendment A51) --------------------------------
#
# html.parser does not match a browser, so no parser decides where the marker is: only a
# doctype, <html> and <head>, then the required <meta charset>, may precede it, in that order,
# with ASCII whitespace between. Everything below breaks that prefix and is refused.


def before_marker(prefix: str, start: str = f"<!doctype html><html><head>{META}") -> str:
    return f"{start}{prefix}{TEMPLATE_MARKER}{REST}"


@pytest.mark.parametrize(
    "prefix",
    [
        # End tags that html.parser lets pop the stack past an inert element.
        "<div><textarea></div>",
        "<div><template></div>",
        "<plaintext></plaintext>",
        "<p><noscript></p>",
        # Markup html.parser reads differently from a browser.
        "<template></ template>",
        "<!-->",
        "<!--->",
        "<!-- a -- >",
        "<!-- a --!>",
        "<![CDATA[x]]>",
        "<![CDATA[",
    ],
    ids=[
        "textarea-popped",
        "template-popped",
        "plaintext-closed",
        "noscript-popped",
        "spaced-end-tag",
        "comment-abrupt",
        "comment-abrupt-dash",
        "comment-spaced-close",
        "comment-bang-close",
        "cdata",
        "cdata-open",
    ],
)
def test_parser_differential_exploits_before_the_marker_are_refused(prefix: str) -> None:
    assert check_template_html(before_marker(prefix))[-1] == MISSING


@pytest.mark.parametrize(
    "prefix",
    [
        "<svg></svg>",
        "<svg><style></style></svg>",
        "<svg><title>t</title></svg>",
        "<svg><![CDATA[x]]></svg>",
        "<math><mi>x</mi></math>",
        "<math><mtext><textarea></textarea></mtext></math>",
        "<!-- note -->",
        "<!---->",
        "<!-- <script> -->",
        "<!>",
        "<?xml version='1.0'?>",
    ],
)
def test_svg_math_and_comments_before_the_marker_are_refused(prefix: str) -> None:
    assert check_template_html(before_marker(prefix))[-1] == MISSING


@pytest.mark.parametrize(
    "html",
    [
        before_marker("x"),  # text
        before_marker("", start="<!doctype html><html><head>"),  # no meta charset (A52)
        before_marker("", start="<!doctype html><html><head>\n"),
        before_marker("", start=""),
        before_marker("<head>", start="<!doctype html><html>"),
        before_marker("<title>t</title>"),
        before_marker('<meta name="viewport" content="width=device-width">'),
        before_marker("<style></style>"),
        before_marker("<script></script>"),
        before_marker("<template></template>"),
        before_marker("<noscript></noscript>"),
        before_marker("<textarea></textarea>"),
        before_marker("<br>"),
        before_marker("<meta charset='utf-8'><meta charset='utf-8'>"),  # twice
        before_marker("", start="<!doctype html><head><html>"),  # out of order
        before_marker("", start="<!doctype html><!doctype html>"),
        before_marker("", start="<!doctype html public '-//W3C//DTD HTML 4.01//EN'>"),
        before_marker("", start="<!doctype html><head lang='es'>"),  # head takes no attributes
        before_marker("", start="<!doctype html><html lang='a>b'>"),
        before_marker("", start='<!doctype html><html lang="<script>">'),
        before_marker("", start="<!doctype html><html/>"),
        before_marker("", start="<!doctype html><html><body>"),
        before_marker("", start="<!doctype html>" + chr(0xA0) + "<html>"),  # not ASCII space
        before_marker("", start="<!doctype html><html>" + chr(0x2003)),
        before_marker("", start="<!doctype html><meta charset='utf-8' http-equiv='x'>"),
        before_marker("", start="<!doctype html><meta char" + chr(0x17F) + "et='utf-8'>"),
        before_marker("", start="<!doctype html><meta charset=''>"),
        before_marker("", start="<!doctype html><meta charset='windows-1252'>"),  # only utf-8
        # A browser reads an unquoted value up to whitespace or `>`: this charset is `utf-8/`.
        before_marker("", start="<!doctype html><meta charset=utf-8/>"),
        before_marker("", start="<!doctype html><meta charset=utf-8//>"),
        before_marker("", start=chr(0xFEFF) + chr(0xFEFF) + "<!doctype html>"),
        f"<!doctype html><html><body><p>x</p>{TEMPLATE_MARKER}</body></html>",  # in the body
    ],
)
def test_anything_else_before_the_marker_is_refused(html: str) -> None:
    assert check_template_html(html)[-1] == MISSING


@pytest.mark.parametrize(
    "start",
    [
        f"<!doctype html>{META}",
        f"<!DOCTYPE html>{META}",
        f"<!DocType HTML>{META}",
        f"<!doctype html><html>{META}",
        f"<HTML LANG=ES>{META}",
        f"<html lang='es' dir=rtl data-theme=\"dark\" hidden>{META}",
        '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n',
        f"<!doctype html><head>{META}",
        "<!doctype html><meta charset=utf-8>",
        "<!doctype html><meta charset=utf-8 />",
        "<!doctype html><meta charset=utf-8\t>",
        "<!doctype html><meta charset='utf-8'/>",
        "<!doctype html><html><head><meta charset='UTF-8' />",
        '<!doctype html><html><HEAD><META CHARSET="utf-8">',
        chr(0xFEFF) + f"<!doctype html>\r\n<html>\r\n<head>\r\n{META}\r\n",
        f" \t\n\f\r<!doctype html> \t<html >\n\n<head>\t{META}\t",
    ],
)
def test_legal_prefixes_are_accepted(start: str) -> None:
    assert check_template_html(f"{start}{TEMPLATE_MARKER}{REST}") == ()


@pytest.mark.parametrize("start", [META, f"<head>{META}"])
def test_a_marker_first_fragment_only_lacks_the_doctype(start: str) -> None:
    html = f"{start}{TEMPLATE_MARKER}{REST}"
    assert check_template_html(html) == (HtmlProblem("not_document", "doctype"),)


@pytest.mark.parametrize("start", ["", "<head>"])
def test_a_fragment_without_the_meta_lacks_the_doctype_and_the_marker(start: str) -> None:
    html = f"{start}{TEMPLATE_MARKER}{REST}"
    assert check_template_html(html) == (HtmlProblem("not_document", "doctype"), MISSING)


# --- one meta charset, the prefix one (security re-review P3, amendment A52) -------------------
#
# Bytes served without a charset leave the encoding tentative, so a later <meta charset> can
# make a browser re-decode the page. No parser decides where a meta is: after the prefix, the
# word `charset` may not appear at all, in any case.

CHARSET_FORBIDDEN = HtmlProblem("tag_forbidden", "meta[charset]")


@pytest.mark.parametrize(
    "after",
    [
        "<meta charset='windows-1252'>",
        "<meta charset='utf-8'>",  # even the same one
        "<META CHARSET=ISO-2022-JP>",
        "<meta name='x>' charset='windows-1252'>",
        "<meta name='x' content='a' charset=utf-16>",
        "<svg><meta charset='windows-1252'/></svg>",
        "<!-- a --!><meta charset='windows-1252'>",
        "<![CDATA[<meta charset='windows-1252'>",
    ],
)
def test_a_second_meta_charset_is_refused(after: str) -> None:
    for html in (
        MARKED.replace("<p>x</p>", f"<p>x</p>{after}"),
        MARKED.replace(f"{TEMPLATE_MARKER}</head>", f"{TEMPLATE_MARKER}{after}</head>"),
    ):
        assert CHARSET_FORBIDDEN in check_template_html(html), html


def test_the_prefix_meta_alone_is_not_a_second_one() -> None:
    assert check_template_html(MARKED) == ()
    assert check_html(doc()) == ()  # check_html keeps no charset rule: HEAD has a meta


@pytest.mark.parametrize(
    "after",
    [
        "<title>t</title><style>p { color: red }</style>",
        "<template><p>row</p></template>",
        "<noscript>Enable scripts</noscript>",
        "<textarea>notes</textarea>",
        "<template><div></template>",
        "<!-- a comment -->",
        "<svg><title>t</title></svg><math><mi>x</mi></math>",
        "<br/><img alt='x'><input>",
    ],
)
def test_anything_may_follow_the_marker(after: str) -> None:
    html = MARKED.replace("<p>x</p>", f"<p>x</p>{after}")
    assert check_template_html(html) == ()
