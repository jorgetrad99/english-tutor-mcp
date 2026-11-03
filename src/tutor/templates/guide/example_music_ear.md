# Worked example: a music ear item from a pool

Kind of practice: recognition by listening. Grading kind `choice_index`, `pool_draw: one_of_choices`, `pool_count: 4`, `needs_view: true` (it plays a sound, so text mode skips it). Practice kind: `drill`.

Spec: one `pool` parameter `notes`. Entries such as `{"cue": "A4", "answer": "La", "accept": ["A"]}`: the cue is a pitch name with its octave, the answer is what the learner picks.

The server's part: it only handles text. It draws one entry plus three entries with other answers, sends `drawn.cue` and `drawn.options` (the four answers in a seeded order), and grades the chosen index. It knows nothing about notes, octaves or frequencies.

The template's part (`music_ear.html`): this is where the field-specific work lives. It turns the cue into a frequency itself (A4 = 440 Hz, twelve equal semitones per octave), creates an `AudioContext` inside the tap handler of a "Escuchar" button (browsers block sound before a gesture), plays a short tone, shows the four options as buttons and calls `tutorSubmit({"answer_kind": "choice_index", "option_index": i})`. A visible label says that a sound plays.
