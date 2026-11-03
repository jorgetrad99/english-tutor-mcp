# Worked example: a numeric answer (authored instances)

Kind of practice: application. Grading kind `numeric`, `numeric_tolerance: 0.01` (answers rounded to two decimals). No pool: each problem is one authored instance. Practice kind: `quiz`.

Spec: `problem` (`text`, prompt), `answer` (`number`, answer_key), `worked_solution` (`text`, reveal). One instance per problem from the note, with the note's own numbers and result.

The server's part: it sends only `problem`, parses the learner's number by its closed rule (`3,14`, `-2`, `1/4`), and marks it right when it is within the tolerance of `answer`; `worked_solution` comes back only with the graded result.

The template's part (`numeric.html`): show the problem, a labelled field with `inputmode="decimal"`, call `tutorSubmit({"answer_kind": "number", "value": text})` with the text as typed, and define `window.tutorOnResult` to show the reveal when a channel delivers it.
