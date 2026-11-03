"""Closed error codes for the use cases (spec section 8.2). Never carries learner text."""

from collections.abc import Iterable
from typing import Literal

from tutor.domain.errors import FieldError

ErrorCode = Literal[
    "onboarding_needed",
    "session_not_found",
    "session_closed",
    "rate_limited",
    "validation_failed",
    "payload_too_large",
    "not_found",
    "limit_exceeded",
    "practice_conflict",
]


class ServiceError(Exception):
    """A use-case failure. `fields` holds field paths for validation_failed, never values."""

    def __init__(self, code: ErrorCode, fields: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code: ErrorCode = code
        self.fields: tuple[str, ...] = fields


def field_error(code: ErrorCode, errors: Iterable[FieldError]) -> ServiceError:
    """The domain's field errors as one ServiceError: paths deduplicated, in order (ruling 13)."""
    return ServiceError(code, tuple(dict.fromkeys(error.field for error in errors)))
