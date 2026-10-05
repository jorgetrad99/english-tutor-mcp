# Run the real app locally (Windows)

Website and MCP server in one process on `http://localhost:8000`, backed by the dev Postgres
(compose service `db`, port 5432). Never use `db-test` (5433) or the spike's Google client and
tunnel. All commands run from the repo root; `uv run` finds the project. PowerShell and Git Bash
differ only where marked.

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

### Secrets

Run each command, paste the output into `deploy/local.env`. The three app secrets must differ.

```
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # TUTOR_JWT_SIGNING_KEY
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # TUTOR_WEB_SESSION_SECRET
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TUTOR_OAUTH_STORAGE_KEY
uv run python -c "import secrets; print(secrets.token_hex(24))"   # APP_DB_PASSWORD, then REPORT_DB_PASSWORD (differ)
```

Put the same `APP_DB_PASSWORD` inside `DATABASE_URL`. Hex or URL-safe values need no URL escaping.

## 3. Migrations and roles

As the owner (`MIGRATION_DATABASE_URL`, the dev db superuser `tutor`):

```
uv run --env-file deploy/local.env alembic upgrade head
uv run --env-file deploy/local.env python -m tutor.ops.provision_roles
```

The second prints `roles ready: tutor_login, tutor_report`. It creates the app login
(NOSUPERUSER NOBYPASSRLS, member of `tutor_app`) and the gate-report role. Re-running is safe and
also applies a changed password. `DATABASE_URL` must be that login, never the owner: startup
refuses a superuser.

## 4. Google OAuth client

In Google Cloud Console, in your own test project (not the spike's client): APIs and services,
OAuth consent screen (External, Testing; add your Google account under Test users), then
Credentials, Create OAuth client ID, type Web application. Authorized redirect URIs, both:

- `http://localhost:8000/oauth/callback` (MCP sign-in)
- `http://localhost:8000/auth/callback` (website sign-in)

Copy the client id and secret into `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
`TUTOR_BASE_URL=http://localhost:8000` and `TUTOR_ENV=dev` are already the defaults (http is
accepted only on loopback).

## 5. Run

```
uv run --env-file deploy/local.env python -m tutor
```

or `uv run just serve-local`. Leave it running (Ctrl+C stops it). Open:

- http://localhost:8000/login: real Google sign-in, then Inicio, Sesiones, Glosario, Perfil, Conectar
  under `/app/`.
- MCP endpoint: http://localhost:8000/mcp (Conectar shows the same URL).

Use `localhost`, not `127.0.0.1` (the website answers only to the `TUTOR_BASE_URL` host).

## 6. Using the MCP locally

Claude.ai and the Claude mobile app cannot reach localhost.

- **Claude Code**: `claude mcp add --transport http tutor http://localhost:8000/mcp`, then `/mcp` in
  Claude Code and authenticate (opens the browser, Google sign-in). Remove with
  `claude mcp remove tutor`.
- **MCP Inspector**: `uv run just inspector` needs Node (not installed here).
- **Claude.ai / mobile**: needs a public https URL. Create a separate Cloudflare Tunnel with its
  own hostname (never repoint the spike's hostname), service `http://localhost:8000`. In
  `deploy/local.env` set `TUTOR_BASE_URL` and `TUTOR_MCP_URL` (`.../mcp`) to that https URL, add
  `<https url>/oauth/callback` and `<https url>/auth/callback` to the Google client, and restart.
  With `cloudflared` on the same machine, add `FORWARDED_ALLOW_IPS=127.0.0.1` (the default).

## 7. Reset, stop, troubleshoot

- Stop the app: Ctrl+C. Stop the database: `docker compose stop db`.
- Clear OAuth registrations: delete `data/oauth/` (clients re-register).
- Wipe all local data (roles included; also the dev data of anything else using this `db`):

  ```
  docker compose rm -sfv db
  docker volume rm english_tutor_mcp_pgdata
  ```

  then repeat steps 1 and 3.
- Smoke check:
  `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/login` is 200;
  `/.well-known/oauth-protected-resource/mcp` is 200; `POST /mcp` without a token is 401;
  `/app/` redirects (303) to `/login`.

| Symptom | Cause and fix |
| --- | --- |
| 400 on every page | You used `127.0.0.1`; use `localhost` (TrustedHost). |
| `DATABASE_URL must be a non-superuser LOGIN role...` | It points at the owner. Use `tutor_login` (step 3), not `tutor`. |
| `Missing settings: ...` / `Invalid settings: ...` | Names the keys (never values): unreplaced placeholder, secret under 32 chars, bad Fernet key, repeated secret. |
| `Cannot provision roles: ...` | Passwords under 32 chars or equal, or db not running. |
| `password authentication failed` at startup | `DATABASE_URL` password differs from `APP_DB_PASSWORD`; fix and re-run provision_roles. |
| Port 8000 busy | Another process: `TUTOR_PORT=8001` and update `TUTOR_BASE_URL`, `TUTOR_MCP_URL` and the Google redirect URIs. The spike server uses 8765; do not stop it. |
| Google `redirect_uri_mismatch` | Registered URIs differ from `TUTOR_BASE_URL` (scheme, host, port). |
| Google `access_denied` | Your account is not under Test users. |
