"""Tool errors (spec 8.2): JSON {code, fields, response_rules}.

Never echoes input values, unknown field names, exception text, tokens or emails: an error is
mapped by its code or class only, and field paths are reduced to a safe alphabet.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import (
    AuthorizationError,
    DisabledError,
    NotFoundError,
    ToolError,
    ValidationError,
)
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST
from pydantic import ValidationError as PydanticValidationError

from tutor.domain.templates import ANSWER_KINDS
from tutor.mcp.rules import McpErrorCode, error_rules
from tutor.services.errors import ServiceError

MAX_FIELDS = 20
MAX_PART_CHARS = 40
MAX_PATH_CHARS = 80
UNKNOWN_FIELD = "unknown_field"
# A path segment keeps names and list indexes: `results[3]` stays `results[3]`.
_TOKEN = re.compile(r"[A-Za-z0-9_]+|\[\d{1,4}\]")
_UNKNOWN_FIELD_TYPES = frozenset({"extra_forbidden", "unexpected_keyword_argument"})
# Lists whose max_length is a per-call count cap of the subjects payload (plan rulings 10, 12).
COUNT_CAP_TOOLS = frozenset({"save_notes"})
COUNT_CAP_FIELDS = frozenset({"notes", "concepts", "links", "instances"})
# Fields that are a union tagged by answer_kind (record_result.answer).
TAGGED_UNIONS = frozenset({"answer"})


class TutorToolError(ToolError):
    """A ToolError built by this module; the middleware lets it through untouched."""


def _part(part: str) -> str:
    tokens = _TOKEN.findall(part)
    return "".join(t if t.startswith("[") else t[:MAX_PART_CHARS] for t in tokens)


def _cap(path: str) -> str:
    if len(path) <= MAX_PATH_CHARS:
        return path
    cut = path[:MAX_PATH_CHARS]
    if cut.rfind("[") > cut.rfind("]"):  # do not leave a half index
        cut = cut[: cut.rfind("[")]
    return cut.rstrip(".")


def field_path(loc: Sequence[int | str], error_type: str) -> str:
    """One format for the model: `items[0].kind`. The name of an unknown field is the
    caller's text, so it is replaced. A tagged union's tag (`answer.choice_index.option_number`)
    is dropped, so the path is the field the caller sent (`answer.option_number`)."""
    loc = [
        part
        for index, part in enumerate(loc)
        if not (index > 0 and loc[index - 1] in TAGGED_UNIONS and part in ANSWER_KINDS)
    ]
    segments: list[str] = []
    for index, part in enumerate(loc):
        if isinstance(part, int):
            if segments:
                segments[-1] += f"[{part}]"
            else:
                segments.append(f"[{part}]")
            continue
        last = index == len(loc) - 1
        segments.append(
            UNKNOWN_FIELD if last and error_type in _UNKNOWN_FIELD_TYPES else _part(part)
        )
    return _cap(".".join(s for s in segments if s)) or UNKNOWN_FIELD


def error_text(code: McpErrorCode, fields: Sequence[str] = (), tool: str | None = None) -> str:
    safe = [p for p in (_cap(".".join(_part(x) for x in f.split("."))) for f in fields) if p]
    kept = safe[:MAX_FIELDS]
    return json.dumps(
        {"code": code, "fields": kept, "response_rules": error_rules(code, tool, kept)}
    )


def tool_error(exc: ServiceError, tool: str | None = None) -> ToolError:
    """An expected use-case error: FastMCP logs its fixed "Error calling tool" line at INFO."""
    return TutorToolError(error_text(exc.code, exc.fields, tool), log_level=logging.INFO)


def validation_fields(exc: BaseException) -> tuple[str, ...]:
    return validation_failure(exc)[1]


def _is_count_cap(tool: str | None, loc: tuple[int | str, ...], error_type: str) -> bool:
    return (
        error_type == "too_long"  # a list over max_length; strings are "string_too_long"
        and tool in COUNT_CAP_TOOLS
        and bool(loc)
        and loc[-1] in COUNT_CAP_FIELDS
    )


def validation_failure(
    exc: BaseException, tool: str | None = None
) -> tuple[McpErrorCode, tuple[str, ...]]:
    """The code and field paths of an argument-schema failure.

    A count cap of the subjects payload (too many notes, concepts, links or instances) is
    limit_exceeded with only those paths, as the domain reports it (plan ruling 12);
    every other failure is validation_failed.
    """
    cause = exc.__cause__
    if not isinstance(cause, PydanticValidationError):
        return "validation_failed", ()
    paths: list[str] = []
    caps: list[str] = []
    for error in cause.errors(include_url=False, include_context=False, include_input=False):
        loc = tuple(error["loc"])
        path = field_path(loc, error["type"])
        bucket = caps if _is_count_cap(tool, loc, error["type"]) else paths
        if path not in bucket:
            bucket.append(path)
    if caps:
        return "limit_exceeded", tuple(caps)
    return "validation_failed", tuple(paths)


def protocol_error(exc: Exception) -> MCPError:
    """A fixed JSON-RPC error for a framework failure: never the tool name or the text.

    Unknown or disabled tools, failed authorization and deliberate MCPErrors (for example a
    missing client capability) stay protocol errors instead of becoming tool results.
    """
    if isinstance(exc, NotFoundError | DisabledError):
        return MCPError(code=INVALID_PARAMS, message="Unknown tool")
    if isinstance(exc, AuthorizationError):
        return MCPError(code=INVALID_REQUEST, message="Not authorized")
    code = exc.error.code if isinstance(exc, MCPError) else INVALID_REQUEST
    return MCPError(code=code, message="Request failed")


class ValidationErrorMiddleware(Middleware):
    """Every tool failure leaves as {code, fields, response_rules}; no exception text gets out.

    Argument-schema failures become validation_failed with field paths (pydantic's own text
    holds the values). A ServiceError, raw or as the cause of FastMCP's wrapping ToolError,
    maps by its code. Framework failures (unknown tool, authorization, MCPError) stay
    protocol errors with fixed text. Anything else (a foreign ToolError, a pydantic error
    raised inside a tool body, any Exception) becomes internal_error. The original is never
    chained and nothing is logged here.
    """

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        tool = getattr(context.message, "name", None)
        try:
            return await call_next(context)
        except TutorToolError:
            raise
        except (NotFoundError, DisabledError, AuthorizationError, MCPError) as exc:
            raise protocol_error(exc) from None
        except ValidationError as exc:
            code, fields = validation_failure(exc, tool)
            raise TutorToolError(error_text(code, fields, tool)) from None
        except ServiceError as exc:
            raise tool_error(exc, tool) from None
        except Exception as exc:
            if isinstance(exc.__cause__, ServiceError):
                raise tool_error(exc.__cause__, tool) from None
            raise TutorToolError(error_text("internal_error", (), tool)) from None
