"""get_profile and end_session (spec §3.2). Tool lines carry the tester label."""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import Tool
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tutor_spike import contract as c
from tutor_spike.eventlog import JsonlLog
from tutor_spike.normalize import said_in_turns
from tutor_spike.sessions import SessionRegistry

SESSION_ID_LOG_CHARS = 64
_UNSAFE_LOC = re.compile(r"[^A-Za-z0-9_]")


def log_session_id(value: Any) -> str | None:
    """A session_id as written to log lines: strings only, at most 64 chars."""
    return value[:SESSION_ID_LOG_CHARS] if isinstance(value, str) else None


def _loc(loc: tuple[int | str, ...]) -> str:
    """Error location; key names reduced to identifier characters so input cannot ride in."""
    return ".".join(
        str(part) if isinstance(part, int) else _UNSAFE_LOC.sub("", part)[:40] for part in loc
    )


def retry_message(exc: ValidationError) -> str:
    """Location and error type of each problem, enum choices from the contract, retry rules.

    Never includes the received values: they are model or user text, not instructions.
    """
    cause = exc.__cause__
    problems: list[str] = []
    if isinstance(cause, PydanticValidationError):
        for error in cause.errors(include_url=False, include_context=False, include_input=False):
            loc = tuple(error["loc"])
            problem = f"{_loc(loc)}: {error['type']}"
            if loc and isinstance(loc[-1], str) and loc[-1] in c.ENUM_VALUES:
                problem += f" (allowed: {', '.join(c.ENUM_VALUES[loc[-1]])})"
            problems.append(problem)
    listed = "; ".join(problems) if problems else "arguments do not match the schema"
    return f"Invalid end_session arguments: {listed}. {c.RETRY_RULES}"


class RetryRulesMiddleware(Middleware):
    """Schema errors on end_session come back with the retry response_rules."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        try:
            return await call_next(context)
        except ValidationError as exc:
            if getattr(context.message, "name", None) != "end_session":
                raise
            raise ToolError(retry_message(exc)) from exc


class CallLogMiddleware(Middleware):
    """One kind:"call" line per tools/call, written before validation or the allowlist runs."""

    def __init__(
        self,
        log: JsonlLog,
        resolve_tester: Callable[[], str | None],
        now: Callable[[], datetime],
    ) -> None:
        self._log = log
        self._resolve_tester = resolve_tester
        self._now = now

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        arguments = getattr(context.message, "arguments", None) or {}
        mcp_session_id, rpc_id = _ids(context.fastmcp_context)
        self._log.write(
            {
                "kind": "call",
                "ts": self._now().isoformat(),
                "tool": getattr(context.message, "name", None),
                "tester": self._resolve_tester(),
                "mcp_session_id": mcp_session_id,
                "rpc_id": rpc_id,
                "session_id": log_session_id(arguments.get("session_id")),
            }
        )
        return await call_next(context)


def _ids(ctx: Context | None) -> tuple[str | None, str | None]:
    """(MCP session id, JSON-RPC request id) of the current request, None when unavailable."""
    if ctx is None:
        return None, None
    try:
        mcp_session_id: str | None = ctx.session_id
    except RuntimeError:
        mcp_session_id = None
    try:
        rpc_id: str | None = ctx.request_id
    except RuntimeError:
        rpc_id = None
    return mcp_session_id, rpc_id


def _resolve(ref: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Follow a local $ref; sibling keys such as the field title win over the target's."""
    if "$ref" not in ref:
        return ref
    return {**defs[ref["$ref"].rsplit("/", 1)[-1]], **{k: v for k, v in ref.items() if k != "$ref"}}


def _copy_titles(target: Any, ref: dict[str, Any], defs: dict[str, Any]) -> None:
    """Copy Field titles from a pydantic reference schema onto FastMCP's advertised schema."""
    ref = _resolve(ref, defs)
    if not isinstance(target, dict):
        return
    if "items" in target and "items" in ref:
        _copy_titles(target["items"], ref["items"], defs)
    for name, prop in target.get("properties", {}).items():
        ref_prop = ref.get("properties", {}).get(name)
        if ref_prop is None:
            continue
        resolved = _resolve(ref_prop, defs)
        if "title" in resolved:
            prop["title"] = resolved["title"]
        _copy_titles(prop, ref_prop, defs)


def _restore_titles(tool: Tool, annotations: dict[str, Any]) -> None:
    """FastMCP prunes every title from tool schemas; the contract requires them."""
    schema = tool.parameters
    for name, annotation in annotations.items():
        ref = TypeAdapter(annotation).json_schema()
        for def_name, target_def in schema.get("$defs", {}).items():
            if def_name in ref.get("$defs", {}):
                _copy_titles(target_def, ref["$defs"][def_name], ref["$defs"])
        prop = schema["properties"][name]
        title = _resolve(ref, ref.get("$defs", {})).get("title")
        if title:
            prop["title"] = title
        _copy_titles(prop, ref, ref.get("$defs", {}))


def build_mcp(
    *,
    registry: SessionRegistry,
    log: JsonlLog,
    resolve_tester: Callable[[], str | None],
    now: Callable[[], datetime],
    display_name: str,
    auth: AuthProvider | None = None,
) -> FastMCP:
    mcp = FastMCP(
        name="english-tutor-spike",
        instructions=c.INSTRUCTIONS,
        auth=auth,
        strict_input_validation=True,
    )
    mcp.add_middleware(CallLogMiddleware(log, resolve_tester, now))
    mcp.add_middleware(RetryRulesMiddleware())

    def tester_or_refuse() -> str:
        tester = resolve_tester()
        if tester is None:
            raise ToolError(c.NOT_ALLOWED)
        return tester

    def log_tool(ctx: Context, tool: str, tester: str, session_id: str, **fields: Any) -> None:
        mcp_session_id, rpc_id = _ids(ctx)
        log.write(
            {
                "kind": "tool",
                "ts": now().isoformat(),
                "tool": tool,
                "tester": tester,
                "mcp_session_id": mcp_session_id,
                "rpc_id": rpc_id,
                "session_id": log_session_id(session_id),
                **fields,
            }
        )

    def get_profile(ctx: Context) -> dict[str, Any]:
        tester = tester_or_refuse()
        session_id = registry.issue(tester)
        log_tool(ctx, "get_profile", tester, session_id=session_id)
        return {
            "display_name": display_name,
            "level": "B1+",
            "domain": "software",
            "native_language": "es",
            "plan_summary": "spike: free practice",
            "streak": 0,
            "open_session": False,
            "provisional_items": 0,
            "session_id": session_id,
            "response_rules": c.GET_PROFILE_RULES,
        }

    def end_session(
        session_id: c.SessionId,
        user_turns: c.UserTurns,
        errors: c.Errors,
        chunks_used: c.ChunksUsed,
        task_result: c.TaskResultField,
        hints_given: c.HintsGiven,
        cefr_estimate: c.CefrField,
        confidence_1_5: c.Confidence15,
        ctx: Context,
        assistant_words_estimate: c.AssistantWords = None,
    ) -> dict[str, Any]:
        tester = tester_or_refuse()
        if registry.owner(session_id) != tester:
            log_tool(
                ctx,
                "end_session",
                tester,
                session_id=session_id,
                accepted=False,
                reason="unknown_session",
            )
            raise ToolError(c.UNKNOWN_SESSION)
        kept = sum(said_in_turns(error.said, user_turns) for error in errors)
        rejected = len(errors) - kept
        repeat = registry.record_end(session_id)
        log_tool(
            ctx,
            "end_session",
            tester,
            session_id=session_id,
            accepted=True,
            errors_kept=kept,
            errors_rejected=rejected,
            repeat=repeat,
        )
        return {
            "accepted": True,
            "errors_kept": kept,
            "errors_rejected": rejected,
            "response_rules": c.END_SESSION_RULES,
        }

    mcp.add_tool(
        Tool.from_function(get_profile, name="get_profile", description=c.GET_PROFILE_DESCRIPTION)
    )
    end_tool = mcp.add_tool(
        Tool.from_function(end_session, name="end_session", description=c.END_SESSION_DESCRIPTION)
    )
    _restore_titles(
        end_tool,
        {k: v for k, v in end_session.__annotations__.items() if k not in ("ctx", "return")},
    )
    return mcp
