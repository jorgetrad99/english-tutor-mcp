"""The unit suite shares compiled Jinja templates; it must never share them across environments
whose filters differ in a way the compiler reads."""

from typing import Any

import pytest
from jinja2 import Environment, TemplateAssertionError, pass_context

pytestmark = pytest.mark.unit

SOURCE = "{{ 'x'|shout }}"


def plain(value: str) -> str:
    return value.upper()


@pass_context
def contextual(ctx: Any, value: str) -> str:
    assert ctx is not None
    return value.upper() + "!"


def render(**filters: Any) -> str:
    env = Environment(autoescape=True)
    env.filters.update(filters)
    return env.from_string(SOURCE).render()


def test_pass_context_marker_is_part_of_the_cache_key() -> None:
    assert render(shout=plain) == "X"
    assert render(shout=contextual) == "X!"
    assert render(shout=plain) == "X"


def test_a_missing_filter_still_fails_after_another_environment_compiled_it() -> None:
    assert render(shout=plain) == "X"
    with pytest.raises(TemplateAssertionError):
        render()


def test_constant_folded_filters_with_different_behaviour_do_not_share_code() -> None:
    def build(fn: Any) -> str:
        env = Environment(autoescape=True)
        env.filters["s"] = fn
        return env.from_string("{{ 'X'|s }}").render()

    assert build(str.upper) == "X"
    assert build(lambda v: v.lower() + "?") == "x?"
    assert build(str.upper) == "X"


def make_suffix(suffix: str) -> Any:
    def add(value: str) -> str:
        return value + suffix

    return add


def test_closures_differing_only_in_captured_values_do_not_share_code() -> None:
    def build(fn: Any) -> str:
        env = Environment(autoescape=True)
        env.filters["s"] = fn
        return env.from_string("{{ 'X'|s }}").render()

    assert build(make_suffix("!")) == "X!"
    assert build(make_suffix("?")) == "X?"
    assert build(make_suffix("!")) == "X!"


def test_closures_with_mutable_captures_never_share_code() -> None:
    def build(box: list[str]) -> str:
        env = Environment(autoescape=True)
        env.filters["s"] = lambda v: v + box[0]
        return env.from_string("{{ 'X'|s }}").render()

    assert build(["1"]) == "X1"
    assert build(["2"]) == "X2"
