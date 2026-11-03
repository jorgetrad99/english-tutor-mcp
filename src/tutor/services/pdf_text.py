"""Text out of an uploaded PDF (subjects spec 1, 10; slice D ruling 58, controller decision Q3).

A PDF is untrusted input. The size and the header are checked here, before any parsing; the
parse runs in a short-lived child interpreter (`tutor.services._pdf_worker`) that is killed when
the time limit passes, because a thread cannot be stopped and pypdf has no deadline of its own.
At most MAX_CONCURRENT_PARSES children run at once in this process; one more upload gets `busy`
at once, never a queue. The child starts with a minimal environment (no secrets) in a neutral
directory, gets the bytes on stdin and answers one JSON object on stdout, read up to
MAX_ANSWER_BYTES. The file is never stored, and neither its content nor its name is logged: the
child's stderr goes to the null device.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from enum import StrEnum
from typing import IO

from tutor.domain.notes import MAX_WEB_NOTE_CHARS

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
PARSE_TIMEOUT_S = 20.0
MAX_CONCURRENT_PARSES = 2
PDF_HEADER = b"%PDF-"
WORKER_MODULE = "tutor.services._pdf_worker"
# The child's JSON (ensure_ascii off): at most 6 bytes per character, plus the envelope.
MAX_ANSWER_BYTES = 6 * MAX_WEB_NOTE_CHARS + 64
# What the interpreter needs to start: Windows wants SYSTEMROOT; nothing else is passed.
CHILD_ENV_KEYS = ("SYSTEMROOT",)
CHILD_CWD = os.path.abspath(os.sep)

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PARSES)


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
    BUSY = "busy"


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


def child_argv() -> list[str]:
    """This interpreter, isolated (`-I`: no PYTHON* variables, user site or cwd on the path)."""
    return [sys.executable, "-I", "-m", WORKER_MODULE]


def child_env() -> dict[str, str]:
    return {key: os.environ[key] for key in CHILD_ENV_KEYS if key in os.environ}


def extract(data: bytes, *, timeout_s: float = PARSE_TIMEOUT_S) -> str | PdfProblem:
    """The PDF's text, or the first problem found. Blocking: a route calls it through
    `run_in_threadpool`."""
    if len(data) > MAX_PDF_BYTES:
        return PdfProblem.TOO_LARGE
    if not data.startswith(PDF_HEADER):
        return PdfProblem.NOT_PDF
    if not _slots.acquire(blocking=False):
        return PdfProblem.BUSY
    try:
        return _run_child(data, timeout_s)
    finally:
        _slots.release()


def _feed(pipe: IO[bytes], data: bytes) -> None:
    with contextlib.suppress(OSError):  # the child may exit or be killed before it reads all
        pipe.write(data)
    with contextlib.suppress(OSError):
        pipe.close()


def _run_child(data: bytes, timeout_s: float) -> str | PdfProblem:
    """Start the child, feed it, read at most MAX_ANSWER_BYTES of its answer; a timer kills it
    at the deadline, which also ends the read."""
    child = subprocess.Popen(  # noqa: S603 - fixed argv: this interpreter, isolated, our module
        child_argv(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=child_env(),
        cwd=CHILD_CWD,
    )
    assert child.stdin is not None and child.stdout is not None  # noqa: S101 - PIPE above
    expired = threading.Event()

    def kill() -> None:
        expired.set()
        child.kill()

    timer = threading.Timer(timeout_s, kill)
    timer.start()
    feeder = threading.Thread(target=_feed, args=(child.stdin, data), daemon=True)
    feeder.start()
    try:
        stdout = child.stdout.read(MAX_ANSWER_BYTES + 1)
        if len(stdout) > MAX_ANSWER_BYTES:
            return PdfProblem.UNREADABLE
        returncode = child.wait()
    finally:
        timer.cancel()
        if child.poll() is None:
            child.kill()
        child.wait()
        feeder.join()
        child.stdout.close()
    if returncode != 0:
        return PdfProblem.TIMEOUT if expired.is_set() else PdfProblem.UNREADABLE
    return read_answer(stdout)


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
