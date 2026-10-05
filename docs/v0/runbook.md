# Core loop v0 runbook

The production stack for the core loop v0 (spec `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md`, section 4). One process (`python -m tutor`) serves MCP (`/mcp`), the OAuth proxy and the website. Commands run on the homelab from `/opt/tutor/deploy` unless stated otherwise. Values in angle brackets are chosen once and kept in your password manager, never in the repository. Deploying with Coolify on a VPS instead: `docs/v0/coolify.md` lists what changes.

| Thing | Value |
| --- | --- |
| Public hostname | `https://<host>` (its own Cloudflare Tunnel, separate from the spike) |
| Compose project | `tutor` (`deploy/compose.prod.yml`): `db`, `migrate` (one-shot), `app`, `cloudflared`; networks `backend` (internal, no egress: db, migrate, app) and `edge` (app, cloudflared) |
| Volumes | `tutor_pgdata` (Postgres), `tutor_oauth` (encrypted OAuth store, owned by uid 10001) |
| Database | PostgreSQL 16 or later (see "PostgreSQL version") |
| Backups | `/var/backups/tutor/tutor-<UTC stamp>.dump` and `tutor-roles-<UTC stamp>.sql`, `tutor-oauth-<UTC stamp>.tgz`, nightly 03:30, 30 days |

Git Bash on Windows rewrites arguments that start with `/` (for example `/app/...`). Prefix such commands (and `backup.sh`, whose `docker run` mounts `/data`) with `MSYS_NO_PATHCONV=1`. The homelab itself is Linux and needs nothing.

## The three database roles

| Role | Credentials live in | Used by | Rights |
| --- | --- | --- | --- |
| Migration owner (`POSTGRES_USER`) | `db.env`, `migrate.env` | the `migrate` service and the backup script | superuser of the container's cluster; never given to the app |
| App login (`APP_DB_USER`, default `tutor_login`) | `migrate.env` (creation), `tutor.env` (`DATABASE_URL`) | the `app` service | `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, member of `tutor_app` with `INHERIT FALSE, SET TRUE`; owns nothing |
| `tutor_report` | `migrate.env` (password) | the gate report, run by hand | `LOGIN BYPASSRLS`, read only, `SELECT` on `sessions`, `session_metrics`, `audit_log` |

`migrate` runs `alembic upgrade head` and then `tutor.ops.provision_roles`, which creates or updates the app login and `tutor_report` and sets their passwords from the secrets. It is idempotent: rerun it after editing a password. `app` starts only after `migrate` exits 0 and refuses to serve if its login fails any check of `tutor.db.engine.check_app_role`; the full list (superuser or BYPASSRLS, directly or through a role, owned tables, relations, functions or schemas, `pg_database_owner`, any membership other than `tutor_app`, no `tutor_app` membership) is amendment 5 of the design spec (`docs/superpowers/specs/2026-10-04-core-loop-v0-design.md`, "Amendments"). It also refuses a `DATABASE_URL` that is not a PostgreSQL URL in prod.

## Google OAuth client

1. Google Cloud console, APIs & Services, Credentials, Create OAuth client ID, Web application.
2. Authorized redirect URIs, **both** required:
   - `https://<host>/oauth/callback` (MCP OAuth proxy, `GoogleProvider(redirect_path="/oauth/callback")`)
   - `https://<host>/auth/callback` (website login, Authlib)
3. OAuth consent screen: External, publishing status Testing; add the author and every invited tester as **test users** (only listed addresses can sign in). Scopes: `openid`, `email`, `profile`.
4. Keep the client id and secret for `tutor.env`.

## Cloudflare Tunnel, DNS and edge rules

1. Zero Trust, Networks, Tunnels, create a tunnel (Cloudflared) named `tutor-v0`; public hostname `<host>`, service type `HTTP`, URL `app:8000`. The DNS record is created and proxied by the tunnel. Copy the token into `tunnel.env`.
2. Cache Rule for `<host>`: bypass cache. No Cloudflare Access application on this hostname (Claude's servers must reach it).
3. SSL/TLS, Edge Certificates: **Always Use HTTPS** on, **Minimum TLS Version 1.2**, and **HSTS** enabled for the zone (start with max-age 6 months, no preload until you are sure every subdomain is HTTPS).
4. **Per-IP rate limit** for the unauthenticated endpoints (the app has no limiter before sign-in; web writes are limited per user inside the app, Task 25). What the expression can match depends on the plan: Free matches URI Path only, Pro adds Host, URI and Query, and `ip.src` or the request method need Business. The counting characteristic is the client IP on every plan.
   - **Free** (one path-only rule; period 10 s, action Block, about 50 requests per 10 s per IP; the rule itself cannot name an IP range on this plan, but a WAF *Skip* rule can exempt Anthropic's range from it, see the next step):

     ```
     (http.request.uri.path in {"/register" "/authorize" "/token" "/consent" "/oauth/callback" "/auth/google" "/auth/callback"})
     ```

   - **Pro**: the same rule with `http.host eq "<host>" and` in front. **Business** can also add `not ip.src in {160.79.104.0/21}` (Anthropic's egress range, shared by all Claude clients) and `or (http.request.method eq "POST" and starts_with(http.request.uri.path, "/app/"))`.
5. **Exempt Anthropic's egress range from rate limiting (optional, every plan).** Custom rules, and their *Skip* action, exist on all plans. Create a custom rule with expression `ip.src in {160.79.104.0/21}`, action *Skip*, and tick *All rate limiting rules* (API: `action_parameters.phases: ["http_ratelimit"]`). Claude's servers share that range, so without the skip all learners' connector calls count against one IP. Order it first.
6. **Bot protection.** On Free, Bot Fight Mode cannot be skipped for any source, and it will challenge Claude's connector calls: turn it **off** for the zone (Security, Bots) and rely on the rate limit. On Pro or higher use Super Bot Fight Mode and extend the skip rule above with Super Bot Fight Mode and the remaining custom rules for `ip.src in {160.79.104.0/21}`, the same range the spike runbook (`spike/README.md`) skips.
7. The application trusts `X-Forwarded-*` only from the cloudflared container (`FORWARDED_ALLOW_IPS=172.30.10.3`, its fixed address on the `edge` network; dynamic addresses come from `172.30.10.128/25`, so they can never take it). If `172.30.10.0/24` collides with your LAN or another Docker network, pick another /24 in `compose.prod.yml` and set the same address in `tutor.env`.

## First deploy

1. Install Docker Engine with the Compose plugin. Create the deploy user and `/opt/tutor`. `git clone <repo> /opt/tutor && cd /opt/tutor && git checkout <release tag>`.
2. Create the backup directory and log file for the deploy user (cron runs as that user, not root):

   ```sh
   sudo install -d -m 700 -o <deploy-user> -g <deploy-user> /var/backups/tutor
   sudo install -m 600 -o <deploy-user> -g <deploy-user> /dev/null /var/log/tutor-backup.log
   ```

3. Create the four env files. They hold secrets: `chmod 600`, never committed (`deploy/*.env` is in `.gitignore`).

   ```sh
   cd /opt/tutor/deploy
   for f in db migrate tutor tunnel; do cp "$f.env.example" "$f.env"; done
   chmod 600 db.env migrate.env tutor.env tunnel.env
   ```

4. Generate secrets (each one different):

   | Secret | Where | Command |
   | --- | --- | --- |
   | `POSTGRES_PASSWORD` | `db.env` and inside `MIGRATION_DATABASE_URL` | `openssl rand -hex 24` |
   | `APP_DB_PASSWORD` | `migrate.env` and inside `DATABASE_URL` in `tutor.env` | `openssl rand -hex 24` |
   | `REPORT_DB_PASSWORD` | `migrate.env` | `openssl rand -hex 24` |
   | `TUTOR_JWT_SIGNING_KEY` | `tutor.env` (at least 32 characters) | `openssl rand -base64 48` |
   | `TUTOR_OAUTH_STORAGE_KEY` | `tutor.env` (Fernet key, distinct from the JWT key) | after step 5: `docker run --rm --entrypoint python tutor:latest -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

   Use hex passwords so they need no URL-encoding in the database URLs. The server refuses to start when the two app secrets are short, malformed or equal, and in prod when `MIGRATION_DATABASE_URL` or `GATE_REPORT_DATABASE_URL` is present in its environment (every settings check: design spec amendment 24). Fill the rest of `tutor.env`: `TUTOR_BASE_URL=https://<host>`, `TUTOR_MCP_URL=https://<host>/mcp` (or empty, which derives it; any other value stops the server), `TUTOR_SUPPORT_EMAIL`, the Google client id and secret, and `DATABASE_URL=postgresql://<APP_DB_USER>:<APP_DB_PASSWORD>@db:5432/<POSTGRES_DB>`. `TUTOR_ENV=prod`, `FORWARDED_ALLOW_IPS=172.30.10.3` (never `*`) and `TUTOR_OAUTH_STORAGE_DIR=/data/oauth` stay as shipped. Leave `TUTOR_TEST_LOGIN` empty.
5. Fetch the pinned third-party images and build ours: `docker compose -f compose.prod.yml pull db cloudflared`, then `docker compose -f compose.prod.yml build migrate`. (`app` and `migrate` share the image `tutor:latest`, built locally and never pulled: `pull_policy: never` for `app`, `build` for `migrate`.) The build fails if the installed package lacks its locale `.po` files, templates, static files or the track YAML. All base images are pinned by `@sha256:` digest; see "Upgrades" for bumping them.
6. Migrate: `docker compose -f compose.prod.yml up -d db`, then `docker compose -f compose.prod.yml run --rm migrate`. Expect Alembic `Running upgrade` lines up to the current head, then `roles ready: <APP_DB_USER>, tutor_report`.
7. Start: `docker compose -f compose.prod.yml up -d`. (`app` waits for `migrate` to complete; `cloudflared` waits for `app` to be healthy.) Check with `docker compose -f compose.prod.yml ps` and `logs app`.
8. Smoke checks from any machine:
   - `curl -s https://<host>/.well-known/oauth-authorization-server` returns JSON whose `authorization_endpoint` is `https://<host>/authorize`; `curl -s https://<host>/.well-known/oauth-protected-resource/mcp` names `https://<host>/mcp` as the resource.
   - `curl -si -X POST https://<host>/mcp` returns `401` with a `WWW-Authenticate` header naming that protected-resource URL.
   - `curl -si -X POST https://<host>/register -H 'content-type: application/json' -d '{}'` returns a 4xx JSON error (not 5xx), and `curl -si https://<host>/consent` answers (4xx without a pending transaction): both routes are reachable through the tunnel.
   - `https://<host>/login` shows the sign-in page with the privacy note; "Entrar con Google" with a test user lands on Perfil. Add `https://<host>/mcp` as a custom connector in Claude and run one lesson.
   - Rate limit, once the rule from the edge section is on. The Free window is 10 s, so send the requests in parallel: `seq 1 200 | xargs -P 40 -I{} curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<host>/token | sort | uniq -c` shows some `429` (or Cloudflare's block status) next to the app's own 4xx.
9. Install the backup and stale-backup crons and run `./backup.sh` once by hand (see Backups).

Existing `oauth` volume from an earlier deployment (created by a root-owned image): hand it to the runtime user once, `docker run --rm --user root --entrypoint sh -v tutor_oauth:/data/oauth tutor:latest -c 'chown -R 10001:10001 /data/oauth && chmod 700 /data/oauth'`. A fresh volume inherits the image's ownership and mode and needs nothing.

The purge of expired web sessions runs inside the app lifespan (at startup, then hourly). There is no cron for it.

## PostgreSQL version

PostgreSQL 16 or later is required, and the compose file pins `postgres:16`. The schema uses `ON DELETE SET NULL (column)` (15+), and the role setup and the startup check rely on the 16 role-membership model: `GRANT ... WITH INHERIT FALSE, SET TRUE` and `pg_has_role(..., 'SET')`. A major upgrade is a dump and restore (below), never a changed image tag over an existing data volume.

## Upgrades and migrations

- Deploy a new release: `git fetch && git checkout <tag>`, `docker compose -f compose.prod.yml build migrate`, then `docker compose -f compose.prod.yml run --rm migrate` (migrations and roles) and `docker compose -f compose.prod.yml up -d`.
- Take a backup (`./backup.sh`) before any release that adds a migration.
- Current revision: `docker compose -f compose.prod.yml run --rm --entrypoint alembic migrate -c /app/alembic.ini current`.
- Never edit an applied migration; a fix is a new revision. Downgrade only to recover, right after a backup.
- Image pins: python, uv, postgres, cloudflared and alpine are referenced by `@sha256:` digest (`deploy/Dockerfile`, `compose.prod.yml`, `backup.sh`). To bump one: `docker pull <tag>`, `docker image inspect <tag> --format '{{index .RepoDigests 0}}'`, replace the digest, rebuild, run the smoke checks. A Postgres major version change is a dump and restore, never a new tag over the data volume.
- Dependency bumps: before bumping `fastmcp` read its auth/OAuth changelog (the OAuth proxy, token storage and consent behaviour are security relevant) and re-run `uv run just check`, `test-int` and the smoke checks above. Rebuild `uv.lock` deliberately, and run `pip-audit` (part of `just check`).

## Backups and the restore drill

`deploy/backup.sh` writes, with `umask 077` (directory 700, files 600):

- `tutor-<stamp>.dump`: `pg_dump --format=custom` of the tutor database (checked with `pg_restore --list`);
- `tutor-roles-<stamp>.sql`: `pg_dumpall --roles-only`. Roles are cluster-level and are **not** in the dump. The file holds password hashes, which is why it is mode 600;
- `tutor-oauth-<stamp>.tgz`: the OAuth store volume (a pinned alpine container tars it read only). Entries are Fernet-encrypted, but treat the archive as sensitive as `TUTOR_OAUTH_STORAGE_KEY`.

Files are written as `.partial` and renamed only when complete; all three kinds older than 30 days are deleted last (`RETENTION_DAYS`, `BACKUP_DIR` override the defaults). Cron, as the deploy user (the directory is created in "First deploy"):

```
30 3 * * * /opt/tutor/deploy/backup.sh >> /var/log/tutor-backup.log 2>&1
30 4 * * * /opt/tutor/deploy/check_backup.sh || echo "tutor backup stale" | mail -s tutor <you>
```

`check_backup.sh` fails when the newest dump is missing or older than 26 hours; wire it to mail, a webhook or your uptime monitor.

**Off-site copy.** A backup on the same disk is not a backup. Copy the directory off the machine **encrypted**, for example with age (`age -r <public key> -o tutor-<stamp>.dump.age tutor-<stamp>.dump`, keep the private key elsewhere) or `gpg --encrypt`, and apply the same 30-day retention at the destination (a lifecycle rule, or `find ... -mtime +30 -delete` there). Every file above is sensitive (learner text, role hashes, tokens).

**What losing the OAuth store means.** If `tutor_oauth` is lost (and not restored from the tarball) or `TUTOR_OAUTH_STORAGE_KEY` changes, the OAuth proxy forgets its client registrations and issued tokens: Claude connections stop working and each learner removes and re-adds the connector (Claude registers again). No learner data is lost; it is all in Postgres, and web sessions are in Postgres too. To restore the volume from the tarball, stop `app`, then run `docker run --rm -i --user root --entrypoint sh -v tutor_oauth:/restore tutor:latest -c 'tar xzf - --strip-components=1 -C /restore && chown -R 10001:10001 /restore && chmod 700 /restore' < tutor-oauth-<stamp>.tgz` and start `app`.

Restore drill (monthly, and once before inviting testers):

1. `latest=$(ls -1 /var/backups/tutor/tutor-2*.dump | tail -n 1)`
2. `docker compose -f compose.prod.yml exec -T db sh -c 'createdb -U "$POSTGRES_USER" tutor_restore'`
3. `docker compose -f compose.prod.yml exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d tutor_restore --no-owner --no-acl' < "$latest"`
4. Compare with live: run the same query against both databases.

   ```sh
   Q='SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM sessions) AS sessions, (SELECT max(started_at) FROM sessions) AS last_session'
   docker compose -f compose.prod.yml exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d tutor_restore -Atc \"$Q\""
   docker compose -f compose.prod.yml exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"$Q\""
   ```

   The rows match up to sessions started after the dump.
5. `docker compose -f compose.prod.yml exec -T db sh -c 'dropdb -U "$POSTGRES_USER" tutor_restore'`

Disaster restore (new or wiped cluster):

1. `docker compose -f compose.prod.yml up -d db`. A fresh cluster already has an empty database named `POSTGRES_DB`, created by the Postgres image for the owner in `db.env`. Do **not** run `createdb` for it (it fails with "already exists"). If the cluster holds data you are replacing, stop `app` and run `dropdb --if-exists` then `createdb` instead.
2. Roles first: `docker compose -f compose.prod.yml exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres' < /var/backups/tutor/tutor-roles-<stamp>.sql`. On a fresh cluster expect "role ... already exists" errors for the owner and similar noise; they are harmless. Anything else is not.
3. `docker compose -f compose.prod.yml exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error' < /var/backups/tutor/tutor-<stamp>.dump`, without `--no-owner --no-acl` (owners and grants must come back).
4. The roles file carries the owner's *old* password verifier. Reset it to the current `POSTGRES_PASSWORD` before anything connects as the owner: `docker compose -f compose.prod.yml exec -e PSQL_HISTORY=/dev/null db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`, then `\password <owner>` (or restore `db.env` and `MIGRATION_DATABASE_URL` to the password in the file).
5. `docker compose -f compose.prod.yml run --rm migrate` (a no-op for the schema; resets both passwords from `migrate.env`), then `up -d`.

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

1. Ask them to remove the "English Tutor" connector in Claude, **and** to remove the app's access at myaccount.google.com/connections (Sign in with Google). The server's Google provider re-checks access tokens and refreshes against Google, so revoking there cuts their access immediately; removing only the connector can leave a live refresh token. Without both, the next tool call could recreate an empty account from the same Google `sub`.
2. As the owner, keeping the session out of psql history: `docker compose -f compose.prod.yml exec -e PSQL_HISTORY=/dev/null db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`. Find the id: `SELECT id FROM users WHERE email = '<their email>';`
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

4. Check `SELECT count(*) FROM users WHERE id = :'uid';` returns 0, then reply to the learner. The OAuth store keeps no per-user index to delete from (entries are Fernet-encrypted), so the Google revocation in step 1 is what ends their tokens. If they did not revoke it, the refresh token stays valid for up to 30 days; with the user row gone it reaches no data. To cut every token at once, rotate `TUTOR_OAUTH_STORAGE_KEY` (below), which disconnects everyone. Old backups still hold the data until they age out (30 days); say so in the reply.

## Rotating keys

| Key | How | Effect |
| --- | --- | --- |
| `GOOGLE_CLIENT_SECRET` | New secret in the Google console, edit `tutor.env`, `docker compose -f compose.prod.yml up -d app`, delete the old secret | None for learners |
| `TUTOR_JWT_SIGNING_KEY` | New `openssl rand -base64 48`, `up -d app` | Every Claude connection must reconnect |
| `TUTOR_OAUTH_STORAGE_KEY` | New Fernet key, `up -d app`; optionally empty the volume: `docker compose -f compose.prod.yml run --rm --no-deps --entrypoint sh app -c 'rm -rf /data/oauth/*'` | Old entries are unreadable; clients re-register |
| `APP_DB_PASSWORD` | Edit `migrate.env` and `DATABASE_URL` in `tutor.env`, `run --rm migrate`, `up -d app` | None |
| `REPORT_DB_PASSWORD` | Edit `migrate.env`, `run --rm migrate` | None |
| `POSTGRES_PASSWORD` | Open psql as in the deletion steps (`exec -e PSQL_HISTORY=/dev/null db ...`) and run `\password <owner>` (psql hashes it client side; never type `ALTER USER ... PASSWORD '...'`, which lands in history and server logs), then edit `db.env` and `MIGRATION_DATABASE_URL` | None |
| `TUNNEL_TOKEN` | Rotate in Zero Trust, edit `tunnel.env`, `up -d cloudflared` | Seconds offline |

## Logs

`docker compose -f compose.prod.yml logs app` prints one JSON line per tool call (`tutor.mcp.calls`) and per HTTP request (`tutor.http`) with `user_hash`, never tokens, codes, emails or learner text. The uvicorn access log is off because it would print `/oauth/callback?code=...`.
