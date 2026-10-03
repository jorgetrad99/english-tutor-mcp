---
name: mcp-tool-authoring
description: Checklist for adding or changing an MCP tool, prompt or the server instructions in src/tutor/mcp (schemas, response_rules, tests, contract review). Use whenever a task creates or modifies an MCP tool's inputs, outputs, description or behaviour. Not for REST API, auth or worker code.
---

# Adding or changing an MCP tool

Source of truth: `docs/requirements.md` sections 7 (tools table, `end_session` schema) and 12 (output governance). Read those two sections first. Use Context7 for the MCP Python SDK / FastMCP API; never rely on memory.

## 1. Name and compatibility
- New tool: snake_case verb, matches section 7 if listed there.
- Existing tool: names and schemas are stable. A breaking change (removed/renamed field, narrowed enum, new required field) ships as a NEW tool name; the old one stays.

## 2. Input model (Pydantic)
- `model_config = ConfigDict(extra="forbid")`: unknown fields are rejected.
- Every enum closed (`Literal[...]` or `Enum`), exactly the values in section 7.
- Every field: `Field(title=..., description=...)`; descriptions are instructions to the model, so write plain, correct English.
- Explicit bounds: list `max_length`, ints `ge`/`le`, string `max_length`; respect 64 KB per call and 20 KB raw evidence.
- User-supplied text is data: never interpolate it into descriptions, `response_rules` or `instructions`.

## 3. Description and result
- Tool description ≤ 120 words: when to call it and what to do with the result.
- Result is structured JSON and always includes `response_rules` (2–4 lines bound to the next action, e.g. "speak only the scenario opener, ≤ 40 words, stay in character").
- If the LLM must say something from the result, provide `summary_text`; it must never read JSON aloud.
- The server computes counts, levels, schedules and grades; the tool takes evidence, not conclusions.
- State idempotency and limits from section 7 in code (e.g. `end_session` idempotent per `session_id`).
- Every query filters by the token's `user_id`.

## 4. Tests (TDD: write them first)
Unit tests in `tests/unit/mcp/`, marked `@pytest.mark.unit`:
- valid minimal payload; valid full payload
- unknown field → rejected; out-of-enum value → rejected
- each bound: list too long, empty required list, oversize string/payload
- idempotency: second identical call returns the same result, no duplicate rows
- `response_rules` present and contains no user text
- Use `hypothesis` for normalisation and validation logic in `tutor.domain`.

Integration test in `tests/integration/`, marked `@pytest.mark.integration`, through the MCP Inspector CLI against a running server:
```
npx @modelcontextprotocol/inspector --cli --transport http --server-url http://localhost:8000/mcp \
  --header "Authorization: Bearer $TOKEN" --method tools/call --tool-name <tool> \
  --tool-args-json '{"...": "..."}' --format json
```
Also run `--method tools/list` and assert the tool's schema (`additionalProperties: false`, titles, descriptions).

## 5. Done
1. `uv run just check` is green.
2. Run the `mcp-contract-reviewer` agent on the diff; fix every BLOCKER and MAJOR.
3. If the change touches auth or data access, also run `security-reviewer`.
4. Record a non-obvious design choice in `docs/adr/NNNN-title.md`.
