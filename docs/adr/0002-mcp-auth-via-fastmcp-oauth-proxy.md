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
- `client_storage` = `FernetEncryptionWrapper(FileTreeStore(<named Docker volume>), Fernet(TUTOR_OAUTH_STORAGE_KEY))`, the library's own default store type. `DiskStore` was dropped: its `diskcache` dependency has an advisory with no fixed release (CVE-2025-69872) that fails `pip-audit`. A wrong or rotated key reads as a cache miss, so clients register again;
- `allowed_client_redirect_uris` limited to the Claude callback;
- scopes `openid` and email only.

Tools identify the user by `get_access_token().claims["sub"]` mapped to `users.google_sub`, never by email.

The website keeps its own Authlib Google login with a server-side session cookie (dashboard spec 9.3). Both paths resolve to the same `users` row.

## Compliance with requirements section 5

Verified on 2026-10-05 against the installed source of fastmcp 4.0.11 and mcp 2.3.0 (paths relative to `site-packages`) and by the tests named below. Any "no" stays here as an accepted v0 gap with its mitigation.

| Section 5 rule | Who meets it | Verified |
| --- | --- | --- |
| Discovery metadata (RFC 9728 / RFC 8414) | Library (`mcp.http_app` serves both at the root) | Yes. `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource/mcp` come from `auth.get_routes` (`fastmcp/server/http.py:601-605`); the PRM `resource` is `base_url + "/mcp"` (`tests/unit/app/test_app_http.py`) |
| Dynamic registration or pre-registered client; client metadata documents | Library (spike-proven with Claude) | Yes. `/register` (`fastmcp/server/auth/oauth_proxy/proxy.py:977+`; https, non-loopback redirect URIs only, 1033-1038); CIMD on by default (proxy.py:699-702, 941-948); client redirect URIs limited to `CLAUDE_REDIRECT_URIS` |
| PKCE S256 mandatory | Library | Yes. `code_challenge` is required and `code_challenge_method` is `Literal["S256"]` (`mcp/server/auth/handlers/authorize.py:32-33`); metadata advertises `["S256"]` (`mcp/server/auth/routes.py:185`); the proxy uses its own S256 pair with Google (proxy.py:869-902) |
| Users linked by Google `sub`, never email alone | Ours (`tutor.auth.identity`) | By design; `tests/unit/auth/test_auth_identity.py` |
| Access token JWT, 60 min | Library (FastMCP JWT; lifetime follows upstream) | Yes. The JWT lifetime mirrors Google's `expires_in`, 3600 s (proxy.py:1355-1356); fallback 1 h (`oauth_proxy/models.py:28`) |
| Rotating refresh token, 30 days, single use | Library + our configuration | Yes. Each refresh issues a new refresh JWT and deletes the old JTI, "one-time use" (proxy.py:1767+, ~1990); a reused token gets `invalid_grant` (proxy.py:1731-1750). 30 days through `fallback_refresh_token_expiry_seconds=REFRESH_TOKEN_SECONDS`; the library default is 1 year (models.py:32, proxy.py:1404-1412). Gap accepted for v0: no reuse detection or token-family revocation |
| Token bound to the MCP resource (audience) | Library | Yes. `aud` = `base_url + "/mcp"` (proxy.py:766-790, `fastmcp/server/auth/jwt_issuer.py:141-142`), checked on every request (jwt_issuer.py:285); the JWT is a reference token whose Google token is re-validated with tokeninfo (proxy.py:2152-2230) |
| Every MCP request carries a Bearer token for one user | Library + ours (every tool resolves `sub`) | Yes. `/mcp` without a token is 401 with `resource_metadata` (`tests/unit/app/test_app_http.py`); `IdentityMiddleware` resolves `sub` on every tool call |
| Audit of login and token issuance | Partly ours: `user_created`, `mcp_first_use` (plan ruling 3); token issuance inside the library is not audited in v0 | Gap, accepted for v0 |
| Secrets never logged | Ours (log policy, tests) | By design: uvicorn runs without its access log (it would print `/oauth/callback?code=…`); the call log holds only `user_hash`, tool, outcome and latency (`tests/unit/mcp/test_mcp_server.py`) |

## Consequences

- **Good:** a working, maintained flow on day one; no hand-written token code; the same configuration the spike proved with Claude.
- **Bad:**
  - Token lifetimes and rotation are the library's, not ours.
  - Token issuance isn't audited.
  - The OAuth store sits outside Postgres, so it isn't in the nightly `pg_dump`; losing it forces users to reconnect.
  - Upgrades of FastMCP can change auth behaviour; the version is pinned and its auth changelog is reviewed on every bump.
- **Revisit before the private beta (Feb 2027):** the external OAuth review (section 15 risk table) decides whether to keep the proxy or move to the MCP SDK's provider interfaces with our own Postgres storage.
