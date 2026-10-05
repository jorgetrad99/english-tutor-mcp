# 04 — Grader reliability

Status: planned
Date(s): 2026-10-05 → 2026-10-11
Section 15 row: also measured: grader reliability

## Question
Grader reliability: variance of CEFR grades across repeats, clients and model versions (section 9).

## Pass criterion
> (none stated in section 15; this item is "not blocking, but they set the trust rules in sections 9 and 11")

If it fails (section 15): no fallback stated; not blocking.

Operational definition (design doc §8.5): `cefr_estimate.speaking` from every valid final payload, on an ordinal half-step scale (B1=0, B1+=1, B2=2, B2+=3, C1=4). Reported: distribution, mode, range, standard deviation in half-steps, share within ±1 half-step of the mode, jumps ≥ 1 full level between consecutive runs; split by account (default model per plan), mode and `model_shown`. The speaker's level is constant across the week, so the spread measures the grader.

## Setup
- Design: `docs/superpowers/specs/2026-10-03-spike-design.md`
- Data source: all valid payloads of experiment 02 (up to 25: 20 text, 5 voice)
- Server commit: `07a4a1d` (tag `spike-instructions-v1`)
- Model version(s): `model_shown` per run in `runs.csv`
- Number of runs planned: no extra runs
- Known limitation: one speaker; "across clients" covers Claude web vs mobile voice and Free vs Pro only (ChatGPT deferred)

## Raw results
| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |
| --- | --- | --- | --- | --- |

Store payload extracts under `docs/spike/04-data/` (no personal data; redact names).

## Analysis

## Decision
