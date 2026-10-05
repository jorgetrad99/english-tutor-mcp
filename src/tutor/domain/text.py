"""Text normalization and word counts shared by glossary, evidence and metrics (spec 10.1, 11.3)."""

import re
import unicodedata
from collections.abc import Sequence

_QUOTES = str.maketrans(
    {
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{MODIFIER LETTER APOSTROPHE}": "'",
        "\N{GRAVE ACCENT}": "'",
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
    }
)
# Everything except word characters, whitespace, apostrophes and hyphens is removed.
_NOT_KEPT = re.compile(r"[^\w\s'\-]")
# An apostrophe or hyphen survives only between two word characters ("don't", "follow-up").
_LOOSE_MARK = re.compile(r"(?<!\w)['\-]|['\-](?!\w)")
_SPACES = re.compile(r"\s+")
_WORD = re.compile("[A-Za-z0-9]+(?:['\N{RIGHT SINGLE QUOTATION MARK}][A-Za-z]+)?")


def normalize(text: str) -> str:
    """NFKC, lowercase, straight quotes, keep inner ' and -, drop other punctuation, trim."""
    text = unicodedata.normalize("NFKC", text).lower().translate(_QUOTES)
    text = _NOT_KEPT.sub("", text)
    text = _LOOSE_MARK.sub("", text)
    return _SPACES.sub(" ", text).strip()


def count_words(text: str) -> int:
    """Number of word tokens as defined by the metrics rule (spec 11.3)."""
    return len(_WORD.findall(text))


def find_turn(needle: str, turns: Sequence[str], *, after: int = -1) -> int | None:
    """First turn index after `after` whose normalized text contains the normalized needle."""
    target = normalize(needle)
    if not target:
        return None
    for index in range(max(after + 1, 0), len(turns)):
        if target in normalize(turns[index]):
            return index
    return None
