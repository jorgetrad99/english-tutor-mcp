"""FastMCP server: six English tools, sixteen subjects tools, the start-lesson prompt and the
instructions (spec section 8; subjects spec 7.1, 15.9, 16.8).

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
from tutor.mcp.observe import (
    CallLogMiddleware,
    Unauthenticated,
    current_call,
    guard_library_logs,
    note_failure,
)
from tutor.mcp.ratelimit import SlidingWindowLimiter
from tutor.services import glossary as glossary_svc
from tutor.services import lesson as lesson_svc
from tutor.services import notes as notes_svc
from tutor.services import practice as practice_svc
from tutor.services import profile as profile_svc
from tutor.services import progress as progress_svc
from tutor.services import session_end as end_svc
from tutor.services import subjects as subjects_svc
from tutor.services import templates as templates_svc
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
            raise Unauthenticated from None
        except Exception as exc:  # a database error's text may hold the sub or the email
            note_failure(exc)
            raise _internal_error(tool) from None
        if not self._limiter.allow(str(state.user_id)):
            raise TutorToolError(error_text("rate_limited", (), tool))
        return await call_next(context)


def _user() -> UUID:
    state = current_call()
    if state is None or state.user_id is None:
        raise RuntimeError("tool ran outside IdentityMiddleware")
    return state.user_id


def _sent_fields() -> frozenset[str]:
    state = current_call()
    if state is None:
        raise RuntimeError("tool ran outside CallLogMiddleware")
    return state.sent_fields


def _guard[T](tool: str, run: Callable[[], T]) -> T:
    """Runs a tool body: a ServiceError maps by its code; anything else (a database error,
    output drift) becomes a fixed internal_error, so FastMCP never logs its text."""
    try:
        return run()
    except ServiceError as exc:
        raise tool_error(exc, tool) from None
    except Exception as exc:
        note_failure(exc)
        raise _internal_error(tool) from None


def _canonical(node: Any) -> Any:
    """Schema without titles and with sorted `required`, for the drift check."""
    if isinstance(node, dict):
        return {
            key: sorted(value)
            if key == "required" and isinstance(value, list)
            else _canonical(value)
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


def _register_subject_tools(mcp: FastMCP, svc: Services) -> None:
    """The ten subjects tools (subjects spec 7.1, 15.9)."""

    def get_subjects() -> s.GetSubjectsOutput:
        def run() -> s.GetSubjectsOutput:
            view = subjects_svc.get_subjects(svc, _user())
            return s.GetSubjectsOutput.of(
                view, rules.get_subjects_rules(has_subjects=bool(view.subjects))
            )

        return _guard("get_subjects", run)

    def save_subject(
        name: s.SubjectName,
        preference: s.PreferenceField,
        exercise_kinds: s.ExerciseKindsField,
        minutes_per_session: s.MinutesPerSession,
        sessions_per_week: s.SessionsPerWeek,
        subject_id: s.SubjectIdOptional = None,
        goal_text: s.SubjectGoalText = None,
        context_text: s.SubjectContextText = None,
        target_date: s.SubjectTargetDate = None,
        subject_status: s.SubjectStatusField = None,
    ) -> s.SaveSubjectOutput:
        def run() -> s.SaveSubjectOutput:
            given: dict[str, Any] = {
                "name": name,
                "preference": preference,
                "exercise_kinds": exercise_kinds,
                "minutes_per_session": minutes_per_session,
                "sessions_per_week": sessions_per_week,
                "subject_id": subject_id,
                "goal_text": goal_text,
                "context_text": context_text,
                "target_date": target_date,
                "subject_status": subject_status,
            }
            sent = _sent_fields()
            # Only what the client sent, so model_fields_set tells omitted from null.
            args = s.SaveSubjectInput(**{k: v for k, v in given.items() if k in sent})
            try:
                result = subjects_svc.save_subject(
                    svc,
                    _user(),
                    args.to_subject_input(),
                    args.subject_id,
                    keep_target_date="target_date" not in args.model_fields_set,
                )
            except ServiceError as exc:
                raise s.mcp_fields(exc) from None
            return s.SaveSubjectOutput.of(result, rules.SAVE_SUBJECT)

        return _guard("save_subject", run)

    def save_notes(
        subject_id: s.SubjectIdField, notes: s.NotesField, source: s.SourceField = None
    ) -> s.SaveNotesOutput:
        def run() -> s.SaveNotesOutput:
            args = s.SaveNotesInput(subject_id=subject_id, notes=notes, source=source)
            try:
                result = notes_svc.save_notes(
                    svc, _user(), subject_id, args.to_source_input(), args.to_note_inputs()
                )
            except ServiceError as exc:
                raise s.mcp_fields(exc) from None
            return s.SaveNotesOutput.of(result, rules.SAVE_NOTES)

        return _guard("save_notes", run)

    def get_template_guide(
        guide_section: s.GuideSectionField = "contract",
    ) -> s.GetTemplateGuideOutput:
        def run() -> s.GetTemplateGuideOutput:
            view = templates_svc.get_template_guide(guide_section)
            return s.GetTemplateGuideOutput.of(view, rules.GET_TEMPLATE_GUIDE)

        return _guard("get_template_guide", run)

    def list_templates(
        subject_id: s.SubjectIdField,
        template_id: s.TemplateIdOptional = None,
        include_html: s.IncludeHtml = False,
    ) -> s.ListTemplatesOutput:
        def run() -> s.ListTemplatesOutput:
            s.ListTemplatesInput(
                subject_id=subject_id, template_id=template_id, include_html=include_html
            ).checked()
            view = templates_svc.list_templates(svc, _user(), subject_id, template_id, include_html)
            return s.ListTemplatesOutput.of(view, rules.LIST_TEMPLATES)

        return _guard("list_templates", run)

    def save_template(
        subject_id: s.SubjectIdField,
        name: s.TemplateName,
        template_purpose: s.TemplatePurposeField,
        grading_kind: s.GradingKindField,
        params: s.ParamSpecsField,
        html: s.TemplateHtml,
        guide_version: s.GuideVersionField,
        learner_confirmed: s.LearnerConfirmed,
        description: s.TemplateDescription = None,
        text_match: s.TextMatchField = None,
        numeric_tolerance: s.NumericTolerance = None,
        pool_draw: s.PoolDrawField = "none",
        pool_count: s.PoolCount = None,
        needs_view: s.NeedsView = False,
        options_param: s.OptionsParam = None,
        replaces_template_id: s.ReplacesTemplateId = None,
    ) -> s.SaveTemplateOutput:
        def run() -> s.SaveTemplateOutput:
            args = s.SaveTemplateInput(
                subject_id=subject_id,
                name=name,
                template_purpose=template_purpose,
                grading_kind=grading_kind,
                params=params,
                html=html,
                guide_version=guide_version,
                learner_confirmed=learner_confirmed,
                description=description,
                text_match=text_match,
                numeric_tolerance=numeric_tolerance,
                pool_draw=pool_draw,
                pool_count=pool_count,
                needs_view=needs_view,
                options_param=options_param,
                replaces_template_id=replaces_template_id,
            )
            saved = templates_svc.save_template(
                svc,
                _user(),
                subject_id,
                args.to_template_input(),
                html,
                guide_version,
                replaces_template_id,
            )
            return s.SaveTemplateOutput.of(saved, rules.SAVE_TEMPLATE)

        return _guard("save_template", run)

    def create_instance(
        subject_id: s.SubjectIdField,
        template_id: s.TemplateIdField,
        params: s.ParamsField,
        prompt_text: s.PromptText,
        note_id: s.NoteIdOptional = None,
        concept: s.InstanceConcept = None,
        replaces_instance_id: s.ReplacesInstanceId = None,
        include_document: s.IncludeDocument = False,
    ) -> s.CreateInstanceOutput:
        def run() -> s.CreateInstanceOutput:
            args = s.CreateInstanceInput(
                subject_id=subject_id,
                template_id=template_id,
                params=params,
                prompt_text=prompt_text,
                note_id=note_id,
                concept=concept,
                replaces_instance_id=replaces_instance_id,
                include_document=include_document,
            )
            saved = templates_svc.create_instance(
                svc,
                _user(),
                subject_id,
                args.to_instance_input(),
                note_id,
                include_document=include_document,
            )
            return s.CreateInstanceOutput.of(saved, rules.SHOW_INSTANCE)

        return _guard("create_instance", run)

    def get_instance_view(instance_id: s.InstanceIdField) -> s.GetInstanceViewOutput:
        def run() -> s.GetInstanceViewOutput:
            view = templates_svc.get_instance_view(svc, _user(), instance_id)
            return s.GetInstanceViewOutput.of(view, rules.SHOW_INSTANCE)

        return _guard("get_instance_view", run)

    def search_notes(
        query: s.SearchQuery,
        subject_id: s.SearchSubjectId = None,
        limit: s.SearchLimit = s.DEFAULT_SEARCH_LIMIT,
    ) -> s.SearchNotesOutput:
        def run() -> s.SearchNotesOutput:
            found = notes_svc.search_notes(svc, _user(), query, subject_id, limit)
            return s.SearchNotesOutput.of(found, rules.SEARCH_NOTES)

        return _guard("search_notes", run)

    def get_note(
        note_id: s.NoteIdField,
        passage_from: s.PassageFrom = None,
        passage_to: s.PassageTo = None,
    ) -> s.GetNoteOutput:
        def run() -> s.GetNoteOutput:
            view = notes_svc.get_note(svc, _user(), note_id, passage_from, passage_to)
            return s.GetNoteOutput.of(view, rules.GET_NOTE)

        return _guard("get_note", run)

    def read(title: str) -> ToolAnnotations:
        return ToolAnnotations(title=title, read_only_hint=True, open_world_hint=False)

    _register(mcp, get_subjects, s.GetSubjectsInput, read("Get subjects"))
    _register(
        mcp,
        save_subject,
        s.SaveSubjectInput,
        ToolAnnotations(title="Save subject", destructive_hint=False, open_world_hint=False),
    )
    _register(
        mcp,
        save_notes,
        s.SaveNotesInput,
        ToolAnnotations(
            title="Save notes",
            idempotent_hint=True,
            destructive_hint=False,
            open_world_hint=False,
        ),
    )
    _register(mcp, search_notes, s.SearchNotesInput, read("Search notes"))
    _register(mcp, get_note, s.GetNoteInput, read("Get note"))
    _register(mcp, get_template_guide, s.GetTemplateGuideInput, read("Get template guide"))
    _register(mcp, list_templates, s.ListTemplatesInput, read("List templates"))
    _register(
        mcp,
        save_template,
        s.SaveTemplateInput,
        # The same spec and HTML twice is one template (`existing`), so a retry is harmless.
        ToolAnnotations(
            title="Save template",
            idempotent_hint=True,
            destructive_hint=False,
            open_world_hint=False,
        ),
    )
    _register(
        mcp,
        create_instance,
        s.CreateInstanceInput,
        ToolAnnotations(
            title="Create instance",
            idempotent_hint=True,
            destructive_hint=False,
            open_world_hint=False,
        ),
    )
    _register(mcp, get_instance_view, s.GetInstanceViewInput, read("Get instance view"))
    _register_practice_tools(mcp, svc)


def _register_practice_tools(mcp: FastMCP, svc: Services) -> None:
    """The five practice tools and get_subject_progress (subjects spec 6.2, 15.9, 16.8)."""

    def start_practice(
        subject_id: s.SubjectIdField, practice_mode: s.StartModeField = "app"
    ) -> s.StartPracticeOutput:
        def run() -> s.StartPracticeOutput:
            try:
                started = practice_svc.start_practice(svc, _user(), subject_id, practice_mode)
            except ServiceError as exc:  # the service's `mode` is this tool's practice_mode
                fields = tuple("practice_mode" if f == "mode" else f for f in exc.fields)
                raise ServiceError(exc.code, fields) from None
            return s.StartPracticeOutput.of(started, rules.START_PRACTICE)

        return _guard("start_practice", run)

    def next_exercise(session_id: s.PracticeSessionIdField) -> s.NextExerciseOutput:
        def run() -> s.NextExerciseOutput:
            step = practice_svc.next_exercise(svc, _user(), session_id)
            chosen = rules.NEXT_EXERCISE_DONE if step.done else rules.NEXT_EXERCISE
            return s.NextExerciseOutput.of(step, chosen)

        return _guard("next_exercise", run)

    def show_exercise(
        subject_id: s.SubjectIdField, instance_id: s.InstanceIdField
    ) -> s.ShowExerciseOutput:
        def run() -> s.ShowExerciseOutput:
            shown = practice_svc.show_exercise(svc, _user(), subject_id, instance_id)
            return s.ShowExerciseOutput.of(shown, rules.SHOW_EXERCISE)

        return _guard("show_exercise", run)

    def record_result(
        session_id: s.PracticeSessionIdField, item_id: s.ItemIdField, answer: s.AnswerField
    ) -> s.RecordResultOutput:
        def run() -> s.RecordResultOutput:
            args = s.RecordResultInput(session_id=session_id, item_id=item_id, answer=answer)
            # Every call reaches the server as the chat's own tool call: the practice app
            # (slice C) has no channel of its own yet, so the source is always the chat.
            graded = practice_svc.record_result(
                svc, _user(), session_id, item_id, args.answer_data(), source="chat"
            )
            return s.RecordResultOutput.of(graded, rules.RECORD_RESULT)

        return _guard("record_result", run)

    def end_practice(
        session_id: s.PracticeSessionIdField, self_rating: s.SelfRatingField = None
    ) -> s.EndPracticeOutput:
        def run() -> s.EndPracticeOutput:
            summary = practice_svc.end_practice(svc, _user(), session_id, self_rating)
            return s.EndPracticeOutput.of(summary, rules.END_PRACTICE)

        return _guard("end_practice", run)

    def get_subject_progress(subject_id: s.SubjectIdField) -> s.GetSubjectProgressOutput:
        def run() -> s.GetSubjectProgressOutput:
            progress = progress_svc.subject_progress(svc, _user(), subject_id)
            return s.GetSubjectProgressOutput.of(subject_id, progress, rules.GET_SUBJECT_PROGRESS)

        return _guard("get_subject_progress", run)

    def write(title: str, *, idempotent: bool) -> ToolAnnotations:
        return ToolAnnotations(
            title=title, idempotent_hint=idempotent, destructive_hint=False, open_world_hint=False
        )

    _register(mcp, start_practice, s.StartPracticeInput, write("Start practice", idempotent=False))
    # next_exercise only reads, except that it stamps shown_at once (the first call wins).
    _register(mcp, next_exercise, s.NextExerciseInput, write("Next exercise", idempotent=True))
    _register(mcp, show_exercise, s.ShowExerciseInput, write("Show exercise", idempotent=False))
    _register(mcp, record_result, s.RecordResultInput, write("Record result", idempotent=True))
    _register(mcp, end_practice, s.EndPracticeInput, write("End practice", idempotent=True))
    _register(
        mcp,
        get_subject_progress,
        s.GetSubjectProgressInput,
        ToolAnnotations(title="Get subject progress", read_only_hint=True, open_world_hint=False),
    )


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
        ToolAnnotations(
            title="Record review",
            idempotent_hint=True,
            destructive_hint=False,
            open_world_hint=False,
        ),
    )
    _register(
        mcp,
        save_glossary,
        s.SaveGlossaryInput,
        ToolAnnotations(title="Save glossary", destructive_hint=False, open_world_hint=False),
    )
    _register(
        mcp,
        end_session,
        s.EndSessionInput,
        ToolAnnotations(
            title="End session",
            idempotent_hint=True,
            destructive_hint=False,
            open_world_hint=False,
        ),
    )

    _register_subject_tools(mcp, svc)

    @mcp.prompt(
        name=text.START_LESSON_PROMPT_NAME, description=text.START_LESSON_PROMPT_DESCRIPTION
    )
    def start_lesson_prompt() -> str:
        return text.START_LESSON_PROMPT

    return mcp
