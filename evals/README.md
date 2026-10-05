# Evals

LLM-in-the-loop evaluations. They never run in hooks, `just check-fast` or `just check`: every eval is marked
`@pytest.mark.eval`, and those recipes select `not eval`. Run them with `uv run just eval`.
The tooling's own unit tests (`test_fidelity_tools.py`) are not evals and run in `just check`.

## Structure (core loop v0)

```
evals/
  fidelity/        tooling copied from the spike: metrics, annotate, redact, report
  fixtures/
    transcripts/   <name>.md (redacted text transcript) and <name>.payload.json (raw_evidence)
    annotations/   <name>.json: {"errors": [{"said": ...}], "chunks_used": ["it-07-c3", ...]}
  raw/             git-ignored: unredacted material, never committed
  test_fidelity_tools.py     unit tests of the tooling (run in `just check`)
  test_evidence_fidelity.py  the eval (marked eval; `just eval`)
```

Committed fixtures are synthetic or redacted. Real learner text stays under the git-ignored `raw/`
until step 4 below has run on a copy.

Workflow for one real text session (names like `2026-11-18-author-01`):

1. Copy the claude.ai conversation into `raw/<name>.md` as `U:` / `A:` lines.
2. Export its payload on the server:
   `docker compose -f deploy/compose.prod.yml exec -T db psql -U tutor -d tutor -At -c "SELECT raw_evidence FROM sessions WHERE id = '<session id>'" > raw/<name>.payload.json`
   (the session id is the last part of its Sesiones page URL).
3. From `evals/`: `uv run python -m fidelity.annotate raw/<name>.md`, then write
   the annotation by hand, without looking at the payload.
4. From `evals/`: `uv run python -m fidelity.redact --names "<every name>" <the three files>`, then
   move them to `fixtures/transcripts/` and `fixtures/annotations/`.
5. `uv run just eval-fidelity` prints recall and precision and exits 1 below a threshold.

## Evidence fidelity (requirements section 11)

Compare each `end_session` payload with the real transcript and the human annotation:

| Field | Recall | Precision |
| --- | --- | --- |
| `user_turns` vs transcript | ≥ 0.9 | ≥ 0.9 |
| `errors` vs annotated error list | ≥ 0.7 | ≥ 0.8 |
| `chunks_used` | ≥ 0.8 | — |

Track results per client and model version as `evidence_fidelity`. A drop below threshold
blocks shipping changes to the server `instructions`. Voice sessions have no transcript, so
their fidelity is assumed equal to text; say so wherever the metric is shown.

## Session eval set (requirements section 12)

30 scripted text sessions run against Claude on every `instructions` change. Pass:
`user_ratio` ≥ 0.4, no markdown in conversation turns, `end_session` called in 100% of runs.
