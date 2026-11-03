# Subjects Slice A: Subjects, Notes, Search and Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a learner creates a subject through chat, Claude uploads lesson notes in batches (text, concepts, exercises, quiz items) and HTML lesson views, and Claude answers questions from a deterministic search over those notes. Seven new MCP tools, one new migration, no change to any English tool or table. Practice (slice B), the website pages (slice D) and the MCP App (slice C, after spike 05) come later.

**Architecture:** new bounded context `subjects` beside the English tutor. `tutor.domain.subjects` (validation, questions), `tutor.domain.notes` (clean text, passages, normalisation, idempotency key, concept names), `tutor.domain.html_guard` (artifact deny list); ports `SubjectRepo` and `NoteRepo` on the `UnitOfWork`; services `tutor.services.subjects` and `tutor.services.notes`; migration `0006_subjects_and_notes` with Postgres full-text search (two configurations with `unaccent`), `pg_trgm` and the concept graph; MCP tools `get_subjects`, `save_subject`, `save_notes`, `save_artifact`, `get_artifact`, `search_notes`, `get_note`.

**Tech Stack:** Python 3.12, uv, FastMCP 4.0.11, SQLAlchemy 2 Core + psycopg 3, Alembic, PostgreSQL 16 (`unaccent`, `pg_trgm`), pytest + Hypothesis. No new runtime dependency in this slice.

**Spec:** `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md` (the authority) sections 3, 4, 5, 7.1 (first seven tools), 7.3, 8.1, 8.3, 10, 11. ADR: `docs/adr/0004-subjects-context-beside-english.md`.

**Execution:** subagent-driven (CLAUDE.md workflow). Implementers never dispatch subagents; review steps marked **(controller)** run after the implementer reports.

**Branch:** `feat/subjects-v0` (already holds the docs), from `main`.

## Before Task 1

1. `uv run just check-fast` passes on the branch (it did on 2026-11-02: 1,268 unit tests).
2. Read, in this order: the spec sections above; `src/tutor/domain/profile.py` (validation and onboarding-question shapes to mirror); `src/tutor/services/ports.py`, `src/tutor/services/glossary.py`, `src/tutor/services/memory.py`, `src/tutor/db/tables.py`, `src/tutor/db/repos/glossary.py`, `src/tutor/db/uow.py`, `alembic/versions/0001_core_schema.py` (helpers `_id`, `_user_id`, `_one_of`, `_max_len`, RLS and grant blocks), `0002_tenant_safe_references.py` (composite FK pattern), `tests/repo_contract.py`, `tests/integration/test_db_schema.py`, `src/tutor/mcp/server.py`, `schemas.py`, `rules.py`, `instructions.py`, `tests/unit/mcp/test_mcp_server.py`, `test_mcp_schemas.py`, `test_mcp_rules.py`, `.claude/skills/mcp-tool-authoring/SKILL.md`.
3. No new MCP SDK, OAuth or Stripe API is used in this slice (plain FastMCP tool registration as today), so no Context7 lookup is needed. Slice C will need one for `fastmcp.apps`.

## Rulings against the spec

Decided while planning, from the code. Task 5 records them in the spec's new "## Amendments" section.

1. **Enum field names never collide with English ones.** `tests/unit/mcp/test_mcp_schemas.py` pins exact output enum sets keyed by field name, so new enum fields use names of their own: `subject_status` (`active|paused|archived`), `outcome` (`new|existing|superseded`) for note results, `source_kind`, `artifact_kind`, `exercise_kind`, `link_kind`, `preference`, `exercise_kinds`. No new field is named `status`, `kind` or `domain`.
2. **In-memory search is a model of the SQL, not a copy.** Memory tokenises `clean_text` and the query with `name_norm` rules (NFKD, marks stripped, casefold) and matches a passage when every query token appears as a whole token; no stemming, no trigram. The shared `RepoContract` search fixture uses exact word forms and exact concept names only, and compares note ids and their order, never excerpts. Stemming, `websearch` syntax, trigram fallback and `ts_headline` are pinned by Postgres integration tests only.
3. **Memory excerpts** are the first 25 words of the first matching passage with `[[`/`]]` around the first matched token; Postgres excerpts come from `ts_headline`. Both are plain text.
4. **Concept links are directed from the note's perspective.** `links[].from` and `links[].to` are concept names of the same subject; both are upserted if missing; `prerequisite` means `from` is required before `to`.
5. **`get_subjects` returns `plan_status: null`** for every subject in this slice; the field and its dataclass exist with the spec's shape so slice B fills it without a schema change.
6. **`save_subject` does not create a plan** in this slice (no `practice_plans` table yet). `plan_changed` is returned as `false` always; slice B gives it meaning. `minutes_per_session`, `sessions_per_week` and `target_date` are still validated and stored on `subjects`.
7. **One migration file, written once.** `protect_paths` blocks edits to any existing file under `alembic/versions/`, so Task 3 writes `0006_subjects_and_notes.py` with a single `Write`; to change it before the commit, `rm` the file and write it again. The extensions and text search configurations are created with `IF NOT EXISTS` / guarded `DO` blocks so the migration is re-runnable on `db-test`.
8. **Composite FK count.** `tests/integration/test_db_schema.py` asserts exactly 7 `%_user_id_fkey` constraints; Task 3 updates the number to 7 plus the new ones and lists them.
9. **`raw_text` immutability** on Postgres is a column-level grant (no `UPDATE` on `raw_text`, `raw_sha256`, `raw_had_nul`, `idempotency_key`, `origin`, `created_at` for `tutor_app`); the port has no method that updates them, and `RepoContract` pins that a second `save_notes` with the same key returns `existing` and leaves the row unchanged in both backends.
10. **Caps live in the domain** as constants (`MAX_NOTES_PER_CALL = 4`, `MAX_NOTE_CHARS = 6000`, `MAX_WEB_NOTE_CHARS = 400_000`, `MAX_CALL_CHARS = 16_000`, `MAX_CONCEPTS = 12`, `MAX_LINKS = 12`, `MAX_EXERCISES = 10`, `MAX_QUIZ_ITEMS = 10`, `MAX_ARTIFACT_BYTES = 262_144`, `MAX_SEARCH_LIMIT = 10`), re-stated as `max_length`/`le` in the MCP schemas and as `CHECK`s in the migration.
11. **`get_note` text window.** `clean_text` is returned whole up to 12,000 characters; above that `truncated` is `true` and Claude reads passages with `passage_from`/`passage_to` (1-based, inclusive, at most 10 per call).
12. **Error codes.** `ErrorCode` gains `not_found` (foreign or missing subject, source, note, artifact) and `limit_exceeded` (caps on counts and total characters; single-field length caps stay `validation_failed`). `rules.ERRORS` covers both.

## Rulings recorded during execution

13. **The domain returns errors; the service raises `ServiceError`** (the `validate_profile` convention: `Profile | tuple[FieldError, ...]`). Task 1 wrongly made `tutor.domain.subjects` and `tutor.domain.notes` raise `ServiceError` imported from `tutor.services.errors`; Task 2 removes that import: `validate_subject` returns `Subject | tuple[FieldError, ...]`, `validate_notes` returns `ValidNotes | NotesErrors` where `NotesErrors(code: Literal["validation_failed", "limit_exceeded"], errors: tuple[FieldError, ...])`, and `tutor.services.subjects`/`notes` map them to `ServiceError(code, fields)` with field paths deduplicated in order. `FieldError` for subjects and notes lives in `tutor.domain.subjects` (`SubjectFieldError`) and `tutor.domain.notes` (`NoteFieldError`) with their own code Literals, or a shared `tutor.domain.errors.FieldError` if that is simpler; no `tutor.domain` module imports `tutor.services`.
14. **Task 1 decisions, accepted** (Task 5 copies them into the spec's Amendments): defaults `exercise_kinds = ("quiz", "exercise")` and `status = "active"`; names, titles, lesson refs, concept and exercise names are stripped with inner whitespace collapsed, free texts stripped only, empty optionals become `None`, lengths measured after that; the `time` question prompt adds the ranges; `clean_text` turns lone `\r`, form feed and vertical tab into `\n`, removes only `Cc` characters (keeps `Cf`, so emoji sequences survive), treats NBSP as horizontal whitespace, trims every line, joins hyphen + newline only between letters, applies NFC before and after; `split_passages` heading rule (markdown `#`, or ≤ 80 chars, ≤ 10 words, no list marker, not ending in `.`/`,`/`;`, uppercase or digit start, and the next non-blank line is not heading-like), lone headings glued to the next paragraph, greedy merge to 1,200, hard split at the latest sentence or line end in the second half of the 2,000 window; `raw_text` hashed after NUL stripping; `validate_notes` order: count caps first (`notes`, then per-note `concepts|links|exercises|quiz_items`), then every field in payload order, source first; MCP field paths (`notes[i].links[j].from`); concept dedup by `name_norm` keeping the first spelling, duplicate links dropped, link endpoints and quiz concepts returned as `ConceptName` but not added to the note's concept list; enum names `NoteExerciseKind`, `ArtifactKind` in `html_guard`; `check_html` reports `too_large` alone, `not_document` beside other problems, forbidden tags once without attribute inspection, `meta[http-equiv]` as `tag_forbidden`, CSS tokens matched after lowercasing and whitespace removal, `data:` in any attribute as `url_forbidden`, `<script>` content not scanned.
15. **`rules.ERRORS` entries for `not_found` and `limit_exceeded`** were added in Task 1 because `test_mcp_rules.py` requires every `ErrorCode` to be covered; Task 4 rewords them.

## Global Constraints

- Every command runs as `uv run …`; `uv run just fmt` before `uv run just lint`; `uv run just check-fast` green after every task (the Stop hook runs it); a task is done when `uv run just check` passes (lint, mypy, all non-eval tests including integration on `db-test` 5433, ≥ 90% coverage on `src/tutor/domain/*`, pip-audit).
- `tutor.domain` is pure (no I/O, clock, environment or logging) and mypy-strict; the HTML guard uses only `html.parser` from the standard library.
- MCP contract (CLAUDE.md, spec 7): closed enums, `extra="forbid"` at every level, `title` and `description` on every field, tool descriptions ≤ 120 words, every result has `response_rules`, tool names stable, instructions ≤ 400 words.
- The server computes: idempotency, normalisation, passages, search order and excerpts, caps. No LLM call on the server. Search is deterministic: same database, same query, same result.
- Every query is filtered by the token's user; every new table has `user_id`, forced RLS and `_own_rows`; repos check ownership before writes that reference another row (`_guards.py` pattern); the report role gets no grant on the new tables.
- Learner text is data: names, goals, context, note text, concept names, exercises, quiz items, artifact HTML and search queries are stored and returned as data, never logged, never in `response_rules`, instructions or error messages, never marked safe in templates.
- Hooks: no edits to `.env*`, `docs/requirements.md` or existing files under `alembic/versions/`.
- `uv` is not on the Bash tool's PATH on this machine: prefix `export PATH="/c/Users/jemtl/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"`.
- Commit messages: `type(scope): summary`, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never `--no-verify`. Never push.

## Review Focus

Inputs the author will hit that the spec implies but its listed tests do not pin. Each line has a test in the owning task, marked `# Review Focus N`.

1. **The same lesson uploaded twice, then uploaded again with one typo fixed:** second call returns `existing` with the original note id; third returns `superseded` with a new note whose `supersedes_note_id` is the first, and both notes are searchable. Tasks 2 and 3.
2. **A note of exactly 6,000 characters with accents, emoji and a NUL byte:** accepted through MCP (code points, not bytes), NUL stripped, `raw_had_nul` true, passages cover the text. Tasks 1, 3 and 4.
3. **A query in Spanish over notes written in Spanish with English terms** ("acorde de séptima en el quinto traste", "power chord") finds the right lessons on Postgres with and without accents, and a misspelled concept ("pentatonca") still finds the concept by trigram with `fuzzy: true`. Task 3.
4. **Artifacts that try to escape:** an `<iframe>`, a `<meta http-equiv="refresh">`, an `<a href="https://…">`, an `<img src="data:…">`, a `style` with `url(` are all rejected with the field and a code; an artifact with only inline `<script>` and `<style>` and `#fragment` links is accepted. Tasks 1 and 4.
5. **Foreign rows:** `save_notes` with another user's `subject_id` or `source_id`, `save_artifact` with another user's `note_id`, `get_note`/`get_artifact`/`search_notes` with another user's ids all give `not_found` and write nothing. Tasks 2, 3 and 4.
6. **Caps:** 5 notes in one call, 16,001 total characters, 13 concepts, 11 quiz items, 3 options of 201 characters, an artifact of 262,145 bytes: each rejected with the right code and field, nothing stored (one transaction per call). Tasks 2 and 4.

---

## File structure

```
src/tutor/
  domain/subjects.py        # T1  Subject, SubjectInput, validate_subject, SUBJECT_QUESTIONS, enums, caps
  domain/notes.py           # T1  clean_text, split_passages, name_norm, idempotency_key, NoteInput and nested inputs, validate_notes
  domain/html_guard.py      # T1  check_html, HtmlProblem, MAX_ARTIFACT_BYTES
  services/ports.py         # T2  SubjectRow, NoteRow, …, SubjectRepo, NoteRepo, UnitOfWork.subjects/.notes
  services/subject_views.py # T2  result dataclasses for the seven tools
  services/subjects.py      # T2  save_subject, get_subjects
  services/notes.py         # T2  save_notes, save_artifact, get_artifact, get_note, search_notes
  services/memory.py        # T2  records, Tables, MemorySubjectRepo, MemoryNoteRepo (incl. search), UoW wiring
  services/errors.py        # T2  not_found, limit_exceeded
  db/tables.py              # T3  ten tables, USER_TABLES
  db/repos/subjects.py      # T3  PgSubjectRepo
  db/repos/notes.py         # T3  PgNoteRepo (incl. the search statement)
  db/uow.py                 # T3  wiring
  mcp/schemas.py, rules.py, instructions.py, server.py  # T4
alembic/versions/0006_subjects_and_notes.py   # T3 (written once)
tests/unit/domain/test_subjects.py, test_notes.py, test_html_guard.py        # T1
tests/repo_contract.py, tests/unit/services/test_subjects_service.py, test_notes_service.py  # T2
tests/integration/test_migration_0006.py, test_pg_notes_search.py, test_db_schema.py  # T3
tests/unit/mcp/test_mcp_subjects.py and the three contract test files           # T4
docs: spec Amendments, runbook cascade comment                                 # T5
```

## Task index

| # | Task | Reviewers (controller) |
| --- | --- | --- |
| 1 | Domain: subjects, notes, HTML guard | — |
| 2 | Ports, memory store, views and services | security-reviewer |
| 3 | Migration 0006, tables, Postgres repos, search | security-reviewer |
| 4 | MCP surface: seven tools | mcp-contract-reviewer |
| 5 | Docs, amendments, phase audit | phase-auditor |

---

### Task 1: Domain: subjects, notes, HTML guard

Spec 3.1, 3.2, 4.1–4.4 (rules only), rulings 1, 10.

**Files:**
- Create: `src/tutor/domain/subjects.py`, `src/tutor/domain/notes.py`, `src/tutor/domain/html_guard.py`
- Test: `tests/unit/domain/test_subjects.py`, `tests/unit/domain/test_notes.py`, `tests/unit/domain/test_html_guard.py`

**Interfaces (produces):**
- `tutor.domain.subjects`: `Preference = Literal["practice_first","theory_first","balanced"]`, `ExerciseKind = Literal["quiz","ear","exercise"]`, `SubjectStatus = Literal["active","paused","archived"]`, tuples `PREFERENCES`, `EXERCISE_KINDS`, `SUBJECT_STATUSES`; caps `NAME_MAX = 80`, `GOAL_TEXT_MAX = 300`, `CONTEXT_TEXT_MAX = 600`, `MINUTES_MIN/MAX = 10/120`, `SESSIONS_MIN/MAX = 1/7`; frozen `Subject(name, goal_text, context_text, preference, exercise_kinds: tuple[ExerciseKind, ...], minutes_per_session, sessions_per_week, target_date, status)`; `SubjectInput` (same fields as `str | int | None` where they come from outside, `exercise_kinds: Sequence[str]`); `validate_subject(data: SubjectInput, today: date) -> Subject` raising `ServiceError("validation_failed", fields)` in the error order name, goal_text, context_text, preference, exercise_kinds, minutes_per_session, sessions_per_week, target_date, status (`target_date` < today is `invalid`); `SubjectQuestion(id, fields, prompt_es, prompt_en, options: tuple[QuestionOption, ...] | None, optional: bool)` and `SUBJECT_QUESTIONS` with the seven ids of spec 3.2 in that order (mirror `OnboardingQuestion` in `profile.py` for the shape).
- `tutor.domain.notes`: caps from ruling 10; `name_norm(text) -> str`; `clean_text(raw) -> str` (NFC, control chars except `\n`/`\t` removed, `\r\n` → `\n`, runs of spaces/tabs collapsed, more than two newlines collapsed to two, hyphen + newline between letters joined, strip); `strip_nul(raw) -> tuple[str, bool]`; `split_passages(text) -> tuple[str, ...]` per spec 4.3 with `PASSAGE_TARGET = 1200`, `PASSAGE_HARD = 2000`; `idempotency_key(subject_id: UUID, source_id: UUID | None, lesson_ref: str | None, raw_text: str) -> str` (hex sha256 over the four parts joined with `\x1f`, `raw_text` hashed first); frozen inputs `ConceptLinkInput(from_name, to_name, kind)`, `ExerciseInput(name, instructions, kind, tempo_bpm, duration_min)`, `QuizItemInput(question, options, answer_index, explanation, concept)`, `NoteInput(title, lesson_ref, position, raw_text, concepts, links, exercises, quiz_items)`, `SourceInput(kind, title, url)`; `validate_notes(source: SourceInput | None, notes: Sequence[NoteInput], *, origin: Literal["mcp","web"]) -> ValidNotes` enforcing every cap (count caps and total characters raise `ServiceError("limit_exceeded", (field,))`, lengths raise `validation_failed` with the field path `notes[2].quiz_items[0].options`), normalising concept names (`name_norm` dedup within a note, self-links rejected), and returning cleaned text, passages, key and `raw_had_nul` per note.
- `tutor.domain.html_guard`: `MAX_ARTIFACT_BYTES = 262_144`; `HtmlProblem(code: Literal["too_large","not_document","tag_forbidden","attr_forbidden","url_forbidden","css_forbidden"], detail: str)` (detail names the tag or attribute, never quotes content); `check_html(html: str) -> tuple[HtmlProblem, ...]` implemented with `html.parser.HTMLParser` (subclass collecting problems; `convert_charrefs=True`), rules exactly as spec 4.4, case-insensitive, `<style>` content and `style=` attributes scanned for `url(`, `@import`, `expression(`.

- [ ] **Step 1: Write the failing tests** (unit, property tests with Hypothesis for `split_passages`: no empty passage, every passage ≤ `PASSAGE_HARD`, concatenation with single spaces contains every non-space character of the input in order; `clean_text` idempotent; `name_norm("Pentatónica Menor") == "pentatonica menor"`; `idempotency_key` differs on any part; `validate_subject` each field and the error order; `SUBJECT_QUESTIONS` ids; `check_html` on the Review Focus 4 cases and `# Review Focus 2` for the 6,000-character note with NUL).
- [ ] **Step 2: Implement** the three modules; `uv run just fmt && uv run just check-fast`; coverage of the three modules ≥ 95% (`uv run pytest tests/unit/domain -q --cov=src/tutor/domain --cov-report=term-missing`).
- [ ] **Step 3: Commit** `feat(domain): subjects, notes and the artifact HTML guard`.

### Task 2: Ports, memory store, views and services

Spec 3, 4, 5 (memory model per ruling 2), 7.1 semantics, rulings 2–6, 9, 12.

**Files:**
- Modify: `src/tutor/services/ports.py`, `src/tutor/services/memory.py`, `src/tutor/services/errors.py`
- Create: `src/tutor/services/subject_views.py`, `src/tutor/services/subjects.py`, `src/tutor/services/notes.py`
- Test: `tests/repo_contract.py` (new methods), `tests/unit/services/test_memory_contract.py` (inherits), `tests/unit/services/test_subjects_service.py`, `tests/unit/services/test_notes_service.py`

**Interfaces:**
- Consumes: Task 1.
- Produces (`ports.py`): frozen rows `SubjectRow(id, subject: Subject, created_at, updated_at)`, `SourceRow(id, subject_id, kind, title, url, created_at)`, `NoteRow(id, subject_id, source_id, title, lesson_ref, position, raw_text, raw_had_nul, clean_text, clean_version, origin, supersedes_note_id, created_at, updated_at)`, `PassageRow(seq, text)`, `ArtifactRow(id, note_id, kind, title, html, bytes, created_at)`, `ExerciseRow`, `QuizItemRow`, `ConceptRow(id, subject_id, name, name_norm)`, `SearchHit(note_id, passages: int, concepts: tuple[str, ...], depth: int | None, fuzzy: bool, excerpts: tuple[str, ...])`.
  `SubjectRepo`: `list() -> tuple[SubjectRow, ...]` (active first, then by name), `get(subject_id) -> SubjectRow | None`, `create(subject, now) -> SubjectRow`, `update(subject_id, subject, now) -> bool`.
  `NoteRepo`: `upsert_source(subject_id, source: SourceInput, now) -> SourceRow` (match on `(subject_id, kind, title)`), `by_idempotency_key(key) -> NoteRow | None`, `latest_by_lesson(subject_id, source_id, lesson_ref) -> NoteRow | None`, `insert(valid_note, *, subject_id, source_id, origin, supersedes, now) -> NoteRow` (writes passages, exercises, quiz items, concepts and links in the same call; raises `LookupError` for a foreign subject or source), `get(note_id) -> NoteRow | None`, `passages(note_id, start, stop) -> tuple[PassageRow, ...]`, `passage_count(note_id) -> int`, `exercises(note_id)`, `quiz_items(note_id)`, `concepts(note_id)`, `artifacts(note_id) -> tuple[ArtifactRow, ...]` (without `html`), `add_artifact(note_id, kind, title, html, now) -> ArtifactRow`, `artifact(artifact_id) -> ArtifactRow | None`, `search(query, subject_id, limit) -> tuple[SearchHit, ...]`.
  `UnitOfWork` gains `subjects: SubjectRepo` and `notes: NoteRepo`.
- Produces (`subject_views.py`): `SubjectsView(subjects: tuple[SubjectView, ...], questions)`, `SubjectView(id, subject, plan_status: PlanStatus | None)`, `PlanStatus` placeholder dataclass with the spec 6.1 fields, `SaveSubjectResult(subject: SubjectView, created: bool, plan_changed: bool)`, `SaveNotesResult(source_id, notes: tuple[NoteOutcome, ...])`, `NoteOutcome(note_id, outcome)`, `ArtifactSaved(artifact_id, web_path)`, `ArtifactView(artifact_id, note_id, kind, title, html)`, `NoteView(…, clean_text, truncated, passage_count, passages, exercises, quiz_items (no answer index), concepts, artifacts)`, `SearchResult(hits: tuple[SearchHitView, ...])`.
- Produces (services): `subjects.save_subject(svc, user_id, data: SubjectInput, subject_id: UUID | None) -> SaveSubjectResult`; `subjects.get_subjects(svc, user_id) -> SubjectsView`; `notes.save_notes(svc, user_id, subject_id, source, notes) -> SaveNotesResult` (one unit of work; outcome per spec 4.1: `existing` by key, `superseded` when the lesson exists with another text, else `new`); `notes.save_artifact(svc, user_id, note_id, kind, title, html) -> ArtifactSaved` (`check_html` first; problems → `validation_failed` on `html` with the first code in the audit meta as an enum); `notes.get_artifact`, `notes.get_note(svc, user_id, note_id, passage_from, passage_to) -> NoteView`, `notes.search_notes(svc, user_id, query, subject_id, limit) -> SearchResult`. Every write records an audit event with ids and enums only (`subject_saved`, `notes_saved` with counts, `artifact_saved`).
- Memory: records and `Tables` fields for every row type; `MemorySubjectRepo`, `MemoryNoteRepo` with search per rulings 2 and 3; `MemoryUnitOfWork` wiring; `memory_uow` snapshot covers the new tables.

- [ ] **Step 1: Write the failing tests:** `RepoContract` methods for subjects (create, list order, update, foreign id → `False`/`None`), notes (insert with everything, key lookup, `# Review Focus 1` supersede, passages window, `# Review Focus 5` foreign subject → `LookupError`, artifacts, search fixture of 12 notes across 2 subjects with 6 queries whose expected id order is listed in the test, concept-graph query reaching a note only through a `related` link at depth 1 and a `prerequisite` at depth 2, subject filter); service tests for every outcome, caps (`# Review Focus 6`, all-or-nothing), `not_found`, audit events, `truncated` window.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast`.
- [ ] **Step 3: Commit** `feat(services): subjects and notes services with the in-memory store`.
- [ ] **Step 4 (controller):** `security-reviewer` on `services/*` (ownership, audit meta, no text in logs).

### Task 3: Migration 0006, tables, Postgres repos, search

Spec 8.1, 8.3, 5.1 (the statement), rulings 7–9.

**Files:**
- Create (once): `alembic/versions/0006_subjects_and_notes.py`
- Modify: `src/tutor/db/tables.py` (ten tables, `USER_TABLES`), `src/tutor/db/uow.py`
- Create: `src/tutor/db/repos/subjects.py`, `src/tutor/db/repos/notes.py`
- Test: `tests/integration/test_pg_contract.py` (inherits), `tests/integration/test_migration_0006.py` (new), `tests/integration/test_pg_notes_search.py` (new), `tests/integration/test_db_schema.py` (seed rows, composite FK count), `docs/v0/runbook.md` cascade comment

**Interfaces:** consumes Tasks 1–2; produces `PgSubjectRepo`, `PgNoteRepo` implementing the ports; the search statement per spec 5.1 as one `text()` query with bound parameters (`:uid`, `:sid`, `:q`, `:q_norm`, `:k`), the trigram threshold fixed at 0.3 via `word_similarity` comparison in the statement (`<%` uses the session default; set `pg_trgm.word_similarity_threshold` with `SET LOCAL` inside the unit of work when needed).

Migration content (write it in one go; `rm` and rewrite to change it before committing):
- `CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS pg_trgm;`
- `DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'tutor_es') THEN CREATE TEXT SEARCH CONFIGURATION tutor_es (COPY = spanish); ALTER TEXT SEARCH CONFIGURATION tutor_es ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem; END IF; END $$;` and the same for `tutor_en` from `english`/`english_stem`.
- Tables with `_id()`, `_user_id()` (CASCADE), `_one_of` checks for every enum, length checks for every cap, `UNIQUE (id, user_id)` on `subjects`, `sources`, `notes`, `concepts`, `note_artifacts`; composite child FKs `(subject_id, user_id)`, `(source_id, user_id)`, `(note_id, user_id)`, `(concept_id, user_id)`, `(from_id, user_id)`, `(to_id, user_id)`, `(supersedes_note_id, user_id)` named `{child}_{col}_user_id_fkey`; `notes.idempotency_key` unique per user; `note_passages.tsv tsvector GENERATED ALWAYS AS (to_tsvector('tutor_es', text) || to_tsvector('tutor_en', text)) STORED` with GIN; `concepts` unique `(user_id, subject_id, name_norm)` plus `GIN (name_norm gin_trgm_ops)`; `notes` `GIN (title_norm gin_trgm_ops)`; `note_concepts` PK `(note_id, concept_id)`; `concept_links` PK `(from_id, to_id, kind)` with `CHECK (from_id <> to_id)`; `note_artifacts.bytes` with `CHECK (bytes BETWEEN 1 AND 262144)`.
- RLS enable + force + `_own_rows` policy per table (0001 pattern); grants: `SELECT, INSERT, UPDATE, DELETE` to `tutor_app` on every table except `notes`, which gets `SELECT, INSERT, DELETE` and `UPDATE (title, lesson_ref, position, clean_text, clean_version, title_norm, supersedes_note_id, updated_at)`; `note_passages` gets `SELECT, INSERT, DELETE` only. Nothing for the report role.
- Downgrade: drop the ten tables in dependency order, drop the two configurations; leave the extensions.

- [ ] **Step 1: Write the failing tests:** `test_migration_0006.py` (upgrade/downgrade round trip, configurations exist, `tutor_app` cannot `UPDATE notes SET raw_text` (expect `InsufficientPrivilege`), `EXPLAIN` of the search over 1,000 generated passages contains `Bitmap Index Scan` on the GIN index); `test_pg_notes_search.py` (`# Review Focus 3`: stemming, accents, English terms, `websearch` quoting, trigram fallback with `fuzzy`, `ts_headline` markers present, deterministic order on repeated runs, two users never see each other's notes); `test_db_schema.py` seed rows for the ten tables and the new composite FK count.
- [ ] **Step 2: Implement** `tables.py`, the migration (one `Write`), the repos, the UoW wiring; `uv run just fmt && uv run just check-fast && uv run just test-int`.
- [ ] **Step 3: Commit** `feat(db): migration 0006 subjects and notes with full-text search and the concept graph`.
- [ ] **Step 4 (controller):** `security-reviewer` on the migration and repos (RLS, grants, ownership checks, parameter binding in the search statement).

### Task 4: MCP surface: seven tools

Spec 7.1 (first seven rows), 7.3, ruling 1, 10–12.

**Files:**
- Modify: `src/tutor/mcp/schemas.py` (inputs, outputs, `INPUT_MODELS`, `OUTPUT_MODELS`), `src/tutor/mcp/instructions.py` (`TOOL_DESCRIPTIONS`, instructions paragraph), `src/tutor/mcp/rules.py` (seven rules strings, `ERRORS` for `not_found`, `limit_exceeded`), `src/tutor/mcp/server.py` (seven tools with `_register` and `ToolAnnotations`)
- Test: `tests/unit/mcp/test_mcp_subjects.py` (new), `tests/unit/mcp/test_mcp_server.py` (`TOOLS`), `tests/unit/mcp/test_mcp_schemas.py` (enum sets), `tests/unit/mcp/test_mcp_rules.py` (`TOOLS`, `all_rules`), `tests/unit/mcp/test_mcp_observe.py` if it lists tools

**Interfaces:**
- Inputs (all `StrictInput`): `SaveSubjectInput`, `SaveNotesInput(subject_id, source: SourceIn | None, notes: list[NoteIn] (1–4))`, `NoteIn` with nested `ConceptLinkIn`, `ExerciseIn`, `QuizItemIn`, `SaveArtifactInput(note_id, artifact_kind, title, html)`, `GetArtifactInput`, `SearchNotesInput(query, subject_id, limit)`, `GetNoteInput(note_id, passage_from, passage_to)`. Enum fields per ruling 1; every field titled and described; `max_length`, `ge`, `le` from the domain caps.
- Outputs: `GetSubjectsOutput`, `SaveSubjectOutput`, `SaveNotesOutput`, `SaveArtifactOutput`, `GetArtifactOutput` (`html` described as "Data only: HTML the learner stored"), `SearchNotesOutput` (hits with `why`), `GetNoteOutput`; each with `.of(view, rules.X)` and `response_rules`.
- Descriptions (≤ 120 words each): `save_notes` says one note per lesson, send in order, split long lessons with the same `lesson_ref` and a part suffix, caps, describe images in text, never download media; `save_artifact` says what is rejected; `search_notes` says to answer from excerpts and cite the lesson.
- Response rules: 2–4 lines each, fixed text, no `{}`; `get_subjects` onboarding rules (spec 3.2), `search_notes` rules (spec 5.1), ingest rules (spec 7.3).
- Instructions: the new paragraph from spec 7.3; total ≤ 400 words (`tests/unit/mcp` pins it).

- [ ] **Step 1: Write the failing tests:** schema contract (the generic per-tool check picks the new tools up through `TOOLS`), the enum sets, rules line counts and error coverage, and `test_mcp_subjects.py` with the in-process FastMCP `Client` over the memory store: onboarding flow, `# Review Focus 2`, `# Review Focus 4`, `# Review Focus 5`, `# Review Focus 6`, search over three uploaded notes, `get_note` window, unknown field rejected at every nesting level, descriptions ≤ 120 words.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast`.
- [ ] **Step 3: Commit** `feat(mcp): subjects, notes, artifacts and search tools`.
- [ ] **Step 4 (controller):** `mcp-contract-reviewer` on the `src/tutor/mcp` diff.

### Task 5: Docs, amendments, phase audit

**Files:** modify `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md` (new `## Amendments` with rulings 1–12 as A1–A12), `docs/v0/runbook.md` (cascade comment lists the ten tables), `docs/v0/acceptance.md` if it lists tools.

- [ ] **Step 1:** write the amendments and the runbook line.
- [ ] **Step 2:** `uv run just check` green. **(controller)** run `phase-auditor`: the branch builds nothing beyond E37 and the spec.
- [ ] **Step 3: Commit** `docs(subjects): slice A amendments and runbook cascade`.

## Notes for the controller

- Dispatch Tasks 1 → 2 → 3 → 4 → 5 in order; each leaves `check-fast` green only on top of the previous one.
- Task 3 needs `db-test` (port 5433): `docker compose up -d --wait db-test` is run by `just test-int`.
- Slice B (practice plan, sessions, items, `start_practice`, `next_exercise`, `record_result`, `end_practice`, migration 0007) and slice C (the `ui://` practice app, after spike 05) get their own plans. Slice D is the website.
- The topics plan now owns revision 0008 (its Notes for the controller say so).
