"""Per-call state, the call log and the library log guards (spec section 13).

The call log is one JSON line per tool call: `event`, `user_hash` (first 12 hex characters of
SHA-256 of the user id, null before the user is known), `tool` (a tool name or "unknown"),
`outcome` ("ok", an error code, "unauthenticated", "protocol_error", "cancelled" or
"internal_error") and `latency_ms`. Only when the outcome is internal_error it adds
`error_class`, the class name of the unexpected exception (null if unknown). Never arguments,
tokens, emails, exception text or learner text. FastMCP and the MCP
SDK log tool failures with tracebacks, call arguments at DEBUG and, in the OAuth proxy, a
whole authorization code; `guard_library_logs` keeps all of that out of the logs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST

from tutor.mcp.instructions import TOOL_DESCRIPTIONS

TOOL_NAMES = frozenset(TOOL_DESCRIPTIONS)
REDACTED = "[redacted]"
# Library loggers whose records keep their fixed template but lose their arguments and
# tracebacks (a logger's filter applies to the records created on that exact logger).
SCRUBBED_LOGGERS = (
    "fastmcp.server.server",  # tool failure tracebacks
    "fastmcp.server.mixins.mcp_operations",  # tools/call arguments at DEBUG
    "fastmcp.server.low_level",
    "fastmcp.tools.base",
    "fastmcp.tools.function_tool",
    "mcp.server.runner",  # handler tracebacks
    "mcp.shared.jsonrpc_dispatcher",
    "mcp.server.auth.handlers.authorize",
    "mcp.server.auth.middleware.bearer_auth",
    "uvicorn.error",  # "Exception in ASGI application" with the traceback of an OAuth route
)
# The OAuth proxy logs authorization codes and token fragments (a replayed code at ERROR),
# often in f-strings no filter can clean, so its whole subtree is silenced.
SILENCED_LOGGERS = ("fastmcp.server.auth",)

_calls = logging.getLogger("tutor.mcp.calls")


class Unauthenticated(MCPError):
    """No usable access token: a fixed protocol error, logged as outcome unauthenticated."""

    def __init__(self) -> None:
        super().__init__(code=INVALID_REQUEST, message="Not authorized")


@dataclass
class CallState:
    """Mutable on purpose: the identity middleware fills it, the tool thread reads it.

    `error_class` is the class name of an unexpected exception, never its text.
    """

    user_id: UUID | None = None
    error_class: str | None = None


CALL_STATE: ContextVar[CallState | None] = ContextVar("tutor_mcp_call", default=None)


def current_call() -> CallState | None:
    return CALL_STATE.get()


def user_hash(user_id: UUID) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:12]


def outcome_of(exc: BaseException) -> str:
    """The error code of a ToolError built by tutor.mcp.errors, else a fixed label."""
    if isinstance(exc, PermissionError | Unauthenticated):
        return "unauthenticated"
    if isinstance(exc, MCPError):
        return "protocol_error"
    if not isinstance(exc, ToolError):
        return "internal_error"
    try:
        code = json.loads(str(exc)).get("code")
    except (ValueError, AttributeError):
        return "tool_error"
    return code if isinstance(code, str) else "tool_error"


def note_failure(exc: BaseException) -> None:
    """Records an unexpected exception's class name (never its text) for the call log."""
    state = current_call()
    if state is not None:
        state.error_class = type(exc).__name__


class ScrubFilter(logging.Filter):
    """Keeps a record's fixed template; drops its arguments, traceback and stack."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, Mapping):
            record.msg = REDACTED
            record.args = None
        elif record.args:
            # Numbers cannot carry learner text and %d/%f templates need them; redact the rest.
            record.args = tuple(a if isinstance(a, int | float) else REDACTED for a in record.args)
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def guard_library_logs() -> None:
    """Idempotent; called by build_mcp so every server process has the guards."""
    for name in SCRUBBED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, ScrubFilter) for f in logger.filters):
            logger.addFilter(ScrubFilter())
    for name in SILENCED_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)


def _emit_json(record: dict[str, Any]) -> None:
    _calls.info(json.dumps(record))


class CallLogMiddleware(Middleware):
    """Outermost middleware: owns the CallState of each tools/call and logs it."""

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None] = _emit_json,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._emit = emit
        self._clock = clock

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        state = CallState()
        token = CALL_STATE.set(state)
        started = self._clock()
        outcome = "ok"
        try:
            return await call_next(context)
        except Exception as exc:
            outcome = outcome_of(exc)
            if outcome == "internal_error" and state.error_class is None:
                state.error_class = type(exc).__name__
            raise
        except BaseException:  # cancellation (the client went away) and interpreter exits
            outcome = "cancelled"
            raise
        finally:
            CALL_STATE.reset(token)
            name = getattr(context.message, "name", None)
            line: dict[str, Any] = {
                "event": "mcp_tool_call",
                "tool": name if name in TOOL_NAMES else "unknown",
                "user_hash": user_hash(state.user_id) if state.user_id else None,
                "outcome": outcome,
                "latency_ms": round((self._clock() - started) * 1000, 1),
            }
            if outcome == "internal_error":
                line["error_class"] = state.error_class
            self._emit(line)
