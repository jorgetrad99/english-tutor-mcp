# 01 — Voice-mode tool calls

Status: planned
Date(s): 2026-10-05 → 2026-10-11
Section 15 row: #1

## Question
Do custom-connector tool calls run inside Claude's voice mode on mobile?

## Pass criterion
> Tools fire in ≥ 4 of 5 sessions without leaving voice

If it fails (section 15): Ship the text→voice→text fallback (section 8) and state it in onboarding

Operational definition (design doc §8.1): a voice run passes when ≥ 1 `tools/call` reaches the server and gets a response, the phone screen recording shows voice stayed active throughout, and Claude continues the conversation using the result. Whether `end_session` also fired in voice is reported separately and feeds experiment 02. Decision rules: design doc §9.1.

## Setup
- Design: `docs/superpowers/specs/2026-10-03-spike-design.md`
- Server commit: `07a4a1d` (tag `spike-instructions-v1`, setup acceptance 2026-10-04); tools exposed: `get_profile`, `end_session`
- Auth: real OAuth via FastMCP 4 `GoogleProvider` (fallbacks: `GitHubProvider`, WorkOS AuthKit); hosting: Cloudflare named tunnel
- Clients and plans: Claude mobile app in voice mode, one Free account (`author-free`) and one Pro account (`author-pro`); connector added on claude.ai web
- Model version(s): `model_shown` recorded per run in `runs.csv`
- Script / prompts used: session protocol in design doc §7; server instructions frozen v1 (design doc §3.3)
- Number of runs planned: 5 (3 Free, 2 Pro), all by the author; aborted runs recorded and replaced
- Evidence per run: server log lines (`spike/data/raw/calls-*.jsonl`), `runs.csv` row, phone screen recording, transcript if claude.ai shows one

Setup acceptance (2026-10-04, evidence moved to `spike/data/setup/`, not part of any run):
- Free and Pro accounts each connected through the connector and called `get_profile`, logged as `author-free` (02:27 UTC Oct 5) and `author-pro` (02:22 and 02:23 UTC Oct 5). A first Pro login was labelled `author-free` because `SPIKE_TESTERS` mapped the wrong email; fixed in `.env` before the accepted calls.
- Restart check in the same chat: the second Pro call came after a server restart with no new login (no `/authorize` or `/token` request), so tokens survive a restart; the server stays stateful.
- Third Google account blocked at Google sign-in; connector listed in the phone app for both accounts (author-confirmed).
- Five `/auth/callback` 500 errors (16:19–16:28 UTC Oct 4) preceded the first successful login on an earlier server process; none since. Watch for them during runs.

Known context before running (2026-10-03): voice mode states Claude "can use the tools you've connected", with examples only for first-party connectors; no official statement covers custom connectors. Two community reports (anthropics/claude-code#77312, anthropics/claude-ai-mcp#146, both closed "not planned") describe "tool … is not registered" in voice while text works.

## Raw results
| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |
| --- | --- | --- | --- | --- |

Store transcripts and payloads under `docs/spike/01-data/` (no personal data; redact names).

## Analysis

## Decision
