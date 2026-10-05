"""FastMCP server: six tools, the start-lesson prompt and the instructions (spec section 8).

Tools are plain `def` functions: FastMCP runs them in a worker thread with contextvars
copied, so the blocking services and get_access_token() work inside them.
Middleware order (outermost first): call log, identity + rate limit, error mapping.
"""

from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

import anyio
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from tutor.auth.identity import current_user_id
from tutor.mcp import instructions as text
from tutor.mcp import rules
from tutor.mcp import schemas as s
from tutor.mcp.errors import TutorToolError, ValidationErrorMiddleware, error_text, tool_error
from tutor.mcp.observe import CallLogMiddleware, current_call, guard_library_logs, log_failure
from tutor.mcp.ratelimit import SlidingWindowLimiter
from tutor.services import glossary as glossary_svc
from tutor.services import lesson as lesson_svc
from tutor.services import profile as profile_svc
from tutor.services import session_end as end_svc
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.ports import IdentityResolver

SERVER_NAME = "english-tutor"


def _internal_error(tool: str | None) -> TutorToolError:
    return TutorToolError(error_text("internal_error", (), tool))


class IdentityMiddleware(Middleware):
    """Resolves the caller once per tools/call and applies the per-user rate limit."""

    def __init__(self, resolve: Callable[[], UUID], limiter: SlidingWindowLimiter) -> None:
        self._resolve = resolve
        self._limiter = limiter

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        tool = getattr(context.message, "name", None)
        state = current_call()
        if state is None:
            raise RuntimeError("CallLogMiddleware must wrap IdentityMiddleware")
        try:
            state.user_id = await anyio.to_thread.run_sync(self._resolve)
        except PermissionError:
            raise PermissionError("no MCP access token") from None
        except Exception as exc:  # a database error's text may hold the sub or the email
            log_failure(str(tool), exc)
            raise _internal_error(tool) from None
        if not self._limiter.allow(str(state.user_id)):
            raise TutorToolError(error_text("rate_limited", (), tool))
        return await call_next(context)


def _user() -> UUID:
    state = current_call()
    if state is None or state.user_id is None:
        raise RuntimeError("tool ran outside IdentityMiddleware")
    return state.user_id


def _guard[T](tool: str, run: Callable[[], T]) -> T:
    """Runs a tool body: a ServiceError maps by its code; anything else (a database error,
    output drift) becomes a fixed internal_error, so FastMCP never logs its text."""
    try:
        return run()
    except ServiceError as exc:
        raise tool_error(exc, tool) from None
    except Exception as exc:
        log_failure(tool, exc)
        raise _internal_error(tool) from None


def _canonical(node: Any) -> Any:
    """Schema without titles and with sorted `required`, for the drift check."""
    if isinstance(node, dict):
        return {
            key: sorted(value) if key == "required" else _canonical(value)
            for key, value in node.items()
            if not (key == "title" and isinstance(value, str))
        }
    if isinstance(node, list):
        return [_canonical(value) for value in node]
    return node


def advertised_schema(
    model: type[BaseModel], mode: Literal["validation", "serialization"] = "validation"
) -> dict[str, Any]:
    schema = model.model_json_schema(mode=mode)
    schema.pop("title", None)
    schema.pop("description", None)
    return schema


def _register(
    mcp: FastMCP, fn: Callable[..., Any], model: type[BaseModel], annotations: ToolAnnotations
) -> None:
    """Add the tool, check its signature and return type match the models, then advertise
    the models' schemas.

    FastMCP strips every `title` from input and output schemas; the contract requires one on
    every field, nested ones included. This is the only place titles are restored.
    """
    name = fn.__name__
    tool = mcp.add_tool(
        Tool.from_function(
            fn, name=name, description=text.TOOL_DESCRIPTIONS[name], annotations=annotations
        )
    )
    inputs = advertised_schema(model)
    outputs = advertised_schema(s.OUTPUT_MODELS[name], mode="serialization")
    if _canonical(inputs) != _canonical(tool.parameters):
        raise RuntimeError(f"{name}: tool signature and {model.__name__} disagree")
    if tool.output_schema is None or _canonical(outputs) != _canonical(tool.output_schema):
        raise RuntimeError(f"{name}: return type and {s.OUTPUT_MODELS[name].__name__} disagree")
    tool.parameters.clear()
    tool.parameters.update(inputs)
    tool.output_schema.clear()
    tool.output_schema.update(outputs)


def build_mcp(
    svc: Services,
    identity: IdentityResolver,
    *,
    auth: AuthProvider | None,
    limiter: SlidingWindowLimiter | None = None,
    user_resolver: Callable[[], UUID] | None = None,
) -> FastMCP:
    def resolve_from_token() -> UUID:
        return current_user_id(identity, svc)

    guard_library_logs()
    mcp = FastMCP(SERVER_NAME, instructions=text.INSTRUCTIONS, auth=auth, mask_error_details=True)
    mcp.add_middleware(CallLogMiddleware())
    mcp.add_middleware(
        IdentityMiddleware(user_resolver or resolve_from_token, limiter or SlidingWindowLimiter())
    )
    mcp.add_middleware(ValidationErrorMiddleware())

    def get_profile() -> s.GetProfileOutput:
        def run() -> s.GetProfileOutput:
            view = profile_svc.get_profile(svc, _user())
            return s.GetProfileOutput.of(
                view, rules.get_profile_rules(onboarding_needed=view.onboarding_needed)
            )

        return _guard("get_profile", run)

    def save_profile(
        self_level: s.SelfLevel,
        domains: s.Domains,
        use_cases: s.UseCases,
        minutes_per_day: s.MinutesPerDay,
        days_per_week: s.DaysPerWeek,
        target_level: s.TargetLevel,
        target_date: s.TargetDate = None,
        goal_text: s.GoalText = None,
    ) -> s.SaveProfileOutput:
        def run() -> s.SaveProfileOutput:
            args = s.SaveProfileInput(
                self_level=self_level,
                domains=domains,
                use_cases=use_cases,
                minutes_per_day=minutes_per_day,
                days_per_week=days_per_week,
                target_level=target_level,
                target_date=target_date,
                goal_text=goal_text,
            )
            result = profile_svc.save_profile(svc, _user(), args.to_profile_input())
            return s.SaveProfileOutput.of(result, rules.SAVE_PROFILE)

        return _guard("save_profile", run)

    def start_lesson(
        mode: s.LessonMode,
        prep: s.Prep = None,
        prep_use_case: s.PrepUseCase = None,
        minutes: s.Minutes = None,
        domain: s.DomainField = "it",
    ) -> s.StartLessonOutput:
        def run() -> s.StartLessonOutput:
            args = s.StartLessonInput(
                mode=mode, prep=prep, prep_use_case=prep_use_case, minutes=minutes, domain=domain
            )
            start = lesson_svc.start_lesson(svc, _user(), args.to_request())
            return s.StartLessonOutput.of(
                start,
                rules.start_lesson_rules(
                    start.mode,
                    has_provisional=bool(start.provisional_items),
                    has_due_reviews=bool(start.due_reviews),
                    plan_exhausted=start.plan_exhausted,
                ),
            )

        return _guard("start_lesson", run)

    def record_review(
        session_id: s.SessionIdField, results: s.ReviewResults
    ) -> s.RecordReviewOutput:
        def run() -> s.RecordReviewOutput:
            pairs = tuple((r.item_id, r.rating) for r in results)
            views = lesson_svc.record_review(svc, _user(), session_id, pairs)
            return s.RecordReviewOutput.of(views, rules.RECORD_REVIEW)

        return _guard("record_review", run)

    def save_glossary(
        session_id: s.SessionIdField, status: s.GlossaryStatusField, items: s.GlossaryItems
    ) -> s.SaveGlossaryOutput:
        def run() -> s.SaveGlossaryOutput:
            incoming = tuple(item.to_incoming() for item in items)
            result = glossary_svc.save_glossary(svc, _user(), session_id, status, incoming)
            return s.SaveGlossaryOutput.of(result, rules.SAVE_GLOSSARY)

        return _guard("save_glossary", run)

    def end_session(
        session_id: s.SessionIdField,
        user_turns: s.UserTurns,
        errors: s.ReportedErrors,
        chunks_used: s.ChunksUsed,
        task_result: s.TaskResultField,
        hints_given: s.HintsGiven,
        cefr_estimate: s.CefrField,
        confidence_1_5: s.Confidence15,
        assistant_words_estimate: s.AssistantWords = None,
    ) -> s.EndSessionOutput:
        def run() -> s.EndSessionOutput:
            args = s.EndSessionInput(
                session_id=session_id,
                user_turns=user_turns,
                errors=errors,
                chunks_used=chunks_used,
                task_result=task_result,
                hints_given=hints_given,
                cefr_estimate=cefr_estimate,
                confidence_1_5=confidence_1_5,
                assistant_words_estimate=assistant_words_estimate,
            )
            result = end_svc.end_session(
                svc, _user(), session_id, args.to_evidence(), args.raw_evidence()
            )
            return s.EndSessionOutput.of(
                result,
                rules.end_session_rules(result.status, already_closed=result.already_closed),
            )

        return _guard("end_session", run)

    _register(
        mcp,
        get_profile,
        s.GetProfileInput,
        ToolAnnotations(title="Get profile", read_only_hint=True, open_world_hint=False),
    )
    _register(
        mcp,
        save_profile,
        s.SaveProfileInput,
        ToolAnnotations(title="Save profile", idempotent_hint=True, open_world_hint=False),
    )
    _register(
        mcp,
        start_lesson,
        s.StartLessonInput,
        ToolAnnotations(title="Start lesson", open_world_hint=False),
    )
    _register(
        mcp,
        record_review,
        s.RecordReviewInput,
        ToolAnnotations(title="Record review", idempotent_hint=True, open_world_hint=False),
    )
    _register(
        mcp,
        save_glossary,
        s.SaveGlossaryInput,
        ToolAnnotations(title="Save glossary", open_world_hint=False),
    )
    _register(
        mcp,
        end_session,
        s.EndSessionInput,
        ToolAnnotations(title="End session", idempotent_hint=True, open_world_hint=False),
    )

    @mcp.prompt(
        name=text.START_LESSON_PROMPT_NAME, description=text.START_LESSON_PROMPT_DESCRIPTION
    )
    def start_lesson_prompt() -> str:
        return text.START_LESSON_PROMPT

    return mcp
