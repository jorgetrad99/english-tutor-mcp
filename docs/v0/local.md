# Run the real app on your machine (Windows)

Website and MCP server in one process on port 8000, reachable from claude.ai and the Claude mobile
app through a second hostname on the spike's Cloudflare tunnel. Example host:
`tutor.develancoders.com`. The spike stays on `tutor-spike.develancoders.com` (port 8765) and keeps
running untouched. Never use `db-test` (5433). Commands run from the repo root.

**The spike connector (`https://tutor-spike.develancoders.com/mcp`) is a different server with 2
tools. The product is a NEW connector at `https://tutor.develancoders.com/mcp` with 6 tools:** add it
as a separate custom connector in claude.ai. If a connector's tool list looks stale after a server
change, remove and re-add the connector (or disconnect and reconnect) and start a new chat.

## Dev profile (Docker, hot reload, no Google)

For working on the server and the website from this checkout: MCP Inspector, Claude Code and
Claude Desktop connect with a bearer token, the website signs in with one click, and a saved file
under `src/` reloads the app. Design: `docs/superpowers/specs/2026-11-02-dev-environment-design.md`.
It uses the root `docker-compose.yml` (`dev` profile) and the dev `db` on 5432; `db-test` (5433)
and `just test-int` are untouched. claude.ai and the mobile app still need the tunnel and real
Google OAuth (the sections below). Nothing in the dev profile can run in production: the server
refuses `TUTOR_DEV_AUTH` unless `TUTOR_ENV` is `dev` or `test` and `TUTOR_BASE_URL` is loopback,
and no production compose or env file carries the flag (tests pin both).

```
uv run just dev
```

The first run writes `deploy/dev.env` (random secrets, git-ignored, mode 0600; `scripts/dev_env.py
--force` rotates them), builds the `dev` image target, starts `db`, runs `migrate-dev` (alembic
head, `provision_roles`, `dev_seed`), waits for `app-dev` to be healthy and prints:

```
uv run python scripts/dev_env.py
C:\...\english-tutor-mcp\deploy\dev.env written (random secrets, git-ignored)
docker compose --profile dev up -d --build --wait app-dev
 ...
 Container english-tutor-mcp-db-1  Healthy
 Container english-tutor-mcp-migrate-dev-1  Exited
 Container english-tutor-mcp-app-dev-1  Healthy
docker compose --profile dev exec -T app-dev python -m tutor.ops.dev_token
MCP URL: http://localhost:8000/mcp
Token (12 h): eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtYW5h...

MCP Inspector CLI (host Node):
  npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --header "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtYW5h..." --method tools/list

Claude Code:
  claude mcp add --transport http tutor-dev http://localhost:8000/mcp --header "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtYW5h..."

Claude Desktop (claude_desktop_config.json, under mcpServers):
  "tutor-dev": {"command": "npx", "args": ["mcp-remote", "http://localhost:8000/mcp", "--header", "Authorization:Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZXYtYW5h..."]}
```

`docker compose --profile dev logs migrate-dev` ends with the migrations (`0001` to `0005`),
`roles ready: tutor_login, tutor_report` and `dev seed: created dev-ana, dev-nuevo; existing
nothing` (on later runs: `created nothing; existing dev-ana, dev-nuevo`). Checked on 2026-11-02
(Windows 11, Docker Desktop, Node 22): the Inspector CLI line above returns the six tools
(`get_profile`, `save_profile`, `start_lesson`, `record_review`, `save_glossary`, `end_session`);
`GET /auth/dev-login?as=dev-ana` is a 303 to `/app/`, `?as=dev-nuevo` a 303 to `/app/profile`,
`?as=demo-ana` a 422; `POST /mcp` without a token is 401; both `/.well-known/oauth-*` paths are 404.
If `npx` picks a cached v1 of the Inspector (it says so), use `@modelcontextprotocol/inspector@latest`.

Two seeded identities: `dev-ana` (onboarded: profile and plan) and `dev-nuevo` (onboarding
pending). Tokens are HS256 JWTs on the local signing key, issuer `http://localhost:8000`, audience
`http://localhost:8000/mcp`, 12 hours by default and 24 at most. There is no OAuth discovery under
dev auth (`/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`
are 404): clients must send the header; a client that tries the OAuth flow instead gets a 401.

| Client | How |
| --- | --- |
| MCP Inspector UI (host Node) | `uv run just inspector-dev` prints the token, then opens the Inspector; in it: transport Streamable HTTP, URL `http://localhost:8000/mcp`, header `Authorization: Bearer <token>` |
| MCP Inspector CLI (host Node) | `npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --header "Authorization: Bearer <token>" --method tools/list` |
| Claude Code | `claude mcp add --transport http tutor-dev http://localhost:8000/mcp --header "Authorization: Bearer <token>"` (remove: `claude mcp remove tutor-dev`) |
| Claude Desktop | in `claude_desktop_config.json` under `mcpServers`: `"tutor-dev": {"command": "npx", "args": ["mcp-remote", "http://localhost:8000/mcp", "--header", "Authorization:Bearer <token>"]}` |
| claude.ai, mobile | unchanged: the tunnel and real Google OAuth (below) |
| Website | http://localhost:8000/auth/dev-login?as=dev-ana (or `dev-nuevo`); `/login` still shows the Google button, which does nothing useful without Google keys |
| Claude in this repo | the in-process tests (`tests/unit/auth/test_dev_auth.py`), `curl` against `localhost:8000/mcp` with the token, or the Inspector CLI |

Recipes: `dev-token sub=dev-nuevo hours=2` (another token from the running app), `dev-logs`
(follow `app-dev`), `dev-down` (stops `app-dev` and `migrate-dev`; `db` keeps running and keeps
its data), `dev-reset` (DESTRUCTIVE: stops the whole compose project, `db` included, and deletes
this project's `pgdata` volume, whose name compose resolves: `uv run python scripts/dev_pgdata.py`
prints it, so a worktree never touches the main checkout's database; refuses unless `CONFIRM=1`),
`inspector-dev` (host Node).

Notes:

- `migrate-dev` runs every time: migrations are idempotent, `provision_roles` re-applies the role
  passwords from `deploy/dev.env`, and `dev_seed` creates nothing the second time. The dev `db` is
  shared with the host alternative below (`deploy/local.env`); both create `tutor_login`, so the
  last `provision_roles` run decides its password. If you use both, keep `APP_DB_PASSWORD` equal in
  the two files.
- Hot reload watches `./src` by polling (`WATCHFILES_FORCE_POLLING`, needed for bind mounts on
  Docker Desktop); a change shows up within a couple of seconds. `alembic/` is bind-mounted into
  `migrate-dev`, so a new revision applies on the next `just dev` without a rebuild; a change to
  `pyproject.toml` or `uv.lock` needs the rebuild `just dev` already does.
- The MCP container's health probe is `GET /mcp` expecting the 401 that proves the server and its
  auth middleware are up; the website's own `/login` is a 200 at http://localhost:8000/login (use
  `localhost`, not `127.0.0.1`: TrustedHost).
- The Inspector is not a compose service: its v2 refuses to bind a non-loopback host inside a
  container without an override flag, so it runs on host Node with its default loopback bind and
  session token, and reaches the app through the published `127.0.0.1:8000`.

## Run with Docker (recommended)

Database, migrations and app in one stack (`deploy/compose.local.yml`, project `tutor-local`): the
production image and the three database roles, with its own volumes. It does not touch the dev
`db` (5432) or `db-test` (5433). The app is published on `127.0.0.1:8000` only; the database is not
published. Your existing host tunnel does the rest.

1. Copy the env examples (git-ignored copies, `deploy/*.env`):

   ```
   cp deploy/db.env.example deploy/local-db.env
   cp deploy/migrate.env.example deploy/local-migrate.env
   cp deploy/local-docker.env.example deploy/local-docker.env
   ```

   PowerShell: `Copy-Item` with the same names.
2. Generate secrets and edit the three files by hand (every `<placeholder>` must go):

   ```
   uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # TUTOR_JWT_SIGNING_KEY
      uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TUTOR_OAUTH_STORAGE_KEY
   uv run python -c "import secrets; print(secrets.token_hex(24))"   # POSTGRES_PASSWORD, APP_DB_PASSWORD, REPORT_DB_PASSWORD (three different)
   ```

   - `local-db.env`: `POSTGRES_USER=tutor_owner`, the password, `POSTGRES_DB=tutor`.
   - `local-migrate.env`: `MIGRATION_DATABASE_URL=postgresql://tutor_owner:<POSTGRES_PASSWORD>@db:5432/tutor`
     plus `APP_DB_PASSWORD` and `REPORT_DB_PASSWORD`.
   - `local-docker.env`: the two app secrets (leave `TUTOR_MCP_URL` empty), and `DATABASE_URL=postgresql://tutor_login:<APP_DB_PASSWORD>@db:5432/tutor`.
3. Paste the spike's Google client id and secret into `local-docker.env` (`GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET`), and add the redirect URIs of step 4 in Google.
4. `uv run just up-local` builds the image, migrates, creates the roles and starts the app
   (detached; first build takes a few minutes). Other recipes: `logs-local`, `down-local` (keeps
   data), `reset-local` (DESTRUCTIVE: also deletes the database, roles and OAuth registrations).
5. Tunnel hostname: step 5 below (`tutor.develancoders.com` -> `http://127.0.0.1:8000`).
6. Connect Claude: step 7 below.

`FORWARDED_ALLOW_IPS=172.31.90.1`: traffic that enters through Docker's published port reaches
uvicorn from the gateway of the `edge` network, not from 127.0.0.1 (verified on Docker Desktop for
Windows). `compose.local.yml` pins that network to `172.31.90.0/24` with gateway `172.31.90.1` so
the value is stable; if the subnet collides with your LAN, change both together. Never `*`.

Smoke check from the host:
`curl -H "Host: tutor.develancoders.com" -H "X-Forwarded-Proto: https" http://127.0.0.1:8000/login`
is 200, `/.well-known/oauth-protected-resource/mcp` shows resource
`https://tutor.develancoders.com/mcp`, and `POST /mcp` without a token is 401.

## Alternative: run on the host with uv

Steps 1 to 4 and 6 below use the dev `db` (5432) and `deploy/local.env`; the Docker path above
replaces them.

## 1. Database

```
docker compose up -d --wait db
```

## 2. Settings file

```
cp deploy/local.env.example deploy/local.env          # Git Bash
Copy-Item deploy/local.env.example deploy/local.env   # PowerShell
```

`deploy/local.env` is git-ignored (`deploy/*.env`). Edit it by hand; every `<placeholder>` must go.
The tunnel values (`TUTOR_BASE_URL`, `FORWARDED_ALLOW_IPS=127.0.0.1`) are already the defaults;
`TUTOR_MCP_URL` stays empty (it is derived as `<TUTOR_BASE_URL>/mcp`, and a different value stops
the server). Generate the secrets and paste each output in; the two app secrets must differ:

```
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # TUTOR_JWT_SIGNING_KEY
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TUTOR_OAUTH_STORAGE_KEY
uv run python -c "import secrets; print(secrets.token_hex(24))"   # APP_DB_PASSWORD, then REPORT_DB_PASSWORD (differ)
```

Put the same `APP_DB_PASSWORD` inside `DATABASE_URL`. `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
are the spike's (step 4).

## 3. Migrations and roles

As the owner (`MIGRATION_DATABASE_URL`, the dev db superuser `tutor`):

```
uv run --env-file deploy/local.env alembic upgrade head
uv run --env-file deploy/local.env python -m tutor.ops.provision_roles
```

The second prints `roles ready: tutor_login, tutor_report`: the app login (NOSUPERUSER
NOBYPASSRLS, member of `tutor_app`) and the gate-report role. Re-running is safe and also applies
a changed password. `DATABASE_URL` must be the login, never the owner: startup refuses a superuser.

## 4. Google OAuth client (reuse the spike's)

Google Cloud Console, APIs and services, Credentials, the spike's Web client. **Add** two
authorized redirect URIs and keep the spike's existing one so the spike keeps working:

- `https://tutor.develancoders.com/oauth/callback` (MCP sign-in)
- `https://tutor.develancoders.com/auth/callback` (website sign-in)

Test users already exist. Copy the client id and secret into `deploy/local.env`.

## 5. Second hostname on the existing tunnel `tutor-spike`

Edit `%USERPROFILE%\.cloudflared\config.yml` (back it up first). The new rule goes above the
spike rule and above the final catch-all; do not change the spike rule:

```yaml
ingress:
  - hostname: tutor.develancoders.com
    service: http://127.0.0.1:8000
  - hostname: tutor-spike.develancoders.com
    service: http://127.0.0.1:8765      # keep whatever your file already has here
  - service: http_status:404
```

Then create the proxied DNS record and restart the tunnel (stop the running one with Ctrl+C):

```
cloudflared tunnel route dns tutor-spike tutor.develancoders.com
cloudflared tunnel run tutor-spike
```

The spike server (8765) and the product (8000) can run at the same time.

Cloudflare dashboard for the new hostname (rationale and details: [runbook.md](runbook.md),
"Cloudflare Tunnel, DNS and edge rules"):

- DNS: proxied (orange cloud), created by the route command.
- Cache Rule: bypass cache for the host. No Cloudflare Access on it.
- SSL/TLS: Always Use HTTPS on.
- WAF custom rule with action Skip for `ip.src in {160.79.104.0/21}` (Anthropic's egress range):
  extend the spike's existing rule to the new hostname (or drop any host condition).
- Bot Fight Mode: on Free it cannot be skipped. If Claude's connector gets challenged or blocked,
  turn it off (Security, Bots).

## 6. Run

```
uv run --env-file deploy/local.env python -m tutor
```

or `uv run just serve-local` (host alternative). Leave it running (Ctrl+C stops it). Checks:

```
curl -s https://tutor.develancoders.com/.well-known/oauth-protected-resource/mcp
```

must show `"resource": "https://tutor.develancoders.com/mcp"`, and
https://tutor.develancoders.com/login must load. Open the website by that hostname only
(TrustedHost). After Google sign-in: Inicio, Sesiones, Glosario, Perfil, Conectar under `/app/`.

uvicorn honours `X-Forwarded-*` only from `FORWARDED_ALLOW_IPS`; cloudflared on this machine
connects from loopback, so `127.0.0.1` is right. The public URL, OAuth issuer and redirect URIs
come from `TUTOR_BASE_URL`, not from the request scheme.

## 7. Connect Claude

- **claude.ai**: Settings, Connectors, Add custom connector, `https://tutor.develancoders.com/mcp`,
  sign in with Google, then new chat and `/start-lesson` (or "start my lesson").
- **Mobile app**: uses the same connector once it is added on your account.
- **Claude Code**: `claude mcp add --transport http tutor https://tutor.develancoders.com/mcp`, then
  `/mcp` to authenticate. Remove with `claude mcp remove tutor`.

## 8. Offline variant (localhost, Claude Code only)

No tunnel, no claude.ai. In `deploy/local.env` set `TUTOR_BASE_URL=http://localhost:8000` (and
`TUTOR_MCP_URL` empty or `http://localhost:8000/mcp`); register
`http://localhost:8000/oauth/callback` and `http://localhost:8000/auth/callback` in Google. Open
http://localhost:8000/login (not `127.0.0.1`) and add the MCP with
`claude mcp add --transport http tutor http://localhost:8000/mcp`. MCP Inspector
(`uv run just inspector`) needs Node, not installed here.

## 9. Reset, stop, troubleshoot

- Stop the app: Ctrl+C. Stop the database: `docker compose stop db`.
- Clear OAuth registrations: delete `data/oauth/` (clients re-register).
- Wipe all local data (roles included; also anything else using this `db`):

  ```
  docker compose rm -sfv db
  uv run python scripts/dev_pgdata.py --remove
  ```

  then repeat steps 1 and 3.
- Local smoke check, bypassing the tunnel:
  `curl -s -o /dev/null -w "%{http_code}" -H "Host: tutor.develancoders.com" -H "X-Forwarded-Proto: https" http://127.0.0.1:8000/login`
  is 200; `POST /mcp` without a token is 401; `/app/` redirects (303) to `/login`.

| Symptom | Cause and fix |
| --- | --- |
| 502 from Cloudflare | Nothing listens on 8000 (product) or 8765 (spike host). Start the app / spike server. |
| 400 on web pages | `TUTOR_BASE_URL` host differs from the hostname you open (or `127.0.0.1` in the offline variant). |
| Google `redirect_uri_mismatch` | A redirect URI is missing or differs in scheme, host or port. |
| Google `access_denied` | Your account is not under Test users. |
| Connector challenged or blocked | Bot Fight Mode or a WAF rule; see step 5. |
| `DATABASE_URL must be a non-superuser LOGIN role...` | It points at the owner. Use `tutor_login` (step 3), not `tutor`. |
| `Missing settings: ...` / `Invalid settings: ...` | Names the keys (never values): unreplaced placeholder, secret under 32 chars, bad Fernet key, repeated secret. |
| `Cannot provision roles: ...` | Passwords under 32 chars or equal, or db not running. |
| `password authentication failed` at startup | `DATABASE_URL` password differs from `APP_DB_PASSWORD`; fix and re-run provision_roles. |
| Port 8000 busy | Set `TUTOR_PORT` and the tunnel rule to another port. The spike uses 8765; do not stop it. |
