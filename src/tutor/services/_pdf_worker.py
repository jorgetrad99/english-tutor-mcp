"""Child process of `tutor.services.pdf_text`: PDF bytes on stdin, one JSON object on stdout,
`{"text": ...}` or `{"problem": ...}`. Run as `python -I -m tutor.services._pdf_worker` with a
minimal environment; the parent kills it at the time limit and discards its stderr (pypdf's
warnings go there). On POSIX the child caps its own address space and CPU time first; pypdf's
decompression and parsing limits are lowered to what a 20 MB, 200-page upload needs."""

from __future__ import annotations

import io
import json
import sys
from typing import Any

from pypdf import PdfReader, apply_configuration

from tutor.domain.notes import MAX_WEB_NOTE_CHARS
from tutor.services.pdf_text import MAX_PDF_BYTES, MAX_PDF_PAGES

PAGE_SEPARATOR = "\n\n"
MAX_CHILD_MEMORY = 512 * 1024 * 1024  # RLIMIT_AS, bytes
MAX_CHILD_CPU_S = 25  # RLIMIT_CPU soft limit; the parent's 20 s wall clock comes first
_MAX_STREAM = 32 * 1024 * 1024  # decoded bytes of any one stream or image

# pypdf 6.21.4 `Configuration` fields (docs/modules/configuration.rst); the defaults are 75 MB
# per stream, 300 M units of decoding work and 2 M page entries.
PYPDF_LIMITS: dict[str, Any] = {
    "maximum_declared_stream_length": MAX_PDF_BYTES,
    "array_based_stream_maximum_output_length": _MAX_STREAM,
    "jbig2_maximum_output_length": _MAX_STREAM,
    "lzw_maximum_output_length": _MAX_STREAM,
    "run_length_maximum_output_length": _MAX_STREAM,
    "zlib_maximum_output_length": _MAX_STREAM,
    "brotli_maximum_output_length": _MAX_STREAM,
    "image_maximum_buffer_size": _MAX_STREAM,
    "stream_decoding_work_maximum_length": 100_000_000,
    "page_tree_maximum_entries": 10_000,
    "page_tree_maximum_page_entries": 10_000,
    "xform_maximum_invocations_per_extraction": 1000,
    "jbig2dec_binary": None,  # never run an external decoder
}


def parse(data: bytes) -> dict[str, str]:
    """The answer for one PDF. Any failure while parsing, of any exception class, is
    `unreadable`; extraction stops as soon as the text passes the cap."""
    try:
        with apply_configuration(**PYPDF_LIMITS):
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


def limit_resources() -> None:
    """Cap this process's memory and CPU time (POSIX only; Windows has no setrlimit)."""
    if sys.platform != "win32":  # pragma: no cover - CI runs it on Linux, in the child
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MAX_CHILD_MEMORY, MAX_CHILD_MEMORY))
        resource.setrlimit(resource.RLIMIT_CPU, (MAX_CHILD_CPU_S, MAX_CHILD_CPU_S + 1))


def encode(answer: dict[str, str]) -> bytes:
    """UTF-8 JSON, at most 6 bytes per character of text (a `\\u00XX` escape); a lone
    surrogate from a broken font map becomes `?`."""
    return json.dumps(answer, ensure_ascii=False).encode("utf-8", "replace")


def main() -> None:  # pragma: no cover - runs in the child process
    limit_resources()
    data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    answer = {"problem": "unreadable"} if len(data) > MAX_PDF_BYTES else parse(data)
    sys.stdout.buffer.write(encode(answer))
    sys.stdout.buffer.flush()


if __name__ == "__main__":  # pragma: no cover - runs in the child process
    main()
