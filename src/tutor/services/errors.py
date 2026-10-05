"""Closed error codes for the use cases (spec section 8.2). Never carries learner text."""

from typing import Literal

ErrorCode = Literal[
    "onboarding_needed",
    "session_not_found",
    "session_closed",
    "rate_limited",
    "validation_failed",
    "payload_too_large",
]


class ServiceError(Exception):
    """A use-case failure. `fields` holds field paths for validation_failed, never values."""

    def __init__(self, code: ErrorCode, fields: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code: ErrorCode = code
        self.fields: tuple[str, ...] = fields
