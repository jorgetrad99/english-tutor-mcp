# Worked example: a vocabulary flashcard from a pool

Kind of practice: recall. Grading kind `exact_text`, `pool_draw: one`, `text_match: keep_accents` (accents change meaning in the language studied). Practice kind: `drill`.

Spec: one parameter `cards` of type and role `pool`. One instance per vocabulary list holds entries such as `{"cue": "the bill", "answer": "la cuenta", "accept": ["cuenta"]}`.

The server's part: at each showing it draws one entry, sends only `drawn.cue`, and grades the learner's text against that entry's `answer` and `accept` after `keep_accents` normalisation (case, spaces and surrounding punctuation are ignored; accents count).

The template's part (`language_flashcard.html`): show the cue, a labelled text field, a "Comprobar" button and Enter to submit; call `tutorSubmit({"answer_kind": "value", "value": text})`. It never sees the answer.
