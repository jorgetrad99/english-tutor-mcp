"""get_profile and end_session (spec §3.2). Tool lines carry the tester label."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import Tool
from pydantic import TypeAdapter

from tutor_spike import contract as c
from tutor_spike.eventlog import JsonlLog
from tutor_spike.normalize import said_in_turns
from tutor_spike.sessions import SessionRegistry


class RetryRulesMiddleware(Middleware):
    """Schema errors on end_session come back with the retry response_rules."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        try:
            return await call_next(context)
        except ValidationError as exc:
            if getattr(context.message, "name", None) != "end_session":
                raise
            raise ToolError(f"Invalid end_session arguments: {exc}. {c.RETRY_RULES}") from exc


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
    mcp.add_middleware(RetryRulesMiddleware())

    def tester_or_refuse() -> str:
        tester = resolve_tester()
        if tester is None:
            raise ToolError(c.NOT_ALLOWED)
        return tester

    def log_tool(ctx: Context, tool: str, tester: str, **fields: Any) -> None:
        try:
            mcp_session_id: str | None = ctx.session_id
        except RuntimeError:
            mcp_session_id = None
        log.write(
            {
                "kind": "tool",
                "ts": now().isoformat(),
                "tool": tool,
                "tester": tester,
                "mcp_session_id": mcp_session_id,
                "rpc_id": ctx.request_id,
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
