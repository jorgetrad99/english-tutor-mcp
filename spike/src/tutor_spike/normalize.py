"""Text normalization shared by the server's said rule and the offline analysis (spec §8)."""

import re
import unicodedata

_PUNCTUATION = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Unicode NFKC, lowercase, punctuation stripped, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _PUNCTUATION.sub("", text)
    return _SPACES.sub(" ", text).strip()


def said_in_turns(said: str, user_turns: list[str]) -> bool:
    """True when the normalized `said` is a non-empty substring of one normalized user turn."""
    needle = normalize(said)
    return bool(needle) and any(needle in normalize(turn) for turn in user_turns)
