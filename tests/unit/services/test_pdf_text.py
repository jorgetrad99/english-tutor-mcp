"""PDF text extraction (subjects spec 10; slice D ruling 58, controller decision Q3).

Most cases call the child's `parse` in this process, which is fast; a few run `extract` end to
end, through the child interpreter, to pin the process boundary and the kill at the deadline.
"""

from __future__ import annotations

import logging

import pytest

from tutor.domain.notes import MAX_WEB_NOTE_CHARS
from tutor.services import _pdf_worker, pdf_text
from tutor.services._pdf_worker import parse
from tutor.services.pdf_text import (
    MAX_PDF_BYTES,
    MAX_PDF_PAGES,
    PdfProblem,
    extract,
    read_answer,
)

from .pdf_fixtures import build_pdf

pytestmark = pytest.mark.unit

LESSON = "El acorde de septima tiene cuatro notas"
PASSWORD = "clave-de-prueba"  # noqa: S105 - a fixture's throwaway password


def test_problem_codes_are_a_closed_enum() -> None:
    assert [p.value for p in PdfProblem] == [
        "too_large",
        "not_pdf",
        "encrypted",
        "too_many_pages",
        "unreadable",
        "no_text",
        "too_long",
        "timeout",
    ]


# --- the parent's checks before any parsing --------------------------------------------------


def test_a_file_over_20_mb_is_refused_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_child(*args: object, **kwargs: object) -> None:
        raise AssertionError("parsed")

    monkeypatch.setattr(pdf_text.subprocess, "run", no_child)
    assert extract(b"%PDF-1.7\n" + b"x" * MAX_PDF_BYTES) is PdfProblem.TOO_LARGE
    assert extract(b"<html>not a pdf</html>") is PdfProblem.NOT_PDF
    assert extract(b"") is PdfProblem.NOT_PDF
    assert extract(b" %PDF-1.7 leading space") is PdfProblem.NOT_PDF


def test_exactly_20_mb_reaches_the_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def fake_run(argv: list[str], *, input: bytes, **kwargs: object) -> object:
        seen.append(len(input))
        raise pdf_text.subprocess.TimeoutExpired(argv, 0.0)

    monkeypatch.setattr(pdf_text.subprocess, "run", fake_run)
    data = b"%PDF-" + b"x" * (MAX_PDF_BYTES - 5)
    assert extract(data) is PdfProblem.TIMEOUT
    assert seen == [MAX_PDF_BYTES]


# --- the child's parse -----------------------------------------------------------------------


def test_text_pages_are_joined_by_a_blank_line() -> None:
    assert parse(build_pdf([LESSON, "Se toca (en el quinto) traste"])) == {
        "text": f"{LESSON}\n\nSe toca (en el quinto) traste"
    }


def test_a_pdf_without_text_is_no_text() -> None:
    assert parse(build_pdf(["", ""])) == {"problem": "no_text"}
    assert parse(build_pdf(["   "])) == {"problem": "no_text"}


def test_an_encrypted_pdf_is_refused() -> None:
    assert parse(build_pdf([LESSON], password=PASSWORD)) == {"problem": "encrypted"}


def test_more_than_200_pages_is_refused() -> None:
    assert parse(build_pdf(["a"] * MAX_PDF_PAGES))["text"].count("a") == MAX_PDF_PAGES
    assert parse(build_pdf(["a"] * (MAX_PDF_PAGES + 1))) == {"problem": "too_many_pages"}


def test_malformed_bytes_with_the_header_are_unreadable() -> None:
    assert parse(b"%PDF-1.7\nthis is not a pdf at all") == {"problem": "unreadable"}
    whole = build_pdf([LESSON])
    assert parse(whole[: len(whole) // 3]) == {"problem": "unreadable"}


def test_text_over_400000_characters_is_too_long() -> None:
    half = MAX_WEB_NOTE_CHARS // 2
    # Two pages and the blank line between them: exactly the cap, then one character over it.
    exact = build_pdf(["a" * half, "b" * (half - 2)])
    assert len(parse(exact)["text"]) == MAX_WEB_NOTE_CHARS
    assert parse(build_pdf(["a" * half, "b" * (half - 1)])) == {"problem": "too_long"}


def test_extraction_stops_at_the_page_that_passes_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = ["a" * (MAX_WEB_NOTE_CHARS + 1), "never read"]
    read: list[int] = []

    class Page:
        def __init__(self, index: int) -> None:
            self.index = index

        def extract_text(self) -> str:
            read.append(self.index)
            return pages[self.index]

    class Reader:
        is_encrypted = False

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.pages = [Page(0), Page(1)]

    monkeypatch.setattr(_pdf_worker, "PdfReader", Reader)
    assert parse(b"%PDF-") == {"problem": "too_long"}
    assert read == [0]


def test_a_pdf_with_no_pages_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        is_encrypted = False
        pages: tuple[object, ...] = ()

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(_pdf_worker, "PdfReader", Reader)
    assert parse(b"%PDF-") == {"problem": "unreadable"}


# --- the parent's reading of the child's answer ----------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (b'{"text": "Hola"}', "Hola"),
        (b'{"text": " \\n "}', PdfProblem.NO_TEXT),
        (b'{"text": "' + b"a" * (MAX_WEB_NOTE_CHARS + 1) + b'"}', PdfProblem.TOO_LONG),
        (b'{"problem": "encrypted"}', PdfProblem.ENCRYPTED),
        (b'{"problem": "too_many_pages"}', PdfProblem.TOO_MANY_PAGES),
        (b'{"problem": "no_text"}', PdfProblem.NO_TEXT),
        (b'{"problem": "too_long"}', PdfProblem.TOO_LONG),
        (b'{"problem": "unreadable"}', PdfProblem.UNREADABLE),
        (b'{"problem": "timeout"}', PdfProblem.UNREADABLE),
        (b'{"problem": "anything"}', PdfProblem.UNREADABLE),
        (b'{"text": 3}', PdfProblem.UNREADABLE),
        (b'["text"]', PdfProblem.UNREADABLE),
        (b"", PdfProblem.UNREADABLE),
        (b"\xff\xfe", PdfProblem.UNREADABLE),
    ],
    ids=lambda value: repr(value)[:24],
)
def test_only_a_known_answer_gets_through(stdout: bytes, expected: str | PdfProblem) -> None:
    assert read_answer(stdout) == expected


# --- end to end, through the child interpreter -----------------------------------------------


def test_extract_returns_the_text_through_the_child(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert extract(build_pdf([LESSON])) == LESSON
    assert extract(build_pdf([LESSON], password=PASSWORD)) is PdfProblem.ENCRYPTED
    assert LESSON not in caplog.text


def test_a_child_that_fails_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_text, "WORKER_MODULE", "tutor.services.no_such_worker")
    assert extract(build_pdf([LESSON])) is PdfProblem.UNREADABLE


def test_a_child_past_the_deadline_is_killed_as_timeout() -> None:
    # Starting an interpreter takes far longer than 10 ms, so the deadline always passes first.
    assert extract(build_pdf([LESSON]), timeout_s=0.01) is PdfProblem.TIMEOUT
