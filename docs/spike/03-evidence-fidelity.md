# 03 — Evidence fidelity

Status: planned
Date(s): 2026-10-05 → 2026-10-11
Section 15 row: also measured: evidence fidelity

## Question
Evidence fidelity: recall/precision of `end_session` payloads against real transcripts (section 11).

## Pass criterion
> recall and precision of `user_turns` (≥ 0.9 / ≥ 0.9), of `errors` against a human-annotated error list (≥ 0.7 / ≥ 0.8), and of `chunks_used` (≥ 0.8)

(Section 11 thresholds, verbatim. Section 15 marks this item as not blocking.)

If it fails (section 15): no fallback stated; not blocking. Results feed the trust rules in section 11.

Operational definitions (design doc §8.4):
- `user_turns`: all 20 text runs, and voice runs if claude.ai shows a transcript. A payload turn matches when its normalized text is a substring of a transcript user message. Fuzzy variant (difflib ratio ≥ 0.9) reported as secondary.
- `errors`: 10 text runs (5 Free, 5 Pro). The author annotates `said`/`correct` from the transcript **before** seeing that run's payload. A payload error matches an annotation when one normalized `said` contains the other.
- `chunks_used`: not measurable (the spike issues no chunks); any non-empty value is counted as a fabrication.

## Setup
- Design: `docs/superpowers/specs/2026-10-03-spike-design.md`
- Data source: the text runs of experiment 02 (same server commit, clients, model versions)
- Server commit: `07a4a1d` (tag `spike-instructions-v1`)
- Transcripts: copied from claude.ai web after each run (design doc §6.3)
- Annotation: blind, by the author, on 2026-10-10
- Number of runs planned: 20 for `user_turns`, 10 for `errors`
- Known limitation: self-annotation by a B1–B2 speaker is weaker ground truth than a teacher's
- Also checks section 11's assumption that voice sessions have no transcript

## Raw results
| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |
| --- | --- | --- | --- | --- |

Store transcripts, annotations and payloads under `docs/spike/03-data/` (no personal data; redact names). Text transcripts, annotations and payloads are also copied to `evals/fixtures/`.

## Analysis

## Decision
