# Template contract (guide version {{GUIDE_VERSION}})

A template is one complete HTML document (start with `<!doctype html>`), at most {{MAX_TEMPLATE_BYTES}} bytes of UTF-8, plus a parameter schema and a grading kind. Send this guide's version as guide_version to save_template.

**Structure.** Put the empty marker {{MARKER}} exactly once, as the first thing in `<head>`. Only these may come before it, in this order, with only spaces or line breaks between: `<!doctype html>`, `<html>` with plain attributes (such as `lang`) and `<head>` without attributes, each optional, then the required `<meta charset="utf-8">`. No comment, text, title or other tag goes before it; put everything else after it, and do not write `charset` anywhere after it. No other element may have id `tutor-params` or `tutor-bridge`. Read the payload with `JSON.parse(document.getElementById("tutor-params").textContent)`. Payload keys: {{PAYLOAD_KEYS}}. `params` holds prompt parameters only; `drawn` holds the draw (or null); for every `choice_index` template, pool or not, `drawn.options` is the list `option_index` counts in, so show those options; `labels` maps prompt parameter names to labels; use `ui_lang` for your own button texts.

**Parameters.** {{LIMITS}}

{{PARAM_TYPES}}

{{PARAM_ROLES}}

**Grading kinds and answer shapes.** A template only collects the answer; the server grades it against the stored key.

{{GRADING_KINDS}}

{{TEXT_MATCH}}

Numbers: {{NUMBERS}}.

**Pools and draws.** A pool template has exactly one parameter of type and role `pool`.

{{POOL_DRAWS}}

**needs_view** true: the item cannot be done from text; text mode skips it and shows no cue.

**Answers.** Call `tutorSubmit(answer)` once with the shape above. Optionally define `window.tutorOnResult(result)` to show a graded result. Displays never call tutorSubmit. What tutorSubmit does depends on the channel:

{{CHANNELS}}

**Sandbox.** No network, no external assets, no `src`, `href` or similar URL attributes except `#fragment` links, no `data:` URLs, no forms, no iframe, frame, object, embed, base, link or meta refresh, no `url(`, `@import` or `expression(` in CSS. Inline script and style only. Build the page with DOM methods and `textContent`; never put a parameter value into `innerHTML`.
