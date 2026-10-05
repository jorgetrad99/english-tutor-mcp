"""Recall and precision of the end_session evidence against a transcript and an annotation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from tutor.domain.text import normalize


def user_messages(transcript: str) -> list[str]:
    """Learner turns of a "U:" / "A:" transcript; continuation lines join the previous turn."""
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

    def __add__(self, other: Match) -> Match:
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
    payload_turns: Sequence[str], transcript_turns: Sequence[str], fuzzy: bool = False
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


def errors_fidelity(payload_said: Sequence[str], annotated_said: Sequence[str]) -> Match:
    return Match(
        hit_ref=sum(any(_said_matches(p, a) for p in payload_said) for a in annotated_said),
        n_ref=len(annotated_said),
        hit_pred=sum(any(_said_matches(p, a) for a in annotated_said) for p in payload_said),
        n_pred=len(payload_said),
    )


def chunks_fidelity(payload_ids: Sequence[str], annotated_ids: Sequence[str]) -> Match:
    """v0 chunks have ids (it-07-c3), so they compare exactly."""
    payload, annotated = set(payload_ids), set(annotated_ids)
    return Match(
        hit_ref=len(annotated & payload),
        n_ref=len(annotated),
        hit_pred=len(payload & annotated),
        n_pred=len(payload),
    )
