# Subjects Slice B: Practice Plan, Sessions and Grading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the server turns a subject's time inputs into a practice plan, reports missed sessions and the projected end date, runs practice sessions whose items it draws and grades (quiz items from the notes, self-reported exercises from the notes, server-generated ear items), and logs attendance and results. Four new MCP tools in text mode; the interactive view (slice C) plugs into the same tools after spike 05.

**Architecture:** `tutor.domain.practice_plan` (schedule arithmetic), `tutor.domain.practice` (mix, deterministic drawing, grading, ear banks); port `PracticeRepo`; service `tutor.services.practice`; migration `0007_practice`; `get_subjects` gains the real `plan_status`; `save_subject` creates or versions the plan; tools `start_practice`, `next_exercise`, `record_result`, `end_practice`.

**Tech Stack:** as slice A. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md` sections 6, 7.1 (last four rows), 7.2 (text fallback only), 8.2, 11, and 16 (metrics). Builds on the slice A plan's rulings; this plan's rulings are numbered from 28 (slice A uses 1–27; renumbered 2026-11-02, they were 13–23).

**Amended 2026-11-02 (exercise templates, domain-neutral):** spec section 15 and ADR 0005 change this plan before it starts. Practice items are showings of stored template instances; grading is `tutor.domain.templates.grade` with the showing's stored draw; `show_exercise` and the `class` session mode are added, and a `class` session counts toward attendance when at least one item is answered. The server ships no templates and knows no subject: **ear training is no longer a server feature.** The kind `ear` becomes the generic `drill`, and a drill item is a showing of a **pool instance** (spec 15.4.1): the server draws entries with a seed, stores the seed and the draw on the item, and derives the key by a closed rule. A music subject gets ear items from a Claude-written template whose pool holds note names; a language subject gets vocabulary drills from the same mechanism. Rulings 31, 32, 33, 34, 37, 38 and Tasks 1, 3 and 4 change as listed under "Effect on slice B" in slice A Task 4b; the text below keeps the original design for history where that list overrides it.

**Amended 2026-11-03 (subject metrics):** spec section 16, approved by the author. This slice also captures what the metrics need and serves them: `practice_items.shown_at` (set once by the server when a showing is first returned), a `notes_searched` audit event from `search_notes` (no query), the pure module `tutor.domain.subject_metrics`, a read port `ProgressRepo`, the service `tutor.services.progress` and a fifth tool, `get_subject_progress`. Rulings 63–71 below (numbered after slice D's 49–62 so every ruling number stays unique across the four plans); the task text marks the additions "(16)". The instance → concept link is validated and defaulted in slice A Task 4b (spec 16.3); if 4b ships without the default, ruling 65 applies it at read time.

**Execution:** subagent-driven, as slice A. **Branch:** `feat/subjects-v0`, after slice A Task 5 is merged or at least committed.

## Before Task 1

1. Slice A complete on the branch: `uv run just check` green, migration 0006 applied on `db-test`.
2. Read the slice A code you will extend: `src/tutor/domain/subjects.py`, `src/tutor/services/subjects.py`, `src/tutor/services/subject_views.py` (`PlanStatus` placeholder), `src/tutor/services/ports.py`, `src/tutor/services/memory.py`, `src/tutor/db/repos/notes.py`, `tests/repo_contract.py`, the MCP files and tests; and `src/tutor/domain/lesson.py` plus `src/tutor/services/lesson.py` for the open-session rule and the audit pattern.
3. No new MCP SDK API in this slice (text mode only). Slice C does the Context7 lookup for `fastmcp.apps`.

## Rulings against the spec

28. **Weeks are calendar-free.** `start_date` is the plan creation date in the learner's timezone (from `users.timezone` through `UserRepo.timezone()`); week `w` covers days `[7w, 7w+7)` from `start_date`; "completed weeks" at `today` = `floor(days / 7)`. No alignment to Monday.
29. **A plan version is created by `save_subject`** when the subject is created and whenever `minutes_per_session`, `sessions_per_week` or `target_date` change; the old version becomes `superseded`; `completed_sessions` counts closed sessions of the **current** version only, and `start_date` of the new version is the day it was created, so a change restarts the count (recorded in `rationale` as `{"reason": "inputs_changed", "previous_version": n}`). Simpler than carrying history across versions; revisit after the author has used it.
30. **One open practice session per user**, independent of English lessons: `start_practice` marks the user's previous open practice session `incomplete` (partial unique index on `practice_sessions (user_id) WHERE status = 'open'`).
31. (amended, see the note at the top) **Items are drawn at `start_practice`** and stored; `next_exercise` only reads. The RNG is `random.Random(session_id.int)`; drawing order: quiz items by `(last_asked_at nulls first, id)`, exercises by `(last_practised_at nulls first, id)`, ear prompts uniformly from the banks with replacement. `last_asked_at`/`last_practised_at` are computed from `practice_items` (no denormalised columns).
32. (amended, see the note at the top) **Ear banks** in `tutor.domain.practice`: `NOTE_BANK` = natural notes A3–A5 with frequencies rounded to 0.01 Hz (A4 = 440), `INTERVAL_BANK` = `P1, m2, M2, m3, M3, P4, TT, P5, m6, M6, m7, M7, P8` ascending from C4 (261.63 Hz) with the second frequency computed by equal temperament. Prompts carry the frequencies so the view (slice C) does no music arithmetic. Options for `note_id` are the 7 natural names; for `interval_id` the 13 interval names.
33. (amended, see the note at the top) **Grading is exact-match on closed enums**: `note` in the 7 names, `interval` in the 13 names, `option_index` within the quiz item's options; anything else is `validation_failed` on the answer field. Exercise self-report: `rating` 1–4 required, `tempo_bpm` 20–300 and `minutes` 1–120 optional; `score = rating * 25`.
34. (amended, see the note at the top) **Text mode skips `ear`** and redistributes its share to the other kinds in the order quiz, exercise (spec 6.2); a subject whose only enabled kind is `ear` cannot start in text mode: `validation_failed` on `mode`.
35. **Item count** = `clamp(round(minutes_per_session / 3), 4, 12)`; when the subject has fewer available quiz items or exercises than their share, the shortfall goes to the other kinds, and if the total still falls below 4 the session starts with what exists (minimum 1) and `summary.short` is `true`.
36. **`end_practice` on a session with unanswered items** closes it as `closed` with `summary.unanswered` counted; `incomplete` is only for sessions superseded by a new `start_practice`.
37. (amended, see the note at the top) **New enum field names** (slice A ruling 1): `item_kind` (`note_id|interval_id|quiz|exercise`), `practice_status` (`open|closed|incomplete`), `answer.kind` is the discriminator inside the tagged union and is allowed because it is nested (the schema test keys top-level output enums only; verify, and if it keys nested ones too, name it `answer_kind`).
38. (amended, see the note at the top) **Migration 0007** is written once, like 0006; `practice_items.prompt` and `answer` are JSONB with the shape validated in the domain, plus `CHECK (jsonb_typeof(prompt) = 'object')`.

Subject metrics (spec section 16, added 2026-11-03):

63. **`shown_at` is the server's moment of handing a showing out.** `PracticeRepo.mark_shown(item_id, now) -> datetime` runs `UPDATE practice_items SET shown_at = :now WHERE id = :id AND user_id = :uid AND shown_at IS NULL` and returns the stored value (the first one wins). `start_practice` marks the first item, `next_exercise` the item it returns, `show_exercise` inserts its showing with `shown_at = now`. `record_result` on an item with `shown_at IS NULL` is `validation_failed` on `item_id` (the error hint says to call `next_exercise` first). No tool takes a duration or a timestamp from the client.
64. **Time per item** (spec 16.4): `answered_at − shown_at` in whole seconds (truncated), graded showings only (`correct IS NOT NULL`; self-reports are never timed), `ITEM_TIME_CAP_SECONDS = 600` excludes and counts (`over_cap`), nearest-rank `median_seconds` and `p90_seconds` (rank `ceil(p × n / 100)`), null below 3 timed showings, per template (retired included) and per concept plus `unassigned`, over all time.
65. **Concept attribution is the instance's `concept_id` only** (spec 16.3). Slice A Task 4b validates it and defaults it from a note with exactly one concept. If 4b ships without the default, `ProgressRepo.showings` applies the same rule at read time (an instance with `concept_id IS NULL` whose note has exactly one row in `note_concepts` is attributed to it), so the domain sees one rule either way; drop that fallback once 4b stores it.
66. **Accuracy** (spec 16.3): graded showings; `attempts`, `correct`, `partial` (`0 < score < 100`), `mean_score` rounded half up with integers (`(2 × sum + n) // (2 × n)`), `accuracy` fraction; all time and the last 14 local days (`answered_at >= local midnight of today − 13`); every concept of the subject listed; order and `concept_rank` exactly as spec 16.3.
67. **The streak reuses the English pure function, not the English service.** `current_days = tutor.domain.metrics.streak_days(practice_days, now, tz)` with `tz = user_zone(uow)`; `longest_days = subject_metrics.longest_streak_days(practice_days, tz)` (new, pure); `last_practice_day` is the latest local day. `practice_days` = the earliest `answered_at` per local day of the subject's answered showings, any mode, self-reports included. `services.context.current_streak` and the English streak do not change (it reads closed English sessions; spec 16.5).
68. **Behaviour counts** (spec 16.6): Monday weeks in the learner's timezone (`tutor.domain.dashboard.home.week_start`, reused), the last 8 including the current one, oldest first, plus totals. `notes_ingested`, `templates_created`, `instances_created` from the rows' `created_at`; `searches` from `notes_searched` events whose `meta.subject_id` is the subject; `practice_started`, `practice_completed` (`closed`), `practice_abandoned` (`incomplete`, or `open` with no activity for more than `STALE_OPEN_SESSION = timedelta(hours=24)`, computed, never written); `unscoped_searches` is the user's total of searches without `subject_id`.
69. **`notes_searched`** joins `AuditEvent`; `services.notes.search_notes` records it in the same unit of work after the search, meta `{"subject_id": str | None, "hits": int, "fuzzy": bool}`. The query never reaches the audit log or a log line.
70. **`ProgressRepo` returns facts; the domain computes.** Methods (all filtered by `user_id` and under RLS; Postgres `db/repos/progress.py`, memory twin): `showings(subject_id) -> tuple[ShowingFact, ...]` (answered showings, graded and self-report, joined through `practice_sessions.subject_id` to `template_instances`; never `answer`), `practice_days(subject_id, tz_name) -> tuple[datetime, ...]`, `sessions(subject_id) -> tuple[SessionFact, ...]`, `activity(subject_id) -> ActivityFacts`, `concepts(subject_id)`, `templates(subject_id)` (any status), `unscoped_search_count() -> int`. The service checks the subject first (`not_found`).
71. **One new read tool, `get_subject_progress(subject_id)`**, not an extension of `get_subjects` or `end_practice` (spec 16.8): output caps `concepts` ≤ 20 (weakest first; `concepts_total` gives the full count) and `templates` ≤ 20, cut in the MCP layer (`SubjectProgress` holds every concept and template in order, because the website shows them all, slice D ruling 72); `summary_text` from numbers only, in Spanish (the v0 interface language), without any concept or template name; new output enum `concept_rank`; `grading_kind` and `template_status` reused. No `_meta.ui`; slice C does not touch it.

Execution of Tasks 1 and 2 (decided by the author on 2026-11-03; numbered after slice D's 72-74):

75. **`projected_end` counts from the current plan week.** `projected_end = start_date + 7 × (completed_weeks + ceil(remaining / sessions_per_week))` with `remaining = max(0, total_sessions − completed)`, and `delay_days = max(0, projected_end − target_date)`. Spec 6.1's `start_date + 7 × ceil(remaining / sessions_per_week)` never moved as time passed, so a missed session could never delay the end; this form puts an on-track plan exactly on its target and pushes the end out a week per week of missed sessions (Review Focus 1). `tutor.domain.practice_plan.plan_status`.
76. **`adherence` is every completed session of the plan version over `expected`**, uncapped (it can exceed 1 when the learner is ahead), and `None` while `expected` is 0 (before the first plan week ends).
77. **A pool instance is unlimited within a session.** `capacities` gives `drill` no cap when the subject has at least one schedulable pool instance; drills rotate over the pool instances least recently shown first. The avoid set (`practice.avoid_for`) holds the entries asked since the last time every entry of that instance was asked, so it resets each cycle and a small pool repeats only after a full pass. `templates.draw` takes the set as `avoid`; an empty set draws exactly as before, so the pinned draws hold.
78. **Active instances of a retired template are still drawn.** Retiring a template (`replaces_template_id`) leaves its instances active and gradable; only a retired instance (`replaces_instance_id`) leaves practice.
79. **`short` means fewer than 4 items exist** (`ITEM_COUNT_MIN`), not fewer than the session's item count; `end_practice` stores it in the summary for non-`class` sessions.
80. **The composed document uses the `artifact` bridge until slice C** gives the app `bridge_app_frame.js`; `start_practice`, `next_exercise` and `show_exercise` return `document: null` in `text` mode.

## Global Constraints

As the slice A plan, plus:
- `practice_sessions`, `practice_plans` and `practice_items` are user-scoped with forced RLS; `practice_items` references `practice_sessions (id, user_id)`; the report role gets nothing.
- The first answer wins: `record_result` is idempotent per `(session, item)`; a repeat returns the stored result with `outcome: existing`.
- Audit events with ids and enums only: `practice_plan_created`, `practice_started`, `practice_ended`, `practice_incomplete`, and `notes_searched` (ruling 69; ids, a count and a flag).
- (16) Metrics hold no learner text: `ProgressRepo` never selects `answer`, `prompt_text`, `params` or note text; concept and template names are returned to their owner as data only.

## Review Focus

1. **Missed sessions arithmetic:** a plan of 3 sessions/week started 10 days ago with 2 closed sessions reports `expected 3`, `missed 1`, and with a target 4 weeks out reports the projected end shifted by one week; an open-ended plan reports `projected_end: null`. Task 1.
2. **Changing sessions per week** creates version 2 and `get_subjects` shows `missed 0` right after. Task 2.
3. **A subject with no notes yet** and `exercise_kinds = [quiz, exercise]`: `start_practice` gives `validation_failed` on `subject_id` with the hint in the rules ("add notes first"); with `ear` enabled it starts with ear items only. Tasks 1 and 2.
4. **Answer shapes:** `{kind: "quiz", option_index: 7}` on a 4-option item, `{kind: "note_id", note: "H"}`, `{kind: "exercise", rating: 5}`, an answer of the wrong kind for the item: each `validation_failed` on the right field, nothing stored. Tasks 1 and 4.
5. **Two users with sessions open at once** never see each other's items; `record_result` with another user's `item_id` is `not_found`. Task 3.
6. **(16) Time is the server's:** an item shown at 10:00:00 and answered at 10:00:41 times 41 s; a second `next_exercise` at 10:00:30 does not move `shown_at`; an item answered 601 s after showing is `over_cap` and not in the percentiles; `record_result` on an item never returned is `validation_failed` on `item_id`; a self-report is never timed. Tasks 1, 2 and 3.
7. **(16) Weakest first, partial credit:** a concept with 3 recent `exact_sequence` showings scored 67, 67 and 100 has `mean_score 78` and `partial 2`, and sorts before a ranked concept at 100; a concept with one attempt scored 0 is `few_attempts` and sorts after every ranked one; a concept with no showing is `not_practised`, last. Task 1.
8. **(16) Streak across midnight and timezones:** a learner in `America/Mexico_City` who answers at 23:50 local and at 00:10 local the next day has 2 days, although both instants fall on one UTC date; `longest_days` survives a later gap. Tasks 1 and 3.
9. **(16) No query, no answer in metrics:** after `search_notes(query="<script>secret</script>")` the `audit_log` row and every log line lack the query; `ProgressRepo`'s statements select no `answer` column (a test reads the compiled SQL). Tasks 2 and 3.

## File structure

```
src/tutor/domain/practice_plan.py   # T1 PlanInputs, PlanStatus, expected/missed/projected, plan_inputs_changed
src/tutor/domain/practice.py        # T1 banks, mix, draw_items, grade, Answer union, caps
src/tutor/domain/subject_metrics.py # T1 (16) facts, accuracy, time stats, longest streak, weekly counts, subject_progress
src/tutor/services/ports.py         # T2 PracticePlanRow, PracticeSessionRow, PracticeItemRow, PracticeRepo
src/tutor/services/subject_views.py # T2 PlanStatus real, PracticeStart, NextItem, Graded, PracticeSummary
src/tutor/services/subjects.py      # T2 save_subject creates/versions plans; get_subjects fills plan_status
src/tutor/services/practice.py      # T2 start_practice, next_exercise, record_result, end_practice; (16) mark_shown calls
src/tutor/services/progress.py      # T2 (16) subject_progress
src/tutor/services/notes.py         # T2 (16) notes_searched audit event in search_notes
src/tutor/services/memory.py        # T2 records and MemoryPracticeRepo
src/tutor/db/tables.py, db/repos/practice.py, db/uow.py   # T3
src/tutor/db/repos/progress.py      # T3 (16) PgProgressRepo
alembic/versions/0007_practice.py   # T3 (written once)
src/tutor/mcp/schemas.py, rules.py, instructions.py, server.py  # T4
tests/unit/domain/test_practice_plan.py, test_practice.py       # T1
tests/unit/domain/test_subject_metrics.py                       # T1 (16)
tests/unit/services/test_progress_service.py                    # T2 (16)
tests/repo_contract.py, tests/unit/services/test_practice_service.py  # T2
tests/integration/test_migration_0007.py, test_db_schema.py     # T3
tests/unit/mcp/test_mcp_practice.py and the contract test files # T4
tests/unit/mcp/test_mcp_progress.py                             # T4 (16)
docs: spec Amendments (A28–A38, A63–A71)                        # T5
```

## Task index

| # | Task | Reviewers (controller) |
| --- | --- | --- |
| 1 | Domain: plan arithmetic, banks, mix, drawing, grading; (16) subject metrics | — |
| 2 | Ports, memory, services: plans in `save_subject`/`get_subjects`, the practice service; (16) `ProgressRepo`, `subject_progress`, `shown_at`, `notes_searched` | security-reviewer |
| 3 | Migration 0007, tables, Postgres repo; (16) `shown_at`, column grant, `PgProgressRepo` | security-reviewer |
| 4 | MCP: four tools, text mode; (16) `get_subject_progress` | mcp-contract-reviewer |
| 5 | Docs, amendments, phase audit | phase-auditor |

---

### Task 1: Domain: plan arithmetic, banks, mix, drawing, grading

**Files:** create `src/tutor/domain/practice_plan.py`, `src/tutor/domain/practice.py`; tests `tests/unit/domain/test_practice_plan.py`, `tests/unit/domain/test_practice.py`.

**Interfaces (produces):**
- `practice_plan`: frozen `PlanInputs(minutes_per_session, sessions_per_week, target_date)`; `total_sessions(inputs, start_date) -> int | None`; frozen `PlanStatus(version, start_date, sessions_per_week, minutes_per_session, target_date, total_sessions, completed, expected, missed, projected_end, delay_days, adherence)`; `plan_status(inputs, *, version, start_date, completed, today) -> PlanStatus` per spec 6.1 and ruling 28; `plan_inputs_changed(old: PlanInputs, new: PlanInputs) -> bool`.
- `practice`: `ItemKind = Literal["note_id","interval_id","quiz","exercise"]`; `NOTE_BANK`, `INTERVAL_BANK` (ruling 32); `MIX: Mapping[Preference, Mapping[ExerciseKind, int]]` (percentages from spec 6.2); `item_count(minutes) -> int`; `plan_mix(preference, enabled: Collection[ExerciseKind], count, *, text_mode) -> dict[ExerciseKind, int]` (ruling 34, 35); frozen candidates `QuizCandidate(item_id, last_asked_at)`, `ExerciseCandidate(exercise_id, last_practised_at)`; `draw_items(seed: UUID, mix, quizzes, exercises) -> tuple[DrawnItem, ...]` where `DrawnItem(seq, kind, prompt: Mapping[str, object])` and prompts have fixed shapes (`{"note": "A4", "hz": 440.0, "options": [...]}`, `{"interval": "P5", "root_hz": 261.63, "hz": 392.0, "options": [...]}`, `{"quiz_item_id": "...", "question": ..., "options": [...]}`, `{"exercise_id": "...", "name": ..., "instructions": ..., "tempo_bpm": ..., "duration_min": ...}`); `Answer` tagged union of four frozen dataclasses and `parse_answer(kind: ItemKind, data: Mapping[str, object]) -> Answer` (ruling 33); `grade(item: DrawnItem, answer: Answer, *, quiz_answer_index: int | None) -> Graded(correct: bool | None, score: int)`.
- Determinism: `draw_items` with the same seed and candidates returns the same tuple (property test).
- (16) `subject_metrics` (spec 16.7, rulings 64–68): frozen `ShowingFact(instance_id, template_id, concept_id, grading_kind, correct, score, shown_at, answered_at)`, `SessionFact(started_at, status, last_activity_at)`, `ConceptFact(id, name, name_norm)`, `TemplateFact(id, name, grading_kind, status)`, `ActivityFacts(notes, templates, instances, searches: tuple[datetime, ...])`; constants `ITEM_TIME_CAP_SECONDS = 600`, `RECENT_DAYS = 14`, `WEEKS_SHOWN = 8`, `RANK_MIN_ATTEMPTS = 3`, `STALE_OPEN_SESSION = timedelta(hours=24)`, `MAX_LISTED = 20`; `ConceptRank = Literal["ranked","few_attempts","not_practised"]`; `round_half_up(total, n) -> int`; `percentile_nearest_rank(sorted_values, p) -> int`; `time_stats(durations, cap) -> TimeStats(timed, over_cap, median_seconds, p90_seconds)`; `Accuracy(attempts, correct, partial, mean_score, accuracy)`; `concept_accuracy(showings, concepts, *, recent_from) -> tuple[ConceptProgress, ...]` (ordered per spec 16.3) plus `unassigned`; `longest_streak_days(moments, tz) -> int`; `weekly_counts(activity, sessions, *, today, tz, now) -> tuple[WeekCounts, ...]`; `subject_progress(showings, practice_days, sessions, activity, concepts, templates, unscoped_searches, *, now, tz) -> SubjectProgress` (calls the reused `tutor.domain.metrics.streak_days` and `tutor.domain.dashboard.home.week_start`).

- [ ] **Step 1:** failing tests (`# Review Focus 1`, `# Review Focus 3` for the mix with no quizzes/exercises, `# Review Focus 4` for `parse_answer`, determinism, every grade path, banks' frequencies, `item_count` bounds; (16) `# Review Focus 6`, `7` and `8`, and the domain list of spec 16.12).
- [ ] **Step 2:** implement; `uv run just fmt && uv run just check-fast`; coverage ≥ 95% on the three modules.
- [ ] **Step 3:** commit `feat(domain): practice plan arithmetic, item drawing, grading and subject metrics`.

### Task 2: Ports, memory, services

**Files:** modify `ports.py`, `subject_views.py`, `subjects.py`, `memory.py`; create `services/practice.py`; tests `repo_contract.py`, `tests/unit/services/test_practice_service.py`, `test_subjects_service.py` (plans).

**Interfaces:**
- `PracticeRepo`: `active_plan(subject_id) -> PracticePlanRow | None`, `create_plan(subject_id, inputs, start_date, now, rationale) -> PracticePlanRow` (supersedes the previous), `completed_count(plan_id) -> int`, `open_session() -> PracticeSessionRow | None`, `create_session(subject_id, plan_id, mode, items: Sequence[DrawnItem], now) -> PracticeSessionRow` (raises `LookupError` for a foreign subject), `mark_incomplete(session_id, now)`, `session(session_id)`, `items(session_id) -> tuple[PracticeItemRow, ...]`, `record(session_id, item_id, answer, graded, now) -> PracticeItemRow | None` (None when already answered), `close(session_id, summary, self_rating, now)`, `quiz_candidates(subject_id)`, `exercise_candidates(subject_id)`, `quiz_answer_index(quiz_item_id) -> int`.
- Services: `practice.start_practice(svc, user_id, subject_id, mode) -> PracticeStart(session_id, first: NextItem | None, item_count, short)`, `next_exercise(svc, user_id, session_id) -> NextItem` (`done: bool`, item fields without the answer), `record_result(svc, user_id, session_id, item_id, answer_data) -> Graded(outcome: new|existing, correct, score, explanation for quiz)`, `end_practice(svc, user_id, session_id, self_rating) -> PracticeSummary(items, answered, unanswered, accuracy_by_kind, minutes, plan_status)`.
- `subjects.save_subject` creates the first plan and new versions (ruling 29); `get_subjects` fills `plan_status` from the active plan and the user's timezone.
- (16) `PracticeRepo.mark_shown(item_id, now) -> datetime` (ruling 63); `PracticeItemRow` gains `shown_at`; `record_result` refuses an unshown item. `ProgressRepo` (ruling 70) on the `UnitOfWork` as `uow.progress`, with `MemoryProgressRepo` over the memory store (it applies the ruling 65 read-time fallback exactly as SQL does, while that fallback exists). `services.progress.subject_progress(svc, user_id, subject_id) -> SubjectProgress` (subject check, then the facts, then `subject_metrics.subject_progress` with `svc.clock()` and `user_zone(uow)`). `services.notes.search_notes` records `notes_searched` (ruling 69); `AuditEvent` gains it.

- [ ] **Step 1:** failing tests (`# Review Focus 2`, `# Review Focus 3`, `# Review Focus 5` in `RepoContract`, idempotent `record`, incomplete-on-restart, summary arithmetic; (16) `# Review Focus 6` and `9`, `RepoContract` for every `ProgressRepo` method on the spec 16.12 fixture, `mark_shown` first-wins, Beto's facts never in Ana's result).
- [ ] **Step 2:** implement; checks green.
- [ ] **Step 3:** commit `feat(services): practice plans and sessions`.
- [ ] **Step 4 (controller):** `security-reviewer`.

### Task 3: Migration 0007, tables, Postgres repo

**Files:** create once `alembic/versions/0007_practice.py`; modify `tables.py`, `uow.py`; create `db/repos/practice.py`; tests `tests/integration/test_migration_0007.py`, `test_pg_contract.py` (inherits), `test_db_schema.py` (seed rows, composite FK count).

Migration: `practice_plans` (`version`, `status` active|superseded, `start_date`, `minutes_per_session`, `sessions_per_week`, `target_date`, `total_sessions`, `rationale` JSONB), `practice_sessions` (`subject_id`, `plan_id`, `mode` app|text, `status`, `started_at`, `ended_at`, `self_rating`, `summary` JSONB, `item_count`, partial unique open index), `practice_items` (`session_id`, `seq`, `kind`, `prompt` JSONB, `answer` JSONB, `correct`, `score`, `answered_at`, `source` app|chat, unique `(session_id, seq)`); composite FKs to `subjects`, `practice_plans`, `practice_sessions`; RLS, policies, grants (`practice_items`: no `DELETE`); downgrade drops the three tables.

(16) `practice_items.shown_at timestamptz NULL` with `CHECK (answered_at IS NULL OR (shown_at IS NOT NULL AND answered_at >= shown_at))` named `ck_practice_items_shown_before_answer`; the `tutor_app` grant on `practice_items` is `SELECT, INSERT, UPDATE (shown_at, answer, correct, score, answered_at, source)` (column-level; no update of `instance_id`, `draw_seed`, `draw`, `seq` or `session_id`). `PgProgressRepo` in `db/repos/progress.py`: each statement filters `user_id = :uid` on every table it reads; `practice_days` groups by `(answered_at AT TIME ZONE :tz)::date` and returns `min(answered_at)` per day; `searches` reads `audit_log` by `(user_id, at)` and `meta->>'subject_id'`. No new index (spec 16.10).

- [ ] **Step 1:** failing tests (round trip, one open session per user enforced by the index, isolation; (16) the `shown_at` check, `InsufficientPrivilege` on updating `instance_id`, `draw_seed` or `draw`, `# Review Focus 8` on Postgres, `PgProgressRepo` through the inherited `RepoContract`).
- [ ] **Step 2:** implement; `uv run just test-int` green.
- [ ] **Step 3:** commit `feat(db): migration 0007 practice plans, sessions and items`.
- [ ] **Step 4 (controller):** `security-reviewer`.

### Task 4: MCP: four tools, text mode

**Files:** modify `schemas.py`, `rules.py`, `instructions.py`, `server.py`; tests `tests/unit/mcp/test_mcp_practice.py` plus the contract files.

- Inputs: `StartPracticeInput(subject_id, mode: Literal["app","text"] = "app")`, `NextExerciseInput(session_id)`, `RecordResultInput(session_id, item_id, answer: QuizAnswer | NoteAnswer | IntervalAnswer | ExerciseAnswer` discriminated on `kind`)`, `EndPracticeInput(session_id, self_rating)`. Outputs with `response_rules`; the item in every output carries a `text` line that states it in words (quiz question with numbered options; exercise name, instructions, tempo; ear items state the note or interval name only in `app` mode, since text mode never draws them).
- Response rules (2–4 lines): `start_practice` and `next_exercise`: "Present the item exactly as given. Record the learner's answer with `record_result`; never grade it yourself. If the interactive view did not appear, present it in text."; `record_result`: "Say whether it was correct in one sentence and move to `next_exercise`."; `end_practice`: "Read the summary in two sentences. If `plan_status.missed` is above 0, say the new end date without blame."
- `_meta.ui.resourceUri` is **not** set in this slice (slice C adds it with the resource).
- (16) `GetSubjectProgressInput(subject_id: UUID)` with `extra="forbid"`; `SubjectProgressOutput` exactly as spec 16.8 (nested models for `streak`, `overall`, `Accuracy`, `Timing`, `ConceptProgress`, `TemplateTiming`, `WeekCounts`), every field with `title` and `description`, `concept_rank` closed; lists capped at 20 (`max_length` on the output too). Description and `response_rules` are the spec 16.8 texts (77 words; 3 lines). `record_result`'s unshown-item error carries the hint "call next_exercise first" in the error, not in `response_rules`.

- [ ] **Step 1:** failing tests (`# Review Focus 4` through the in-process client, rules line counts, enum sets with the ruling 37 names, descriptions ≤ 120 words, instructions ≤ 400 words; (16) the `get_subject_progress` contract of spec 16.12: unknown field rejected, `not_found` for Beto's subject, caps of 20, `summary_text` and `response_rules` free of a fixture concept name and template name, the tool count of 25).
- [ ] **Step 2:** implement; checks green.
- [ ] **Step 3:** commit `feat(mcp): practice tools in text mode and get_subject_progress`.
- [ ] **Step 4 (controller):** `mcp-contract-reviewer`.

### Task 5: Docs, amendments, phase audit

- [ ] Spec `## Amendments` A28–A38 and A63–A71 (section 16 already holds the metrics design); `docs/v0/runbook.md` cascade comment; `uv run just check`; **(controller)** `phase-auditor`; commit `docs(subjects): slice B amendments`.

## Notes for the controller

- Slice C (the `ui://tutor/practice/v1/app.html` resource, `meta` on the two app tools, the handshake, Web Audio, the in-process resource test) starts only after spike 05 has a Setup section filled and the author has run at least the claude.ai and Desktop attempts; it is a one-task plan.
- Slice D (website: Materias, Materia, Nota, raw artifact route with the header set, PDF upload with `pypdf`) is written after slice A ships; it adds the only new dependency.
