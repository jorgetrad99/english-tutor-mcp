# Subjects Slice C: The Practice App (MCP App) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** practice items appear in chat as an interactive view on claude.ai, Claude Desktop, iOS and Android. One static MCP App resource, `ui://tutor/practice/v1/app.html`, receives each item's composed template document in the tool result, shows it in a nested sandboxed frame (or, failing that, a plain control drawn from the grading kind), relays the learner's answer as `record_result`, and moves on with `next_exercise`. The server still draws, grades and logs; the app computes nothing. The text fallback of slice B stays the contract everywhere else.

**Architecture:** a packaged static file `src/tutor/mcp/apps/practice_v1.html` served as a `ui://` resource with MIME `text/html;profile=mcp-app`; `_meta.ui.resourceUri` on `start_practice`, `next_exercise` and `show_exercise`; a third server-owned bridge, `src/tutor/templates/bridge_app_frame.js`, chosen by the server for the `app_frame` channel of `tutor.domain.templates.compose_document`; no new table, no new tool, no new dependency.

**Tech Stack:** as slice A. FastMCP 4.0.11 apps API (`fastmcp.apps`), looked up with Context7 at Step 0 of Task 1. No MCP Apps SDK in the page (spec S10, S15).

**Spec:** `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md` sections 7.2, 10 (practice view bullet), 13 (amended line on slice C), 15.5 (bridges), 15.7 (paths and fallbacks), 15.11 (the app never shares a document with a template). ADR 0004 decision 5, ADR 0005 decision 9. Spike: `docs/spike/05-mcp-apps-practice-view.md` (items 1–6 and 8). This plan's rulings are numbered from 39 (slice A uses 1–29, slice B 28–38 in its own plan).

**Scope note (2026-11-03):** this slice was requested as "behaviour metrics and logging". The spec, both ADRs and both earlier plans define slice C as the practice app only; practice logging (attendance, results, the `end_practice` summary, audit events `practice_*`) is slice B, and the website views of it are slice D. Nothing in the spec defines further behaviour metrics for subjects. This plan therefore covers the practice app; see Open questions, item 1.

**Execution:** subagent-driven, as slice A. **Branch:** `feat/subjects-v0`.

## Dependencies

- **Slice A Task 4b** (in progress; names follow spec section 15 and may still shift): `tutor.domain.templates.compose_document`, `public_payload`, `encode_payload`, `render_text`, the `Channel` literal with `app_frame`, `tutor.services.template_files.bridge(channel)`, the marker constant in `tutor.domain.html_guard`, the guide example `example_music_ear` (used by the spike).
- **Slice B** (in progress): `start_practice`, `next_exercise`, `show_exercise`, `record_result`, `end_practice` with items carrying `item_id`, `instance_id`, `practice_kind`, `grading_kind`, `needs_view`, `document`, `text`; text mode skipping `needs_view` items (slice B ruling 34 as amended). This slice adds `meta` to three of those tools and changes nothing in their input or output models beyond the field decided by ruling 39.
- **Spike 05**: Task 3 runs it. Task 2 may be built before the spike, but nothing is registered on production until the spike's Decision line is filled (ruling 47).

## Before Task 1

1. Slices A and B complete on the branch: `uv run just check` green.
2. Read: spec 7.2, 15.5, 15.7, 15.11; ADR 0005 decision 9; spike 05 whole; `src/tutor/mcp/server.py` (`_register`, `_guard`, the slice B practice tools), `src/tutor/mcp/schemas.py` (practice outputs), `src/tutor/domain/templates.py` (`compose_document`, `Channel`), `src/tutor/services/template_files.py`, `src/tutor/templates/bridge_artifact.js` and `bridge_web.js`, the guide's `contract` section (what `tutorSubmit` and `tutorOnResult` promise a template).
3. **Context7 (required, CLAUDE.md):** resolve `/prefecthq/fastmcp` and query the pinned 4.0.x docs for (a) how a tool declares its `ui://` resource (`AppConfig(resource_uri=...)` through `@mcp.tool(app=...)` or `Tool.from_function(..., meta=...)`; spec 7.2 names the second, the 4.1 docs show the first), (b) how a `ui://` resource is registered with the `text/html;profile=mcp-app` MIME type and no CSP, (c) whether a tool result can carry data for the app that the model does not receive (`structuredContent` versus result `_meta`), (d) the host messages the page must handle (`ui/initialize`, `ui/notifications/initialized`, `ui/notifications/tool-result`, `tools/call`, the size-change notification) in the MCP Apps spec. Record the findings in Task 1's commit body; rulings 39 and 40 are confirmed or corrected from them before any code.

## Rulings against the spec

39. **Where the document travels.** Spec 15.7 leaves it to this slice: the composed document goes in the field the app reads and the model does not need. Default, to be confirmed at Step 0: the practice outputs keep `document` in `structuredContent` (as slice B ships it) unless the pinned FastMCP version lets a tool return result `_meta` that hosts pass to the app and not to the model; if it does, `document` moves to `_meta["tutor/document"]` for app-mode results and `structuredContent` keeps `text`. Either way `text` stays in `content` for the model, and the output model change (if any) is made in place, because these tools are unreleased (spec 15.9, "Changing tools in place").
40. **Registration.** `_register` gains an optional `app_resource: str | None` argument; when set, the tool is created with the apps metadata found at Step 0, and the existing title-restoring and schema-agreement checks run unchanged. `start_practice`, `next_exercise` and `show_exercise` set it; `record_result`, `end_practice` and every other tool do not. `record_result` stays visible to the model (spec 7.2; no `visibility: ["app"]` in v0).
41. **The resource is one packaged file, versioned by URI.** `src/tutor/mcp/apps/practice_v1.html`, read once with `importlib.resources` and cached, registered as `ui://tutor/practice/v1/app.html` with MIME `text/html;profile=mcp-app` and no `csp`, `permissions` or `domain` (spec 7.2). The resource carries no user data, so its read needs nothing beyond the OAuth proxy. If spike 05 item 6 shows that hosts cache by URI, any later change to the file ships as `v2` with a new file and URI; a test pins the URI string and the file's SHA-256 together so an edit without a version decision fails.
42. **The app composes nothing.** The server composes the document with the `app_frame` bridge (spec 15.5) for every item it returns from an app-enabled tool in `app` mode; the app only places it. Which bridge `show_exercise` and text-mode `start_practice` use is Open question 2; until it is answered, `show_exercise` uses `app_frame` and text mode uses `artifact`.
43. **The nested frame is the only place a template runs in the app.** The app creates `<iframe sandbox="allow-scripts" srcdoc="...">` with the document as `srcdoc`, never `allow-same-origin`, never any other sandbox token, and never writes a template into its own document (spec 15.7, 15.11; `document.open`, `document.write`, `innerHTML` and `insertAdjacentHTML` do not appear in the file, test-pinned).
44. **One relayed message, for the current item only.** `bridge_app_frame.js` defines `tutorSubmit(answer)` as `parent.postMessage({type: "tutor-answer", answer}, "*")` (the parent is the app; the frame's origin is opaque, so no target origin can be named) and handles one incoming message, `{type: "tutor-result", result}`, by calling `window.tutorOnResult(result)` when defined. The app accepts a message only when `event.source === frame.contentWindow` of the current item, `type` is `tutor-answer`, and `answer` has exactly the shape of the item's `grading_kind` (spec 15.8: keys, types and lengths, checked in the app before sending; the server checks again). The first accepted answer per item is relayed as `tools/call record_result {session_id, item_id, answer}` with ids the app holds, never ids from the frame (the payload carries `instance_id` only); later messages for that item are ignored. After the graded result the app posts `tutor-result` (correctness, score, reveal) into the frame, then calls `next_exercise`.
45. **The plain view.** When the nested frame is not available (spike 05 item 8 fails, ruling 47), the app draws a plain control from the public payload and the grading kind (spec 15.7): option buttons for `choice_index` (labels from the `options_param` list, or `drawn.options`), a text field for `exact_text`, a multi-select built from the prompt list for `exact_set` when one is labelled as options, otherwise a comma-separated text field, an ordering list with up and down buttons for `exact_sequence` (from `drawn.cues`), a number field for `numeric`, and a 1–4 rating with optional tempo and minutes for `self_report`. Every value is set with `textContent` or `value`. Items with `needs_view: true` are never drawn on this path (ruling 47).
46. **Look and behaviour of the shell.** Host theme variables with a fallback for light and dark; no fixed width; the document height is reported after every render with the size notification found at Step 0; real buttons, labels and visible focus; the graded result is announced in an `aria-live` region; the app keeps the session and item ids in memory only (no storage API). The shell's own texts are Spanish (the learner's interface language in v0, spec 15.5 D7).
47. **The spike decides the path, as data on the server.** A constant `NESTED_FRAME_VERIFIED: bool` in `tutor.domain.templates` (default `False`) records spike 05 item 8. While it is `False`, the server does not schedule `needs_view` items in `app` mode either (slice B's text-mode rule applied to both modes), `show_exercise` refuses a `needs_view` instance with `validation_failed` on `instance_id`, and the app uses the plain view. Flipping it to `True` is a one-line commit after the spike's Decision, reviewed with the spike record. If spike items 1–4 fail, the resource and the `meta` stay unregistered (spike 05 "If it fails"), and practice stays text-only.
48. **No new tool, table, audit event or response rule text beyond one line.** The practice tools' rules already say "If the interactive view did not appear, present it in text" (slice B Task 4). `start_practice` and `next_exercise` rules gain at most one line: "If the view shows the item, do not repeat it; wait for the learner." (2–4 lines per rules string still holds; measured in Task 2.)

## Global Constraints

As the slice A plan, plus:
- The app HTML is static and self-contained: no `src`/`href` URL other than a fragment, no external font, image, script or style, no `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `importScripts`, `eval` or `new Function`, no storage API. It passes `tutor.domain.html_guard.check_html` (the same deny list as templates; the `iframe` it creates is built with DOM methods at run time, so the file has no `<iframe>` tag).
- The app and the bridge never grade, never compute a key, never see a pool or answer-key parameter: the payload they receive is `public_payload` (slice A ruling 25), and the graded result comes from `record_result`.
- Learner text (prompt text, parameter values, cues, options, reveals) is set with `textContent`/`value` only, never parsed as markup.
- No change to English tools, tables or the instructions string beyond what ruling 48 states; instructions stay ≤ 400 words.

## Review Focus

1. **A template that tries to act for the learner:** a template whose script calls `parent.postMessage` with `{type: "tools/call", ...}`, with a `tutor-answer` for another item id, with a wrong shape (`option_index: "2"`, an extra key), twice in a row, or from a second frame it creates: the app relays nothing but the first well-shaped answer of the current item, as `record_result` only. Task 2 (a DOM-free unit test of the app's message filter, extracted as a pure function in the file and exercised by a Python test that runs the file's filter through `node` when available, skipped otherwise; plus the manual spike case).
2. **No answer key reaches the app:** a numeric instance, a flashcard pool and a choices pool shown through `start_practice` in app mode: neither `document` nor `structuredContent` nor `_meta` contains the fixture answer-key, reveal or undrawn pool strings before `record_result`; after it, the reveal arrives only in the graded result. Task 1 (extends slice A ruling 25's walker to the practice outputs).
3. **The view does not appear** (Claude Code, voice, a host that ignores `_meta.ui`): the same calls return a complete `text` and `record_result` works by text, five of five times. Tasks 2 and 3.
4. **A `needs_view` item while `NESTED_FRAME_VERIFIED` is `False`:** never drawn by `start_practice` in either mode; `show_exercise` on it is `validation_failed` on `instance_id`; with the flag `True` it is drawn in `app` mode only. Task 1.
5. **The resource changes without a new URI:** the pinned hash test fails until the URI and file name move to `v2` or the hash is updated with a recorded reason. Task 2.

## File structure

```
src/tutor/templates/bridge_app_frame.js        # T1 tutorSubmit -> parent.postMessage; tutor-result -> tutorOnResult
src/tutor/services/template_files.py           # T1 bridge("app_frame")
src/tutor/domain/templates.py                  # T1 NESTED_FRAME_VERIFIED (ruling 47)
src/tutor/services/practice.py                 # T1 compose app-mode items with app_frame; needs_view rule
src/tutor/mcp/apps/__init__.py                 # T2
src/tutor/mcp/apps/practice_v1.html            # T2 the shell (handshake, frame, plain view, relay)
src/tutor/mcp/server.py, schemas.py, rules.py  # T2 resource, meta on three tools, ruling 39 field, ruling 48 line
tests/unit/domain/test_templates.py            # T1 flag constant, compose with the app_frame bridge
tests/unit/services/test_template_guide.py     # T1 bridge rules
tests/unit/services/test_practice_service.py   # T1 Review Focus 2 and 4
tests/unit/mcp/test_mcp_practice_app.py        # T2 resource read, MIME, meta, static checks, Review Focus 1, 5
docs/spike/05-mcp-apps-practice-view.md        # T3 Setup, Raw results, Analysis, Decision
docs: spec Amendments (A39–A48)                # T4
```

## Task index

| # | Task | Reviewers (controller) |
| --- | --- | --- |
| 1 | The `app_frame` bridge, the nested-frame flag, app-mode composition | mcp-contract-reviewer (services and outputs) |
| 2 | The `ui://` practice app and its registration | mcp-contract-reviewer |
| 3 | Spike 05 run and the flag decision (manual, author) | — |
| 4 | Docs, amendments, phase audit | phase-auditor |

---

### Task 1: The `app_frame` bridge, the nested-frame flag, app-mode composition

Spec 15.5, 15.7, 15.11; rulings 42, 44, 47.

**Files:**
- Create: `src/tutor/templates/bridge_app_frame.js`
- Modify: `src/tutor/services/template_files.py` (`bridge("app_frame")`), `src/tutor/domain/templates.py` (`NESTED_FRAME_VERIFIED = False`), `src/tutor/services/practice.py` (composition channel per mode; the `needs_view` rule of ruling 47 in drawing and in `show_exercise`)
- Test: `tests/unit/domain/test_templates.py`, `tests/unit/services/test_template_guide.py`, `tests/unit/services/test_practice_service.py`

**Interfaces:**
- Consumes: slice A Task 4b (`compose_document`, `Channel`, `template_files.bridge`), slice B (`practice.start_practice`, `next_exercise`, `show_exercise`).
- Produces: `template_files.bridge("app_frame") -> str`; `practice.compose_item(item, *, mode) -> ComposedItem(document, text)` (or the slice B helper of that role, extended) choosing `app_frame` for `app` mode per ruling 42; `NESTED_FRAME_VERIFIED` read by the drawing step and by `show_exercise`.

- [ ] **Step 0 (controller):** the Context7 lookup of "Before Task 1", item 3; rulings 39 and 40 confirmed or corrected in writing before dispatch.
- [ ] **Step 1: Write the failing tests:** the bridge file contains `postMessage` exactly once, to `parent`, with type `tutor-answer`; it listens for `tutor-result` only and calls `tutorOnResult` only when it is a function; it contains no `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, storage API, `eval`, `Function(` or URL; `compose_document` with the `app_frame` bridge on each guide example yields one `tutor-params` and one `tutor-bridge` element (stdlib `html.parser` count); `start_practice` in `app` mode returns documents composed with `app_frame`, in `text` mode with `artifact` (ruling 42); `# Review Focus 2` over `start_practice`, `next_exercise`, `show_exercise` outputs; `# Review Focus 4` with the flag at both values (monkeypatched).
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast`.
- [ ] **Step 3: Commit** `feat(templates): app_frame bridge and the nested-frame flag for the practice app` (commit body: the Context7 findings).
- [ ] **Step 4 (controller):** `mcp-contract-reviewer` on the services and output diff.

### Task 2: The `ui://` practice app and its registration

Spec 7.2, 15.7; rulings 39–41, 43–46, 48.

**Files:**
- Create: `src/tutor/mcp/apps/__init__.py`, `src/tutor/mcp/apps/practice_v1.html`
- Modify: `src/tutor/mcp/server.py` (`_register(..., app_resource=...)`, the resource registration, `meta` on three tools), `src/tutor/mcp/schemas.py` (only if ruling 39 moves `document`), `src/tutor/mcp/rules.py` (ruling 48 line), `pyproject.toml` (only if the build excludes `.html` under `src/tutor/mcp/apps/`)
- Test: `tests/unit/mcp/test_mcp_practice_app.py` (new), `tests/unit/mcp/test_mcp_server.py`, `test_mcp_rules.py`

**The page, in order of execution:** register `message` handlers; send `ui/initialize`; on its answer send `ui/notifications/initialized`; on `ui/notifications/tool-result` read the item (ids, `grading_kind`, `needs_view`, `document` from the ruling 39 field, the public payload for the plain view); render it (nested frame while the item has a document and the frame path is available, plain view otherwise, ruling 45); on an accepted answer (ruling 44) call `record_result` through `tools/call`, show the graded result, post it into the frame, then call `next_exercise` and render the next item until `done`; report the height after each render. Every host message shape is the one found at Step 0 of Task 1, not from memory.

- [ ] **Step 1: Write the failing tests:** the resource is listed and read in-process with MIME `text/html;profile=mcp-app` and no `csp`/`permissions`/`domain`; `tools/list` shows the apps metadata with `ui://tutor/practice/v1/app.html` on `start_practice`, `next_exercise`, `show_exercise` and on no other tool; the file passes `check_html`, contains no `<iframe`, `allow-same-origin`, `document.write`, `document.open`, `innerHTML`, `insertAdjacentHTML`, `outerHTML =`, `fetch`, `XMLHttpRequest`, `WebSocket`, `localStorage`, `sessionStorage`, `indexedDB`, `eval`, `Function(`, `http:` or `https:`; it sets `sandbox` to exactly `allow-scripts`; `# Review Focus 1` on the message filter; `# Review Focus 3` through the in-process client without app support (text and `record_result`); `# Review Focus 5` (URI plus SHA-256 pin); rules line counts; descriptions unchanged and ≤ 120 words; instructions ≤ 400 words.
- [ ] **Step 2: Implement**; `uv run just fmt && uv run just check-fast`.
- [ ] **Step 3: Manual (controller):** run `uv run just inspector`, open the resource, and in a scratch host page (an HTML file in the scratchpad that plays the host side of the handshake with canned tool results for each guide example) check both themes, keyboard only, the plain view for every grading kind and the nested frame for every example; note the result in the commit body.
- [ ] **Step 4: Commit** `feat(mcp): practice app resource with nested template frame and plain view`.
- [ ] **Step 5 (controller):** `mcp-contract-reviewer` on the `src/tutor/mcp` diff.

### Task 3: Spike 05 run and the flag decision (manual, author)

Spike 05; ruling 47.

- [ ] **Step 1:** fill the spike's Setup (server commit, tools, resource URI) and deploy the branch to the author's test environment (never production before the Decision).
- [ ] **Step 2 (author):** run checks 1–6 and 8 on claude.ai web, Claude Desktop, iOS and Android, and the text attempts in Claude Code; record every run in Raw results (no personal data), with item 8 run on the guide's `example_music_ear` template.
- [ ] **Step 3:** write Analysis and Decision. If item 8 passes, commit `NESTED_FRAME_VERIFIED = True` with the test that pins it (`feat(templates): nested template frame verified by spike 05`). If items 1–4 fail, remove the resource registration and the `meta` (ruling 47) in one commit and record it in ADR 0004 as its spike line says.
- [ ] **Step 4: Commit** `docs(spike): 05 practice view results`.

### Task 4: Docs, amendments, phase audit

- [ ] Spec `## Amendments` A39–A48 (and the spike decision in a line); `uv run just check`; **(controller)** `phase-auditor`; commit `docs(subjects): slice C amendments`.

## Notes for the controller

- Dispatch 1 → 2 → 3 → 4. Task 2 can be built before the spike; Task 3 is the author's and gates production.
- Task 1 Step 0 is a controller step: no implementer starts without the Context7 findings, because the `meta` shape and the host messages must not come from memory (CLAUDE.md).
- Slice D does not depend on this slice; it can run in parallel once slices A and B are done.

## Open questions for Jorge

1. **Scope of "slice C".** You described slice C as behaviour metrics and logging. The spec (7.2, 13, 15.7, 15.9), ADR 0005 and both earlier plans make slice C the practice app, and put practice logging (attendance, results, the `end_practice` summary, audit events) in slice B and its display in slice D. Is this plan what you want for slice C? If you want subject behaviour metrics beyond slice B (for example per-concept accuracy, time per item, streaks), the spec has to define them first.
2. **Which bridge for an item the server cannot place.** `show_exercise` and the practice tools do not know whether the client renders apps. Proposed: `app_frame` in `app` mode and for `show_exercise`, `artifact` in `text` mode (ruling 42). Alternative: one bridge that posts to the app and, with no acknowledgement within a second, shows the artifact text ("Díselo a Claude"). Which one?
3. **A `needs_view` item that is already drawn when the frame fails at run time** (the flag says verified, but one host blocks `srcdoc`): `next_exercise` returns the first unanswered item, so the session would stop there. Should the app show "este ejercicio necesita la vista interactiva; termina la sesión" and leave it unanswered, or does the server need a "skip" path (a new answer shape or tool, which the spec does not have)?
4. **Spike pass rule for item 8.** Spike 05's pass criterion counts checks 1–4 only. How many of five attempts must item 8 pass, and on which surfaces, before `NESTED_FRAME_VERIFIED` becomes `True` (proposed: 4 of 5 on claude.ai web and on Desktop, as the other checks)?

## Decisions by the controller (2026-11-03)

- Q1 scope: slice C is the practice app, as the spec says. Whether subject metrics beyond slice B are wanted is asked to Jorge separately; nothing is added here until the spec defines them.
- Q2 bridge: `app_frame` for app mode and `show_exercise`; `artifact` for text mode. No acknowledgement-timeout fallback.
- Q3 frame failure at run time: add a server skip path. The app calls `record_result` with a closed `skipped` outcome; the item is not graded, does not count toward accuracy, and the session continues. Record it as a ruling in Task 1.
- Q4 spike item 8 pass rule: 4 of 5 attempts on claude.ai web and 4 of 5 on Claude Desktop before `NESTED_FRAME_VERIFIED` flips.
