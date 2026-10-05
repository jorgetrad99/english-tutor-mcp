# Run the real app on your machine (Windows)

Website and MCP server in one process on port 8000, backed by the dev Postgres (compose service
`db`, port 5432), reachable from claude.ai and the Claude mobile app through a second hostname on
the spike's Cloudflare tunnel. Example host: `tutor.develancoders.com`. The spike stays on
`tutor-spike.develancoders.com` (port 8765) and keeps running untouched. Never use `db-test`
(5433). Commands run from the repo root. Offline variant: section 8.

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
The tunnel values (`TUTOR_BASE_URL`, `TUTOR_MCP_URL`, `FORWARDED_ALLOW_IPS=127.0.0.1`) are already
the defaults. Generate the secrets and paste each output in; the three app secrets must differ:

```
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # TUTOR_JWT_SIGNING_KEY
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"   # TUTOR_WEB_SESSION_SECRET
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

or `uv run just serve-local`. Leave it running (Ctrl+C stops it). Checks:

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

No tunnel, no claude.ai. In `deploy/local.env` set `TUTOR_BASE_URL=http://localhost:8000` and
`TUTOR_MCP_URL=http://localhost:8000/mcp`; register
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
  docker volume rm english_tutor_mcp_pgdata
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
