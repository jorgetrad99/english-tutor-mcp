# Development harness

## Plugins (project scope, `.claude/settings.json`)

From `claude-plugins-official`: superpowers, pyright-lsp, commit-commands, pr-review-toolkit, security-guidance.

## Rejected

- **caveman / terse-output skills**: small savings on agentic work, and they risk degrading the English we write into tool descriptions, `instructions` and `response_rules`, which is product content (requirements §12).
- **graphify / codebase-graph tools**: the repo is empty; revisit when `src/` exceeds ~60 files.
- **"Mega packs" of agents/skills**: overlapping instructions fight with Superpowers and inflate context.
- **Playwright**: no frontend until after the validation gate.
