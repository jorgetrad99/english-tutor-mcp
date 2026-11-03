"""Text out of an uploaded PDF (subjects spec 1, 10; slice D ruling 58, controller decision Q3).

A PDF is untrusted input. The size and the header are checked here, before any parsing; the
parse runs in a short-lived child interpreter (`tutor.services._pdf_worker`) that is killed when
the time limit passes, because a thread cannot be stopped and pypdf has no deadline of its own.
The child gets the bytes on stdin and answers one JSON object on stdout. The file is never
stored, and neither its content nor its name is logged: the child's stderr is discarded.
"""

from __future__ import annotations

import json
import subprocess
import sys
from enum import StrEnum

from tutor.domain.notes import MAX_WEB_NOTE_CHARS

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
PARSE_TIMEOUT_S = 20.0
PDF_HEADER = b"%PDF-"
WORKER_MODULE = "tutor.services._pdf_worker"


class PdfProblem(StrEnum):
    """Why a PDF gave no text; each code has its own field message on the website."""

    TOO_LARGE = "too_large"
    NOT_PDF = "not_pdf"
    ENCRYPTED = "encrypted"
    TOO_MANY_PAGES = "too_many_pages"
    UNREADABLE = "unreadable"
    NO_TEXT = "no_text"
    TOO_LONG = "too_long"
    TIMEOUT = "timeout"


# What the child may answer; anything else from it reads as `unreadable`.
CHILD_PROBLEMS = frozenset(
    {
        PdfProblem.ENCRYPTED,
        PdfProblem.TOO_MANY_PAGES,
        PdfProblem.UNREADABLE,
        PdfProblem.NO_TEXT,
        PdfProblem.TOO_LONG,
    }
)


def extract(data: bytes, *, timeout_s: float = PARSE_TIMEOUT_S) -> str | PdfProblem:
    """The PDF's text, or the first problem found. Blocking: a route calls it through
    `run_in_threadpool`."""
    if len(data) > MAX_PDF_BYTES:
        return PdfProblem.TOO_LARGE
    if not data.startswith(PDF_HEADER):
        return PdfProblem.NOT_PDF
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv: this interpreter, isolated, our module
            [sys.executable, "-I", "-m", WORKER_MODULE],
            input=data,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:  # run() has already killed and reaped the child
        return PdfProblem.TIMEOUT
    if done.returncode != 0:
        return PdfProblem.UNREADABLE
    return read_answer(done.stdout)


def read_answer(stdout: bytes) -> str | PdfProblem:
    """The child's answer, checked again here: only a known problem code or a text within the
    caps gets through."""
    try:
        answer = json.loads(stdout)
    except ValueError:
        return PdfProblem.UNREADABLE
    if not isinstance(answer, dict):
        return PdfProblem.UNREADABLE
    text = answer.get("text")
    if isinstance(text, str):
        if not text.strip():
            return PdfProblem.NO_TEXT
        if len(text) > MAX_WEB_NOTE_CHARS:
            return PdfProblem.TOO_LONG
        return text
    problem = answer.get("problem")
    if problem in CHILD_PROBLEMS:
        return PdfProblem(problem)
    return PdfProblem.UNREADABLE
