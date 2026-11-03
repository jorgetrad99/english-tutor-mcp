# 05 — MCP Apps practice view on Claude's surfaces

Status: planned
Date(s): 2026-11-02 → (set when run)
Section 15 row: not in section 15; added by E37 (Subjects v0), spec `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md` section 7.2

## Question

Does a self-contained MCP App (`ui://tutor/practice/v1/app.html`, inline script, no SDK, no external domain) served by our OAuth custom connector render in Claude, play a Web Audio tone after a tap, and record an answer through `tools/call` (`record_result`) with the same user identity as the chat, on claude.ai web, Claude Desktop, Claude iOS and Claude Android; and does the text fallback work where apps do not render (Claude Code, voice mode)?

## Pass criterion

> The practice view renders and records a graded result in at least 4 of 5 attempts on claude.ai web and in at least 4 of 5 on Claude Desktop; the recorded `sub` equals the chat token's `sub` in every attempt; the ear tone plays after a tap in at least 3 of 5 attempts on one mobile surface (iOS or Android); in Claude Code the same tools return a usable text item and `record_result` works by text in 5 of 5 attempts.

If it fails: the practice slice ships text-only (quiz and self-reported exercises), `ear` is removed from `exercise_kinds`, and the app resource stays unregistered until a later spike passes. (Amended by ADR 0005: `ear` is now the generic `drill`; if the spike fails, drills stay in text mode and only items whose template has `needs_view: true` wait.)

## Setup

- Server commit: (fill in); tools exposed: `start_practice`, `next_exercise`, `record_result`, `end_practice`, resource `ui://tutor/practice/v1/app.html`
- Clients and plans: claude.ai web (Free and Pro), Claude Desktop Windows, Claude iOS, Claude Android, Claude Code CLI, Claude voice mode (observational only)
- Model version(s) as shown by the client
- Script: `/practice` prompt or "start a practice session for guitar"; answer 3 items; end the session. Each attempt is one full session.
- Number of runs planned: 5 per surface (25 app attempts, 5 text attempts, 2 voice observations)

## Checks per run

1. The view appears (Allow prompt accepted once) and shows the first item; `tool-result` carried `structuredContent`.
2. Tapping "Play" produces a tone (ear item) without any CSP domain declared.
3. Answering calls `record_result`; the server log shows the same `sub` as `start_practice`; the app shows the graded result; note whether Claude asked for per-call consent.
4. `next_exercise` from inside the app renders the next item; two items in one thread show no clipping or stale height (Android).
5. Dark and light theme both legible.
6. Edit the HTML without changing the URI: does the client serve the stale file? Record whether a versioned path is required.
7. Optional, information only: can the app replace its own document (`document.open`/`write`/`close`) and keep calling tools? Since ADR 0005 every template is written by an LLM and is never placed in the app's own document (it could call any tool), so this item no longer decides a path.
8. Can the app show a composed template inside a nested `<iframe sandbox="allow-scripts" srcdoc="...">`, and can that frame `postMessage` an answer to the app, which then calls `record_result`? Record any CSP violation in the host console. Decides the "app, nested frame" path, the only way a template's own HTML is shown inside the app; if it fails, the app draws a plain view from the grading kind and `needs_view` items wait (spec section 15.7, ADR 0005). Since ADR 0005 the app plays no sound itself, so run this item with the guide's `example_music_ear` template: record whether its tone plays after a tap inside the nested frame (this replaces check 2 for the template path).

## Raw results

| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |
| --- | --- | --- | --- | --- |

Store screenshots and log excerpts under `docs/spike/05-data/` (no personal data; redact names and tokens).

## Analysis

Numbers only: counts and rates against the criterion per surface, consent prompts seen, height or caching problems, anything that broke.

## Decision

PASS | FAIL | INCONCLUSIVE — (one line).
Consequence: practice slice ships with the app view, or text-only per the fallback above.
ADR: `docs/adr/0004-subjects-context-beside-english.md` (amend if the fallback is adopted).
