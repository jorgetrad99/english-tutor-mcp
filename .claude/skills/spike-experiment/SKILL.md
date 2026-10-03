---
name: spike-experiment
description: How to plan and record a week-0 spike experiment in docs/spike/NN-name.md (voice-mode tool calls, end_session reliability, evidence fidelity, grader reliability, cross-client consistency). Use when starting, running or writing up a spike experiment from requirements section 15.
---

# Recording a spike experiment

Source of truth: `docs/requirements.md` section 15 (blocking unknowns table and "Also measured in the spike"). Read only that section. Spike code is throwaway: keep it in `spike/` or behind the minimal tools section 15 names, and never build beyond it.

## File
`docs/spike/NN-kebab-name.md`, `NN` = next two-digit number (`01-voice-mode-tool-calls.md`). One experiment per file.

## Template
```markdown
# NN — <short title>

Status: planned | running | done
Date(s): YYYY-MM-DD → YYYY-MM-DD
Section 15 row: <# or "also measured: <name>">

## Question
<copy the question from section 15>

## Pass criterion
> <copied VERBATIM from section 15; do not paraphrase or soften>

If it fails (section 15): <copied verbatim>

## Setup
- Server commit: <sha>; tools exposed: <list>
- Clients and plans: <e.g. Claude iOS, Free + Pro accounts>
- Model version(s) as shown by the client
- Script / prompts used: <path or inline>
- Number of runs planned: <n>

## Raw results
| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |
| --- | --- | --- | --- | --- |

Store transcripts and payloads under `docs/spike/NN-data/` (no personal data; redact names).

## Analysis
Numbers only: counts, rates against the criterion, and anything that broke.

## Decision
PASS | FAIL | INCONCLUSIVE — <one line>.
Consequence: <architecture choice or fallback adopted, per section 15>.
ADR: <docs/adr/NNNN-title.md if the decision changes the architecture>
```

## Rules
- Write Question, Pass criterion and Setup BEFORE running anything.
- Record every run, including failed and aborted ones; never drop outliers silently.
- The decision follows the pre-written criterion; if you want to change the criterion, stop and ask the author.
- When all blocking rows are answered, run the `phase-auditor` agent and record the go/no-go in an ADR.
