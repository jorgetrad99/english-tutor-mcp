# Worked example: ordering from a pool (a timeline)

Kind of practice: sequencing. Grading kind `exact_sequence`, `pool_draw: ordered`, `pool_count: 4`. Practice kind: `drill`.

Spec: one `pool` parameter `events`. Entries carry an `order` number: `{"cue": "Independencia de México", "answer": "1810", "order": 1810, "explain": "Grito de Dolores"}`.

The server's part: it draws four entries, sends their cues in a shuffled order that is never the right one (`drawn.cues`), and grades the learner's sequence against the cues sorted by `order`, with partial credit by longest common subsequence. `explain` comes back with the graded result.

The template's part (`ordering.html`): list the cues with "Subir" and "Bajar" buttons on each row (keyboard operable, focus follows the moved row), then call `tutorSubmit({"answer_kind": "sequence", "values": cues})` in the learner's order.
