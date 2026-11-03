# Display templates

A display shows lesson content; it is not graded: `template_purpose: display`, `grading_kind: none`, no answer_key, no reveal, no pool, and it never calls tutorSubmit. Its instance's prompt_text is a caption that stands in for the view in text mode.

Everything visible must come from parameters, so the text view can show the same content:

- Tables and timelines: one `text_table` parameter (rows of cells), drawn as a real `<table>` with `<th scope="col">` headers. A timeline is a two-column table (date, event).
- Diagrams: inline `<svg>` built with `document.createElementNS` from numeric or list parameters (`integer_list`, `number_list`, `text_list`); give the svg a `role="img"` and an `aria-label`, and print the values beside it.
- Code: a `text` parameter in `<pre><code>` set with `textContent`; never executed.
- Notation in plain text (tablature, chord charts, formulas): a `text` parameter in a monospace `<pre>`, or numbers in an `integer_list`.

Reuse one display template for every lesson with the same shape; a new table is new parameters, not a new template.

## Worked display example: a table

`display_table.html` draws any `text_table` with an optional caption. Spec: one prompt parameter `table` (`text_table`) and an optional `caption` (`text`). The template reads `params.table`, uses the first row as headers and builds the table with DOM methods. The server's part is only to validate the rows and send them as JSON; the layout is the template's business.
