# 02 — `end_session` reliability

Status: planned
Date(s): 2026-10-05 → 2026-10-11
Section 15 row: #2

## Question
Does Claude reliably call `end_session` with a valid payload at the end of an unscripted 15-minute conversation?

## Pass criterion
> ≥ 90% valid payloads; ≥ 80% of `errors[].said` pass validation

If it fails (section 15): Add an explicit `/end` prompt and a reminder in `start_lesson`; if still < 70%, rethink evidence capture

Operational definitions (design doc §8.2–8.3):
- Valid payload, per run with `status=ok`: the **final** `end_session` call passes the strict section 7 model (closed enums, unknown fields rejected), has ≥ 1 `user_turns`, and carries a `session_id` issued by `get_profile` to the same tester in that run. No `end_session` call → invalid. Valid on first call and calls per run are reported alongside.
- `errors[].said`: pooled over valid final payloads; passes when its normalized text (NFKC, lowercase, punctuation stripped, whitespace collapsed) is a substring of some normalized `user_turns` entry.
- With 25 runs, 90% means ≥ 23 valid runs.

Decision rules: design doc §9.2 (including the 10-run `/end` extension if valid < 70%).

## Setup
- Design: `docs/superpowers/specs/2026-10-03-spike-design.md`
- Server commit: `07a4a1d` (tag `spike-instructions-v1`, setup acceptance 2026-10-04); tools exposed: `get_profile`, `end_session`
- Auth: real OAuth via FastMCP 4 `GoogleProvider`; hosting: Cloudflare named tunnel
- Clients and plans: claude.ai web / desktop for text, Claude mobile app for voice; one Free and one Pro account
- Model version(s): `model_shown` recorded per run in `runs.csv`
- Script / prompts used: session protocol in design doc §7 (no mention of tools, natural closing, no nudge); server instructions frozen v1 (design doc §3.3)
- Number of runs planned: 25 = 20 text (10 Free, 10 Pro) + 5 voice (shared with experiment 01), all by the author
- Known limitation: one speaker, so section 15's "varied users" is not covered; re-checked at the validation gate

## Raw results
| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |
| --- | --- | --- | --- | --- |

Store transcripts and payloads under `docs/spike/02-data/` (no personal data; redact names).

## Analysis

## Decision
