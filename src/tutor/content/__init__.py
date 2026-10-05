"""Seed content shipped as package data (spec 7.1). Reads files, so it lives outside the domain."""

from collections.abc import Mapping
from functools import cache
from importlib.resources import files

import yaml

from tutor.domain.track import TrackError, TrackItem, parse_track, track_problems

TRACK_IT_V0 = "track_it_v0.yaml"


def parse_track_text(text: str) -> tuple[TrackItem, ...]:
    """Parse track YAML text and enforce the coverage rules; raise TrackError on any problem."""
    data = yaml.safe_load(text)
    if not isinstance(data, Mapping):
        raise TrackError(("track file must be a mapping",))
    items = parse_track(data)
    problems = track_problems(items)
    if problems:
        raise TrackError(problems)
    return items


@cache
def load_track() -> tuple[TrackItem, ...]:
    """The IT starter track, parsed and checked against the coverage rules."""
    return parse_track_text(files(__name__).joinpath(TRACK_IT_V0).read_text(encoding="utf-8"))
