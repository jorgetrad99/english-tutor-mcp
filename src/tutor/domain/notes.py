"""Notes: text cleaning, passages, name normalisation, the idempotency key and the `save_notes`
payload rules (subjects spec 4.1-4.3). Pure and deterministic; learner text is data."""

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeGuard
from uuid import UUID

from tutor.domain.errors import FieldError, FieldErrorCode
from tutor.domain.subjects import collapse_name

SourceKind = Literal["course", "pdf", "text", "url", "other"]
LinkKind = Literal["prerequisite", "related"]
NoteExerciseKind = Literal["technique", "repertoire", "theory", "ear", "other"]
Origin = Literal["mcp", "web"]

SOURCE_KINDS: tuple[SourceKind, ...] = ("course", "pdf", "text", "url", "other")
LINK_KINDS: tuple[LinkKind, ...] = ("prerequisite", "related")
NOTE_EXERCISE_KINDS: tuple[NoteExerciseKind, ...] = (
    "technique",
    "repertoire",
    "theory",
    "ear",
    "other",
)
ORIGINS: tuple[Origin, ...] = ("mcp", "web")

# Caps (plan ruling 10); the MCP schemas and the migration restate them.
MIN_NOTES_PER_CALL = 1
MAX_NOTES_PER_CALL = 4
MAX_NOTE_CHARS = 6000
MAX_WEB_NOTE_CHARS = 400_000
MAX_CALL_CHARS = 16_000
MAX_CONCEPTS = 12
MAX_LINKS = 12
MAX_EXERCISES = 10
MAX_QUIZ_ITEMS = 10
MAX_SEARCH_LIMIT = 10
DEFAULT_SEARCH_LIMIT = 5
QUERY_MAX = 200

# Field lengths (spec 4.1, 4.2).
TITLE_MAX = 200
LESSON_REF_MAX = 120
SOURCE_TITLE_MAX = 200
URL_MAX = 2000
CONCEPT_NAME_MAX = 80
EXERCISE_NAME_MAX = 120
INSTRUCTIONS_MAX = 2000
TEMPO_MIN = 20
TEMPO_MAX = 300
DURATION_MIN = 1
DURATION_MAX = 120
QUESTION_MAX = 500
OPTION_MAX = 200
MIN_OPTIONS = 2
MAX_OPTIONS = 5
EXPLANATION_MAX = 500

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
class ExerciseInput:
    name: str
    instructions: str
    kind: str
    tempo_bpm: int | None = None
    duration_min: int | None = None


@dataclass(frozen=True, slots=True)
class QuizItemInput:
    question: str
    options: Sequence[str]
    answer_index: int
    explanation: str | None = None
    concept: str | None = None


@dataclass(frozen=True, slots=True)
class NoteInput:
    title: str
    lesson_ref: str | None
    position: int | None
    raw_text: str
    concepts: Sequence[str] = ()
    links: Sequence[ConceptLinkInput] = ()
    exercises: Sequence[ExerciseInput] = ()
    quiz_items: Sequence[QuizItemInput] = ()


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
class Exercise:
    name: str
    instructions: str
    kind: NoteExerciseKind
    tempo_bpm: int | None
    duration_min: int | None


@dataclass(frozen=True, slots=True)
class QuizItem:
    question: str
    options: tuple[str, ...]
    answer_index: int
    explanation: str | None
    concept: ConceptName | None


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
    exercises: tuple[Exercise, ...]
    quiz_items: tuple[QuizItem, ...]


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


def _is_exercise_kind(value: str) -> TypeGuard[NoteExerciseKind]:
    return value in NOTE_EXERCISE_KINDS


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


def _optional_range(
    value: int | None, low: int, high: int, field: str, errors: Errors
) -> int | None:
    if value is not None and not low <= value <= high:
        _fail(errors, field, "out_of_range")
        return None
    return value


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
    url = _optional_text(data.url, URL_MAX, "source.url", errors)
    return ValidSource(kind, title, url) if kind is not None else None


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


def _exercise(data: ExerciseInput, path: str, errors: Errors) -> Exercise | None:
    name = _name(data.name, EXERCISE_NAME_MAX, f"{path}.name", errors)
    instructions = _text(data.instructions, INSTRUCTIONS_MAX, f"{path}.instructions", errors)
    kind_value = data.kind.strip()
    kind = kind_value if _is_exercise_kind(kind_value) else None
    if kind is None:
        _fail(errors, f"{path}.kind", "required" if not kind_value else "invalid_choice")
    tempo = _optional_range(data.tempo_bpm, TEMPO_MIN, TEMPO_MAX, f"{path}.tempo_bpm", errors)
    duration = _optional_range(
        data.duration_min, DURATION_MIN, DURATION_MAX, f"{path}.duration_min", errors
    )
    return Exercise(name, instructions, kind, tempo, duration) if kind is not None else None


def _quiz_item(data: QuizItemInput, path: str, errors: Errors) -> QuizItem:
    question = _text(data.question, QUESTION_MAX, f"{path}.question", errors)
    options = tuple(option.strip() for option in data.options)
    options_ok = MIN_OPTIONS <= len(options) <= MAX_OPTIONS and all(
        1 <= len(option) <= OPTION_MAX for option in options
    )
    if not options_ok:
        _fail(errors, f"{path}.options", "invalid")  # count outside 2-5, or an option's length
    elif not 0 <= data.answer_index < len(options):
        _fail(errors, f"{path}.answer_index", "out_of_range")
    explanation = _optional_text(data.explanation, EXPLANATION_MAX, f"{path}.explanation", errors)
    concept = None
    if data.concept is not None and data.concept.strip():
        concept = _concept(data.concept, f"{path}.concept", errors)
    return QuizItem(question, options, data.answer_index, explanation, concept)


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
    exercises = tuple(
        exercise
        for index, item in enumerate(data.exercises)
        if (exercise := _exercise(item, f"{path}.exercises[{index}]", errors)) is not None
    )
    quiz_items = tuple(
        _quiz_item(item, f"{path}.quiz_items[{index}]", errors)
        for index, item in enumerate(data.quiz_items)
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
        exercises=exercises,
        quiz_items=quiz_items,
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
            ("exercises", len(note.exercises), MAX_EXERCISES),
            ("quiz_items", len(note.quiz_items), MAX_QUIZ_ITEMS),
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
    path (`notes[2].quiz_items[0].options`) in payload order. Concept names are deduplicated by
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
