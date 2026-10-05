"""Starter track model, parser and coverage rules (spec 7.1)."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal, TypeGuard

from tutor.domain.profile import DOMAINS, USE_CASES, Domain, UseCase
from tutor.domain.text import normalize

InteractionType = Literal[
    "explain", "negotiate", "disagree", "ask_for_help", "give_feedback", "small_talk"
]
Skill = Literal["speaking", "writing"]
TrackLevel = Literal["B1", "B2"]

INTERACTION_TYPES: tuple[InteractionType, ...] = (
    "explain",
    "negotiate",
    "disagree",
    "ask_for_help",
    "give_feedback",
    "small_talk",
)
SKILLS: tuple[Skill, ...] = ("speaking", "writing")
TRACK_LEVELS: tuple[TrackLevel, ...] = ("B1", "B2")
CHUNKS_PER_ITEM = 5
MIN_ITEMS_PER_USE_CASE = 3
MIN_ITEMS_PER_INTERACTION = 3
MIN_WRITING_ITEMS = 4

_TOP_KEYS = frozenset({"domain", "version", "items"})
_TEXT_KEYS = (
    "can_do_en",
    "can_do_es",
    "character",
    "objective",
    "obstacle",
    "scenario_hint",
)
_ITEM_KEYS = frozenset(
    {"id", "order_no", "cefr", "skill", "interaction_type", "use_cases", "chunks", *_TEXT_KEYS}
)
_CHUNK_KEYS = frozenset({"id", "position", "text", "example"})


@dataclass(frozen=True, slots=True)
class TrackChunk:
    id: str
    position: int
    text: str
    example: str


@dataclass(frozen=True, slots=True)
class TrackItem:
    id: str
    domain: Domain
    order_no: int
    cefr: TrackLevel
    skill: Skill
    interaction_type: InteractionType
    use_cases: tuple[UseCase, ...]
    can_do_en: str
    can_do_es: str
    character: str
    objective: str
    obstacle: str
    scenario_hint: str
    chunks: tuple[TrackChunk, ...]


class TrackError(ValueError):
    """The track file is malformed or breaks a coverage rule."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__("; ".join(self.problems))


def _is_mapping(value: object) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping) and all(isinstance(k, str) for k in value)


def _is_domain(value: object) -> TypeGuard[Domain]:
    return value in DOMAINS


def _is_use_case(value: object) -> TypeGuard[UseCase]:
    return value in USE_CASES


def _is_interaction(value: object) -> TypeGuard[InteractionType]:
    return value in INTERACTION_TYPES


def _is_skill(value: object) -> TypeGuard[Skill]:
    return value in SKILLS


def _is_track_level(value: object) -> TypeGuard[TrackLevel]:
    return value in TRACK_LEVELS


class _Reader:
    """Collects problems while reading one mapping; returns safe defaults on error."""

    def __init__(self, data: Mapping[str, Any], path: str, problems: list[str]) -> None:
        self.data = data
        self.path = path
        self.problems = problems

    def keys(self, allowed: frozenset[str]) -> None:
        for key in sorted(set(self.data) - allowed):
            self.problems.append(f"{self.path}.{key}: unknown field")
        for key in sorted(allowed - set(self.data)):
            self.problems.append(f"{self.path}.{key}: missing")

    def text(self, key: str) -> str:
        value = self.data.get(key)
        if key in self.data and (not isinstance(value, str) or not value.strip()):
            self.problems.append(f"{self.path}.{key}: must be a non-empty string")
        return value.strip() if isinstance(value, str) else ""

    def integer(self, key: str) -> int:
        value = self.data.get(key)
        if key in self.data and (isinstance(value, bool) or not isinstance(value, int)):
            self.problems.append(f"{self.path}.{key}: must be an integer")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def bad_choice(self, key: str) -> None:
        if key in self.data:
            self.problems.append(f"{self.path}.{key}: invalid value {self.data[key]!r}")


def _chunk(raw: object, path: str, problems: list[str]) -> TrackChunk | None:
    if not _is_mapping(raw):
        problems.append(f"{path}: must be a mapping")
        return None
    reader = _Reader(raw, path, problems)
    reader.keys(_CHUNK_KEYS)
    return TrackChunk(
        id=reader.text("id"),
        position=reader.integer("position"),
        text=reader.text("text"),
        example=reader.text("example"),
    )


def _item(raw: object, domain: Domain, path: str, problems: list[str]) -> TrackItem | None:
    if not _is_mapping(raw):
        problems.append(f"{path}: must be a mapping")
        return None
    reader = _Reader(raw, path, problems)
    reader.keys(_ITEM_KEYS)
    cefr: TrackLevel = "B1"
    if _is_track_level(raw.get("cefr")):
        cefr = raw["cefr"]
    else:
        reader.bad_choice("cefr")
    skill: Skill = "speaking"
    if _is_skill(raw.get("skill")):
        skill = raw["skill"]
    else:
        reader.bad_choice("skill")
    interaction: InteractionType = "explain"
    if _is_interaction(raw.get("interaction_type")):
        interaction = raw["interaction_type"]
    else:
        reader.bad_choice("interaction_type")
    raw_use_cases = raw.get("use_cases", [])
    use_cases: list[UseCase] = []
    if not isinstance(raw_use_cases, list) or ("use_cases" in raw and not raw_use_cases):
        problems.append(f"{path}.use_cases: must be a non-empty list")
    else:
        for value in raw_use_cases:
            if _is_use_case(value):
                use_cases.append(value)
            else:
                problems.append(f"{path}.use_cases: invalid value {value!r}")
    raw_chunks = raw.get("chunks", [])
    chunks: list[TrackChunk] = []
    if not isinstance(raw_chunks, list):
        problems.append(f"{path}.chunks: must be a list")
    else:
        for index, value in enumerate(raw_chunks):
            chunk = _chunk(value, f"{path}.chunks[{index}]", problems)
            if chunk is not None:
                chunks.append(chunk)
    texts = {key: reader.text(key) for key in _TEXT_KEYS}
    return TrackItem(
        id=reader.text("id"),
        domain=domain,
        order_no=reader.integer("order_no"),
        cefr=cefr,
        skill=skill,
        interaction_type=interaction,
        use_cases=tuple(use_cases),
        can_do_en=texts["can_do_en"],
        can_do_es=texts["can_do_es"],
        character=texts["character"],
        objective=texts["objective"],
        obstacle=texts["obstacle"],
        scenario_hint=texts["scenario_hint"],
        chunks=tuple(chunks),
    )


def parse_track(data: Mapping[str, Any]) -> tuple[TrackItem, ...]:
    """Parse the track YAML structure into items in file order; raise TrackError on any problem."""
    problems: list[str] = []
    for key in sorted(set(data) - _TOP_KEYS):
        problems.append(f"track.{key}: unknown field")
    domain: Domain = "it"
    if _is_domain(data.get("domain")):
        domain = data["domain"]
    else:
        problems.append(f"track.domain: invalid value {data.get('domain')!r}")
    raw_items = data.get("items")
    items: list[TrackItem] = []
    if not isinstance(raw_items, list) or not raw_items:
        problems.append("track.items: must be a non-empty list")
    else:
        for index, raw in enumerate(raw_items):
            item = _item(raw, domain, f"items[{index}]", problems)
            if item is not None:
                items.append(item)
    if problems:
        raise TrackError(problems)
    return tuple(items)


def _chunk_problems(item: TrackItem) -> list[str]:
    problems: list[str] = []
    positions = [chunk.position for chunk in item.chunks]
    if positions != list(range(1, CHUNKS_PER_ITEM + 1)):
        problems.append(f"{item.id}: chunk positions must be 1..{CHUNKS_PER_ITEM} in order")
    for chunk in item.chunks:
        if chunk.id != f"{item.id}-c{chunk.position}":
            problems.append(f"{chunk.id}: id must be {item.id}-c{chunk.position}")
        text = normalize(chunk.text)
        if len(text.split()) < 2:
            problems.append(f"{chunk.id}: chunk text must have at least two words")
        if text not in normalize(chunk.example):
            problems.append(f"{chunk.id}: chunk text does not appear in its example")
    return problems


def track_problems(items: Sequence[TrackItem]) -> tuple[str, ...]:
    """Every coverage rule of spec 7.1 that the items break; empty when the track is valid."""
    problems: list[str] = []
    for item_id, count in sorted(Counter(item.id for item in items).items()):
        if count > 1:
            problems.append(f"{item_id}: duplicate item id")
    if sorted(item.order_no for item in items) != list(range(1, len(items) + 1)):
        problems.append(f"order_no must be contiguous from 1 to {len(items)}")
    for use_case in USE_CASES:
        count = sum(use_case in item.use_cases for item in items)
        if count < MIN_ITEMS_PER_USE_CASE:
            problems.append(f"use case {use_case}: {count} items, needs {MIN_ITEMS_PER_USE_CASE}")
    for interaction in INTERACTION_TYPES:
        count = sum(item.interaction_type == interaction for item in items)
        if count < MIN_ITEMS_PER_INTERACTION:
            problems.append(
                f"interaction type {interaction}: {count} items, needs {MIN_ITEMS_PER_INTERACTION}"
            )
    writing = sum(item.skill == "writing" for item in items)
    if writing < MIN_WRITING_ITEMS:
        problems.append(f"writing items: {writing}, needs {MIN_WRITING_ITEMS}")
    for item in items:
        problems.extend(_chunk_problems(item))
    chunk_ids = Counter(chunk.id for item in items for chunk in item.chunks)
    for chunk_id, count in sorted(chunk_ids.items()):
        if count > 1:
            problems.append(f"{chunk_id}: duplicate chunk id")
    chunk_texts = Counter(normalize(chunk.text) for item in items for chunk in item.chunks)
    for text, count in sorted(chunk_texts.items()):
        if count > 1:
            problems.append(f"chunk text {text!r} appears {count} times")
    ordered = sorted(items, key=lambda item: item.order_no)
    for previous, current in pairwise(ordered):
        if previous.interaction_type == current.interaction_type:
            problems.append(
                f"{previous.id} and {current.id}: consecutive items share "
                f"interaction type {current.interaction_type}"
            )
    return tuple(problems)
