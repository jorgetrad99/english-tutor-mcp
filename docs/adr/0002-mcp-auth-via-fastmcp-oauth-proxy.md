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

Re-derived on 2026-10-05 against the installed source of fastmcp 4.0.11 and mcp 2.3.0, the versions locked in `uv.lock`. Cites are file plus symbol, paths relative to `site-packages`. A row marked "pending" depends on code that lands in a later task and is not claimed yet. Any "no" stays here as an accepted v0 gap with its mitigation.

The consent screen (`require_authorization_consent=True`, the library default, set explicitly in `build_google_provider`) stays on. Accepted: it adds one click on first connection and protects against confused-deputy attacks on the shared Google client.

| Section 5 rule | Who meets it | Verified |
| --- | --- | --- |
| Discovery metadata (RFC 9728 / RFC 8414) | Library (`mcp.http_app` serves both at the root) | Routes proven by `tests/unit/auth/test_mcp_auth.py::test_provider_serves_the_callback_and_discovery_routes` (provider routes for `/mcp`); the served-app check lands with the HTTP app: pending (Task 20) |
| Dynamic registration or pre-registered client; client metadata documents | Library (spike-proven with Claude) | Yes. `/register` is `OAuthProxy.register_client` (`fastmcp/server/auth/oauth_proxy/proxy.py`), which accepts only non-loopback https redirect URIs; CIMD is on (`enable_cimd=True` default); client redirect URIs are limited to `CLAUDE_REDIRECT_URIS` (`allowed_client_redirect_uris`) |
| PKCE S256 mandatory | Library | Yes. `AuthorizationRequest.code_challenge_method` is `Literal["S256"]` (`mcp/server/auth/handlers/authorize.py`); metadata advertises `code_challenge_methods_supported=["S256"]` (`mcp/server/auth/routes.py`); the proxy builds its own S256 pair for Google (`OAuthProxy._generate_pkce_pair`, `_build_upstream_authorize_url`) |
| Users linked by Google `sub`, never email alone | Ours (`tutor.auth.identity`) | Yes; `tests/unit/auth/test_auth_identity.py` (`test_users_are_keyed_by_sub_not_email`) |
| Access token JWT, 60 min | Library (FastMCP JWT; lifetime follows upstream) | Yes. `OAuthProxy.exchange_authorization_code` sets the FastMCP JWT lifetime from Google's `expires_in` (3600 s); `fastmcp_access_token_expiry_seconds=ACCESS_TOKEN_SECONDS` (3600) is also set explicitly; the library fallback is `DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS` = 1 h (`oauth_proxy/models.py`). Asserted in `test_provider_uses_the_v0_configuration` |
| Rotating refresh token, 30 days, single use | Library + our configuration | Yes for rotation: `OAuthProxy.exchange_refresh_token` issues a new refresh JWT and invalidates the old JTI on each use (see facts check of 2026-10-05). 30 days through `fallback_refresh_token_expiry_seconds=REFRESH_TOKEN_SECONDS`, asserted in the provider test; the library default `DEFAULT_REFRESH_TOKEN_EXPIRY_SECONDS` is 1 year (`oauth_proxy/models.py`). Gap accepted for v0: no reuse detection or token-family revocation |
| Token bound to the MCP resource (audience) | Library | Yes. `OAuthProxy.set_mcp_path` creates `JWTIssuer(audience=str(self._resource_url))`, i.e. `base_url + "/mcp"`; `JWTIssuer.verify_token` validates `aud` (`fastmcp/server/auth/jwt_issuer.py`). The JWT is a reference token: `OAuthProxy.load_access_token` re-validates the Google token with `GoogleTokenVerifier` (tokeninfo) |
| Every MCP request carries a Bearer token for one user | Library + ours (every tool resolves `sub`) | Token to user: `current_user_id` (tested). The 401 without a token and the per-tool identity middleware land with the HTTP app and MCP server: pending (Task 20) and pending (Task 21) |
| Audit of login and token issuance | Partly ours: `user_created`, `mcp_first_use` (plan ruling 3); token issuance inside the library is not audited in v0 | Gap, accepted for v0 |
| Secrets never logged | Ours (log policy, tests) | Settings and errors never print values (`tests/unit/test_settings.py`). The uvicorn access-log policy and the call log (`user_hash`, tool, outcome, latency) land later: pending (Task 20) and pending (Task 21) |

## Consequences

- **Good:** a working, maintained flow on day one; no hand-written token code; the same configuration the spike proved with Claude.
- **Bad:**
  - Token lifetimes and rotation are the library's, not ours.
  - Token issuance isn't audited.
  - The OAuth store sits outside Postgres, so it isn't in the nightly `pg_dump`; losing it forces users to reconnect.
  - Upgrades of FastMCP can change auth behaviour; the version is locked in `uv.lock` and its auth changelog is reviewed on every bump.
- **Revisit before the private beta (Feb 2027):** the external OAuth review (section 15 risk table) decides whether to keep the proxy or move to the MCP SDK's provider interfaces with our own Postgres storage.

## Build notes (2026-10-05)

Facts that changed or were settled while the v0 branch was built (spec Amendments 1, 8, 14, 23, 34). The Status stays "proposed" until the author's acceptance checks in `docs/v0/acceptance.md` (Inspector auth check, a real Claude Free connection) pass; then it becomes accepted.

- **Store.** `client_storage` is `FernetEncryptionWrapper(FileTreeStore(TUTOR_OAUTH_STORAGE_DIR), Fernet(TUTOR_OAUTH_STORAGE_KEY))` (`tutor.auth.mcp_auth`). The directory is created with mode 0700 on the `tutor_oauth` volume, owned by the image's uid 10001, and backed up nightly as a tarball (runbook "Backups"). The three app secrets (JWT key, storage key, web-session secret) must be distinct, and the server refuses to start otherwise.
- **Callback.** The proxy's Google callback is `/oauth/callback` (`GoogleProvider(redirect_path=MCP_CALLBACK_PATH)`), because the website's Authlib login owns `/auth/callback`. Both URIs are registered on the one Google OAuth client. `tutor.app.PathDispatch` routes `/authorize`, `/token`, `/register`, `/consent`, `/oauth/callback`, `/mcp` and `/.well-known/*` to the MCP app.
- **Consent screen.** `require_authorization_consent=True` is passed explicitly. CIMD stays on (Claude registers with a client metadata document), and client redirect URIs are limited to `https://claude.ai/api/mcp/auth_callback`.
- **Access tokens: 3600 s, set explicitly** (`fastmcp_access_token_expiry_seconds=ACCESS_TOKEN_SECONDS`), not only inherited from Google's `expires_in`.
- **Refresh tokens: 30 days from the first login, not a sliding window.** Google returns no `refresh_expires_in`, so the first exchange uses `fallback_refresh_token_expiry_seconds` (30 days). Each rotation then keeps the original expiry: `OAuthProxy.exchange_refresh_token` takes the "keep existing expiry if upstream doesn't provide new one" branch in `fastmcp/server/auth/oauth_proxy/proxy.py` (4.0.11). So a learner re-authorizes in Claude at most 30 days after connecting. Rotation (old refresh JTI refused) is as in the compliance table; reuse detection and family revocation remain an accepted gap.
- **Deletion leaves up to a 30-day refresh-token gap.** The encrypted store has no per-user index, so deleting a user cannot remove their OAuth entries. The cut is the learner revoking the app at their Google account: the proxy re-validates access tokens with Google and refreshes against Google, so revocation ends access at once. The deletion runbook asks for that, and for removing the Claude connector, before the SQL. Without the revocation, a refresh token keeps working for up to 30 days. With the `users` row gone it reaches no data, but a tool call would create a new, empty user from the same `sub`. Rotating `TUTOR_OAUTH_STORAGE_KEY` cuts everyone at once.
- **Token issuance is not audited** (unchanged gap). The audit log has `user_created` and `mcp_first_use` (the first tool call per user, written by our identity middleware), plus `web_login` for the website. The library's issuance and refresh are not audited. Its `fastmcp.server.auth` logger is silenced, because it logs authorization codes, so `RequestLog` writes one line per OAuth route (route template, status, latency, never the query string).
- **Pending rows closed by later tasks.** Several compliance rows were marked "pending" when written; tests now cover them:
  - the served discovery metadata and the 401 with `resource_metadata` (`tests/unit/app/test_app_http.py`: `test_mcp_without_token_is_401_pointing_to_the_resource_metadata`, `test_authorization_server_metadata_is_at_the_root`);
  - the per-tool identity check, where a call without a token gets the fixed "Not authorized" protocol error (`tests/unit/mcp/test_mcp_server.py::test_calls_without_a_token_are_refused`, and on Postgres `tests/integration/test_mcp_lesson_pg.py::test_a_call_without_a_token_is_refused`);
  - the log policy, with the uvicorn access log off and `uvicorn.error` scrubbed (`tests/unit/app/test_tutor_main.py`).

  The checks against the deployed host and Claude are in `docs/v0/acceptance.md` section 1.
