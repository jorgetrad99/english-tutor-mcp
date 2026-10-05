# Core loop v0 runbook

The production stack for the core loop v0 (spec `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md`, section 4). One process (`python -m tutor`) serves MCP (`/mcp`), the OAuth proxy and the website. Commands run on the homelab from `/opt/tutor/deploy` unless stated otherwise. Values in angle brackets are chosen once and kept in your password manager, never in the repository.

| Thing | Value |
| --- | --- |
| Public hostname | `https://<host>` (its own Cloudflare Tunnel, separate from the spike) |
| Compose project | `tutor` (`deploy/compose.prod.yml`): `db`, `migrate` (one-shot), `app`, `cloudflared` |
| Volumes | `tutor_pgdata` (Postgres), `tutor_oauth` (encrypted OAuth store, owned by uid 10001) |
| Database | PostgreSQL 16 or later (see "PostgreSQL version") |
| Backups | `/var/backups/tutor/tutor-<UTC stamp>.dump` and `tutor-roles-<UTC stamp>.sql`, nightly 03:30, 30 days |

Git Bash on Windows rewrites arguments that start with `/` (for example `/app/...`). Prefix such commands with `MSYS_NO_PATHCONV=1`. The homelab itself is Linux and needs nothing.

## The three database roles

| Role | Credentials live in | Used by | Rights |
| --- | --- | --- | --- |
| Migration owner (`POSTGRES_USER`) | `db.env`, `migrate.env` | the `migrate` service and the backup script | superuser of the container's cluster; never given to the app |
| App login (`APP_DB_USER`, default `tutor_login`) | `migrate.env` (creation), `tutor.env` (`DATABASE_URL`) | the `app` service | `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, member of `tutor_app` with `INHERIT FALSE, SET TRUE`; owns nothing |
| `tutor_report` | `migrate.env` (password) | the gate report, run by hand | `LOGIN BYPASSRLS`, read only, `SELECT` on `sessions`, `session_metrics`, `audit_log` |

`migrate` runs `alembic upgrade head` (head is `0005`) and then `tutor.ops.provision_roles`, which creates or updates the app login and `tutor_report` and sets their passwords from the secrets. It is idempotent: rerun it after editing a password. `app` starts only after `migrate` exits 0 and refuses to serve if its login is a superuser, has BYPASSRLS, owns user tables (directly or through a role), is a member of such a role, or is not a member of `tutor_app`, or if `DATABASE_URL` is not a PostgreSQL URL in prod.

## Google OAuth client

1. Google Cloud console, APIs & Services, Credentials, Create OAuth client ID, Web application.
2. Authorized redirect URIs, **both** required:
   - `https://<host>/oauth/callback` (MCP OAuth proxy, `GoogleProvider(redirect_path="/oauth/callback")`)
   - `https://<host>/auth/callback` (website login, Authlib)
3. OAuth consent screen: External, publishing status Testing; add the author and every invited tester as **test users** (only listed addresses can sign in). Scopes: `openid`, `email`, `profile`.
4. Keep the client id and secret for `tutor.env`.

## Cloudflare Tunnel, DNS and edge rules

1. Zero Trust, Networks, Tunnels, create a tunnel (Cloudflared) named `tutor-v0`; public hostname `<host>`, service type `HTTP`, URL `app:8000`. The DNS record is created and proxied by the tunnel. Copy the token into `tunnel.env`.
2. After the first `docker compose pull`, pin cloudflared: replace `cloudflare/cloudflared:latest` in `compose.prod.yml` with the output of `docker image inspect cloudflare/cloudflared:latest --format '{{index .RepoDigests 0}}'` and commit it.
3. Cache Rule for `<host>`: bypass cache. No Cloudflare Access application on this hostname (Claude's servers must reach it).
4. WAF custom rule *Skip* (all remaining custom rules, Bot Fight Mode / Super Bot Fight Mode) for `ip.src in {160.79.104.0/21}`, Anthropic's egress range for Claude's connector calls (same as the spike, `spike/README.md`).
5. **Per-IP rate-limit rule** (the app has no limiter before sign-in; this is the control for the unauthenticated endpoints). Expression, with action Block and, for example, 30 requests per 10 seconds per IP:

   ```
   (http.host eq "<host>" and not ip.src in {160.79.104.0/21}
     and (http.request.uri.path in {"/register" "/authorize" "/token" "/auth/google"}
          or (http.request.method eq "POST" and starts_with(http.request.uri.path, "/app/"))))
   ```

   It covers `/register`, `/authorize`, `/token`, `/auth/google` and every `POST /app/*`. The Anthropic range is exempt because all Claude clients share it.
6. The application only trusts `X-Forwarded-*` from the cloudflared container (`FORWARDED_ALLOW_IPS=172.30.10.3`, a fixed address on the compose network), so client addresses in logs come from Cloudflare, not from a spoofable header.

## First deploy

1. Install Docker Engine with the Compose plugin. Create the deploy user and `/opt/tutor`. `git clone <repo> /opt/tutor && cd /opt/tutor && git checkout <release tag>`.
2. Create the four env files. They hold secrets: `chmod 600`, never committed (`deploy/*.env` is in `.gitignore`).

   ```sh
   cd /opt/tutor/deploy
   for f in db migrate tutor tunnel; do cp "$f.env.example" "$f.env"; done
   chmod 600 db.env migrate.env tutor.env tunnel.env
   ```

3. Generate secrets (one value per line item; each one different):

   | Secret | Where | Command |
   | --- | --- | --- |
   | `POSTGRES_PASSWORD` | `db.env` and inside `MIGRATION_DATABASE_URL` | `openssl rand -hex 24` |
   | `APP_DB_PASSWORD` | `migrate.env` and inside `DATABASE_URL` in `tutor.env` | `openssl rand -hex 24` |
   | `REPORT_DB_PASSWORD` | `migrate.env` | `openssl rand -hex 24` |
   | `TUTOR_JWT_SIGNING_KEY` | `tutor.env` (at least 32 characters) | `openssl rand -base64 48` |
   | `TUTOR_WEB_SESSION_SECRET` | `tutor.env` (at least 32 characters, **distinct** from the JWT key) | `openssl rand -base64 48` |
   | `TUTOR_OAUTH_STORAGE_KEY` | `tutor.env` (Fernet key, distinct from the other two) | after step 4: `docker run --rm --entrypoint python tutor:latest -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

   Use hex passwords so they need no URL-encoding in the database URLs. The server refuses to start when the three app secrets are short, malformed or equal. Fill the rest of `tutor.env`: `TUTOR_BASE_URL=https://<host>`, `TUTOR_MCP_URL=https://<host>/mcp`, `TUTOR_SUPPORT_EMAIL`, the Google client id and secret, and `DATABASE_URL=postgresql://<APP_DB_USER>:<APP_DB_PASSWORD>@db:5432/<POSTGRES_DB>`. `TUTOR_ENV=prod`, `FORWARDED_ALLOW_IPS=172.30.10.3` (never `*`; the server refuses it in prod) and `TUTOR_OAUTH_STORAGE_DIR=/data/oauth` stay as shipped. Leave `TUTOR_TEST_LOGIN` empty. Do not put `MIGRATION_DATABASE_URL` in `tutor.env`.
4. Build: `docker compose -f compose.prod.yml build migrate`. The build fails if the installed package lacks its locale `.po` files, templates, static files or the track YAML.
5. Migrate: `docker compose -f compose.prod.yml up -d db`, then `docker compose -f compose.prod.yml run --rm migrate`. Expect `Running upgrade  -> 0001` through `0004 -> 0005`, then `roles ready: <APP_DB_USER>, tutor_report`.
6. Start: `docker compose -f compose.prod.yml up -d`. (`app` waits for `migrate` to complete; `cloudflared` waits for `app` to be healthy.) Check with `docker compose -f compose.prod.yml ps` and `logs app`.
7. Smoke checks from any machine:
   - `curl -s https://<host>/.well-known/oauth-authorization-server` returns JSON whose `authorization_endpoint` is `https://<host>/authorize`.
   - `curl -si -X POST https://<host>/mcp` returns `401` with a `WWW-Authenticate` header naming `https://<host>/.well-known/oauth-protected-resource/mcp`.
   - `https://<host>/login` shows the sign-in page with the privacy note; "Entrar con Google" with a test user lands on Perfil. Add `https://<host>/mcp` as a custom connector in Claude and run one lesson.
   - Rate limit: 40 quick `curl -s -o /dev/null -w '%{http_code}\n' https://<host>/token -X POST` calls end in `429` (or the Cloudflare block page).
8. Install the backup cron and run `./backup.sh` once by hand (see Backups).

Existing `oauth` volume from an earlier deployment (created by a root-owned image): hand it to the runtime user once, `docker run --rm --user root --entrypoint chown -v tutor_oauth:/data/oauth tutor:latest -R 10001:10001 /data/oauth`. A fresh volume inherits the image's ownership and needs nothing.

The purge of expired web sessions runs inside the app lifespan (at startup, then hourly). There is no cron for it.

## PostgreSQL version

PostgreSQL 16 or later is required, and the compose file pins `postgres:16`. The schema uses `ON DELETE SET NULL (column)` (15+), and the role setup and the startup check rely on the 16 role-membership model: `GRANT ... WITH INHERIT FALSE, SET TRUE` and `pg_has_role(..., 'SET')`. A major upgrade is a dump and restore (below), never a changed image tag over an existing data volume.

## Upgrades and migrations

- Deploy a new release: `git fetch && git checkout <tag>`, `docker compose -f compose.prod.yml build migrate`, then run `docker compose -f compose.prod.yml run --rm migrate` (migrations and roles) and `docker compose -f compose.prod.yml up -d`.
- Take a backup (`./backup.sh`) before any release that adds a migration.
- Current revision: `docker compose -f compose.prod.yml run --rm --entrypoint alembic migrate -c /app/alembic.ini current`.
- Never edit an applied migration; a fix is a new revision. Downgrade only to recover, right after a backup.
- Dependency bumps: before bumping `fastmcp` read its auth/OAuth changelog (the OAuth proxy, token storage and consent behaviour are security relevant) and re-run `uv run just check`, `test-int` and the smoke checks above. Rebuild `uv.lock` deliberately, and run `pip-audit` (part of `just check`).

## Backups and the restore drill

`deploy/backup.sh` writes, with `umask 077` (directory 700, files 600):

- `tutor-<stamp>.dump`: `pg_dump --format=custom` of the tutor database (checked with `pg_restore --list`);
- `tutor-roles-<stamp>.sql`: `pg_dumpall --roles-only`. Roles are cluster-level and are **not** in the dump. The file holds password hashes, which is why it is mode 600.

Files are written as `.partial` and renamed only when complete; files older than 30 days are deleted last (`RETENTION_DAYS`, `BACKUP_DIR` override the defaults). Cron, as the deploy user: `30 3 * * * /opt/tutor/deploy/backup.sh >> /var/log/tutor-backup.log 2>&1`. Copy the directory off the machine too; a backup on the same disk is not a backup.

**Not in the backup:** the OAuth store (`tutor_oauth`, an encrypted file tree) is not in `pg_dump`. Losing it, or `TUTOR_OAUTH_STORAGE_KEY`, loses only the OAuth proxy's client registrations and issued tokens: Claude connections stop working and each learner removes and re-adds the connector (Claude registers again). No learner data is lost (it is all in Postgres). To protect against the volume loss itself, `docker run --rm -v tutor_oauth:/data/oauth:ro -v /var/backups/tutor:/out alpine tar czf /out/oauth-$(date -u +%Y%m%d).tgz -C /data oauth` is optional and the tarball is as sensitive as the key.

Restore drill (monthly, and once before inviting testers):

1. `latest=$(ls -1 /var/backups/tutor/tutor-2*.dump | tail -n 1)`
2. `docker compose -f compose.prod.yml exec -T db sh -c 'createdb -U "$POSTGRES_USER" tutor_restore'`
3. `docker compose -f compose.prod.yml exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d tutor_restore --no-owner --no-acl' < "$latest"`
4. Compare with live, using `psql -U "$POSTGRES_USER" -d tutor_restore` and `-d tutor` (`docker compose -f compose.prod.yml exec db psql ...`): `SELECT count(*) FROM users; SELECT count(*) FROM sessions; SELECT max(started_at) FROM sessions;`. Counts match up to sessions started after the dump.
5. `docker compose -f compose.prod.yml exec -T db sh -c 'dropdb -U "$POSTGRES_USER" tutor_restore'`

Disaster restore (new or wiped cluster):

1. `docker compose -f compose.prod.yml up -d db` (empty cluster with the same `db.env`).
2. Roles first: `docker compose -f compose.prod.yml exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres' < /var/backups/tutor/tutor-roles-<stamp>.sql`. "role already exists" for the owner is expected.
3. `docker compose -f compose.prod.yml exec -T db sh -c 'createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'`, then `pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error` with the dump on stdin, as in the drill but without `--no-owner --no-acl` (owners and grants must come back).
4. `docker compose -f compose.prod.yml run --rm migrate` (no-op for the schema; resets both passwords from `migrate.env`), then `up -d`.

## Gate report

Read only, as `tutor_report`, on demand (never the app's `DATABASE_URL`, which sees no rows through RLS). Enter the password without leaving it in shell history:

```sh
cd /opt/tutor/deploy
read -rsp 'tutor_report password: ' RP; echo
export GATE_REPORT_DATABASE_URL="postgresql://tutor_report:${RP}@db:5432/tutor"   # <POSTGRES_DB>
docker compose -f compose.prod.yml run --rm --no-deps -e GATE_REPORT_DATABASE_URL \
  -v "$PWD/../labels.json:/labels.json:ro" --entrypoint python app \
  -m tutor.ops.gate_report --from 2026-11-16 --to 2026-12-04 --labels /labels.json --author author
unset RP GATE_REPORT_DATABASE_URL
```

Omit the `-v` and `--labels/--author` options if you have no labels file. On Git Bash use `MSYS_NO_PATHCONV=1` (the `/labels.json` mount). `labels.json` maps user hashes to names (`{"a1b2c3d4e5f6": "author"}`); a hash is the first 12 hex characters of SHA-256 of the user id, the `user_hash` the logs show. Keep it out of the repository (`labels*.json` is ignored) and never publish it next to the report.

## User deletion (spec 13)

On a learner's written request, from the address their Google account uses:

1. Ask them to remove the "English Tutor" connector in Claude first; otherwise their next tool call recreates an empty account from the same Google `sub`.
2. As the owner: `docker compose -f compose.prod.yml exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`. Find the id: `SELECT id FROM users WHERE email = '<their email>';`
3. Run (the owner is a superuser, so RLS does not block it):

```sql
\set uid '<user id>'
BEGIN;
-- Keep a pseudonymous trace: the same 12-hex user_hash the logs use.
UPDATE audit_log
   SET meta = meta || jsonb_build_object(
         'deleted_user_hash', left(encode(sha256(convert_to(:'uid', 'UTF8')), 'hex'), 12))
 WHERE user_id = :'uid';
INSERT INTO audit_log (user_id, event, meta, at)
VALUES (NULL, 'user_deleted',
        jsonb_build_object('user_hash', left(encode(sha256(convert_to(:'uid', 'UTF8')), 'hex'), 12)),
        now());
-- Cascades to profiles, plans, sessions, glossary, reviews and web_sessions; audit_log.user_id becomes NULL.
DELETE FROM users WHERE id = :'uid';
COMMIT;
```

4. Check `SELECT count(*) FROM users WHERE id = :'uid';` returns 0, then reply to the learner. Their refresh tokens in the OAuth store expire on their own; to cut them at once, rotate `TUTOR_OAUTH_STORAGE_KEY` (below), which disconnects everyone. Old backups still hold the data until they age out (30 days); say so in the reply.

## Rotating keys

| Key | How | Effect |
| --- | --- | --- |
| `GOOGLE_CLIENT_SECRET` | New secret in the Google console, edit `tutor.env`, `docker compose -f compose.prod.yml up -d app`, delete the old secret | None for learners |
| `TUTOR_JWT_SIGNING_KEY` | New `openssl rand -base64 48`, `up -d app` | Every Claude connection must reconnect |
| `TUTOR_OAUTH_STORAGE_KEY` | New Fernet key, `up -d app`; optionally empty the volume: `docker compose -f compose.prod.yml run --rm --no-deps --entrypoint sh app -c 'rm -rf /data/oauth/*'` | Old entries are unreadable; clients re-register |
| `TUTOR_WEB_SESSION_SECRET` | New value (at least 32 characters, distinct), `up -d app` | Web sign-in secrets change; `DELETE FROM web_sessions;` ends all web sessions |
| `APP_DB_PASSWORD` | Edit `migrate.env` and `DATABASE_URL` in `tutor.env`, `run --rm migrate`, `up -d app` | None |
| `REPORT_DB_PASSWORD` | Edit `migrate.env`, `run --rm migrate` | None |
| `POSTGRES_PASSWORD` | `ALTER USER <owner> PASSWORD '<new>'` in `psql`, edit `db.env` and `MIGRATION_DATABASE_URL` | None |
| `TUNNEL_TOKEN` | Rotate in Zero Trust, edit `tunnel.env`, `up -d cloudflared` | Seconds offline |

## Logs

`docker compose -f compose.prod.yml logs app` prints one JSON line per tool call (`tutor.mcp.calls`) and per HTTP request (`tutor.http`) with `user_hash`, never tokens, codes, emails or learner text. The uvicorn access log is off because it would print `/oauth/callback?code=...`. Known cosmetic noise: at startup the scrub filter on `uvicorn.error` can make the "Uvicorn running on ..." line print a `--- Logging error ---` block; it does not affect serving.
