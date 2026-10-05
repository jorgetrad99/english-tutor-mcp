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
