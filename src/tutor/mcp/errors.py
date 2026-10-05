"""Tool errors (spec 8.2): JSON {code, fields, response_rules}.

Never echoes input values, unknown field names, exception text, tokens or emails: an error is
mapped by its code or class only, and field paths are reduced to a safe alphabet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import ValidationError as PydanticValidationError

from tutor.mcp.rules import error_rules
from tutor.services.errors import ErrorCode, ServiceError

MAX_FIELDS = 20
MAX_PART_CHARS = 40
UNKNOWN_FIELD = "unknown_field"
# A path segment keeps names and list indexes: `results[3]` stays `results[3]`.
_TOKEN = re.compile(r"[A-Za-z0-9_]+|\[\d{1,4}\]")
_UNKNOWN_FIELD_TYPES = frozenset({"extra_forbidden", "unexpected_keyword_argument"})


def _part(part: int | str) -> str:
    if isinstance(part, int):
        return str(part)
    tokens = _TOKEN.findall(part)
    return "".join(t if t.startswith("[") else t[:MAX_PART_CHARS] for t in tokens)


def field_path(loc: Sequence[int | str], error_type: str) -> str:
    """Dotted location; the name of an unknown field is the caller's text, so it is replaced."""
    parts = [_part(p) for p in loc]
    if error_type in _UNKNOWN_FIELD_TYPES and parts:
        parts[-1] = UNKNOWN_FIELD
    return ".".join(p for p in parts if p) or UNKNOWN_FIELD


def error_text(code: ErrorCode, fields: Sequence[str] = ()) -> str:
    safe = [p for p in (".".join(_part(x) for x in f.split(".")) for f in fields) if p]
    return json.dumps(
        {"code": code, "fields": safe[:MAX_FIELDS], "response_rules": error_rules(code)}
    )


def tool_error(exc: ServiceError) -> ToolError:
    return ToolError(error_text(exc.code, exc.fields))


def validation_fields(exc: BaseException) -> tuple[str, ...]:
    cause = exc.__cause__
    if not isinstance(cause, PydanticValidationError):
        return ()
    paths: list[str] = []
    for error in cause.errors(include_url=False, include_context=False, include_input=False):
        path = field_path(tuple(error["loc"]), error["type"])
        if path not in paths:
            paths.append(path)
    return tuple(paths)


class ValidationErrorMiddleware(Middleware):
    """Schema failures become validation_failed with field paths; pydantic's text holds values.

    A ServiceError raised inside a tool (FastMCP wraps it in a ToolError) is mapped by its
    code too. The original exception is not chained, so its text cannot reach a client or a log.
    """

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        try:
            return await call_next(context)
        except ValidationError as exc:
            raise ToolError(error_text("validation_failed", validation_fields(exc))) from None
        except ServiceError as exc:
            raise tool_error(exc) from None
        except ToolError as exc:
            if isinstance(exc.__cause__, ServiceError):
                raise tool_error(exc.__cause__) from None
            raise
