from collections import Counter
from importlib.resources import files

import pytest

from tutor.content import TRACK_IT_V0, load_track, parse_track_text
from tutor.domain.track import TrackError

pytestmark = pytest.mark.unit


def test_track_file_is_package_data() -> None:
    assert files("tutor.content").joinpath(TRACK_IT_V0).is_file()


def test_load_track_returns_the_24_item_it_track() -> None:
    items = load_track()
    assert [item.id for item in items] == [f"it-{n:02d}" for n in range(1, 25)]
    assert [item.order_no for item in items] == list(range(1, 25))
    assert Counter(item.cefr for item in items) == {"B1": 12, "B2": 12}
    assert {item.domain for item in items} == {"it"}
    assert sum(len(item.chunks) for item in items) == 120


def test_load_track_first_item_content() -> None:
    first = load_track()[0]
    assert first.interaction_type == "explain"
    assert first.use_cases == ("standup",)
    assert first.chunks[2].id == "it-01-c3"
    assert first.chunks[2].text == "I'm blocked on"


def test_load_track_is_cached() -> None:
    assert load_track() is load_track()


def test_parse_track_text_rejects_non_mapping_yaml() -> None:
    with pytest.raises(TrackError) as caught:
        parse_track_text("- just\n- a list\n")
    assert caught.value.problems == ("track file must be a mapping",)


def test_parse_track_text_rejects_structural_problems() -> None:
    with pytest.raises(TrackError) as caught:
        parse_track_text("domain: it\nitems:\n  - not-a-mapping\n")
    assert caught.value.problems == ("items[0]: must be a mapping",)


def test_parse_track_text_enforces_coverage_rules() -> None:
    text = files("tutor.content").joinpath(TRACK_IT_V0).read_text(encoding="utf-8")
    broken = text.replace("interaction_type: small_talk", "interaction_type: explain", 1)
    with pytest.raises(TrackError) as caught:
        parse_track_text(broken)
    assert "it-01 and it-02: consecutive items share interaction type explain" in (
        caught.value.problems
    )
