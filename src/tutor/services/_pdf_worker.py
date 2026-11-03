"""Child process of `tutor.services.pdf_text`: PDF bytes on stdin, one JSON object on stdout,
`{"text": ...}` or `{"problem": ...}`. Run as `python -I -m tutor.services._pdf_worker`; the
parent kills it at the time limit and discards its stderr (pypdf's warnings go there)."""

from __future__ import annotations

import io
import json
import sys

from pypdf import PdfReader

from tutor.domain.notes import MAX_WEB_NOTE_CHARS
from tutor.services.pdf_text import MAX_PDF_PAGES

PAGE_SEPARATOR = "\n\n"


def parse(data: bytes) -> dict[str, str]:
    """The answer for one PDF. Any failure while parsing, of any exception class, is
    `unreadable`; extraction stops as soon as the text passes the cap."""
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            return {"problem": "encrypted"}
        count = len(reader.pages)
        if count > MAX_PDF_PAGES:
            return {"problem": "too_many_pages"}
        if count == 0:
            return {"problem": "unreadable"}
        parts: list[str] = []
        size = -len(PAGE_SEPARATOR)
        for page in reader.pages:
            text = page.extract_text()
            parts.append(text)
            size += len(PAGE_SEPARATOR) + len(text)
            if size > MAX_WEB_NOTE_CHARS:
                return {"problem": "too_long"}
    except Exception:  # a hostile file may raise anything; no detail leaves the child
        return {"problem": "unreadable"}
    text = PAGE_SEPARATOR.join(parts)
    if not text.strip():
        return {"problem": "no_text"}
    return {"text": text}


def main() -> None:
    answer = parse(sys.stdin.buffer.read())
    sys.stdout.buffer.write(json.dumps(answer).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":  # pragma: no cover - runs in the child process
    main()
