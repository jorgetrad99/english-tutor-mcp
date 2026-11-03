# 0004 — A subjects context beside the English tutor

- **Status:** proposed
- **Date:** 2026-11-02
- **Deciders:** Jorge
- **Spec:** `docs/superpowers/specs/2026-11-02-subjects-and-notes-design.md`
- **Requirement edit:** E37 in `docs/v0/requirements-edits.md`
- **Number note:** 0001 is reserved for the spike go/no-go.
- **Amended by:** `0005-exercise-templates.md` (decision 6: free per-note HTML artifacts replaced by templates; decision 8: 0006 rewritten before merge).

## Context

The requirements describe one product: an English tutor whose content the server owns, with a validation gate on November 30 and a rule that nothing beyond the core loop is built before it (section 16). On 2026-11-02 the author decided to add a second line: the server also holds what a learner studies on their own (first case: a paid guitar course), fed by Claude taking notes in the browser and uploading them through MCP tools, searchable, with a practice plan and interactive exercises.

Three stated rules were in the way:

- principle 5 (section 2): no file uploads;
- section 4: no vector databases, and no LLM API calls in the lesson loop;
- section 16: no new scope before the gate.

Research done the same day established: MCP Apps (`io.modelcontextprotocol/ui`) render in claude.ai, Claude Desktop, iOS and Android for custom connectors, with a text-only fallback elsewhere, and fastmcp 4.0.11 (the pinned version) supports them; Postgres 16 full-text search with two language configurations, `pg_trgm` and a concept graph in plain tables covers deterministic retrieval at the expected scale (thousands of notes per user) without embeddings; LLM-authored HTML can be shown safely from its own URL under a `Content-Security-Policy: sandbox` header in a sandboxed frame.

## Decision

1. **A new bounded context, not a generalisation.** Subjects, sources, notes, passages, artifacts, exercises, quiz items, concepts, links, practice plans, sessions and items get their own tables, repos, services, tool names and pages. English tools, tables and migrations are not changed. Tool names stay stable on both sides.
2. **The server computes, the LLM reports, still.** Claude extracts and authors (notes, concepts, quiz items, exercise descriptions, lesson views); the server stores, indexes, searches, schedules, draws and grades. No LLM API call is added to the server. Retrieval is deterministic and explains itself.
3. **No vector database.** Search is Postgres full-text search plus a concept graph. `pgvector` is not adopted; if recall on real notes proves insufficient, a later ADR decides, with the cost of an embedding call at ingest and at query time stated.
4. **Uploads, narrowly.** The website accepts a PDF for a subject (text extracted by `pypdf`, no LLM). MCP tools accept text only, under caps. Principle 5 is edited accordingly (E37).
5. **MCP Apps enter the stack** for the practice view only: one versioned `ui://` resource, self-contained, no SDK, no external domains, with a text fallback that is part of the contract. Spike 05 verifies it before the practice slice ships.
6. **LLM-authored HTML is data.** Stored with caps and a deny-list validator, served from its own URL under a `sandbox allow-scripts` policy with no network, embedded in a sandboxed frame, never with `srcdoc` or `allow-same-origin`.
7. **Phase rule exception.** Subjects v0 is built beside the gate, for the author's own use, without moving the gate's dates or criteria. The phase auditor reads E37 as the authority for this line.
8. **Migrations are additive and written once** (`0006_subjects_and_notes`, `0007_practice`); the queued topics migration becomes `0008`. Production data is untouched.

## Consequences

- Two product lines share one server, one login and one database. The English line keeps its gate; the subjects line has its own acceptance in E37.
- Raw lesson text is stored verbatim for one account, by the author's choice for personal use. Before any sharing or public feature, this and the artifact origin (a dedicated subdomain with signed URLs) must be revisited; both are listed in the spec as out of scope.
- The MCP surface grows from nine to twenty tools. Descriptions, enums and response rules stay under the same contract tests; the instructions string gains one paragraph and stays under 400 words.
- Two new Postgres extensions (`unaccent`, `pg_trgm`, both trusted) and two text search configurations are created by migration; backups and the restore drill must include them (runbook).
- The practice view depends on host behaviour that is documented but not under our control (height, caching, consent prompts). The spike records what holds; the text fallback keeps the feature usable where it does not.
- Local development needs a token path without Google; `2026-11-02-dev-environment-design.md` provides it.
