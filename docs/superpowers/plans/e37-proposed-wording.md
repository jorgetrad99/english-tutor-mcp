# E37: proposed wording after exercise templates (for the author)

- **For:** Jorge, author of `docs/v0/requirements-edits.md`. Nothing here has been applied; E37 is unchanged.
- **Why:** E37 was written on 2026-11-02, before exercise templates (subjects spec section 15, ADR 0005, slice A plan Task 4b). Its section 1, 6, 7 and 16 replacements still name `note_artifacts`, `exercises`, `quiz_items`, `save_artifact`, `get_artifact` and "artifacts". Slice A removed them before release: templates and template instances took their place.
- **Source of the names:** subjects spec "Amendments" A19, A20 and A45 (`docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md`).
- **How to read this:** each block below changes the **Replace with** text inside E37. It quotes the E37 text that changes and gives the proposed text. E37's **Find** blocks (the current requirements text) do not change.

## 1. E37, Section 1 bullet

Current E37 replacement text (part):

```text
deterministic search (Postgres full-text search plus a concept graph); HTML lesson views shown in a sandboxed frame; a practice plan computed by the server; practice sessions with server-generated and server-graded exercises shown as an MCP App with a text fallback; attendance and results logged per session.
```

Proposed:

```text
deterministic search (Postgres full-text search plus a concept graph); exercise and lesson-display templates that Claude writes from a versioned server guide, only after the learner says yes, and fills from each lesson, shown in a sandboxed frame; a practice plan computed by the server; practice sessions whose items the server draws and grades from the stored instances, shown as an MCP App with a text fallback; attendance and results logged per session.
```

## 2. E37, Section 6

Current E37 replacement text (part):

```text
Subjects v0 (E37) adds its own user-scoped tables, all under row-level security: `subjects`, `sources`, `notes`, `note_passages`, `note_artifacts`, `exercises`, `quiz_items`, `concepts`, `note_concepts`, `concept_links`, `practice_plans`, `practice_sessions`, `practice_items`.
```

Proposed:

```text
Subjects v0 (E37) adds its own user-scoped tables, all under row-level security: `subjects`, `sources`, `notes`, `note_passages`, `concepts`, `note_concepts`, `concept_links`, `exercise_templates`, `template_instances` (migration 0006), and `practice_plans`, `practice_sessions`, `practice_items` (migration 0007).
```

The sentence after it ("Their columns and rules are in the 2026-11-02 spec; `notes.raw_text` is immutable.") can stay. If you want it to name the other rule that the data model depends on, add: "Templates and instances are retired, never deleted or edited."

## 3. E37, Section 7

Current E37 replacement text (part):

```text
Subjects v0 (E37) adds eleven tools under the same rules: `get_subjects`, `save_subject`, `save_notes`, `save_artifact`, `get_artifact`, `search_notes`, `get_note`, `start_practice`, `next_exercise`, `record_result`, `end_practice`, plus one `ui://tutor/practice/v1/app.html` resource.
```

Proposed:

```text
Subjects v0 (E37) adds fifteen tools under the same rules: `get_subjects`, `save_subject`, `save_notes`, `search_notes`, `get_note`, `get_template_guide`, `list_templates`, `save_template`, `create_instance`, `get_instance_view`, `start_practice`, `next_exercise`, `show_exercise`, `record_result`, `end_practice`, plus one `ui://tutor/practice/v1/app.html` resource.
```

Status, for your information: the first ten are built on `feat/subjects-v0` (slice A). The last five come with slice B, and the `ui://` resource comes with slice C.

## 4. E37, Section 16 table row

Current E37 replacement text (part):

```text
and sees notes, artifacts, plan status and attendance on the website.
```

Proposed:

```text
and sees notes with their exercises and lesson displays, plan status and attendance on the website.
```

## 5. Optional: a requirement before multi-user use

Subjects spec Amendment A50 records that per-user quotas are required before the server is opened to anyone else. Slice A caps each call, but nothing caps a user's totals. If you want E37 to say this too, one sentence can go at the end of the Section 16 rule replacement (after "...without moving the gate's dates or criteria."):

```text
Before Subjects v0 is opened to any user but the author, the server enforces per-user quotas on stored subjects, notes, templates and instances.
```

## Not affected

- E37's intro paragraph, the Section 2 principle 5 text and the Section 4 text name no removed table or tool.
- `docs/v0/acceptance.md`: the "Token works" row now says that a release including Subjects v0 also lists the ten subject tools (edited on the branch; no E37 change needed).
