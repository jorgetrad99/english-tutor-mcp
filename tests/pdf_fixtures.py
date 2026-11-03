"""PDFs built in the test with pypdf's writer (slice D ruling 58): no PDF file is checked in. Shared
by the extraction, website and Postgres tests."""

from __future__ import annotations

import io

from pypdf import PdfWriter
from pypdf.generic import ContentStream, DictionaryObject, NameObject

_FONT = DictionaryObject(
    {
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }
)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: list[str], *, password: str | None = None) -> bytes:
    """One page per string, each drawn as one line of Helvetica; an empty string is a blank
    page (no text at all, as an image-only scan gives)."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): _FONT})}
            )
            stream = ContentStream(None, writer)
            stream.set_data(f"BT /F1 12 Tf 72 720 Td ({_escape(text)}) Tj ET".encode("latin-1"))
            page.replace_contents(stream)
    if password is not None:
        writer.encrypt(password, algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
