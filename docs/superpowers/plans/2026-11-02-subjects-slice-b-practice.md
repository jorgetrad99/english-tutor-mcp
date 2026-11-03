# Subjects Slice B: Practice Plan, Sessions and Grading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the server turns a subject's time inputs into a practice plan, reports missed sessions and the projected end date, runs practice sessions whose items it draws and grades (quiz items from the notes, self-reported exercises from the notes, server-generated ear items), and logs attendance and results. Four new MCP tools in text mode; the interactive view (slice C) plugs into the same tools after spike 05.

**Architecture:** `tutor.domain.practice_plan` (schedule arithmetic), `tutor.domain.practice` (mix, deterministic drawing, grading, ear banks); port `PracticeRepo`; service `tutor.services.practice`; migration `0007_practice`; `get_subjects` gains the real `plan_status`; `save_subject` creates or versions the plan; tools `start_practice`, `next_exercise`, `record_result`, `end_practice`.

**Tech Stack:** as slice A. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md` sections 6, 7.1 (last four rows), 7.2 (text fallback only), 8.2, 11. Builds on the slice A plan's rulings; this plan's rulings are numbered from 13.

**Execution:** subagent-driven, as slice A. **Branch:** `feat/subjects-v0`, after slice A Task 5 is merged or at least committed.

## Before Task 1

1. Slice A complete on the branch: `uv run just check` green, migration 0006 applied on `db-test`.
2. Read the slice A code you will extend: `src/tutor/domain/subjects.py`, `src/tutor/services/subjects.py`, `src/tutor/services/subject_views.py` (`PlanStatus` placeholder), `src/tutor/services/ports.py`, `src/tutor/services/memory.py`, `src/tutor/db/repos/notes.py`, `tests/repo_contract.py`, the MCP files and tests; and `src/tutor/domain/lesson.py` plus `src/tutor/services/lesson.py` for the open-session rule and the audit pattern.
3. No new MCP SDK API in this slice (text mode only). Slice C does the Context7 lookup for `fastmcp.apps`.

## Rulings against the spec

13. **Weeks are calendar-free.** `start_date` is the plan creation date in the learner's timezone (from `users.timezone` through `UserRepo.timezone()`); week `w` covers days `[7w, 7w+7)` from `start_date`; "completed weeks" at `today` = `floor(days / 7)`. No alignment to Monday.
14. **A plan version is created by `save_subject`** when the subject is created and whenever `minutes_per_session`, `sessions_per_week` or `target_date` change; the old version becomes `superseded`; `completed_sessions` counts closed sessions of the **current** version only, and `start_date` of the new version is the day it was created, so a change restarts the count (recorded in `rationale` as `{"reason": "inputs_changed", "previous_version": n}`). Simpler than carrying history across versions; revisit after the author has used it.
15. **One open practice session per user**, independent of English lessons: `start_practice` marks the user's previous open practice session `incomplete` (partial unique index on `practice_sessions (user_id) WHERE status = 'open'`).
16. **Items are drawn at `start_practice`** and stored; `next_exercise` only reads. The RNG is `random.Random(session_id.int)`; drawing order: quiz items by `(last_asked_at nulls first, id)`, exercises by `(last_practised_at nulls first, id)`, ear prompts uniformly from the banks with replacement. `last_asked_at`/`last_practised_at` are computed from `practice_items` (no denormalised columns).
17. **Ear banks** in `tutor.domain.practice`: `NOTE_BANK` = natural notes A3–A5 with frequencies rounded to 0.01 Hz (A4 = 440), `INTERVAL_BANK` = `P1, m2, M2, m3, M3, P4, TT, P5, m6, M6, m7, M7, P8` ascending from C4 (261.63 Hz) with the second frequency computed by equal temperament. Prompts carry the frequencies so the view (slice C) does no music arithmetic. Options for `note_id` are the 7 natural names; for `interval_id` the 13 interval names.
18. **Grading is exact-match on closed enums**: `note` in the 7 names, `interval` in the 13 names, `option_index` within the quiz item's options; anything else is `validation_failed` on the answer field. Exercise self-report: `rating` 1–4 required, `tempo_bpm` 20–300 and `minutes` 1–120 optional; `score = rating * 25`.
19. **Text mode skips `ear`** and redistributes its share to the other kinds in the order quiz, exercise (spec 6.2); a subject whose only enabled kind is `ear` cannot start in text mode: `validation_failed` on `mode`.
20. **Item count** = `clamp(round(minutes_per_session / 3), 4, 12)`; when the subject has fewer available quiz items or exercises than their share, the shortfall goes to the other kinds, and if the total still falls below 4 the session starts with what exists (minimum 1) and `summary.short` is `true`.
21. **`end_practice` on a session with unanswered items** closes it as `closed` with `summary.unanswered` counted; `incomplete` is only for sessions superseded by a new `start_practice`.
22. **New enum field names** (slice A ruling 1): `item_kind` (`note_id|interval_id|quiz|exercise`), `practice_status` (`open|closed|incomplete`), `answer.kind` is the discriminator inside the tagged union and is allowed because it is nested (the schema test keys top-level output enums only; verify, and if it keys nested ones too, name it `answer_kind`).
23. **Migration 0007** is written once, like 0006; `practice_items.prompt` and `answer` are JSONB with the shape validated in the domain, plus `CHECK (jsonb_typeof(prompt) = 'object')`.

## Global Constraints

As the slice A plan, plus:
- `practice_sessions`, `practice_plans` and `practice_items` are user-scoped with forced RLS; `practice_items` references `practice_sessions (id, user_id)`; the report role gets nothing.
- The first answer wins: `record_result` is idempotent per `(session, item)`; a repeat returns the stored result with `outcome: existing`.
- Audit events with ids and enums only: `practice_plan_created`, `practice_started`, `practice_ended`, `practice_incomplete`.

## Review Focus

1. **Missed sessions arithmetic:** a plan of 3 sessions/week started 10 days ago with 2 closed sessions reports `expected 3`, `missed 1`, and with a target 4 weeks out reports the projected end shifted by one week; an open-ended plan reports `projected_end: null`. Task 1.
2. **Changing sessions per week** creates version 2 and `get_subjects` shows `missed 0` right after. Task 2.
3. **A subject with no notes yet** and `exercise_kinds = [quiz, exercise]`: `start_practice` gives `validation_failed` on `subject_id` with the hint in the rules ("add notes first"); with `ear` enabled it starts with ear items only. Tasks 1 and 2.
4. **Answer shapes:** `{kind: "quiz", option_index: 7}` on a 4-option item, `{kind: "note_id", note: "H"}`, `{kind: "exercise", rating: 5}`, an answer of the wrong kind for the item: each `validation_failed` on the right field, nothing stored. Tasks 1 and 4.
5. **Two users with sessions open at once** never see each other's items; `record_result` with another user's `item_id` is `not_found`. Task 3.

## File structure

```
src/tutor/domain/practice_plan.py   # T1 PlanInputs, PlanStatus, expected/missed/projected, plan_inputs_changed
src/tutor/domain/practice.py        # T1 banks, mix, draw_items, grade, Answer union, caps
src/tutor/services/ports.py         # T2 PracticePlanRow, PracticeSessionRow, PracticeItemRow, PracticeRepo
src/tutor/services/subject_views.py # T2 PlanStatus real, PracticeStart, NextItem, Graded, PracticeSummary
src/tutor/services/subjects.py      # T2 save_subject creates/versions plans; get_subjects fills plan_status
src/tutor/services/practice.py      # T2 start_practice, next_exercise, record_result, end_practice
src/tutor/services/memory.py        # T2 records and MemoryPracticeRepo
src/tutor/db/tables.py, db/repos/practice.py, db/uow.py   # T3
alembic/versions/0007_practice.py   # T3 (written once)
src/tutor/mcp/schemas.py, rules.py, instructions.py, server.py  # T4
tests/unit/domain/test_practice_plan.py, test_practice.py       # T1
tests/repo_contract.py, tests/unit/services/test_practice_service.py  # T2
tests/integration/test_migration_0007.py, test_db_schema.py     # T3
tests/unit/mcp/test_mcp_practice.py and the contract test files # T4
docs: spec Amendments (A13–A23)                                 # T5
```

## Task index

| # | Task | Reviewers (controller) |
| --- | --- | --- |
| 1 | Domain: plan arithmetic, banks, mix, drawing, grading | — |
| 2 | Ports, memory, services: plans in `save_subject`/`get_subjects`, the practice service | security-reviewer |
| 3 | Migration 0007, tables, Postgres repo | security-reviewer |
| 4 | MCP: four tools, text mode | mcp-contract-reviewer |
| 5 | Docs, amendments, phase audit | phase-auditor |

---

### Task 1: Domain: plan arithmetic, banks, mix, drawing, grading

**Files:** create `src/tutor/domain/practice_plan.py`, `src/tutor/domain/practice.py`; tests `tests/unit/domain/test_practice_plan.py`, `tests/unit/domain/test_practice.py`.

**Interfaces (produces):**
- `practice_plan`: frozen `PlanInputs(minutes_per_session, sessions_per_week, target_date)`; `total_sessions(inputs, start_date) -> int | None`; frozen `PlanStatus(version, start_date, sessions_per_week, minutes_per_session, target_date, total_sessions, completed, expected, missed, projected_end, delay_days, adherence)`; `plan_status(inputs, *, version, start_date, completed, today) -> PlanStatus` per spec 6.1 and ruling 13; `plan_inputs_changed(old: PlanInputs, new: PlanInputs) -> bool`.
- `practice`: `ItemKind = Literal["note_id","interval_id","quiz","exercise"]`; `NOTE_BANK`, `INTERVAL_BANK` (ruling 17); `MIX: Mapping[Preference, Mapping[ExerciseKind, int]]` (percentages from spec 6.2); `item_count(minutes) -> int`; `plan_mix(preference, enabled: Collection[ExerciseKind], count, *, text_mode) -> dict[ExerciseKind, int]` (ruling 19, 20); frozen candidates `QuizCandidate(item_id, last_asked_at)`, `ExerciseCandidate(exercise_id, last_practised_at)`; `draw_items(seed: UUID, mix, quizzes, exercises) -> tuple[DrawnItem, ...]` where `DrawnItem(seq, kind, prompt: Mapping[str, object])` and prompts have fixed shapes (`{"note": "A4", "hz": 440.0, "options": [...]}`, `{"interval": "P5", "root_hz": 261.63, "hz": 392.0, "options": [...]}`, `{"quiz_item_id": "...", "question": ..., "options": [...]}`, `{"exercise_id": "...", "name": ..., "instructions": ..., "tempo_bpm": ..., "duration_min": ...}`); `Answer` tagged union of four frozen dataclasses and `parse_answer(kind: ItemKind, data: Mapping[str, object]) -> Answer` (ruling 18); `grade(item: DrawnItem, answer: Answer, *, quiz_answer_index: int | None) -> Graded(correct: bool | None, score: int)`.
- Determinism: `draw_items` with the same seed and candidates returns the same tuple (property test).

- [ ] **Step 1:** failing tests (`# Review Focus 1`, `# Review Focus 3` for the mix with no quizzes/exercises, `# Review Focus 4` for `parse_answer`, determinism, every grade path, banks' frequencies, `item_count` bounds).
- [ ] **Step 2:** implement; `uv run just fmt && uv run just check-fast`; coverage ≥ 95% on both modules.
- [ ] **Step 3:** commit `feat(domain): practice plan arithmetic, item drawing and grading`.

### Task 2: Ports, memory, services

**Files:** modify `ports.py`, `subject_views.py`, `subjects.py`, `memory.py`; create `services/practice.py`; tests `repo_contract.py`, `tests/unit/services/test_practice_service.py`, `test_subjects_service.py` (plans).

**Interfaces:**
- `PracticeRepo`: `active_plan(subject_id) -> PracticePlanRow | None`, `create_plan(subject_id, inputs, start_date, now, rationale) -> PracticePlanRow` (supersedes the previous), `completed_count(plan_id) -> int`, `open_session() -> PracticeSessionRow | None`, `create_session(subject_id, plan_id, mode, items: Sequence[DrawnItem], now) -> PracticeSessionRow` (raises `LookupError` for a foreign subject), `mark_incomplete(session_id, now)`, `session(session_id)`, `items(session_id) -> tuple[PracticeItemRow, ...]`, `record(session_id, item_id, answer, graded, now) -> PracticeItemRow | None` (None when already answered), `close(session_id, summary, self_rating, now)`, `quiz_candidates(subject_id)`, `exercise_candidates(subject_id)`, `quiz_answer_index(quiz_item_id) -> int`.
- Services: `practice.start_practice(svc, user_id, subject_id, mode) -> PracticeStart(session_id, first: NextItem | None, item_count, short)`, `next_exercise(svc, user_id, session_id) -> NextItem` (`done: bool`, item fields without the answer), `record_result(svc, user_id, session_id, item_id, answer_data) -> Graded(outcome: new|existing, correct, score, explanation for quiz)`, `end_practice(svc, user_id, session_id, self_rating) -> PracticeSummary(items, answered, unanswered, accuracy_by_kind, minutes, plan_status)`.
- `subjects.save_subject` creates the first plan and new versions (ruling 14); `get_subjects` fills `plan_status` from the active plan and the user's timezone.

- [ ] **Step 1:** failing tests (`# Review Focus 2`, `# Review Focus 3`, `# Review Focus 5` in `RepoContract`, idempotent `record`, incomplete-on-restart, summary arithmetic).
- [ ] **Step 2:** implement; checks green.
- [ ] **Step 3:** commit `feat(services): practice plans and sessions`.
- [ ] **Step 4 (controller):** `security-reviewer`.

### Task 3: Migration 0007, tables, Postgres repo

**Files:** create once `alembic/versions/0007_practice.py`; modify `tables.py`, `uow.py`; create `db/repos/practice.py`; tests `tests/integration/test_migration_0007.py`, `test_pg_contract.py` (inherits), `test_db_schema.py` (seed rows, composite FK count).

Migration: `practice_plans` (`version`, `status` active|superseded, `start_date`, `minutes_per_session`, `sessions_per_week`, `target_date`, `total_sessions`, `rationale` JSONB), `practice_sessions` (`subject_id`, `plan_id`, `mode` app|text, `status`, `started_at`, `ended_at`, `self_rating`, `summary` JSONB, `item_count`, partial unique open index), `practice_items` (`session_id`, `seq`, `kind`, `prompt` JSONB, `answer` JSONB, `correct`, `score`, `answered_at`, `source` app|chat, unique `(session_id, seq)`); composite FKs to `subjects`, `practice_plans`, `practice_sessions`; RLS, policies, grants (`practice_items`: no `DELETE`); downgrade drops the three tables.

- [ ] **Step 1:** failing tests (round trip, one open session per user enforced by the index, isolation).
- [ ] **Step 2:** implement; `uv run just test-int` green.
- [ ] **Step 3:** commit `feat(db): migration 0007 practice plans, sessions and items`.
- [ ] **Step 4 (controller):** `security-reviewer`.

### Task 4: MCP: four tools, text mode

**Files:** modify `schemas.py`, `rules.py`, `instructions.py`, `server.py`; tests `tests/unit/mcp/test_mcp_practice.py` plus the contract files.

- Inputs: `StartPracticeInput(subject_id, mode: Literal["app","text"] = "app")`, `NextExerciseInput(session_id)`, `RecordResultInput(session_id, item_id, answer: QuizAnswer | NoteAnswer | IntervalAnswer | ExerciseAnswer` discriminated on `kind`)`, `EndPracticeInput(session_id, self_rating)`. Outputs with `response_rules`; the item in every output carries a `text` line that states it in words (quiz question with numbered options; exercise name, instructions, tempo; ear items state the note or interval name only in `app` mode, since text mode never draws them).
- Response rules (2–4 lines): `start_practice` and `next_exercise`: "Present the item exactly as given. Record the learner's answer with `record_result`; never grade it yourself. If the interactive view did not appear, present it in text."; `record_result`: "Say whether it was correct in one sentence and move to `next_exercise`."; `end_practice`: "Read the summary in two sentences. If `plan_status.missed` is above 0, say the new end date without blame."
- `_meta.ui.resourceUri` is **not** set in this slice (slice C adds it with the resource).

- [ ] **Step 1:** failing tests (`# Review Focus 4` through the in-process client, rules line counts, enum sets with the ruling 22 names, descriptions ≤ 120 words, instructions ≤ 400 words).
- [ ] **Step 2:** implement; checks green.
- [ ] **Step 3:** commit `feat(mcp): practice tools in text mode`.
- [ ] **Step 4 (controller):** `mcp-contract-reviewer`.

### Task 5: Docs, amendments, phase audit

- [ ] Spec `## Amendments` A13–A23; `docs/v0/runbook.md` cascade comment; `uv run just check`; **(controller)** `phase-auditor`; commit `docs(subjects): slice B amendments`.

## Notes for the controller

- Slice C (the `ui://tutor/practice/v1/app.html` resource, `meta` on the two app tools, the handshake, Web Audio, the in-process resource test) starts only after spike 05 has a Setup section filled and the author has run at least the claude.ai and Desktop attempts; it is a one-task plan.
- Slice D (website: Materias, Materia, Nota, raw artifact route with the header set, PDF upload with `pypdf`) is written after slice A ships; it adds the only new dependency.
