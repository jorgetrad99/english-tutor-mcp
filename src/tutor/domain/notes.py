"""Notes: text cleaning, passages, name normalisation, the idempotency key and the `save_notes`
payload rules (subjects spec 4.1-4.3). Pure and deterministic; learner text is data."""

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeGuard
from urllib.parse import urlsplit
from uuid import UUID

from tutor.domain.errors import FieldError, FieldErrorCode
from tutor.domain.subjects import collapse_name
from tutor.domain.templates import MAX_INSTANCES, PROMPT_TEXT_MAX, ParamPair

SourceKind = Literal["course", "pdf", "text", "url", "other"]
LinkKind = Literal["prerequisite", "related"]
Origin = Literal["mcp", "web"]

SOURCE_KINDS: tuple[SourceKind, ...] = ("course", "pdf", "text", "url", "other")
LINK_KINDS: tuple[LinkKind, ...] = ("prerequisite", "related")
ORIGINS: tuple[Origin, ...] = ("mcp", "web")

# Caps (plan ruling 10); the MCP schemas and the migration restate them.
MIN_NOTES_PER_CALL = 1
MAX_NOTES_PER_CALL = 4
MAX_NOTE_CHARS = 6000
MAX_WEB_NOTE_CHARS = 400_000
MAX_CALL_CHARS = 16_000
MAX_CONCEPTS = 12
MAX_LINKS = 12
# MAX_INSTANCES (12 per note) lives in tutor.domain.templates (plan ruling 24).
MAX_SEARCH_LIMIT = 10
DEFAULT_SEARCH_LIMIT = 5
QUERY_MAX = 200

# Field lengths (spec 4.1, 4.2).
TITLE_MAX = 200
LESSON_REF_MAX = 120
SOURCE_TITLE_MAX = 200
URL_MAX = 2000
URL_SCHEMES = frozenset({"http", "https"})
CONCEPT_NAME_MAX = 80

PASSAGE_TARGET = 1200
PASSAGE_HARD = 2000
CLEAN_VERSION = 1

_SPACES = re.compile(r"\s+")
# Cc except tab and newline (\r, \v and \f become newlines before this runs).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_HORIZONTAL = re.compile(r"[^\S\n]+")
_LETTER = r"[^\W\d_]"
_HYPHEN_BREAK = re.compile(rf"(?<={_LETTER})-\n(?={_LETTER})")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_ELLIPSIS = chr(0x2026)
_SENTENCE_OR_LINE_END = re.compile(rf"[.!?{_ELLIPSIS}](?=\s)|\n")
_WHITESPACE = re.compile(r"\s")

_HEADING_MAX_CHARS = 80
_HEADING_MAX_WORDS = 10
_LIST_MARKERS = frozenset("-*+>|" + chr(0x2022) + chr(0xB7))
_NOT_HEADING_ENDINGS = frozenset(".,;")


# --- text -------------------------------------------------------------------------------------


def name_norm(text: str) -> str:
    """NFKD, nonspacing marks stripped, casefold, whitespace collapsed: the concept key."""
    decomposed = unicodedata.normalize("NFKD", text)
    bare = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return _SPACES.sub(" ", bare.casefold()).strip()


def strip_nul(raw: str) -> tuple[str, bool]:
    """Remove NUL characters (Postgres text cannot hold them); say whether any was present."""
    return raw.replace("\x00", ""), "\x00" in raw


def clean_text(raw: str) -> str:
    """Derive `clean_text` from `raw_text` (spec 4.1, `clean_version` 1). Idempotent.

    NFC; `\\r\\n`, `\\r`, form feed and vertical tab become `\\n`; other control characters
    except `\\t` are removed; runs of horizontal whitespace collapse to one space and lines are
    trimmed; a hyphen before a line break joins the letters on both sides (PDF line breaks);
    more than one blank line collapses to one; the whole is stripped.
    """
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    text = text.replace("\x0b", "\n")
    text = unicodedata.normalize("NFC", _CONTROL.sub("", text))
    text = _HORIZONTAL.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _HYPHEN_BREAK.sub("", text)
    text = _MANY_NEWLINES.sub("\n\n", text)
    return unicodedata.normalize("NFC", text.strip())


def heading_like(line: str) -> bool:
    """A short line that looks like a title: a markdown heading, or at most 80 characters and
    10 words, not a list item, not ending in `.`, `,` or `;`, starting with a capital or digit."""
    line = line.strip()
    if not line or len(line) > _HEADING_MAX_CHARS:
        return False
    if line.startswith("#"):
        return True
    if line[0] in _LIST_MARKERS or line[-1] in _NOT_HEADING_ENDINGS:
        return False
    if len(line.split()) > _HEADING_MAX_WORDS:
        return False
    first = next((ch for ch in line if ch.isalnum()), None)
    return first is not None and (first.isupper() or first.isdigit())


def _is_heading(lines: Sequence[str], index: int) -> bool:
    """Heading-like and followed (after any blank lines) by a line that is not heading-like,
    so a run of short lines (a list of titles) does not split."""
    if not heading_like(lines[index]):
        return False
    following = next((line for line in lines[index + 1 :] if line), None)
    return following is not None and not heading_like(following)


def _paragraphs(text: str) -> list[tuple[str, str]]:
    """`(separator before, text)` per paragraph: blank lines and heading lines split; the
    separator keeps the original structure (`\\n\\n` after a blank line, `\\n` before a heading)."""
    lines = [line.strip() for line in text.split("\n")]
    paragraphs: list[tuple[str, str]] = []
    buffer: list[str] = []
    buffer_sep = ""
    pending_sep = ""
    for index, line in enumerate(lines):
        if not line:
            if buffer:
                paragraphs.append((buffer_sep, "\n".join(buffer)))
                buffer = []
            pending_sep = "\n\n"
            continue
        if buffer and _is_heading(lines, index):
            paragraphs.append((buffer_sep, "\n".join(buffer)))
            buffer = []
            pending_sep = "\n"
        if not buffer:
            buffer_sep = pending_sep
        buffer.append(line)
    if buffer:
        paragraphs.append((buffer_sep, "\n".join(buffer)))
    return _glue_lone_headings(paragraphs)


def _glue_lone_headings(paragraphs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """A paragraph that is only a heading line belongs with the paragraph after it."""
    glued: list[tuple[str, str]] = []
    index = 0
    while index < len(paragraphs):
        sep, text = paragraphs[index]
        if index + 1 < len(paragraphs) and "\n" not in text and heading_like(text):
            next_sep, next_text = paragraphs[index + 1]
            if not heading_like(next_text.split("\n", 1)[0]):
                glued.append((sep, f"{text}{next_sep}{next_text}"))
                index += 2
                continue
        glued.append((sep, text))
        index += 1
    return glued


def _hard_split(passage: str) -> list[str]:
    """Cut a passage longer than `PASSAGE_HARD` at the latest sentence or line end in the second
    half of the window, else at the last whitespace, else at the limit."""
    pieces: list[str] = []
    rest = passage.strip()
    while len(rest) > PASSAGE_HARD:
        window = rest[: PASSAGE_HARD + 1]
        ends = [m.end() for m in _SENTENCE_OR_LINE_END.finditer(window) if m.end() <= PASSAGE_HARD]
        late = [end for end in ends if end >= PASSAGE_HARD // 2]
        if late:
            cut = late[-1]
        else:
            spaces = [m.start() for m in _WHITESPACE.finditer(window)]
            cut = spaces[-1] if spaces else PASSAGE_HARD
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()  # never empty: `rest` ends on a non-space character
    pieces.append(rest)
    return pieces


def split_passages(text: str) -> tuple[str, ...]:
    """Search units of a note (spec 4.3): split on blank lines and heading-like lines, merge
    short paragraphs up to `PASSAGE_TARGET`, hard-split above `PASSAGE_HARD`, never empty."""
    passages: list[str] = []
    current = ""
    for sep, paragraph in _paragraphs(text):
        if not current:
            current = paragraph
        elif len(current) + len(sep) + len(paragraph) <= PASSAGE_TARGET:
            current = f"{current}{sep}{paragraph}"
        else:
            passages.extend(_hard_split(current))
            current = paragraph
    if current:
        passages.extend(_hard_split(current))
    return tuple(passages)


def raw_sha256(text: str) -> str:
    """Hex SHA-256 of the UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def idempotency_key(
    subject_id: UUID, source_id: UUID | None, lesson_ref: str | None, raw_text: str
) -> str:
    """`sha256(subject_id | source_id | lesson_ref | sha256(raw_text))`, parts joined with
    U+001F, absent parts empty (spec 4.1)."""
    parts = (str(subject_id), str(source_id) if source_id else "", lesson_ref or "")
    joined = "\x1f".join((*parts, raw_sha256(raw_text)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --- payload ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceInput:
    kind: str
    title: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ConceptLinkInput:
    from_name: str
    to_name: str
    kind: str


@dataclass(frozen=True, slots=True)
class InstanceInput:
    """One instance of a subject's template (spec 15.4). Its parameters are checked against the
    template by the service (plan ruling 23); here only the shape around them."""

    template_id: UUID | str
    params: Sequence[ParamPair]
    prompt_text: str
    concept: str | None = None
    replaces_instance_id: UUID | str | None = None


@dataclass(frozen=True, slots=True)
class NoteInput:
    title: str
    lesson_ref: str | None
    position: int | None
    raw_text: str
    concepts: Sequence[str] = ()
    links: Sequence[ConceptLinkInput] = ()
    instances: Sequence[InstanceInput] = ()


@dataclass(frozen=True, slots=True)
class ValidSource:
    kind: SourceKind
    title: str
    url: str | None


@dataclass(frozen=True, slots=True)
class ConceptName:
    name: str
    name_norm: str


@dataclass(frozen=True, slots=True)
class ConceptLink:
    """Directed from the note's perspective: `prerequisite` means `from_name` comes first."""

    from_name: ConceptName
    to_name: ConceptName
    kind: LinkKind


@dataclass(frozen=True, slots=True)
class ValidInstance:
    template_id: UUID
    params: tuple[ParamPair, ...]
    prompt_text: str
    concept: ConceptName | None
    replaces_instance_id: UUID | None


@dataclass(frozen=True, slots=True)
class ValidNote:
    """One note ready to store. `raw_text` is as received minus NUL; the service computes the
    idempotency key from `raw_text` once the source id is known."""

    title: str
    lesson_ref: str | None
    position: int | None
    raw_text: str
    raw_had_nul: bool
    raw_sha256: str
    clean_text: str
    passages: tuple[str, ...]
    concepts: tuple[ConceptName, ...]
    links: tuple[ConceptLink, ...]
    instances: tuple[ValidInstance, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidNotes:
    source: ValidSource | None
    notes: tuple[ValidNote, ...]


NotesErrorCode = Literal["validation_failed", "limit_exceeded"]


@dataclass(frozen=True, slots=True)
class NotesErrors:
    """Why a payload was refused: `limit_exceeded` carries the one cap that was hit,
    `validation_failed` every bad field in payload order (ruling 13)."""

    code: NotesErrorCode
    errors: tuple[FieldError, ...]

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(error.field for error in self.errors)


def _is_source_kind(value: str) -> TypeGuard[SourceKind]:
    return value in SOURCE_KINDS


def _is_link_kind(value: str) -> TypeGuard[LinkKind]:
    return value in LINK_KINDS


Errors = list[FieldError]


def _fail(errors: Errors, field: str, code: FieldErrorCode) -> None:
    errors.append(FieldError(field, code))


def _length(text: str, limit: int, field: str, errors: Errors, *, required: bool) -> None:
    if not text:
        if required:
            _fail(errors, field, "required")
    elif len(text) > limit:
        _fail(errors, field, "too_long")


def _name(value: str, limit: int, field: str, errors: Errors) -> str:
    text = collapse_name(value)
    _length(text, limit, field, errors, required=True)
    return text


def _optional_name(value: str | None, limit: int, field: str, errors: Errors) -> str | None:
    text = collapse_name(value or "")
    _length(text, limit, field, errors, required=False)
    return text if text and len(text) <= limit else None


def _text(value: str, limit: int, field: str, errors: Errors) -> str:
    text = value.strip()
    _length(text, limit, field, errors, required=True)
    return text


def _optional_text(value: str | None, limit: int, field: str, errors: Errors) -> str | None:
    text = (value or "").strip()
    _length(text, limit, field, errors, required=False)
    return text if text and len(text) <= limit else None


def _concept(value: str, field: str, errors: Errors) -> ConceptName | None:
    name = collapse_name(value)
    norm = name_norm(name)
    if not name or not norm:
        _fail(errors, field, "required")
        return None
    if len(name) > CONCEPT_NAME_MAX:
        _fail(errors, field, "too_long")
        return None
    return ConceptName(name, norm)


def _source(data: SourceInput, errors: Errors) -> ValidSource | None:
    kind_value = data.kind.strip()
    kind = kind_value if _is_source_kind(kind_value) else None
    if kind is None:
        _fail(errors, "source.kind", "required" if not kind_value else "invalid_choice")
    title = _name(data.title, SOURCE_TITLE_MAX, "source.title", errors)
    url = _url(data.url, errors)
    return ValidSource(kind, title, url) if kind is not None else None


def _url(value: str | None, errors: Errors) -> str | None:
    """An optional absolute http or https address with a host and no whitespace; stored as data
    and never fetched, but a website link must not carry `javascript:` or `data:`."""
    text = _optional_text(value, URL_MAX, "source.url", errors)
    if text is None:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:  # an unbalanced IPv6 bracket
        parts = None
    if (
        parts is None
        or parts.scheme.lower() not in URL_SCHEMES
        or not parts.hostname
        or _WHITESPACE.search(text) is not None
    ):
        _fail(errors, "source.url", "invalid")
        return None
    return text


def _concepts(values: Sequence[str], field: str, errors: Errors) -> tuple[ConceptName, ...]:
    seen: dict[str, ConceptName] = {}
    for index, value in enumerate(values):
        concept = _concept(value, f"{field}[{index}]", errors)
        if concept is not None and concept.name_norm not in seen:
            seen[concept.name_norm] = concept
    return tuple(seen.values())


def _links(
    values: Sequence[ConceptLinkInput], field: str, errors: Errors
) -> tuple[ConceptLink, ...]:
    links: dict[tuple[str, str, str], ConceptLink] = {}
    for index, value in enumerate(values):
        path = f"{field}[{index}]"
        from_name = _concept(value.from_name, f"{path}.from", errors)
        to_name = _concept(value.to_name, f"{path}.to", errors)
        kind = value.kind.strip()
        if not _is_link_kind(kind):
            _fail(errors, f"{path}.kind", "required" if not kind else "invalid_choice")
            continue
        if from_name is None or to_name is None:
            continue
        if from_name.name_norm == to_name.name_norm:
            _fail(errors, path, "invalid")  # a self-link
            continue
        links.setdefault(
            (from_name.name_norm, to_name.name_norm, kind), ConceptLink(from_name, to_name, kind)
        )
    return tuple(links.values())


def _uuid(value: UUID | str, field: str, errors: Errors) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value.strip())
    except ValueError:
        _fail(errors, field, "invalid")
        return None


def validate_instance(data: InstanceInput, path: str, errors: Errors) -> ValidInstance | None:
    """`template_id` (and `replaces_instance_id`) a UUID, `prompt_text` 1-500 after strip, the
    optional concept name. Paths start at `path` (`""` for the top level)."""
    at = f"{path}." if path else ""
    template_id = _uuid(data.template_id, f"{at}template_id", errors)
    replaces = None
    if data.replaces_instance_id is not None:
        replaces = _uuid(data.replaces_instance_id, f"{at}replaces_instance_id", errors)
    prompt_text = _text(data.prompt_text, PROMPT_TEXT_MAX, f"{at}prompt_text", errors)
    concept = None
    if data.concept is not None and data.concept.strip():
        concept = _concept(data.concept, f"{at}concept", errors)
    if template_id is None:
        return None
    return ValidInstance(template_id, tuple(data.params), prompt_text, concept, replaces)


def _note(data: NoteInput, path: str, *, note_cap: int, errors: Errors) -> ValidNote:
    title = _name(data.title, TITLE_MAX, f"{path}.title", errors)
    lesson_ref = _optional_name(data.lesson_ref, LESSON_REF_MAX, f"{path}.lesson_ref", errors)
    if data.position is not None and data.position < 0:
        _fail(errors, f"{path}.position", "out_of_range")
    raw, had_nul = strip_nul(data.raw_text)
    cleaned = clean_text(raw)
    if not raw or not cleaned:
        _fail(errors, f"{path}.raw_text", "required")
    elif len(raw) > note_cap:
        _fail(errors, f"{path}.raw_text", "too_long")
    concepts = _concepts(data.concepts, f"{path}.concepts", errors)
    links = _links(data.links, f"{path}.links", errors)
    instances = tuple(
        instance
        for index, item in enumerate(data.instances)
        if (instance := validate_instance(item, f"{path}.instances[{index}]", errors)) is not None
    )
    return ValidNote(
        title=title,
        lesson_ref=lesson_ref,
        position=data.position,
        raw_text=raw,
        raw_had_nul=had_nul,
        raw_sha256=raw_sha256(raw),
        clean_text=cleaned,
        passages=split_passages(cleaned),
        concepts=concepts,
        links=links,
        instances=instances,
    )


def _limit_hit(notes: Sequence[NoteInput], origin: Origin) -> FieldError | None:
    """The first count or total-size cap the payload breaks, in payload order."""
    if len(notes) > MAX_NOTES_PER_CALL:
        return FieldError("notes", "too_many")
    if origin == "mcp":
        total = sum(len(strip_nul(note.raw_text)[0]) for note in notes)
        if total > MAX_CALL_CHARS:
            return FieldError("notes", "too_long")
    for index, note in enumerate(notes):
        counts = (
            ("concepts", len(note.concepts), MAX_CONCEPTS),
            ("links", len(note.links), MAX_LINKS),
            ("instances", len(note.instances), MAX_INSTANCES),
        )
        for field, count, cap in counts:
            if count > cap:
                return FieldError(f"notes[{index}].{field}", "too_many")
    return None


def validate_notes(
    source: SourceInput | None, notes: Sequence[NoteInput], *, origin: Origin
) -> ValidNotes | NotesErrors:
    """Validate a `save_notes` payload (spec 4.1, 4.2).

    Count and total-character caps give `limit_exceeded` with the offending field before any
    field is examined; then every field is checked and `validation_failed` lists each bad field
    path (`notes[2].instances[0].prompt_text`) in payload order. Concept names are deduplicated by
    `name_norm` within a note, duplicate links are dropped and self-links are rejected.
    """
    if len(notes) < MIN_NOTES_PER_CALL:
        return NotesErrors("validation_failed", (FieldError("notes", "required"),))
    limit = _limit_hit(notes, origin)
    if limit is not None:
        return NotesErrors("limit_exceeded", (limit,))
    note_cap = MAX_NOTE_CHARS if origin == "mcp" else MAX_WEB_NOTE_CHARS
    errors: Errors = []
    valid_source = _source(source, errors) if source is not None else None
    valid_notes = tuple(
        _note(note, f"notes[{index}]", note_cap=note_cap, errors=errors)
        for index, note in enumerate(notes)
    )
    if errors:
        return NotesErrors("validation_failed", tuple(errors))
    return ValidNotes(valid_source, valid_notes)
