# 0002 — MCP authorization through the FastMCP Google OAuth proxy

- **Status:** proposed (accepted when the core loop v0 spec is approved; compliance table completed during planning)
- **Date:** 2026-10-04
- **Deciders:** Jorge
- **Spec:** `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md` (D5)
- **Number note:** 0001 is reserved for the spike go/no-go on Oct 11.

## Context

Requirements section 4 names "Authlib (OIDC with Google) + own OAuth 2.1 authorization server with PKCE and dynamic client registration". Section 5 lists the rules that server must meet.

The spike connected Claude (web and mobile, Free and Pro) through FastMCP's `GoogleProvider`, an OAuth proxy that:
- serves the MCP authorization-server metadata;
- accepts Claude's client registration (Claude used a client metadata document);
- sends the user to Google;
- issues its own JWTs to the client.

Setup acceptance on 2026-10-04 confirmed it works with both plans (`docs/spike/01-voice-mode-tool-calls.md`).

v0 serves about four users and is built by one part-time developer. An own authorization server is about 1.5–2 extra weeks of the most security-sensitive code in the project. The section 15 risk table already says "use a maintained library".

## Decision

v0 authorizes MCP clients with FastMCP's `GoogleProvider`, configured for production as the FastMCP docs require:

- a dedicated `jwt_signing_key` from the environment;
- `client_storage` = `FernetEncryptionWrapper(DiskStore(<named Docker volume>), Fernet(OAUTH_STORAGE_ENCRYPTION_KEY))`;
- `allowed_client_redirect_uris` limited to the Claude callback;
- scopes `openid` and email only.

Tools identify the user by `get_access_token().claims["sub"]` mapped to `users.google_sub`, never by email.

The website keeps its own Authlib Google login with a server-side session cookie (dashboard spec 9.3). Both paths resolve to the same `users` row.

## Compliance with requirements section 5

The planning step fills the "Verified" column from Context7 and the FastMCP source for the pinned version. Any "no" stays here as an accepted v0 gap with its mitigation.

| Section 5 rule | Who meets it | Verified |
| --- | --- | --- |
| Discovery metadata (RFC 9728 / RFC 8414) | Library (`get_well_known_routes` at root) | pending |
| Dynamic registration or pre-registered client; client metadata documents | Library (spike-proven with Claude) | pending |
| PKCE S256 mandatory | Library | pending |
| Users linked by Google `sub`, never email alone | Ours (`tutor.auth`) | by design |
| Access token JWT, 60 min | Library (FastMCP JWT; lifetime follows upstream unless configured) | pending |
| Rotating refresh token, 30 days, single use | Library | pending |
| Token bound to the MCP resource (audience) | Library | pending |
| Every MCP request carries a Bearer token for one user | Library + ours (every tool resolves `sub`) | by design |
| Audit of login and token issuance | Partly ours: `user_created`, `mcp_client_seen`; token issuance inside the library is not audited in v0 | gap, accepted for v0 |
| Secrets never logged | Ours (log policy, tests) | by design |

## Consequences

- **Good:** a working, maintained flow on day one; no hand-written token code; the same configuration the spike proved with Claude.
- **Bad:**
  - Token lifetimes and rotation are the library's, not ours.
  - Token issuance isn't audited.
  - The OAuth store sits outside Postgres, so it isn't in the nightly `pg_dump`; losing it forces users to reconnect.
  - Upgrades of FastMCP can change auth behaviour; the version is pinned and its auth changelog is reviewed on every bump.
- **Revisit before the private beta (Feb 2027):** the external OAuth review (section 15 risk table) decides whether to keep the proxy or move to the MCP SDK's provider interfaces with our own Postgres storage.
