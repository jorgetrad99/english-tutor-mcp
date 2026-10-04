"""Evidence fidelity and grader spread (spec §8.4-8.5)."""

import statistics
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import pairwise

from tutor_spike.normalize import normalize

SCALE = {"B1": 0, "B1+": 1, "B2": 2, "B2+": 3, "C1": 4}
LEVELS = {value: name for name, value in SCALE.items()}


def user_messages(transcript: str) -> list[str]:
    turns: list[tuple[str, str]] = []
    for line in transcript.splitlines():
        if line.startswith(("U:", "A:")):
            turns.append((line[0], line[2:].strip()))
        elif line.strip() and turns:
            speaker, text = turns[-1]
            turns[-1] = (speaker, f"{text} {line.strip()}")
    return [text for speaker, text in turns if speaker == "U"]


@dataclass(frozen=True)
class Match:
    hit_ref: int
    n_ref: int
    hit_pred: int
    n_pred: int

    @property
    def recall(self) -> float | None:
        return self.hit_ref / self.n_ref if self.n_ref else None

    @property
    def precision(self) -> float | None:
        return self.hit_pred / self.n_pred if self.n_pred else None

    def __add__(self, other: "Match") -> "Match":
        return Match(
            self.hit_ref + other.hit_ref,
            self.n_ref + other.n_ref,
            self.hit_pred + other.hit_pred,
            self.n_pred + other.n_pred,
        )


def _turn_matches(payload_turn: str, transcript_turn: str, fuzzy: bool) -> bool:
    p, t = normalize(payload_turn), normalize(transcript_turn)
    if not p:
        return False
    if p in t:
        return True
    return fuzzy and SequenceMatcher(None, p, t).ratio() >= 0.9


def turns_fidelity(
    payload_turns: list[str], transcript_turns: list[str], fuzzy: bool = False
) -> Match:
    return Match(
        hit_ref=sum(
            any(_turn_matches(p, t, fuzzy) for p in payload_turns) for t in transcript_turns
        ),
        n_ref=len(transcript_turns),
        hit_pred=sum(
            any(_turn_matches(p, t, fuzzy) for t in transcript_turns) for p in payload_turns
        ),
        n_pred=len(payload_turns),
    )


def _said_matches(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na and nb) and (na in nb or nb in na)


def errors_fidelity(payload_said: list[str], annotated_said: list[str]) -> Match:
    return Match(
        hit_ref=sum(any(_said_matches(p, a) for p in payload_said) for a in annotated_said),
        n_ref=len(annotated_said),
        hit_pred=sum(any(_said_matches(p, a) for a in annotated_said) for p in payload_said),
        n_pred=len(payload_said),
    )


@dataclass(frozen=True)
class CefrStats:
    n: int
    mode: str
    low: str
    high: str
    sd_half_steps: float
    within_one_of_mode: float
    full_level_jumps: int


def cefr_stats(levels: list[str]) -> CefrStats | None:
    """Levels in chronological order; ties for the mode go to the lower level."""
    if not levels:
        return None
    values = [SCALE[level] for level in levels]
    counts = Counter(values)
    mode = min(counts, key=lambda v: (-counts[v], v))
    return CefrStats(
        n=len(values),
        mode=LEVELS[mode],
        low=LEVELS[min(values)],
        high=LEVELS[max(values)],
        sd_half_steps=statistics.pstdev(values),
        within_one_of_mode=sum(abs(v - mode) <= 1 for v in values) / len(values),
        full_level_jumps=sum(abs(b - a) >= 2 for a, b in pairwise(values)),
    )
