"""Field errors the domain returns instead of raising (plan ruling 13).

A validator returns its value or a tuple of `FieldError`; the service turns the tuple into a
`ServiceError` with the field paths. `field` is a path such as `notes[2].quiz_items[0].options`,
never a value.
"""

from dataclasses import dataclass
from typing import Literal

FieldErrorCode = Literal[
    "required",
    "invalid_choice",
    "too_long",
    "too_many",
    "out_of_range",
    "invalid",
]


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    code: FieldErrorCode
