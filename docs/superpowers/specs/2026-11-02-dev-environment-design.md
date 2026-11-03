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
