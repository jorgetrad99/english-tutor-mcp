"""Exercise templates and instances (subjects spec 15): the domain-neutral contract.

A template is HTML plus a closed parameter schema and a grading kind; an instance is a template
with parameters. Everything here is pure and deterministic: template validation, parameter
validation, seeded pool draws, the public payload (answer keys, reveals and pools removed), the
composed document, the text rendering, answer normalisation and grading. The server holds no
concept of any field of study: parameter types, grading kinds and draw rules are generic.
Learner and template text is data; field errors carry paths, never values.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, TypeGuard
from uuid import UUID

from tutor.domain.errors import FieldError, FieldErrorCode
from tutor.domain.html_guard import MAX_TEMPLATE_BYTES, TEMPLATE_MARKER
from tutor.domain.subjects import collapse_name

TemplatePurpose = Literal["exercise", "display"]
PracticeKind = Literal["quiz", "drill", "exercise"]  # the subject's exercise_kinds values
GradingKind = Literal[
    "none", "choice_index", "exact_text", "exact_set", "exact_sequence", "numeric", "self_report"
]
TextMatch = Literal["fold_all", "keep_accents", "exact_case"]
PoolDraw = Literal["none", "one", "one_of_choices", "ordered"]
ParamType = Literal[
    "text",
    "text_list",
    "text_table",
    "integer",
    "integer_list",
    "number",
    "number_list",
    "boolean",
    "pool",
]
ParamRole = Literal["prompt", "answer_key", "reveal", "pool"]
GuideSection = Literal[
    "choosing_exercises",
    "contract",
    "display_templates",
    "accessibility_and_themes",
    "example_pool_recall",
    "example_numeric",
    "example_ordering",
    "example_pool_choice_media",
]
Origin = Literal["ingest", "class"]
Channel = Literal["artifact", "web", "app_frame"]
TemplateStatus = Literal["active", "retired"]
InstanceStatus = Literal["active", "retired"]
AnswerKind = Literal["choice_index", "value", "values", "sequence", "number", "self_report"]

TEMPLATE_PURPOSES: tuple[TemplatePurpose, ...] = ("exercise", "display")
PRACTICE_KINDS: tuple[PracticeKind, ...] = ("quiz", "drill", "exercise")
GRADING_KINDS: tuple[GradingKind, ...] = (
    "none",
    "choice_index",
    "exact_text",
    "exact_set",
    "exact_sequence",
    "numeric",
    "self_report",
)
TEXT_MATCHES: tuple[TextMatch, ...] = ("fold_all", "keep_accents", "exact_case")
POOL_DRAWS: tuple[PoolDraw, ...] = ("none", "one", "one_of_choices", "ordered")
PARAM_TYPES: tuple[ParamType, ...] = (
    "text",
    "text_list",
    "text_table",
    "integer",
    "integer_list",
    "number",
    "number_list",
    "boolean",
    "pool",
)
PARAM_ROLES: tuple[ParamRole, ...] = ("prompt", "answer_key", "reveal", "pool")
GUIDE_SECTIONS: tuple[GuideSection, ...] = (
    "choosing_exercises",
    "contract",
    "display_templates",
    "accessibility_and_themes",
    "example_pool_recall",
    "example_numeric",
    "example_ordering",
    "example_pool_choice_media",
)
ORIGINS: tuple[Origin, ...] = ("ingest", "class")
CHANNELS: tuple[Channel, ...] = ("artifact", "web", "app_frame")
TEMPLATE_STATUSES: tuple[TemplateStatus, ...] = ("active", "retired")
INSTANCE_STATUSES: tuple[InstanceStatus, ...] = ("active", "retired")
ANSWER_KINDS: tuple[AnswerKind, ...] = (
    "choice_index",
    "value",
    "values",
    "sequence",
    "number",
    "self_report",
)

# The guide version Claude must send to save_template, and the hash of the guide it names: the
# contract constants, every rendered guide section and every worked-example file. A test
# recomputes it (tutor.services.template_files.guide_sha256); change both together (plan
# rulings 21 and 31).
TEMPLATE_GUIDE_VERSION = 4
TEMPLATE_CONTRACT_SHA256 = "39fb0d78870fd993394e548c6b98bb0e43a3df251575c70308470d4125d70318"

# Caps (plan ruling 24; spec 15.3, 15.4). The MCP schemas and migration 0006 restate them.
NAME_MAX = 80
DESCRIPTION_MAX = 300
LABEL_MAX = 60
MIN_TEMPLATE_PARAMS = 1
MAX_TEMPLATE_PARAMS = 8
MAX_INSTANCES = 12  # per note
PROMPT_TEXT_MAX = 500
MAX_PARAMS_JSON_BYTES = 32_768
MAX_PUBLIC_PAYLOAD_BYTES = 8_192
INLINE_GUIDE_MAX_CHARS = 6_000  # the decision path only (plan ruling 33)
NUMERIC_TOLERANCE_MAX = 1_000_000
VALUE_LIMIT = 1_000_000_000
NUMBER_DECIMALS = 6
TEXT_MAX_LENGTH = 4_000
TEXT_DEFAULT_MAX_LENGTH = 500
LIST_MAX_ITEMS = 24
LIST_DEFAULT_MAX_ITEMS = 12
ITEM_MAX_LENGTH = 200
ITEM_DEFAULT_MAX_LENGTH = 80
TABLE_MAX_ROWS = 40
TABLE_MAX_CELLS = 8
CELL_MAX_LENGTH = 200
MIN_POOL_ENTRIES = 2
MAX_POOL_ENTRIES = 200
POOL_CUE_MAX = 200
POOL_ANSWER_MAX = 200
POOL_ACCEPT_MAX = 6
POOL_EXPLAIN_MAX = 300
# Three, not two: a sequence is never shown in the right order, so two items shown would be
# the reverse of the answer (security review P3, plan ruling 34).
SEQUENCE_MIN_ITEMS = 3
SEQUENCE_MAX_ITEMS = 12
ANSWER_VALUE_MAX = 200
ANSWER_VALUES_MAX = 24
ANSWER_SEQUENCE_MAX = 12
ANSWER_NUMBER_MAX = 40
RATING_MIN = 1
RATING_MAX = 4
TEMPO_MIN = 20
TEMPO_MAX = 300
MINUTES_MIN = 1
MINUTES_MAX = 240
PAYLOAD_VERSION = 1
UI_LANG = "es"  # the learner's interface language; Spanish only in v0 (spec 15.5)

PARAM_NAME_PATTERN = r"[a-z][a-z0-9_]{0,31}"
RESERVED_PARAM_NAMES: tuple[str, ...] = ("instance_id", "drawn", "labels", "ui_lang")
LIST_TYPES: tuple[ParamType, ...] = ("text_list", "text_table", "integer_list", "number_list")
TEXT_GRADING_KINDS: tuple[GradingKind, ...] = ("exact_text", "exact_set", "exact_sequence")
# grading kind -> the one pool draw it allows (spec 15.3 compatibility table)
POOL_DRAW_FOR: Mapping[GradingKind, PoolDraw] = {
    "choice_index": "one_of_choices",
    "exact_text": "one",
    "exact_sequence": "ordered",
}
POOL_COUNT_RANGES: Mapping[PoolDraw, tuple[int, int]] = {
    "one_of_choices": (2, 8),
    "ordered": (SEQUENCE_MIN_ITEMS, SEQUENCE_MAX_ITEMS),
}
# grading kind -> the answer_key types it takes without a pool
ANSWER_KEY_TYPES: Mapping[GradingKind, tuple[ParamType, ...]] = {
    "choice_index": ("integer",),
    "exact_text": ("text", "text_list"),
    "exact_set": ("text_list",),
    "exact_sequence": ("text_list",),
    "numeric": ("number",),
}
ANSWER_KIND_FOR: Mapping[GradingKind, AnswerKind] = {
    "choice_index": "choice_index",
    "exact_text": "value",
    "exact_set": "values",
    "exact_sequence": "sequence",
    "numeric": "number",
    "self_report": "self_report",
}
# Removed around a text answer under fold_all and keep_accents (spec 15.8).
EDGE_PUNCTUATION = ".,;:!?¡¿\"'«»"

_NAME = re.compile(PARAM_NAME_PATTERN)
_SPACES = re.compile(r"\s+")
_DECIMAL = re.compile(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)")
_FRACTION = re.compile(r"([+-]?\d+)/(\d+)")
_AMBIGUOUS_THOUSANDS = re.compile(r"[+-]?0*[1-9]\d{0,2},\d{3}")
_MASK64 = (1 << 64) - 1

Errors = list[FieldError]


# --- contract constants (the guide's generated tables and the hash) ---------------------------


def contract_constants() -> Mapping[str, Any]:
    """Every number, name and rule the guide's contract tables show, as plain JSON data.

    `TEMPLATE_CONTRACT_SHA256` covers this mapping with the rendered guide and its examples; a
    change here without a guide version bump fails a test (plan rulings 21 and 31)."""
    caps_list = f"1-{LIST_MAX_ITEMS} items (max_items, default {LIST_DEFAULT_MAX_ITEMS})"
    value_range = f"min/max within ±{VALUE_LIMIT:,}"
    return {
        "marker": TEMPLATE_MARKER,
        "max_template_bytes": MAX_TEMPLATE_BYTES,
        "max_params": MAX_TEMPLATE_PARAMS,
        "params_json_max_bytes": MAX_PARAMS_JSON_BYTES,
        "public_payload_max_bytes": MAX_PUBLIC_PAYLOAD_BYTES,
        "prompt_text_max": PROMPT_TEXT_MAX,
        "name_max": NAME_MAX,
        "description_max": DESCRIPTION_MAX,
        "label_max": LABEL_MAX,
        "param_name_pattern": f"^{PARAM_NAME_PATTERN}$",
        "reserved_param_names": list(RESERVED_PARAM_NAMES),
        "param_types": {
            "text": {
                "value": "string",
                "caps": f"max_length 1-{TEXT_MAX_LENGTH:,} (default {TEXT_DEFAULT_MAX_LENGTH})",
            },
            "text_list": {
                "value": "list of strings",
                "caps": f"{caps_list}; each max_length 1-{ITEM_MAX_LENGTH} "
                f"(default {ITEM_DEFAULT_MAX_LENGTH})",
            },
            "text_table": {
                "value": "list of rows, each a list of strings",
                "caps": f"1-{TABLE_MAX_ROWS} rows (max_items), 1-{TABLE_MAX_CELLS} cells per "
                f"row, each at most max_length (1-{CELL_MAX_LENGTH})",
            },
            "integer": {"value": "integer", "caps": value_range},
            "integer_list": {"value": "list of integers", "caps": f"{caps_list}; {value_range}"},
            "number": {
                "value": f"finite decimal, at most {NUMBER_DECIMALS} decimal places",
                "caps": value_range,
            },
            "number_list": {"value": "list of numbers", "caps": f"{caps_list}; {value_range}"},
            "boolean": {"value": "true or false", "caps": "-"},
            "pool": {
                "value": f"{MIN_POOL_ENTRIES}-{MAX_POOL_ENTRIES} entries {{cue, answer, "
                "accept?, explain?, order?}",
                "caps": f"cue and answer 1-{POOL_CUE_MAX}; accept up to {POOL_ACCEPT_MAX} "
                f"strings of 1-{POOL_ANSWER_MAX}; explain up to {POOL_EXPLAIN_MAX}; order a "
                "number",
            },
        },
        "param_roles": {
            "prompt": "sent to the template and the text view",
            "answer_key": "never sent before grading; the server grades against it",
            "reveal": "sent only with the graded result",
            "pool": "never sent; the server sends only what it draws",
        },
        "grading_kinds": {
            "none": {
                "purpose": "display",
                "without_pool": "no answer_key, no reveal, no pool",
                "with_pool": "not allowed",
                "answer": "-",
                "score": "-",
            },
            "choice_index": {
                "purpose": "exercise",
                "without_pool": "one answer_key of type integer (0-based) and options_param "
                "naming a prompt text_list",
                "with_pool": "pool_draw one_of_choices; no answer_key",
                "answer": '{"answer_kind": "choice_index", "option_index": 0}',
                "score": "100 or 0",
            },
            "exact_text": {
                "purpose": "exercise",
                "without_pool": "one answer_key of type text or text_list (accepted answers)",
                "with_pool": "pool_draw one; no answer_key",
                "answer": f'{{"answer_kind": "value", "value": "..."}} (at most '
                f"{ANSWER_VALUE_MAX} characters)",
                "score": "100 or 0",
            },
            "exact_set": {
                "purpose": "exercise",
                "without_pool": "one answer_key of type text_list",
                "with_pool": "not allowed",
                "answer": f'{{"answer_kind": "values", "values": [...]}} (at most '
                f"{ANSWER_VALUES_MAX})",
                "score": "100 x size of (answer and key) / size of (answer or key), rounded",
            },
            "exact_sequence": {
                "purpose": "exercise",
                "without_pool": f"one answer_key of type text_list in the right order "
                f"({SEQUENCE_MIN_ITEMS}-{SEQUENCE_MAX_ITEMS} distinct items); the server sends "
                "them shuffled",
                "with_pool": "pool_draw ordered; no answer_key",
                "answer": f'{{"answer_kind": "sequence", "values": [...]}} (at most '
                f"{ANSWER_SEQUENCE_MAX})",
                "score": "100 x longest common subsequence / longer length, rounded",
            },
            "numeric": {
                "purpose": "exercise",
                "without_pool": "one answer_key of type number; numeric_tolerance (absolute)",
                "with_pool": "not allowed",
                "answer": f'{{"answer_kind": "number", "value": "3,14"}} (a string of at most '
                f"{ANSWER_NUMBER_MAX})",
                "score": "100 or 0",
            },
            "self_report": {
                "purpose": "exercise",
                "without_pool": "no answer_key",
                "with_pool": "not allowed",
                "answer": f'{{"answer_kind": "self_report", "rating": {RATING_MIN}-{RATING_MAX},'
                ' "tempo_bpm"?, "minutes"?}',
                "score": "rating x 25; not right or wrong",
            },
        },
        "practice_kinds": {
            "drill": "pool_draw is not none",
            "exercise": "grading_kind self_report",
            "quiz": "any other exercise",
            "-": "display",
        },
        "text_match": {
            "fold_all": "NFKC; trim; inner spaces to one; case folded; accents folded; "
            f"surrounding punctuation {EDGE_PUNCTUATION} removed (the default)",
            "keep_accents": "as fold_all, but accents kept (año is not ano)",
            "exact_case": "NFKC; trim; inner spaces to one; nothing else",
        },
        "text_match_kinds": list(TEXT_GRADING_KINDS),
        "numeric_tolerance_max": NUMERIC_TOLERANCE_MAX,
        "numbers": "optional sign, digits, one decimal separator (. or ,) or a fraction p/q; "
        "no thousands separators (1,000 is refused; write 1000, or 1.000 for one)",
        "pool_draws": {
            "none": {
                "picks": "-",
                "drawn": 'null; for choice_index, {"options"}: the options_param list in '
                "its authored order",
                "key": "the answer_key",
            },
            "one": {
                "picks": "1 entry",
                "drawn": '{"cue"}',
                "key": "the entry's answer and its accept values",
            },
            "one_of_choices": {
                "picks": "1 entry plus pool_count - 1 entries with other answers",
                "drawn": '{"cue", "options"}: the answers in a seeded order',
                "key": "the index of the drawn entry's answer in options",
            },
            "ordered": {
                "picks": "pool_count entries; every entry needs a distinct order and a cue "
                "distinct after text_match",
                "drawn": '{"cues"}: in a seeded order that is not the sorted one',
                "key": "the cues sorted by order ascending",
            },
            "exact_sequence without a pool": {
                "picks": "-",
                "drawn": '{"cues"}: the key items in a seeded order that is not the right one',
                "key": "the answer_key list",
            },
        },
        "pool_count_ranges": {draw: list(pair) for draw, pair in POOL_COUNT_RANGES.items()},
        "pool_entries": [MIN_POOL_ENTRIES, MAX_POOL_ENTRIES],
        "payload_keys": [
            "v",
            "instance_id",
            "template_purpose",
            "grading_kind",
            "ui_lang",
            "params",
            "drawn",
            "labels",
        ],
        "channels": {
            "artifact": "tutorSubmit shows the answer in words and asks the learner to tell Claude",
            "web": "tutorSubmit shows the answer; nothing is recorded",
            "app_frame": "tutorSubmit hands the answer to the practice app, which records it",
        },
    }


# --- types --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParamSpecInput:
    name: str
    param_type: str
    param_role: str
    label: str
    required: bool = True
    min_items: int | None = None
    max_items: int | None = None
    max_length: int | None = None
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True, slots=True)
class TemplateInput:
    """A template as it arrives from outside (save_template); the HTML is checked apart."""

    name: str
    description: str | None
    template_purpose: str
    grading_kind: str
    params: Sequence[ParamSpecInput]
    text_match: str | None = None
    numeric_tolerance: float | Decimal | None = None
    pool_draw: str | None = None
    pool_count: int | None = None
    needs_view: bool = False
    options_param: str | None = None


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One parameter of a template, with every default filled."""

    name: str
    param_type: ParamType
    param_role: ParamRole
    required: bool
    label: str
    min_items: int | None = None
    max_items: int | None = None
    max_length: int | None = None
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    name: str
    description: str | None
    purpose: TemplatePurpose
    practice_kind: PracticeKind | None
    grading_kind: GradingKind
    text_match: TextMatch | None
    numeric_tolerance: Decimal | None
    pool_draw: PoolDraw
    pool_count: int | None
    needs_view: bool
    params: tuple[ParamSpec, ...]
    options_param: str | None

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)

    def by_role(self, role: ParamRole) -> tuple[ParamSpec, ...]:
        return tuple(p for p in self.params if p.param_role == role)


@dataclass(frozen=True, slots=True)
class PoolEntryInput:
    cue: str
    answer: str
    accept: Sequence[str] = ()
    explain: str | None = None
    order: float | Decimal | None = None


@dataclass(frozen=True, slots=True)
class ParamPair:
    """One parameter on the wire: its name, the value field that was sent (a ParamType name)
    and the value (a PoolEntryInput sequence for a pool)."""

    name: str
    param_type: str
    value: object


@dataclass(frozen=True, slots=True)
class Draw:
    """The picked pool entries and the order shown (spec 15.4.1); empty for no pool and no
    sequence. `order[k]` is the index (into `entries`, or into the key for an authored
    sequence) of what is shown at position k."""

    entries: tuple[int, ...] = ()
    order: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Graded:
    correct: bool | None
    score: int
    reveal: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    option_index: int


@dataclass(frozen=True, slots=True)
class ValueAnswer:
    value: str


@dataclass(frozen=True, slots=True)
class ValuesAnswer:
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SequenceAnswer:
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NumberAnswer:
    value: Decimal


@dataclass(frozen=True, slots=True)
class SelfReportAnswer:
    rating: int
    tempo_bpm: int | None = None
    minutes: int | None = None


Answer = (
    ChoiceAnswer | ValueAnswer | ValuesAnswer | SequenceAnswer | NumberAnswer | SelfReportAnswer
)


# --- small helpers ------------------------------------------------------------------------------


def _fail(errors: Errors, field: str, code: FieldErrorCode) -> None:
    errors.append(FieldError(field, code))


def _choice[T: str](value: str | None, allowed: tuple[T, ...]) -> T | None:
    return next((a for a in allowed if a == value), None)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _decimal(value: object) -> Decimal | None:
    """A finite Decimal from an int, a float or a Decimal; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        number = Decimal(repr(value))
    elif isinstance(value, Decimal):
        number = value
    else:
        return None
    return number if number.is_finite() else None


def _decimals_ok(number: Decimal) -> bool:
    exponent = number.as_tuple().exponent
    return isinstance(exponent, int) and exponent >= -NUMBER_DECIMALS


def _number_out(number: Decimal) -> float:
    return float(number)


def canonical_json(value: object) -> str:
    """Sorted keys, no spaces, UTF-8 kept: the form hashed and measured."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _bytes(value: object) -> int:
    return len(canonical_json(value).encode("utf-8"))


# --- templates ----------------------------------------------------------------------------------


def _length(text: str, limit: int, field: str, errors: Errors) -> None:
    if not text:
        _fail(errors, field, "required")
    elif len(text) > limit:
        _fail(errors, field, "too_long")


def _items_range(param_type: ParamType) -> tuple[int, int, int]:
    """(low, high, default max) for max_items."""
    if param_type == "text_table":
        return 1, TABLE_MAX_ROWS, TABLE_MAX_ROWS
    return 1, LIST_MAX_ITEMS, LIST_DEFAULT_MAX_ITEMS


def _length_range(param_type: ParamType) -> tuple[int, int] | None:
    """(high, default) for max_length, None where max_length does not apply."""
    if param_type == "text":
        return TEXT_MAX_LENGTH, TEXT_DEFAULT_MAX_LENGTH
    if param_type == "text_list":
        return ITEM_MAX_LENGTH, ITEM_DEFAULT_MAX_LENGTH
    if param_type == "text_table":
        return CELL_MAX_LENGTH, CELL_MAX_LENGTH
    return None


_NUMERIC_TYPES: tuple[ParamType, ...] = ("integer", "integer_list", "number", "number_list")


def _bound(
    value: float | None, default: int, param_type: ParamType, field: str, errors: Errors
) -> float | None:
    integral = param_type in ("integer", "integer_list")
    if value is None:
        return default if integral else float(default)
    number = _decimal(value)
    if (
        number is None
        or not _decimals_ok(number)
        or (integral and number != number.to_integral_value())
    ):
        _fail(errors, field, "invalid")
        return None
    if abs(number) > VALUE_LIMIT:
        _fail(errors, field, "out_of_range")
        return None
    return int(number) if integral else _number_out(number)


def _param_spec(data: ParamSpecInput, path: str, errors: Errors) -> ParamSpec | None:
    before = len(errors)
    if not _NAME.fullmatch(data.name) or data.name in RESERVED_PARAM_NAMES:
        _fail(errors, f"{path}.name", "invalid")
    param_type = _choice(data.param_type, PARAM_TYPES)
    if param_type is None:
        _fail(errors, f"{path}.param_type", "invalid_choice")
    param_role = _choice(data.param_role, PARAM_ROLES)
    if param_role is None:
        _fail(errors, f"{path}.param_role", "invalid_choice")
    label = collapse_name(data.label)
    _length(label, LABEL_MAX, f"{path}.label", errors)
    if param_type is None or param_role is None:
        return None
    min_items = max_items = max_length = None
    minimum = maximum = None
    if param_type in LIST_TYPES:
        low, high, default = _items_range(param_type)
        min_items = low if data.min_items is None else data.min_items
        max_items = default if data.max_items is None else data.max_items
        if not low <= max_items <= high:
            _fail(errors, f"{path}.max_items", "out_of_range")
        if not low <= min_items <= high:
            _fail(errors, f"{path}.min_items", "out_of_range")
        elif min_items > max_items:
            _fail(errors, f"{path}.min_items", "invalid")
    else:
        for field, value in (("min_items", data.min_items), ("max_items", data.max_items)):
            if value is not None:
                _fail(errors, f"{path}.{field}", "invalid")
    lengths = _length_range(param_type)
    if lengths is not None:
        high, default = lengths
        max_length = default if data.max_length is None else data.max_length
        if not 1 <= max_length <= high:
            _fail(errors, f"{path}.max_length", "out_of_range")
    elif data.max_length is not None:
        _fail(errors, f"{path}.max_length", "invalid")
    if param_type in _NUMERIC_TYPES:
        minimum = _bound(data.min, -VALUE_LIMIT, param_type, f"{path}.min", errors)
        maximum = _bound(data.max, VALUE_LIMIT, param_type, f"{path}.max", errors)
        if minimum is not None and maximum is not None and minimum > maximum:
            _fail(errors, f"{path}.min", "invalid")
    else:
        for field, number in (("min", data.min), ("max", data.max)):
            if number is not None:
                _fail(errors, f"{path}.{field}", "invalid")
    if len(errors) > before:
        return None
    return ParamSpec(
        name=data.name,
        param_type=param_type,
        param_role=param_role,
        required=data.required,
        label=label,
        min_items=min_items,
        max_items=max_items,
        max_length=max_length,
        min=minimum,
        max=maximum,
    )


def derive_practice_kind(
    purpose: TemplatePurpose, grading_kind: GradingKind, pool_draw: PoolDraw
) -> PracticeKind | None:
    """`drill` with a pool, `exercise` for self_report, `quiz` otherwise; None for a display."""
    if purpose == "display":
        return None
    if pool_draw != "none":
        return "drill"
    if grading_kind == "self_report":
        return "exercise"
    return "quiz"


def _tolerance(value: float | Decimal | None, errors: Errors) -> Decimal | None:
    if value is None:
        return Decimal(0)
    number = _decimal(value)
    if number is None or not _decimals_ok(number):
        _fail(errors, "numeric_tolerance", "invalid")
        return None
    if not 0 <= number <= NUMERIC_TOLERANCE_MAX:
        _fail(errors, "numeric_tolerance", "out_of_range")
        return None
    return number


def _check_roles(
    specs: Sequence[tuple[int, ParamSpec]],
    grading: GradingKind,
    pool_draw: PoolDraw,
    errors: Errors,
) -> None:
    """Pool, answer_key and reveal parameters against the compatibility table of spec 15.3."""
    pools = [(i, p) for i, p in specs if p.param_type == "pool" or p.param_role == "pool"]
    if pool_draw == "none":
        for index, param in pools:
            field = "param_type" if param.param_type == "pool" else "param_role"
            _fail(errors, f"params[{index}].{field}", "invalid")
    else:
        proper = [(i, p) for i, p in pools if p.param_type == "pool" and p.param_role == "pool"]
        if not proper:
            _fail(errors, "params", "invalid")  # a pool draw needs its pool parameter
        for index, param in pools:
            if (index, param) in proper[:1]:
                continue
            field = "param_type" if param.param_type == "pool" else "param_role"
            _fail(errors, f"params[{index}].{field}", "invalid")
    keys = [(i, p) for i, p in specs if p.param_role == "answer_key"]
    if grading == "none":
        for index, _ in [*keys, *((i, p) for i, p in specs if p.param_role == "reveal")]:
            _fail(errors, f"params[{index}].param_role", "invalid")
        return
    if grading == "self_report" or pool_draw != "none":
        for index, _ in keys:
            _fail(errors, f"params[{index}].param_role", "invalid")
        return
    if not keys:
        _fail(errors, "params", "invalid")  # this grading kind needs one answer_key
        return
    for index, _ in keys[1:]:
        _fail(errors, f"params[{index}].param_role", "invalid")
    index, key = keys[0]
    if key.param_type not in ANSWER_KEY_TYPES[grading]:
        _fail(errors, f"params[{index}].param_type", "invalid")


def _check_options_param(
    data: TemplateInput,
    specs: Sequence[ParamSpec],
    grading: GradingKind,
    pool_draw: PoolDraw,
    errors: Errors,
) -> str | None:
    if grading == "choice_index" and pool_draw == "none":
        if not data.options_param:
            _fail(errors, "options_param", "required")
            return None
        target = next((p for p in specs if p.name == data.options_param), None)
        if (
            target is None
            or target.param_type != "text_list"
            or target.param_role != "prompt"
            or not target.required
        ):
            _fail(errors, "options_param", "invalid")
            return None
        return data.options_param
    if data.options_param is not None:
        _fail(errors, "options_param", "invalid")
    return None


def validate_template(
    data: TemplateInput, guide_version: int
) -> TemplateSpec | tuple[FieldError, ...]:
    """Check a template against spec 15.3 and derive its practice kind.

    Errors name the field (`grading_kind`, `params[2].param_role`, `options_param`, ...), in
    field order; `guide_version` other than TEMPLATE_GUIDE_VERSION is `stale`."""
    errors: Errors = []
    if guide_version != TEMPLATE_GUIDE_VERSION:
        _fail(errors, "guide_version", "stale")
    name = collapse_name(data.name)
    _length(name, NAME_MAX, "name", errors)
    description = (data.description or "").strip() or None
    if description is not None and len(description) > DESCRIPTION_MAX:
        _fail(errors, "description", "too_long")
    purpose = _choice(data.template_purpose, TEMPLATE_PURPOSES)
    if purpose is None:
        _fail(errors, "template_purpose", "invalid_choice")
    grading = _choice(data.grading_kind, GRADING_KINDS)
    if grading is None:
        _fail(errors, "grading_kind", "invalid_choice")
    pool_draw = _choice(data.pool_draw or "none", POOL_DRAWS)
    if pool_draw is None:
        _fail(errors, "pool_draw", "invalid_choice")
    if not data.params:
        _fail(errors, "params", "required")
    elif len(data.params) > MAX_TEMPLATE_PARAMS:
        _fail(errors, "params", "too_many")
    specs: list[tuple[int, ParamSpec]] = []
    seen: set[str] = set()
    for index, item in enumerate(data.params):
        spec = _param_spec(item, f"params[{index}]", errors)
        if item.name in seen:
            _fail(errors, f"params[{index}].name", "invalid")  # a duplicate name
        seen.add(item.name)
        if spec is not None:
            specs.append((index, spec))
    if purpose is None or grading is None or pool_draw is None:
        return tuple(errors)
    if (purpose == "display") != (grading == "none"):
        _fail(errors, "grading_kind", "invalid")
    text_match: TextMatch | None = None
    if grading in TEXT_GRADING_KINDS:
        text_match = _choice(data.text_match or "fold_all", TEXT_MATCHES)
        if text_match is None:
            _fail(errors, "text_match", "invalid_choice")
    elif data.text_match is not None:
        _fail(errors, "text_match", "invalid")
    tolerance: Decimal | None = None
    if grading == "numeric":
        tolerance = _tolerance(data.numeric_tolerance, errors)
    elif data.numeric_tolerance is not None:
        _fail(errors, "numeric_tolerance", "invalid")
    if pool_draw != "none" and POOL_DRAW_FOR.get(grading) != pool_draw:
        _fail(errors, "pool_draw", "invalid")
    count_range = POOL_COUNT_RANGES.get(pool_draw)
    if count_range is None:
        if data.pool_count is not None:
            _fail(errors, "pool_count", "invalid")
    elif data.pool_count is None:
        _fail(errors, "pool_count", "required")
    elif not count_range[0] <= data.pool_count <= count_range[1]:
        _fail(errors, "pool_count", "out_of_range")
    _check_roles(specs, grading, pool_draw, errors)
    for index, spec in specs:
        if spec.param_role in ("answer_key", "pool") and not spec.required:
            _fail(errors, f"params[{index}].required", "invalid")
    options_param = _check_options_param(data, [s for _, s in specs], grading, pool_draw, errors)
    if errors:
        return tuple(errors)
    return TemplateSpec(
        name=name,
        description=description,
        purpose=purpose,
        practice_kind=derive_practice_kind(purpose, grading, pool_draw),
        grading_kind=grading,
        text_match=text_match,
        numeric_tolerance=tolerance,
        pool_draw=pool_draw,
        pool_count=data.pool_count if count_range is not None else None,
        needs_view=data.needs_view,
        params=tuple(spec for _, spec in specs),
        options_param=options_param,
    )


def param_specs_to_json(params: Sequence[ParamSpec]) -> list[dict[str, Any]]:
    """The stored form of a template's parameter specs (a JSON array)."""
    return [
        {
            "name": p.name,
            "param_type": p.param_type,
            "param_role": p.param_role,
            "required": p.required,
            "label": p.label,
            "min_items": p.min_items,
            "max_items": p.max_items,
            "max_length": p.max_length,
            "min": p.min,
            "max": p.max,
        }
        for p in params
    ]


def param_specs_from_json(data: Sequence[Mapping[str, Any]]) -> tuple[ParamSpec, ...]:
    """The inverse of `param_specs_to_json` for rows written by it."""
    specs: list[ParamSpec] = []
    for item in data:
        param_type = _choice(str(item["param_type"]), PARAM_TYPES)
        param_role = _choice(str(item["param_role"]), PARAM_ROLES)
        if param_type is None or param_role is None:
            raise ValueError("stored parameter spec has an unknown type or role")
        integral = param_type in ("integer", "integer_list")
        minimum, maximum = item.get("min"), item.get("max")
        specs.append(
            ParamSpec(
                name=str(item["name"]),
                param_type=param_type,
                param_role=param_role,
                required=bool(item["required"]),
                label=str(item["label"]),
                min_items=item.get("min_items"),
                max_items=item.get("max_items"),
                max_length=item.get("max_length"),
                min=None if minimum is None else (int(minimum) if integral else float(minimum)),
                max=None if maximum is None else (int(maximum) if integral else float(maximum)),
            )
        )
    return tuple(specs)


def spec_hash(spec: TemplateSpec) -> str:
    """SHA-256 of the whole spec: with the HTML hash, what makes a save `existing`."""
    data = {
        "name": spec.name,
        "description": spec.description,
        "purpose": spec.purpose,
        "practice_kind": spec.practice_kind,
        "grading_kind": spec.grading_kind,
        "text_match": spec.text_match,
        "numeric_tolerance": None
        if spec.numeric_tolerance is None
        else str(spec.numeric_tolerance),
        "pool_draw": spec.pool_draw,
        "pool_count": spec.pool_count,
        "needs_view": spec.needs_view,
        "params": param_specs_to_json(spec.params),
        "options_param": spec.options_param,
    }
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


# --- parameters ---------------------------------------------------------------------------------

_BAD = object()  # a value that failed; its error is already recorded


def _strings(
    value: object, param: ParamSpec, path: str, errors: Errors, *, limit: int
) -> list[str] | object:
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        _fail(errors, path, "invalid")
        return _BAD
    items = [str(v).strip() for v in value]
    if not _count_ok(len(items), param, path, errors):
        return _BAD
    ok = True
    for index, item in enumerate(items):
        if not item:
            _fail(errors, f"{path}[{index}]", "required")
            ok = False
        elif len(item) > limit:
            _fail(errors, f"{path}[{index}]", "too_long")
            ok = False
    return items if ok else _BAD


def _count_ok(count: int, param: ParamSpec, path: str, errors: Errors) -> bool:
    low = param.min_items or 1
    high = param.max_items or LIST_DEFAULT_MAX_ITEMS
    if count > high:
        _fail(errors, path, "too_many")
        return False
    if count < low:
        _fail(errors, path, "required" if count == 0 else "invalid")
        return False
    return True


def _in_bounds(number: Decimal, param: ParamSpec) -> bool:
    low = Decimal(repr(param.min)) if param.min is not None else Decimal(-VALUE_LIMIT)
    high = Decimal(repr(param.max)) if param.max is not None else Decimal(VALUE_LIMIT)
    return low <= number <= high


def _scalar(value: object, param: ParamSpec, path: str, errors: Errors) -> int | float | object:
    integral = param.param_type in ("integer", "integer_list")
    if integral and not _is_int(value):
        _fail(errors, path, "invalid")
        return _BAD
    number = _decimal(value)
    if number is None or not _decimals_ok(number):
        _fail(errors, path, "invalid")
        return _BAD
    if not _in_bounds(number, param):
        _fail(errors, path, "out_of_range")
        return _BAD
    return int(number) if integral else _number_out(number)


def _numbers(value: object, param: ParamSpec, path: str, errors: Errors) -> list[Any] | object:
    if not isinstance(value, list | tuple):
        _fail(errors, path, "invalid")
        return _BAD
    if not _count_ok(len(value), param, path, errors):
        return _BAD
    items = [_scalar(v, param, f"{path}[{i}]", errors) for i, v in enumerate(value)]
    return _BAD if any(item is _BAD for item in items) else items


def _table(value: object, param: ParamSpec, path: str, errors: Errors) -> list[Any] | object:
    if not isinstance(value, list | tuple) or not all(isinstance(r, list | tuple) for r in value):
        _fail(errors, path, "invalid")
        return _BAD
    if not _count_ok(len(value), param, path, errors):
        return _BAD
    limit = param.max_length or CELL_MAX_LENGTH
    rows: list[list[str]] = []
    ok = True
    for r, row in enumerate(value):
        if not 1 <= len(row) <= TABLE_MAX_CELLS or not all(isinstance(c, str) for c in row):
            _fail(errors, f"{path}[{r}]", "invalid")
            ok = False
            continue
        cells = [str(c).strip() for c in row]
        for c, cell in enumerate(cells):
            if len(cell) > limit:
                _fail(errors, f"{path}[{r}][{c}]", "too_long")
                ok = False
        rows.append(cells)
    return rows if ok else _BAD


def _entry_text(value: object, limit: int, path: str, errors: Errors) -> str | None:
    if not isinstance(value, str):
        _fail(errors, path, "invalid")
        return None
    text = value.strip()
    _length(text, limit, path, errors)
    return text if text and len(text) <= limit else None


def _pool(value: object, spec: TemplateSpec, path: str, errors: Errors) -> list[Any] | object:
    if not isinstance(value, list | tuple) or not all(isinstance(e, PoolEntryInput) for e in value):
        _fail(errors, path, "invalid")
        return _BAD
    if len(value) > MAX_POOL_ENTRIES:
        _fail(errors, path, "too_many")
        return _BAD
    if len(value) < MIN_POOL_ENTRIES:
        _fail(errors, path, "required" if not value else "invalid")
        return _BAD
    before = len(errors)
    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        at = f"{path}[{index}]"
        entry = raw if isinstance(raw, PoolEntryInput) else PoolEntryInput("", "")
        cue = _entry_text(entry.cue, POOL_CUE_MAX, f"{at}.cue", errors)
        answer = _entry_text(entry.answer, POOL_ANSWER_MAX, f"{at}.answer", errors)
        accept: list[str] = []
        if len(entry.accept) > POOL_ACCEPT_MAX:
            _fail(errors, f"{at}.accept", "too_many")
        else:
            for k, alt in enumerate(entry.accept):
                text = _entry_text(alt, POOL_ANSWER_MAX, f"{at}.accept[{k}]", errors)
                if text is not None:
                    accept.append(text)
        explain = None
        if entry.explain is not None and entry.explain.strip():
            explain = entry.explain.strip()
            if len(explain) > POOL_EXPLAIN_MAX:
                _fail(errors, f"{at}.explain", "too_long")
        order: float | None = None
        if spec.pool_draw == "ordered":
            number = None if entry.order is None else _decimal(entry.order)
            if entry.order is None:
                _fail(errors, f"{at}.order", "required")
            elif number is None or not _decimals_ok(number) or abs(number) > VALUE_LIMIT:
                _fail(errors, f"{at}.order", "invalid")
            else:
                order = _number_out(number)
        elif entry.order is not None:
            _fail(errors, f"{at}.order", "invalid")
        entries.append(
            {"cue": cue, "answer": answer, "accept": accept, "explain": explain, "order": order}
        )
    if len(errors) > before:
        return _BAD
    count = spec.pool_count or 0
    if spec.pool_draw == "ordered":
        orders = [e["order"] for e in entries]
        text_match = spec.text_match or "fold_all"
        cues = {normalise_answer(e["cue"], text_match) for e in entries}
        if len(set(orders)) != len(orders) or len(cues) != len(entries) or len(entries) < count:
            # orders and normalised cues must be distinct, as an authored key's items are
            # (`_check_keys`; plan ruling 42)
            _fail(errors, path, "invalid")
            return _BAD
    if spec.pool_draw == "one_of_choices":
        answers = {normalise_answer(e["answer"], "fold_all") for e in entries}
        if len(answers) < count:
            _fail(errors, path, "invalid")  # too few distinct answers for the options
            return _BAD
    return entries


def _value(
    param: ParamSpec, value: object, spec: TemplateSpec, path: str, errors: Errors
) -> object:
    kind = param.param_type
    if kind == "text":
        if not isinstance(value, str):
            _fail(errors, path, "invalid")
            return _BAD
        if not value.strip():
            _fail(errors, path, "required")
            return _BAD
        if len(value) > (param.max_length or TEXT_DEFAULT_MAX_LENGTH):
            _fail(errors, path, "too_long")
            return _BAD
        return value
    if kind == "text_list":
        return _strings(
            value, param, path, errors, limit=param.max_length or ITEM_DEFAULT_MAX_LENGTH
        )
    if kind == "text_table":
        return _table(value, param, path, errors)
    if kind in ("integer", "number"):
        return _scalar(value, param, path, errors)
    if kind in ("integer_list", "number_list"):
        return _numbers(value, param, path, errors)
    if kind == "boolean":
        if not isinstance(value, bool):
            _fail(errors, path, "invalid")
            return _BAD
        return value
    return _pool(value, spec, path, errors)


def validate_params(
    spec: TemplateSpec, pairs: Sequence[ParamPair], *, path: str
) -> dict[str, object] | tuple[FieldError, ...]:
    """Check an instance's parameters against its template (spec 15.4, 15.4.1).

    Returns the canonical parameters: every name of the spec (absent optional ones as None),
    keys sorted, answer key, reveal and pool included. Paths start at `path` (`""` for the
    top level): `notes[0].instances[2].params[1].pool[7].answer`."""
    base = f"{path}.params" if path else "params"
    errors: Errors = []
    values: dict[str, object] = {}
    where: dict[str, str] = {}
    for index, pair in enumerate(pairs):
        at = f"{base}[{index}]"
        param = spec.param(pair.name)
        if param is None or pair.name in where:
            _fail(errors, f"{at}.name", "invalid")  # unknown or repeated
            continue
        where[pair.name] = f"{at}.{param.param_type}"
        if pair.param_type != param.param_type:
            _fail(errors, at, "invalid")  # the wrong value field for this parameter
            continue
        value = _value(param, pair.value, spec, where[pair.name], errors)
        if value is not _BAD:
            values[pair.name] = value
    for param in spec.params:
        if param.required and param.name not in where:
            _fail(errors, base, "required")
    if errors:
        return tuple(dict.fromkeys(errors))
    complete = {p.name: values.get(p.name) for p in spec.params}
    _check_keys(spec, complete, where, errors)
    if not errors and _bytes(complete) > MAX_PARAMS_JSON_BYTES:
        _fail(errors, base, "too_long")
    if not errors and _worst_public_bytes(spec, complete) > MAX_PUBLIC_PAYLOAD_BYTES:
        _fail(errors, base, "too_long")
    if errors:
        return tuple(errors)
    return dict(sorted(complete.items()))


def _check_keys(
    spec: TemplateSpec, values: Mapping[str, object], where: Mapping[str, str], errors: Errors
) -> None:
    keys = spec.by_role("answer_key")
    if not keys:
        return
    key = keys[0]
    value = values[key.name]
    if spec.grading_kind == "choice_index" and spec.options_param is not None:
        options = values.get(spec.options_param)
        count = len(options) if isinstance(options, list) else 0
        if not _is_int(value) or not 0 <= value < count:
            _fail(errors, where[key.name], "out_of_range")
    if spec.grading_kind == "exact_sequence" and isinstance(value, list):
        text_match = spec.text_match or "fold_all"
        normalised = {normalise_answer(str(v), text_match) for v in value}
        if not SEQUENCE_MIN_ITEMS <= len(value) <= SEQUENCE_MAX_ITEMS or len(normalised) != len(
            value
        ):
            _fail(errors, where[key.name], "invalid")


def _pool_entries(spec: TemplateSpec, params: Mapping[str, object]) -> list[Mapping[str, Any]]:
    pools = spec.by_role("pool")
    if not pools:
        return []
    value = params.get(pools[0].name)
    return [e for e in value if isinstance(e, Mapping)] if isinstance(value, list) else []


def _worst_drawn(spec: TemplateSpec, params: Mapping[str, object]) -> object:
    """The largest `drawn` any showing of this instance can send."""
    entries = _pool_entries(spec, params)

    def longest(texts: list[str], count: int) -> list[str]:
        return sorted(texts, key=lambda t: len(t.encode("utf-8")), reverse=True)[:count]

    cues = [str(e["cue"]) for e in entries]
    count = spec.pool_count or 0
    if spec.pool_draw == "one":
        return {"cue": longest(cues, 1)[0] if cues else ""}
    if spec.pool_draw == "one_of_choices":
        answers = list({str(e["answer"]): None for e in entries})
        return {"cue": longest(cues, 1)[0] if cues else "", "options": longest(answers, count)}
    if spec.pool_draw == "ordered":
        return {"cues": longest(cues, count)}
    if spec.grading_kind == "exact_sequence":
        key = params.get(spec.by_role("answer_key")[0].name)
        return {"cues": key if isinstance(key, list) else []}
    return _authored_options(spec, params)


def _authored_options(spec: TemplateSpec, params: Mapping[str, object]) -> object:
    """An authored choice's options as `drawn.options`, in the order option_index counts, so
    a bridge can word the answer (plan ruling 35); the options are prompt parameters already,
    and the key is never part of it."""
    if spec.pool_draw != "none" or spec.grading_kind != "choice_index" or not spec.options_param:
        return None
    options = params.get(spec.options_param)
    return {"options": list(options)} if isinstance(options, list) else None


def _worst_public_bytes(spec: TemplateSpec, params: Mapping[str, object]) -> int:
    public = {p.name: params.get(p.name) for p in spec.by_role("prompt")}
    return _bytes({"params": public, "drawn": _worst_drawn(spec, params)})


def public_params(spec: TemplateSpec, params: Mapping[str, object]) -> dict[str, object]:
    """Prompt parameters only: what may reach a client before grading (plan ruling 25)."""
    return {p.name: params.get(p.name) for p in spec.by_role("prompt")}


def pool_size(spec: TemplateSpec, params: Mapping[str, object]) -> int | None:
    """How many entries the instance's pool holds; None without a pool."""
    if spec.pool_draw == "none":
        return None
    return len(_pool_entries(spec, params))


def content_key(
    subject_id: UUID,
    template_id: UUID,
    params: Mapping[str, object],
    prompt_text: str,
    note_id: UUID | None,
) -> str:
    """`sha256(subject | template | canonical params | prompt_text | note)`, parts joined with
    U+001F (spec 15.4): the same instance twice is one row."""
    parts = (
        str(subject_id),
        str(template_id),
        canonical_json(dict(params)),
        prompt_text,
        str(note_id) if note_id is not None else "",
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# --- draws --------------------------------------------------------------------------------------


def seed_for(identifier: UUID) -> int:
    """The first 8 bytes of `sha256(identifier.bytes)` as a signed 64-bit integer, so it fits a
    Postgres bigint (spec 15.4.1)."""
    digest = hashlib.sha256(identifier.bytes).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class _SplitMix64:
    """SplitMix64: a fixed generator, so a pinned seed gives a pinned draw on any Python."""

    def __init__(self, seed: int) -> None:
        self._state = seed & _MASK64

    def next(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK64
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        return z ^ (z >> 31)

    def below(self, bound: int) -> int:
        return (self.next() * bound) >> 64

    def permutation(self, size: int) -> list[int]:
        items = list(range(size))
        for index in range(size - 1, 0, -1):
            other = self.below(index + 1)
            items[index], items[other] = items[other], items[index]
        return items


def _not(order: list[int], forbidden: tuple[int, ...]) -> tuple[int, ...]:
    """The order, rotated by one if it equals the forbidden one (both have at least
    SEQUENCE_MIN_ITEMS items, so the shown order never gives the right one away)."""
    if tuple(order) == forbidden:
        order = order[1:] + order[:1]
    return tuple(order)


def draw(spec: TemplateSpec, params: Mapping[str, object], seed: int) -> Draw:
    """What one showing presents (spec 15.4.1): picked pool entries and the order shown."""
    rng = _SplitMix64(seed)
    entries = _pool_entries(spec, params)
    count = spec.pool_count or 0
    if spec.pool_draw == "one" and entries:
        return Draw(entries=(rng.below(len(entries)),))
    if spec.pool_draw == "one_of_choices" and entries:
        first = rng.below(len(entries))
        picked = [first]
        seen = {normalise_answer(str(entries[first]["answer"]), "fold_all")}
        for index in rng.permutation(len(entries)):
            if len(picked) == count:
                break
            answer = normalise_answer(str(entries[index]["answer"]), "fold_all")
            if answer not in seen:
                seen.add(answer)
                picked.append(index)
        return Draw(entries=tuple(picked), order=tuple(rng.permutation(len(picked))))
    if spec.pool_draw == "ordered" and entries:
        picked = rng.permutation(len(entries))[:count]
        shown = rng.permutation(len(picked))
        right = tuple(sorted(range(len(picked)), key=lambda k: entries[picked[k]]["order"]))
        return Draw(entries=tuple(picked), order=_not(shown, right))
    if spec.pool_draw == "none" and spec.grading_kind == "exact_sequence":
        key = params.get(spec.by_role("answer_key")[0].name)
        size = len(key) if isinstance(key, list) else 0
        if size >= SEQUENCE_MIN_ITEMS:
            return Draw(order=_not(rng.permutation(size), tuple(range(size))))
    return Draw()


def _drawn(spec: TemplateSpec, params: Mapping[str, object], shown: Draw) -> object:
    entries = _pool_entries(spec, params)
    if spec.pool_draw == "one" and shown.entries:
        return {"cue": entries[shown.entries[0]]["cue"]}
    if spec.pool_draw == "one_of_choices" and shown.entries:
        options = [entries[shown.entries[k]]["answer"] for k in shown.order]
        return {"cue": entries[shown.entries[0]]["cue"], "options": options}
    if spec.pool_draw == "ordered" and shown.entries:
        return {"cues": [entries[shown.entries[k]]["cue"] for k in shown.order]}
    if spec.pool_draw == "none" and spec.grading_kind == "exact_sequence" and shown.order:
        key = params.get(spec.by_role("answer_key")[0].name)
        items = key if isinstance(key, list) else []
        return {"cues": [items[k] for k in shown.order]}
    return _authored_options(spec, params)


def public_payload(
    instance_id: UUID,
    spec: TemplateSpec,
    params: Mapping[str, object],
    shown: Draw,
    ui_lang: str = UI_LANG,
) -> dict[str, object]:
    """The JSON a template reads (spec 15.5): prompt parameters and the draw only."""
    return {
        "v": PAYLOAD_VERSION,
        "instance_id": str(instance_id),
        "template_purpose": spec.purpose,
        "grading_kind": spec.grading_kind,
        "ui_lang": ui_lang,
        "params": public_params(spec, params),
        "drawn": _drawn(spec, params, shown),
        "labels": {p.name: p.label for p in spec.by_role("prompt")},
    }


_SCRIPT_ESCAPES = (
    ("<", "\\u003c"),
    (">", "\\u003e"),
    ("&", "\\u0026"),
    ("'", "\\u0027"),
    (chr(0x2028), "\\u2028"),
    (chr(0x2029), "\\u2029"),
)


def encode_payload(payload: Mapping[str, object]) -> str:
    """Sorted-key JSON safe inside a `<script>` element: `<`, `>`, `&`, `'`, U+2028 and U+2029
    become JSON escapes, so no value can close the element or start markup (spec 15.5)."""
    text = canonical_json(dict(payload))
    for raw, escaped in _SCRIPT_ESCAPES:
        text = text.replace(raw, escaped)
    return text


def compose_document(template_html: str, payload_json: str, bridge_js: str) -> str:
    """Put the payload inside the marker and the bridge right after it: the only two changes
    to the template's text. ValueError unless the marker is there exactly once, or when the
    payload or the bridge could end their element (callers pass validated HTML only)."""
    if template_html.count(TEMPLATE_MARKER) != 1:
        raise ValueError("the template must contain the parameter marker exactly once")
    if "<" in payload_json or "</script" in bridge_js.lower():
        raise ValueError("payload or bridge would end its script element")
    filled = (
        '<script type="application/json" id="tutor-params">'
        f"{payload_json}</script>"
        f'<script id="tutor-bridge">{bridge_js}</script>'
    )
    return template_html.replace(TEMPLATE_MARKER, filled, 1)


# --- the text view ------------------------------------------------------------------------------

_ANSWER_FORMAT: Mapping[GradingKind, str] = {
    "choice_index": "Responde con el número de la opción.",
    "exact_text": "Responde con una palabra o frase corta.",
    "exact_set": "Responde con todos los elementos, separados por comas.",
    "exact_sequence": "Responde con los elementos en orden, separados por comas.",
    "numeric": "Responde con un número.",
    "self_report": "Cuando termines, di del 1 al 4 cómo te fue.",
}
NEEDS_VIEW_TEXT = "Este ejercicio necesita la vista interactiva."


def _format_number(value: object) -> str:
    number = _decimal(value)
    if number is None:
        return str(value)
    text = format(number.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _render_value(param: ParamSpec, value: object, *, display: bool, numbered: bool) -> list[str]:
    if value is None:
        return []
    label = param.label
    if param.param_type == "text":
        text = str(value)
        if display or "\n" in text:
            return [f"{label}:", "```", text, "```"]
        return [f"{label}: {text}"]
    if param.param_type == "text_list" and isinstance(value, list):
        if numbered:
            return [f"{label}:", *(f"{i}. {v}" for i, v in enumerate(value, start=1))]
        return [f"{label}: " + "; ".join(str(v) for v in value)]
    if param.param_type == "text_table" and isinstance(value, list):
        rows = [" | ".join(str(c) for c in row) for row in value if isinstance(row, list)]
        return [f"{label}:", *rows]
    if param.param_type in ("integer_list", "number_list") and isinstance(value, list):
        return [f"{label}: " + ", ".join(_format_number(v) for v in value)]
    if param.param_type == "boolean":
        return [f"{label}: {'sí' if value else 'no'}"]
    return [f"{label}: {_format_number(value)}"]


def render_text(
    spec: TemplateSpec, params: Mapping[str, object], shown: Draw, prompt_text: str
) -> str:
    """The plain view every result carries (spec 15.7): the task, the prompt parameters with
    their labels, the drawn cue or cues, numbered options and the answer format. A template
    that needs the interactive view shows no cue."""
    lines = [prompt_text]
    display = spec.purpose == "display"
    for param in spec.by_role("prompt"):
        lines += _render_value(
            param,
            params.get(param.name),
            display=display,
            numbered=param.name == spec.options_param,
        )
    if spec.needs_view and not display:
        lines.append(NEEDS_VIEW_TEXT)
    else:
        drawn = _drawn(spec, params, shown)
        if isinstance(drawn, Mapping):
            if "cue" in drawn:
                lines.append(f"Pista: {drawn['cue']}")
            if "options" in drawn and spec.pool_draw != "none":  # authored: listed above
                lines += [f"{i}. {o}" for i, o in enumerate(drawn["options"], start=1)]
            if "cues" in drawn:
                lines += [f"- {c}" for c in drawn["cues"]]
    if spec.grading_kind in _ANSWER_FORMAT and not (spec.needs_view and not display):
        lines.append(_ANSWER_FORMAT[spec.grading_kind])
    return "\n".join(line for line in lines if line)


# --- answers and grading ------------------------------------------------------------------------


def normalise_answer(text: str, text_match: TextMatch) -> str:
    """Spec 15.8: NFKC, trim, one space; then, except for exact_case, casefold, accents folded
    (not for keep_accents) and surrounding punctuation of the closed set removed."""
    value = _SPACES.sub(" ", unicodedata.normalize("NFKC", text)).strip()
    if text_match == "exact_case":
        return value
    value = value.casefold()
    if text_match == "fold_all":
        decomposed = unicodedata.normalize("NFKD", value)
        value = unicodedata.normalize(
            "NFC", "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
        )
    return value.strip(EDGE_PUNCTUATION + " ").strip()


def parse_number(text: str) -> Decimal | None:
    """A number by the closed rule of spec 15.8: optional sign, digits, at most one decimal
    separator (`.` or `,`), or a fraction `p/q` with q != 0. No thousands separators: a comma
    followed by exactly three digits after a non-zero integer part (`1,000`, `12,500`) is
    ambiguous and refused; a dot is always the decimal point (`2.718`)."""
    value = text.strip()
    if len(value) > ANSWER_NUMBER_MAX:
        return None
    fraction = _FRACTION.fullmatch(value)
    if fraction is not None:
        denominator = int(fraction.group(2))
        if denominator == 0:
            return None
        return Decimal(int(fraction.group(1))) / Decimal(denominator)
    if not _DECIMAL.fullmatch(value) or _AMBIGUOUS_THOUSANDS.fullmatch(value):
        return None
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:  # pragma: no cover - the pattern admits only decimal forms
        return None


def _text_list(data: Mapping[str, object], limit: int) -> tuple[str, ...] | None:
    values = data.get("values")
    if not isinstance(values, list | tuple) or len(values) > limit:
        return None
    if not all(isinstance(v, str) and len(v) <= ANSWER_VALUE_MAX for v in values):
        return None
    return tuple(str(v) for v in values)


def _optional_int(data: Mapping[str, object], key: str, low: int, high: int) -> int | object | None:
    value = data.get(key)
    if value is None:
        return None
    if not _is_int(value) or not low <= value <= high:
        return _BAD
    return value


def parse_answer(
    grading_kind: GradingKind, data: Mapping[str, object]
) -> Answer | tuple[FieldError, ...]:
    """The answer union of spec 15.8 (`answer_kind` is the discriminator). A shape that does
    not match the grading kind is `invalid` on `answer`; an unreadable number on `answer.value`."""
    wrong = (FieldError("answer", "invalid"),)
    expected = ANSWER_KIND_FOR.get(grading_kind)
    if expected is None or data.get("answer_kind") != expected:
        return wrong
    if expected == "choice_index":
        index = data.get("option_index")
        return ChoiceAnswer(index) if _is_int(index) and index >= 0 else wrong
    if expected == "value":
        value = data.get("value")
        ok = isinstance(value, str) and len(value) <= ANSWER_VALUE_MAX
        return ValueAnswer(str(value)) if ok else wrong
    if expected in ("values", "sequence"):
        limit = ANSWER_VALUES_MAX if expected == "values" else ANSWER_SEQUENCE_MAX
        values = _text_list(data, limit)
        if values is None:
            return wrong
        return ValuesAnswer(values) if expected == "values" else SequenceAnswer(values)
    if expected == "number":
        raw = data.get("value")
        number = parse_number(raw) if isinstance(raw, str) else None
        return (
            NumberAnswer(number) if number is not None else (FieldError("answer.value", "invalid"),)
        )
    rating = data.get("rating")
    tempo = _optional_int(data, "tempo_bpm", TEMPO_MIN, TEMPO_MAX)
    minutes = _optional_int(data, "minutes", MINUTES_MIN, MINUTES_MAX)
    if not _is_int(rating) or not RATING_MIN <= rating <= RATING_MAX or _BAD in (tempo, minutes):
        return wrong
    return SelfReportAnswer(
        rating,
        tempo if isinstance(tempo, int) else None,
        minutes if isinstance(minutes, int) else None,
    )


def _percent(part: int, whole: int) -> int:
    if whole == 0:
        return 0
    return int((Decimal(100 * part) / Decimal(whole)).quantize(Decimal(1), ROUND_HALF_UP))


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    lengths = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a, start=1):
        for j, y in enumerate(b, start=1):
            lengths[i][j] = (
                lengths[i - 1][j - 1] + 1 if x == y else max(lengths[i - 1][j], lengths[i][j - 1])
            )
    return lengths[len(a)][len(b)]


def _reveal(spec: TemplateSpec, params: Mapping[str, object], shown: Draw) -> dict[str, object]:
    reveal: dict[str, object] = {p.name: params.get(p.name) for p in spec.by_role("reveal")}
    entries = _pool_entries(spec, params)
    explains = [entries[i]["explain"] for i in shown.entries if entries[i].get("explain")]
    if explains:
        reveal["explain"] = explains
    return reveal


def _sequence_key(spec: TemplateSpec, params: Mapping[str, object], shown: Draw) -> list[str]:
    if spec.pool_draw == "ordered":
        entries = _pool_entries(spec, params)
        picked = sorted(shown.entries, key=lambda i: entries[i]["order"])
        return [str(entries[i]["cue"]) for i in picked]
    key = params.get(spec.by_role("answer_key")[0].name)
    return [str(v) for v in key] if isinstance(key, list) else []


def _text_key(spec: TemplateSpec, params: Mapping[str, object], shown: Draw) -> list[str]:
    if spec.pool_draw == "one":
        entry = _pool_entries(spec, params)[shown.entries[0]]
        return [str(entry["answer"]), *(str(a) for a in entry["accept"])]
    key = params.get(spec.by_role("answer_key")[0].name)
    return [str(v) for v in key] if isinstance(key, list) else [str(key)]


def grade(
    spec: TemplateSpec, params: Mapping[str, object], shown: Draw, answer: Answer
) -> Graded | tuple[FieldError, ...]:
    """Grade an answer against the stored instance and the showing's draw (spec 15.8)."""
    wrong = (FieldError("answer", "invalid"),)
    reveal = _reveal(spec, params, shown)
    text_match: TextMatch = spec.text_match or "fold_all"
    if spec.grading_kind == "choice_index" and isinstance(answer, ChoiceAnswer):
        if spec.pool_draw == "one_of_choices":
            count, key = len(shown.order), shown.order.index(0) if shown.order else -1
        else:
            options = params.get(spec.options_param or "")
            count = len(options) if isinstance(options, list) else 0
            stored = params.get(spec.by_role("answer_key")[0].name)
            key = stored if _is_int(stored) else -1
        if answer.option_index >= count:
            return wrong
        correct = answer.option_index == key
        return Graded(correct, 100 if correct else 0, reveal)
    if spec.grading_kind == "exact_text" and isinstance(answer, ValueAnswer):
        accepted = {normalise_answer(k, text_match) for k in _text_key(spec, params, shown)}
        correct = normalise_answer(answer.value, text_match) in accepted
        return Graded(correct, 100 if correct else 0, reveal)
    if spec.grading_kind == "exact_set" and isinstance(answer, ValuesAnswer):
        stored = params.get(spec.by_role("answer_key")[0].name)
        keys = (
            {normalise_answer(str(k), text_match) for k in stored}
            if isinstance(stored, list)
            else set()
        )
        given = {normalise_answer(v, text_match) for v in answer.values} - {""}
        return Graded(given == keys, _percent(len(given & keys), len(given | keys)), reveal)
    if spec.grading_kind == "exact_sequence" and isinstance(answer, SequenceAnswer):
        expected = [normalise_answer(k, text_match) for k in _sequence_key(spec, params, shown)]
        given_list = [normalise_answer(v, text_match) for v in answer.values]
        score = _percent(_lcs(given_list, expected), max(len(given_list), len(expected)))
        return Graded(given_list == expected, score, reveal)
    if spec.grading_kind == "numeric" and isinstance(answer, NumberAnswer):
        stored_key = _decimal(params.get(spec.by_role("answer_key")[0].name))
        tolerance = spec.numeric_tolerance or Decimal(0)
        correct = stored_key is not None and abs(answer.value - stored_key) <= tolerance
        return Graded(correct, 100 if correct else 0, reveal)
    if spec.grading_kind == "self_report" and isinstance(answer, SelfReportAnswer):
        return Graded(None, answer.rating * 25, reveal)
    return wrong
