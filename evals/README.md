# Evals

LLM-in-the-loop evaluations. They never run in hooks, `just check-fast` or `just check`;
pytest only collects `tests/` by default, and every eval is marked `@pytest.mark.eval`.
A dedicated `just eval` recipe is added when the first real eval lands (after the spike).

## Planned structure

```
evals/
  fixtures/
    transcripts/   real text-session transcripts (redacted), one file per session
    annotations/   human-annotated ground truth per transcript: user turns, errors, chunks used
  test_*.py        eval tests, marked eval
```

A transcript and its annotation share a basename (`2026-10-07-claude-pro-01.*`), plus the
`end_session` payload the client produced for that session.

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
