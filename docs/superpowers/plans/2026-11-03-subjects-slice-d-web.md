# Subjects Slice D: Website Pages, Sandboxed Instances and Uploads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the learner sees subjects, notes, template instances, plan status and attendance on the website, adds material by uploading a PDF or pasting text, and retires a template they no longer want. Instances are shown only in a sandboxed frame that loads them from their own URL under a `sandbox allow-scripts` policy with no network and no answer key. No change to any MCP tool or English page.

**Architecture:** a web port `tutor.web.subjects` on `app.state` (the `web/profile.py` pattern) with a Postgres adapter over `Services` and a memory adapter over `MemoryStore`; route modules `routes/subjects.py` (pages, upload, paste, retire) and `routes/instances.py` (the raw frame document); Jinja pages and partials under the existing layout, i18n through the English `.po`; `SecurityHeadersMiddleware` gains one per-route header set for the raw route and `frame-src 'self'` in the dashboard CSP; `tutor.services.pdf_text` wraps `pypdf` (the only new dependency) in a worker with caps.

**Tech Stack:** as slice A, plus `pypdf` (spec S4, S15). FastAPI, Jinja2, htmx 2.0.11 (vendored), Babel, `python-multipart` (already a dependency).

**Spec:** `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md` sections 1 (pasted text), 4.1 (`origin = web`, 400,000 characters), 9, 10 (template bullet, PDFs), 11 (web tests), 15.4.1 (preview draw), 15.5 (`web` bridge), 15.7 (website row), 15.11, 15.13 D9 (the retire button). ADR 0004 decisions 4 and 6, ADR 0005 decision 8. Patterns: `docs/superpowers/plans/2026-10-04-dashboard-web-2-pages.md` "Conventions for page tasks" and Task 29 (route sweep). This plan's rulings are numbered from 49 (slice C ends at 48).

**Execution:** subagent-driven, as slice A. **Branch:** `feat/subjects-v0`.

## Dependencies

- **Slice A Task 4b** (in progress; names follow spec section 15 and may still shift): `TemplateRepo` (`list_active`, `get`), `InstanceRepo` (`get`, `for_note`), `services.templates.get_instance_view` (or the composition helper under it) with the `web` channel, `template_files.bridge("web")`, `public_payload` and the preview draw seeded by the instance id (slice A ruling 26), `services.notes.get_note` with instances, `tutor.domain.notes.MAX_WEB_NOTE_CHARS = 400_000` and `validate_notes(..., origin="web")`, the 0006 grant `exercise_templates UPDATE (status, retired_at, updated_at)` (no migration in this slice).
- **Slice B** (in progress): the real `PlanStatus` from `get_subjects`, `PracticeRepo` sessions with `mode`, `status`, `started_at`, `ended_at`, `summary` (items, answered, accuracy, minutes), and the attendance rule (a `class` session counts when at least one item is answered, spec 15.13 D5). Task 2's practice history needs one read method on `PracticeRepo` (ruling 55).
- **Slice C:** none.

## Before Task 1

1. Slices A and B complete on the branch: `uv run just check` green.
2. Read: spec section 9 and the sections above; `src/tutor/web/profile.py` (port, `ServicesProfiles`, `MemoryProfiles`, `install_profiles`), `src/tutor/app.py` (`build_app`, `BodySizeGuard`, `MAX_BODY_BYTES`), `src/tutor/web/app.py`, `security.py` (`CSP`, `SecurityHeadersMiddleware`), `deps.py` (`APP_ROUTER_DEPS`, `require_csrf`, `WriteLimits`), `sessions.py` (`__Host-tutor_session`, `SameSite=Lax`), `views.py`, `i18n.py`, `query.py`, `routes/glossary.py`, `routes/profile.py`, `templates/layouts/app.html`, `templates/partials/glossary_*.html`, `locale/en/LC_MESSAGES/messages.po`, `static/icons/sprite.svg`; tests `tests/unit/web/conftest.py`, `test_web_sweep.py`, `test_web_template_safety.py`, `test_web_v0_nav.py`, `test_web_i18n.py`, `test_web_profile.py`, `tests/integration/test_pg_profile_web.py`; the dashboard plan's "Conventions for page tasks".
3. Context7 is not required (no MCP SDK, OAuth or Stripe API). Read `pypdf`'s docs for the pinned version before Task 4 (encrypted files, `PdfReader(strict=...)`, page count, `extract_text`), not from memory.

## Rulings against the spec

Decided while planning, from the code. Task 6 records them in the spec's Amendments.

49. **Paths follow the spec, in Spanish,** although the existing pages use English paths (`/app/glossary`, `/app/profile`): `/app/materias`, `/app/materias/{subject_id}`, `/app/notas/{note_id}`, `/app/instancias/{instance_id}/raw`, and the writes `POST /app/materias/{subject_id}/pdf`, `POST /app/materias/{subject_id}/texto`, `POST /app/plantillas/{template_id}/retirar`. Open question 1 asks whether to switch to English paths; the change would be mechanical.
50. **One web port, `SubjectsPort`, on `app.state.subjects`** (`install_subjects`, `get_subjects_port`), with `ServicesSubjects(svc)` wired in `tutor.app.build_app` next to `install_profiles`, and `MemorySubjects(clock, store=None)` for tests and `just dashboard-demo` (`_ensure_user` as `MemoryProfiles`). Pages never import a repo. Methods (all take the session's `user_id`; foreign or missing ids return `None` and the route answers 404):
    - `subjects(user_id) -> SubjectsPage` (each subject with its `PlanStatus`),
    - `subject(user_id, subject_id) -> SubjectPage | None` (settings, plan status, notes grouped by source in `position` then `created_at` order, active templates with their instance counts, practice history),
    - `note(user_id, note_id) -> NotePage | None` (metadata, `clean_text` whole, concepts, active instances with `prompt_text`, template name and purpose),
    - `instance_document(user_id, instance_id) -> str | None` (composed with the `web` bridge and the preview draw; retired instances and instances of retired templates are still served by id, as `get_instance_view` does),
    - `add_pdf(user_id, subject_id, title, data: bytes) -> WebNoteSaved` and `add_text(user_id, subject_id, title, lesson_ref, text) -> WebNoteSaved` (both raise `ServiceError` with field codes),
    - `retire_template(user_id, template_id) -> RetireOutcome | None` (`retired` or `already_retired`).
51. **The raw route sends exactly the spec 9 header set and nothing from the dashboard set.** `SecurityHeadersMiddleware` keeps a fixed route table: for a `200` response whose path matches `^/app/instancias/[0-9a-f-]{36}/raw$` it sets `Content-Security-Policy: sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: private, no-store`, `Cross-Origin-Resource-Policy: same-origin`, plus the HSTS line every response gets; it does not set the dashboard CSP, `Permissions-Policy` or `Cross-Origin-Opener-Policy` on it. Error responses on that path keep the dashboard set. The route returns a plain `Response(media_type="text/html; charset=utf-8")`, never `render()` and never `Markup`.
52. **The dashboard CSP gains `frame-src 'self'`** and nothing else; `frame-ancestors 'none'` stays for every other page.
53. **One frame partial, the only `<iframe>` in the templates.** `partials/instance_frame.html` renders `<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" loading="lazy" src="/app/instancias/{{ id }}/raw" title="{{ prompt_text }}" class="instance-frame instance-frame--{{ purpose }}"></iframe>` with the `prompt_text` beside it as text. Never `srcdoc`, never `allow-same-origin` or any other sandbox token. `test_web_template_safety.py` gains an `iframe element` rule whose only allowance is that file, and a check that the allowed line has exactly those attributes. Heights are fixed by CSS class per purpose (`exercise`, `display`), because the dashboard has no `message` listener in v0 (spec 10); see Open question 4.
54. **Answers in the website frame are shown, not recorded** (spec 9, 15.5 `web` bridge). The page says so once, above the instances: "Aquí puedes probar el ejercicio; la calificación cuenta solo en una práctica con Claude."
55. **Practice history is a read of slice B rows.** `PracticeRepo.sessions_for_subject(subject_id, limit=50) -> tuple[PracticeSessionRow, ...]` (newest first; Postgres and memory, `RepoContract` case). The page shows date (learner's timezone, `day` filter), mode, items answered of total, accuracy (`percent` filter), minutes and whether the session counted toward the plan (slice B's attendance rule, computed in the service, never in the template). Incomplete sessions are listed and marked; nothing is recomputed on the web.
56. **Notes listing is a new read.** `NoteRepo.list_for_subject(subject_id) -> tuple[NoteListRow, ...]` (`id`, `source_id`, `source_title`, `source_kind`, `title`, `lesson_ref`, `position`, `origin`, `created_at`, `superseded: bool`), ordered by source title, `position` nulls last, `created_at`, id. Superseded notes are listed under the note that replaced them, muted.
57. **Uploads and pasted text go through one service path.** `services.notes.save_web_note(svc, user_id, subject_id, source: SourceInput, title, lesson_ref, raw_text)` calls `validate_notes(..., origin="web")` with one note, `MAX_WEB_NOTE_CHARS` instead of the MCP caps, no concepts, links or instances, and the same idempotency and supersede rules as `save_notes` (spec 4.1). A PDF creates or reuses a source `{kind: "pdf", title}`; pasted text a source `{kind: "text", title}`. Audit event `notes_saved` with `origin: web` and counts only. The result page links to the new note; `existing` says "Ese material ya estaba guardado."
58. **PDF text extraction** (`tutor.services.pdf_text.extract(data: bytes) -> str | PdfProblem`): refuse before parsing above 20 MB (`too_large`) or without the `%PDF-` header (`not_pdf`); parse with `pypdf` in a worker (`run_in_threadpool` from the route), refuse encrypted files (`encrypted`), more than 200 pages (`too_many_pages`), any `pypdf` error (`unreadable`), an extraction whose stripped text is empty (`no_text`, shown as "el PDF no tiene texto"), and a result above 400,000 characters (`too_long`). The time limit is Open question 3. The file is never stored; only the extracted text becomes `raw_text`. `PdfProblem` codes are enums; no PDF content or file name is logged.
59. **The retire button** (spec 15.13 D9, slice A ruling 29) is a `POST /app/plantillas/{template_id}/retirar` form on each active template row of the subject page, with the CSRF field, `hx-confirm` text "¿Retirar esta plantilla? Claude ya no la usará para ejercicios nuevos." and a no-JS fallback (303 back to the subject page). Service `templates.retire_template(svc, user_id, template_id)` sets `status = 'retired'`, `retired_at`, `updated_at` through `TemplateRepo.retire(template_id, now) -> bool` (the only columns `tutor_app` may update), is idempotent (`already_retired`), and records `template_retired` with ids and enums only. A retired template no longer appears in `list_templates`; what happens to its active instances is Open question 2, and until it is answered they stay active and drawable (the slice A behaviour for `replaces_template_id`).
60. **Write limits and CSRF unchanged** (spec 9): the three new POST routes sit under `APP_ROUTER_DEPS` (`require_csrf`, then `limit_writes` at 30 per minute per user). The upload form posts `multipart/form-data` with the hidden `csrf_token` field, which `require_csrf` already reads. The body size of the two upload routes is Open question 5; Task 4 is blocked on it.
61. **Navigation:** "Materias" after "Glosario" in `layouts/app.html` (key `subjects`, a new `subjects` symbol in `sprite.svg`); `test_web_v0_nav.py`'s `NAV` gains `/app/materias` in that position.
62. **Learner text is data on every page:** subject names, goals, context, note titles, lesson refs, clean text, concept names, template names and descriptions, prompt texts, source titles and upload titles are autoescaped Jinja output only; `clean_text` is shown in a `<pre class="note-text">` block; nothing is marked safe; template HTML is never read by a page template, only by the raw route.

## Global Constraints

As the slice A plan and the dashboard plan's page conventions, plus:
- Every page works without JavaScript; HTMX only progressively enhances (partials for the retire row and the upload result).
- Every new msgid is es-MX source text with an English entry in `locale/en/LC_MESSAGES/messages.po`; `missing_translations()` stays empty; no plural forms.
- Every new path parameter (`subject_id`, `note_id`, `instance_id`, `template_id`) gets an isolation case in `test_web_sweep.py`; anonymous GETs redirect to `/login` (303, or 401 with `HX-Redirect` for HTMX).
- Nothing on the website records a practice answer or calls a grading function.
- `pypdf` is pinned in `pyproject.toml` and `uv.lock`, and `pip-audit` (part of `uv run just check`) is clean.

## Review Focus

1. **The raw route's headers are exactly the set of ruling 51,** and the composed document holds no answer-key, reveal or undrawn pool fixture string (the slice A ruling 25 fixture), for a numeric instance, a flashcard pool and a choices pool. Task 3.
2. **The frame cannot reach the dashboard:** the only `<iframe>` in the templates is the partial's, with `sandbox="allow-scripts"` and no `srcdoc`; the raw document is never served without the `sandbox` CSP (checked on the response, not the template); a GET of the raw URL as a top-level page still carries the sandbox CSP. Task 3.
3. **Foreign and missing ids:** Beto's subject, note, instance and template ids give Ana 404 on every GET and POST, and the retire POST changes nothing; a malformed id is 404, not 422. Tasks 2, 3 and 5.
4. **Uploads that should fail:** a 20 MB + 1 byte file, a file that is not a PDF, an encrypted PDF, a 201-page PDF, a PDF with images only, a PDF whose text is over 400,000 characters, an empty pasted text, a pasted text of 400,001 characters, a title of 201 characters: each a 422 page with the field message, nothing stored. The same PDF twice gives `existing`. Task 4.
5. **Learner text that looks like markup:** a note title, a concept name, a template name and a `prompt_text` holding `<script>alert(1)</script>` render as text on every page. Task 2.
6. **Retiring:** a template retired from the website disappears from `list_templates`, `save_notes` with its id is `not_found`, the button no longer shows, a second POST is `already_retired`, and its instances behave as Open question 2 decides. Task 5.

## File structure

```
src/tutor/web/subjects.py                     # T1 SubjectsPort, page dataclasses, ServicesSubjects, MemorySubjects, install_subjects
src/tutor/services/notes.py                   # T1 list_notes (ruling 56); T4 save_web_note (ruling 57)
src/tutor/services/practice.py                # T1 history (ruling 55)
src/tutor/services/templates.py               # T5 retire_template (ruling 59)
src/tutor/services/ports.py, memory.py        # T1 NoteRepo.list_for_subject, PracticeRepo.sessions_for_subject; T5 TemplateRepo.retire
src/tutor/db/repos/notes.py, practice.py, templates.py   # T1, T5
src/tutor/services/pdf_text.py                # T4 extract, PdfProblem
src/tutor/web/routes/subjects.py              # T2 pages; T4 upload and paste; T5 retire
src/tutor/web/routes/instances.py             # T3 raw route
src/tutor/web/routes/__init__.py              # T2, T3 routers
src/tutor/web/security.py                     # T3 frame-src, raw header set
src/tutor/app.py                              # T1 install_subjects in build_app; T4 upload body limit (after Open question 5)
src/tutor/web/demo_server.py                  # T1 MemorySubjects
src/tutor/web/templates/pages/subjects.html, subject.html, note.html, subject_upload_result.html   # T2, T4
src/tutor/web/templates/partials/instance_frame.html, template_row.html, upload_forms.html       # T2, T3, T4, T5
src/tutor/web/templates/layouts/app.html      # T2 nav
src/tutor/web/static/css/app.css, static/icons/sprite.svg   # T2, T3
src/tutor/web/locale/en/LC_MESSAGES/messages.po             # T2–T5
pyproject.toml, uv.lock                       # T4 pypdf
tests/repo_contract.py                        # T1, T5
tests/unit/web/conftest.py                    # T1 subjects port fixture, Beto's rows
tests/unit/web/test_web_subjects.py           # T2, T4, T5 (new)
tests/unit/web/test_web_instances.py          # T3 (new)
tests/unit/services/test_pdf_text.py          # T4 (new, PDFs generated in the test)
tests/unit/web/test_web_sweep.py, test_web_template_safety.py, test_web_v0_nav.py, test_web_i18n.py   # T2, T3
tests/integration/test_pg_subjects_web.py     # T2–T5 (new; Postgres adapter end to end)
docs: spec Amendments (A49–A62), runbook upload note     # T6
```

## Task index

| # | Task | Reviewers (controller) |
| --- | --- | --- |
| 1 | Web port and adapters, the two read methods | security-reviewer |
| 2 | Pages: Materias, Materia, Nota; navigation and i18n | — |
| 3 | The raw instance route, its headers and the frame partial | security-reviewer |
| 4 | PDF upload and pasted text (`pypdf`) | security-reviewer |
| 5 | The template retire button | security-reviewer |
| 6 | Docs, amendments, phase audit | phase-auditor |

---

### Task 1: Web port and adapters, the two read methods

Spec 9 ("Routes use the `app.state` port pattern"); rulings 50, 55, 56.

**Files:**
- Create: `src/tutor/web/subjects.py`
- Modify: `src/tutor/services/ports.py`, `src/tutor/services/memory.py`, `src/tutor/services/notes.py` (`list_notes`), `src/tutor/services/practice.py` (`history`), `src/tutor/db/repos/notes.py`, `src/tutor/db/repos/practice.py`, `src/tutor/app.py` (`install_subjects(web, ServicesSubjects(svc))`), `src/tutor/web/demo_server.py`
- Test: `tests/repo_contract.py`, `tests/unit/services/test_notes_service.py`, `tests/unit/services/test_practice_service.py`, `tests/unit/web/conftest.py`, `tests/unit/app/test_app_http.py` (the port is installed)

**Interfaces (produces):** frozen page dataclasses `SubjectsPage(subjects: tuple[SubjectCard, ...])`, `SubjectCard(id, name, status, plan: PlanStatus | None)`, `SubjectPage(subject: SubjectView, sources: tuple[SourceGroup, ...], templates: tuple[TemplateCard, ...], history: tuple[HistoryRow, ...])`, `SourceGroup(source_id, title, kind, notes: tuple[NoteListRow, ...])`, `TemplateCard(id, name, description, purpose, grading_kind, practice_kind, active_instances: int)`, `HistoryRow(session_id, started_at, mode, answered, items, accuracy, minutes, status, counted: bool)`, `NotePage(note: NoteView, instances: tuple[InstanceCard, ...])`, `InstanceCard(id, template_name, purpose, prompt_text)`, `WebNoteSaved(note_id, outcome)`, `RetireOutcome = Literal["retired","already_retired"]`; the `SubjectsPort` Protocol and both adapters of ruling 50; `install_subjects`, `optional_subjects`, `get_subjects_port`.

- [ ] **Step 1: Write the failing tests:** `RepoContract` for `list_for_subject` (order, superseded flag, foreign subject empty) and `sessions_for_subject` (newest first, limit, foreign subject empty); service tests for `list_notes` and `history` (the `counted` flag for a closed session, an incomplete one and a `class` session with and without an answered item); the memory adapter returns `None` for another user's ids; `build_app` installs `ServicesSubjects`.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast && uv run just test-int`.
- [ ] **Step 3: Commit** `feat(web): subjects web port with Postgres and memory adapters`.
- [ ] **Step 4 (controller):** `security-reviewer` on the repo methods (user filter, RLS) and the port.

### Task 2: Pages: Materias, Materia, Nota; navigation and i18n

Spec 9 (first three bullets), 4.1; rulings 49, 53 (the partial is only included here; Task 3 serves its URL), 54, 55, 56, 61, 62.

**Files:**
- Create: `src/tutor/web/routes/subjects.py` (GET routes), `templates/pages/subjects.html`, `subject.html`, `note.html`, `templates/partials/instance_frame.html`, `template_row.html` (without the button; Task 5 adds it)
- Modify: `routes/__init__.py`, `templates/layouts/app.html`, `static/css/app.css` (`subject-*`, `note-text`, `instance-frame`, `instance-frame--exercise`, `instance-frame--display`), `static/icons/sprite.svg` (`subjects`), `locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/web/test_web_subjects.py`, `test_web_v0_nav.py`, `test_web_sweep.py` (`_other_users_ids` gains `subject_id`, `note_id`, `instance_id`, `template_id` from Beto's seeded rows), `test_web_i18n.py`, `tests/integration/test_pg_subjects_web.py`

- `/app/materias`: one card per subject (name, status chip, sessions per week, `missed`, `projected_end`, `delay_days`, adherence) or an empty state that says subjects are created by talking to Claude ("Pídele a Claude que cree una materia"). The plan numbers come from `PlanStatus` only.
- `/app/materias/{subject_id}`: settings read-only (goal and context as text), plan status, notes by source (title links to the note page, lesson ref, origin chip), templates (ruling 59 list, no button yet), practice history table (ruling 55), and the upload and paste forms from Task 4 (placeholder include until then).
- `/app/notas/{note_id}`: title, lesson ref, source, concepts as chips, `clean_text` in `<pre class="note-text">`, the ruling 54 line, then each active instance as the frame partial with its `prompt_text` and template name beside it.

- [ ] **Step 1: Write the failing tests** (the dashboard plan's page-task minimum): Ana with two subjects and notes from two sources; a subject with no notes; a new user with no subjects; the HTMX partial carries no `<html`; one string in English; `# Review Focus 5`; `# Review Focus 3` for the GETs; the nav order; the frame partial is rendered once per active instance with `sandbox="allow-scripts"` and the raw URL; retired instances are not listed; `missing_translations()` is empty; the Postgres adapter renders the three pages from seeded rows.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast`.
- [ ] **Step 3: Manual (controller):** `uv run just dashboard-demo`, both themes, phone width, keyboard only; note the result in the commit body.
- [ ] **Step 4: Commit** `feat(web): Materias, Materia and Nota pages`.

### Task 3: The raw instance route, its headers and the frame partial

Spec 9 (fourth bullet), 10, 15.5 (`web` bridge), 15.7 (website row), 15.11; rulings 51–54.

**Files:**
- Create: `src/tutor/web/routes/instances.py`
- Modify: `src/tutor/web/security.py` (`CSP` with `frame-src 'self'`, `RAW_INSTANCE_HEADERS`, the route table of ruling 51), `routes/__init__.py`, `tests/unit/web/test_web_template_safety.py` (the `iframe element` rule and its one allowance)
- Test: `tests/unit/web/test_web_instances.py`, `tests/unit/web/test_web_app.py` (dashboard CSP), `tests/integration/test_pg_subjects_web.py`

`GET /app/instancias/{instance_id}/raw` loads the user (login required, as every `/app` page), parses the id (`parse_uuid`, 404 otherwise), calls `get_subjects_port(request).instance_document(user.id, iid)` in a worker thread, and returns the string as a plain `Response`. 404 for a foreign or missing instance.

- [ ] **Step 1: Write the failing tests:** `# Review Focus 1` (exact header names and values, including the absence of the dashboard CSP, `Permissions-Policy` and COOP on the 200; the 404 keeps the dashboard set); `# Review Focus 2`; the document holds exactly one `tutor-params` element whose JSON round-trips and the `web` bridge; a pool instance shows the same preview draw twice; anonymous GET is 303 to login; HTMX GET is 401 with `HX-Redirect`; the dashboard CSP contains `frame-src 'self'` and still `frame-ancestors 'none'`; the template safety test fails for an `<iframe>` in any other template and for the partial with `srcdoc` or a second sandbox token.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast && uv run just test-int`.
- [ ] **Step 3: Manual (controller):** open a note page with each guide example instance in a browser: the frames run, a template's `fetch` attempt shows a CSP violation in the console, `document.cookie` inside the frame is empty, and `window.parent.document` access throws. Note the result in the commit body.
- [ ] **Step 4: Commit** `feat(web): sandboxed raw instance route and the instance frame`.
- [ ] **Step 5 (controller):** `security-reviewer` on `security.py`, the route and the template rule.

### Task 4: PDF upload and pasted text (`pypdf`)

Spec 1, 4.1, 9 (upload form), 10 (PDFs are untrusted); rulings 57, 58, 60. **Blocked on Open questions 3 and 5.**

**Files:**
- Create: `src/tutor/services/pdf_text.py`, `templates/partials/upload_forms.html`, `templates/pages/subject_upload_result.html`
- Modify: `pyproject.toml`, `uv.lock` (`pypdf`, pinned), `src/tutor/services/notes.py` (`save_web_note`), `src/tutor/web/subjects.py` (`add_pdf`, `add_text`), `src/tutor/web/routes/subjects.py` (two POST routes), `src/tutor/app.py` (the body limit decided by Open question 5), `templates/pages/subject.html`, `locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/services/test_pdf_text.py` (PDFs built in the test with `pypdf`'s writer: text, image-only, encrypted, 201 pages, malformed bytes), `tests/unit/services/test_notes_service.py`, `tests/unit/web/test_web_subjects.py`, `tests/unit/app/test_app_http.py` (body limits), `tests/integration/test_pg_subjects_web.py`

- Forms: PDF (`file` input accepting `application/pdf`, `title` defaulting to the file name without extension, cut to 200 characters, editable) and pasted text (`title`, optional `lesson_ref`, `text` textarea with the 400,000 cap stated). Both carry `csrf_token`. Errors redisplay the form with field messages (422); success redirects (303) to the new note, or renders the result partial for HTMX.
- Field messages: `title` (required, too long), `file` (`too_large` "El PDF pasa de 20 MB.", `not_pdf`, `encrypted` "El PDF está protegido con contraseña.", `too_many_pages` "El PDF tiene más de 200 páginas.", `unreadable`, `no_text` "el PDF no tiene texto", `too_long`), `text` (required, too long).

- [ ] **Step 1: Write the failing tests:** `# Review Focus 4` end to end; `save_web_note` outcomes (`new`, `existing`, `superseded` for the same source and lesson ref with another text); `origin = web` stored; the extracted text is `raw_text` and passages are built; the audit meta has counts and enums only; no log line contains PDF text or the file name (caplog); the body limit outside the two upload routes is still 64 KB.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast && uv run just test-int`; `uv run just check` for pip-audit.
- [ ] **Step 3: Commit** `feat(web): PDF upload and pasted text add notes to a subject`.
- [ ] **Step 4 (controller):** `security-reviewer` on the upload path (size checks before parsing, worker, caps, logging, body limit).

### Task 5: The template retire button

Spec 15.13 D9, slice A ruling 29; ruling 59.

**Files:**
- Modify: `src/tutor/services/ports.py`, `memory.py` (`TemplateRepo.retire`), `src/tutor/db/repos/templates.py`, `src/tutor/services/templates.py` (`retire_template`), `src/tutor/web/subjects.py`, `src/tutor/web/routes/subjects.py` (POST route), `templates/partials/template_row.html`, `locale/en/LC_MESSAGES/messages.po`
- Test: `tests/repo_contract.py`, `tests/unit/services/test_templates_service.py`, `tests/unit/web/test_web_subjects.py`, `test_web_sweep.py` (the POST is covered by the CSRF and isolation sweeps), `tests/integration/test_pg_subjects_web.py`

- [ ] **Step 1: Write the failing tests:** `# Review Focus 6`; `# Review Focus 3` for the POST; `RepoContract.retire` (`True` once, `False` for a retired, foreign or missing id); on Postgres the update touches only `status`, `retired_at`, `updated_at` (the grant would refuse anything else); the audit event; the HTMX response is the row partial without the button; the no-JS response is 303 to the subject page.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast && uv run just test-int`.
- [ ] **Step 3: Commit** `feat(web): retire a template from the subject page`.
- [ ] **Step 4 (controller):** `security-reviewer` on the repo method and the route.

### Task 6: Docs, amendments, phase audit

- [ ] Spec `## Amendments` A49–A62 and the answers to the open questions; `docs/v0/runbook.md` (the upload body limit and where PDFs are never stored); propose to the author the E37 wording ("artifacts" becomes "template instances" in the Subjects v0 acceptance row; the author edits `docs/v0/requirements-edits.md`); `uv run just check`; **(controller)** `phase-auditor`; commit `docs(subjects): slice D amendments`.

## Notes for the controller

- Dispatch 1 → 2 → 3 → 5 → 4 → 6: Task 4 waits for Open questions 3 and 5, and Task 5 for Open question 2 only if the answer changes what retiring does to instances.
- Task 1 needs slice B's `PracticeRepo`; if slice B is late, Task 2 can ship the history section empty behind the port, but not merge without it (the E37 acceptance names attendance on the website).
- Slice C is independent; both can run after slices A and B.

## Open questions for Jorge

1. **Paths in Spanish or English?** The spec names `/app/materias`, `/app/notas/{id}`, `/app/instancias/{iid}/raw`; every existing page uses English paths (`/app/glossary`, `/app/profile`, `/app/sessions`) with Spanish labels. Keep the spec's Spanish paths (ruling 49) or use `/app/subjects`, `/app/notes`, `/app/instances`?
2. **What retiring a template does to its instances.** The spec says only that a retired template is not offered again. Should its active instances (a) stay active and keep being drawn in practice, (b) stop being drawn but stay visible on note pages, or (c) be retired with it? The same question applies to `replaces_template_id` today, where slice A keeps them active.
3. **PDF time limit.** Spec 10 asks for a time limit on parsing. A Python thread cannot be stopped, so a timeout in a worker thread only stops waiting; the parse keeps using a CPU. Options: (a) a thread with a 20-second wait and the page/size caps as the real bound; (b) a short-lived subprocess per upload, killed at 20 seconds (more code, real bound). Which one, and what limit?
4. **Frame height.** With no `message` listener on the dashboard (spec 10), frames cannot size themselves. Is a fixed height per purpose (for example 22 rem for exercises, 28 rem for displays) with scrolling inside the frame acceptable for v0?
5. **Upload body size.** `BodySizeGuard` caps every web request at 64 KB, which a 20 MB PDF and a 400,000-character paste (up to about 1.6 MB of UTF-8) exceed. Proposed: a per-path limit of 20 MB + 64 KB on `POST /app/materias/{id}/pdf` and 2 MB on `POST /app/materias/{id}/texto`, 64 KB everywhere else, and the same at the edge (Cloudflare tunnel, Coolify proxy). Do you accept these limits, and should the paste form exist at all (spec section 1 mentions pasting text; section 9 lists only the PDF form)?
6. **Templates on the website beyond the retire button.** Spec 9 lists no templates page. This plan shows a list of active templates on the subject page (name, description, purpose, grading kind, active instance count) so the button has a place. Is that enough, or do you also want to see a template rendered (it needs an instance to show anything) or retired templates listed?
7. **Retiring an instance from the website.** D9 covers templates only; instances are retired through `replaces_instance_id`. Do you want a retire button on note pages too, or not in v0?
