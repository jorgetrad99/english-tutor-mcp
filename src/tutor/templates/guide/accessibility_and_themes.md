# Accessibility and themes

- Use real `<button type="button">` elements with visible text for every action; never a clickable `<div>`.
- Give every input a `<label>`; group options in a `role="radiogroup"` or a list of buttons with `aria-pressed`.
- Everything works with the keyboard: Tab order follows the page, Enter submits a text answer, arrow-key or up/down buttons reorder lists. Never trap focus.
- Show focus: keep the outline, or style `:focus-visible` with a clear ring.
- Never rely on colour or sound alone: put text beside every colour cue (a "correct" word, not only green) and a visible label beside any sound.
- Announce results in an element with `role="status"` (or `aria-live="polite"`).
- Themes: define colours as CSS variables, give dark values under `@media (prefers-color-scheme: dark)`, and prefer the host's theme variables when present (for example `var(--color-text, #1a1a1a)`). Keep contrast at 4.5:1 or more.
- Labels: write your own button texts in `ui_lang` from the payload (a small table of strings per language, Spanish for `es`); parameter labels come in `labels`.
- Size: fit a narrow phone width; no horizontal scrolling; text at least 16px.
