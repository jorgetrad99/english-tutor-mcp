# Local development environment: design

- **Status:** proposed 2026-11-02 from the environment audit; awaiting the author's review
- **Date:** 2026-11-02
- **Author:** Jorge (decisions); drafted with Claude
- **Phase:** tooling; no product scope. Needed by Subjects v0 (`2026-11-02-subjects-and-notes-design.md`) so the author and Claude can test the MCP tools and the website without the tunnel or Google.
- **Builds on:** `docs/v0/local.md`, `deploy/compose.local.yml`, ADR 0002 (OAuth proxy), ADR 0003 (Coolify).

## 1. Goal

Today the only way to call the MCP server locally is a real Google OAuth flow through the production image, which Claude Code, Claude Desktop and MCP Inspector cannot complete (loopback redirect URIs are refused by design), and every code change needs an image rebuild. There is no seeded user and no worker service.

After this change, `uv run just dev` brings up Postgres, the app with hot reload and a migrated, seeded database, and prints an MCP URL plus a bearer token that MCP Inspector, Claude Code and Claude Desktop can use at once. The website signs in with one click on a dev-only route. `db-test` on port 5433 and `just test-int` are unchanged. Nothing in this design can run in production.

## 2. Decisions

| # | Decision |
| --- | --- |
| D1 | New setting `TUTOR_DEV_AUTH` (default off). It is refused at startup unless `TUTOR_ENV` is `dev` or `test` and `TUTOR_BASE_URL` is loopback, the same guard as test login. With it on, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` become optional. |
| D2 | With dev auth on, `build_app` uses a FastMCP JWT verifier (HS256, key `TUTOR_JWT_SIGNING_KEY`, issuer = base URL, audience = MCP URL) instead of the Google OAuth proxy. The exact verifier class is confirmed with Context7 against fastmcp 4.0.11 at implementation time; `StaticTokenVerifier` is the fallback. |
| D3 | `python -m tutor.ops.dev_token [--sub dev-ana] [--hours 12]` mints a token with `sub`, `email`, `name`, `iss`, `aud`, `exp` using `authlib.jose` (already a dependency), refuses outside dev auth, and prints the MCP URL, the token and ready-to-paste lines for MCP Inspector, Claude Code and Claude Desktop (`mcp-remote`). |
| D4 | `python -m tutor.ops.dev_seed` is idempotent and goes through the services layer: `dev-ana` (onboarded, one saved profile) and `dev-nuevo` (onboarding pending). No English content is seeded beyond the migrations. |
| D5 | Website: `WebConfig.dev_auth` and `GET /auth/dev-login?as=dev-ana` sign the browser in as that dev identity through the existing `users.sign_in`. `pg_web_deps` accepts dev auth on Postgres; test login stays memory-only. The route does not exist when the flag is off (not registered, so the CSRF route walker and the sweep test see nothing). |
| D6 | `deploy/Dockerfile` gets a `dev` target: the build stage plus the dev dependency group, running `uvicorn --factory tutor.dev:create --reload --reload-dir /src/src --host 0.0.0.0 --port 8000`. `src/tutor/dev.py` exposes `create()` = `Settings.from_env()` → `build_app`. |
| D7 | Root `docker-compose.yml` gains a `dev` profile: `migrate-dev` (alembic upgrade head, `provision_roles`, `dev_seed`; owner URL `tutor:tutor@db`), `app-dev` (target `dev`, `./src` bind-mounted, `127.0.0.1:8000:8000`, `TUTOR_DEV_AUTH=1`, app URL as `tutor_login@db`, depends on `migrate-dev` success), and `inspector` (`node:22-alpine` running `npx @modelcontextprotocol/inspector`, ports `127.0.0.1:6274` and `6277`). `db` and `db-test` stay as they are, without a profile. |
| D8 | Secrets for the dev profile live in git-ignored `deploy/dev.env`, generated once by `scripts/dev_env.py` (random signing key, Fernet key, app and report passwords). The file is never committed; `deploy/dev.env.example` lists the keys and the env-example test covers it. |
| D9 | `just` recipes: `dev` (generate env if missing, `docker compose --profile dev up -d --build --wait app-dev`, then print the token), `dev-token`, `dev-logs`, `dev-down`, `dev-reset` (refuses unless `CONFIRM=1`, because it drops the `pgdata` volume), `inspector-dev` (opens the Inspector service URL). The existing `inspector` recipe stays for host Node. |
| D10 | Production safety: `tests/unit/deploy/test_coolify_compose.py` and the prod compose test assert `TUTOR_DEV_AUTH` is absent from `compose.prod.yml`, `compose.coolify.yml` and `compose.local.yml`; a unit test asserts `Settings` refuses the flag with `TUTOR_ENV=prod` or a non-loopback base URL; tokens expire within 24 hours at most. |

## 3. How each client connects

| Client | How |
| --- | --- |
| MCP Inspector (service) | `http://localhost:6274`, transport Streamable HTTP, URL `http://app-dev:8000/mcp` from inside the compose network, header `Authorization: Bearer <token>` |
| MCP Inspector CLI (host Node) | `npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --header "Authorization: Bearer <token>" --method tools/list` |
| Claude Code | `claude mcp add --transport http tutor-dev http://localhost:8000/mcp --header "Authorization: Bearer <token>"` |
| Claude Desktop | `claude_desktop_config.json` entry running `npx mcp-remote http://localhost:8000/mcp --header "Authorization:Bearer <token>"` |
| claude.ai, mobile | unchanged: the tunnel and real Google OAuth (`docs/v0/local.md`) |
| Website | `http://localhost:8000/auth/dev-login?as=dev-ana` |
| Claude in this repo | the FastMCP in-process `Client` in tests; `curl` against `localhost:8000/mcp` with the token; the Inspector CLI |

## 4. Security

- The flag cannot be enabled in production: startup refuses, the prod compose files do not carry it, and a test pins both.
- Dev tokens are signed with the local signing key only; the key in `deploy/dev.env` is random per machine and git-ignored.
- The dev login route exists only when the flag is on and signs in only the two seeded identities (`as` is a closed enum).
- The `security-reviewer` agent reviews the diff (auth, app wiring, compose).

## 5. Testing and done criterion

Done when `uv run just check` passes and `uv run just dev` followed by the Inspector CLI `tools/list` returns the nine English tools on a fresh machine.

- Unit: settings guard; verifier accepts `dev_token` output and rejects a wrong audience, issuer, expired token; `dev_seed` idempotent (integration, `db-test`); dev login route present only with the flag; env example keys.
- Integration: `migrate-dev` command sequence against `db-test` (alembic head, roles, seed).
- Manual, recorded in `docs/v0/local.md`: Claude Code and Claude Desktop connect and call `get_profile`.

## 6. Out of scope

- A worker service (no worker code exists yet).
- Running the tunnel locally.
- Windows-native (non-Docker) hot reload; `uv run just serve-local` stays as it is.

## Amendments

Recorded at implementation time (2026-11-02, branch `feat/dev-environment`); the plan's "Rulings" section has the reasons.

- **D2.** Confirmed with Context7 (`/prefecthq/fastmcp`) against the installed fastmcp 4.0.11: `fastmcp.server.auth.providers.jwt.JWTVerifier(public_key=TUTOR_JWT_SIGNING_KEY, issuer=<base URL>, audience=<base URL>/mcp, algorithm="HS256")`. The `public_key` parameter takes the shared secret for HS* algorithms; every claim lands in `AccessToken.claims`, so `token_identity()` is unchanged. `StaticTokenVerifier` is not used. A plain `TokenVerifier` serves no `/.well-known/*` route: under dev auth there is no OAuth discovery and no protected-resource metadata (clients send the bearer header directly), and the `dev` image target probes `GET /mcp` (401) for its health check.
- **D3.** `authlib.jose` is deprecated in authlib 1.8 (successor `joserfc`); it is used with the deprecation warning filtered at the import. Tokens default to 12 hours and never exceed 24. The signing key is never printed.
- **D5.** The dev login reuses the Google callback's tail: Perfil first when onboarding is needed, `/app/` otherwise, the deletion-pending page for a learner who asked for deletion.
- **D7.** `migrate-dev` reads both the owner URL (migrations, `provision_roles`) and the login URL (`dev_seed`, which refuses the owner through `check_app_role`) from `deploy/dev.env`. The `dev` target runs as uid 10001 and sets `WATCHFILES_FORCE_POLLING=true` so reload works on Docker Desktop bind mounts.
- **D8.** `deploy/dev.env.example` holds `<KEY>` placeholders that `scripts/dev_env.py` replaces with random values (mode 0600, idempotent, `--force` to rotate). `TUTOR_DEV_AUTH` is not in the file: compose sets it on `migrate-dev` and `app-dev`, so the file by itself enables nothing.
- **D7, D9: no `inspector` service.** MCP Inspector v2 refuses to bind a non-loopback host inside a container without `DANGEROUSLY_BIND_ALL_INTERFACES=true`, and bound to `localhost` it is unreachable through published ports. The profile has `migrate-dev` and `app-dev` only; `inspector-dev` prints the token and runs the Inspector on host Node (default loopback bind and session token) against `http://localhost:8000/mcp`. `dev-down` stops the two dev services (`db` and `db-test` keep running); `dev-reset` takes the project down and removes `<project>_pgdata`, refusing without `CONFIRM=1`.
- **D10.** `TUTOR_DEV_AUTH` is absent from every production compose file and env example, `deploy/tutor.env.example` included (a comment there says why); `tutor.settings.DEV_ONLY_KEYS` names it for the tests. `is_loopback` also accepts `*.localhost` names (RFC 6761).
- **Section 5.** This branch registers six tools; "the nine English tools" is the count after Subjects v0.

### Security review fixes (2026-11-02)

- **D1, D10.** `Settings.__post_init__` repeats the env and loopback guard (same `SystemExit` as `from_env`), so a `Settings` built directly or through `dataclasses.replace` cannot carry the flag in prod. `from_env` also refuses the flag when `FORWARDED_ALLOW_IPS` is set (blank counts as unset): a proxy in front could expose dev auth.
- **D2.** fastmcp 4.0.11's `JWTVerifier.load_access_token` checks `exp` only when present and never the lifetime or `sub`. `DevJWTVerifier` runs it, then refuses a token without `iat` or `exp`, living over 24 hours, expiring more than 24 hours from now, or naming a `sub` other than `dev-ana` and `dev-nuevo`. With the flag on, the MCP side sits behind Starlette's `TrustedHostMiddleware` (`localhost`, `127.0.0.1`, `[::1]` and the loopback base URL host), so a host-side tunnel forwarding a public Host gets 400; the Google path is unchanged.
- **D6.** The `dev` stage sits above `runtime`, so a build without `--target` gets the product image, and every production build block (`compose.prod.yml`, both in `compose.coolify.yml`, `compose.local.yml`) pins `target: runtime`.
- **D7.** `db` and `db-test` publish on `127.0.0.1` only (superuser `tutor:tutor`). `app-dev` gets `MIGRATION_DATABASE_URL` and `REPORT_DB_PASSWORD` blanked through `environment`; `migrate-dev` keeps them from `deploy/dev.env`.
- **D9.** `dev-reset` removes only this compose project's `pgdata` volume: `scripts/dev_pgdata.py` takes the project name and the volume's resolved name from `docker compose config --format json` and refuses anything but `<project>_pgdata`. The `CONFIRM=1` gate is unchanged.

### Known accepted risks

- **D5: login CSRF on the dev login.** `GET /auth/dev-login?as=...` switches the browser session on a plain GET, so any page the developer opens could sign the browser in as `dev-ana` or `dev-nuevo`. Accepted and not changed: the route exists only with the flag (loopback dev), names only the two seeded identities, and reaches no real account or data beyond them.
- **D2, D5: a Host-rewriting tunnel.** The loopback `TrustedHost` checks (website and MCP side) trust the client's `Host` header, so a tunnel that rewrites `Host` to `localhost` (for example `ngrok --host-header=localhost:8000`) passes them and would expose dev tokens and the dev login. Never tunnel the dev profile. As a partial guard, with the flag on both sides answer 400 to any request carrying `Forwarded` or an `X-Forwarded-*` header (`tutor.app.RejectProxyHeaders`), which such tunnels add; a tunnel that strips them as well is not detectable, so the rule stands. The Google path is unchanged: production always arrives with `X-Forwarded-*` from its tunnel.
