# Core loop v0 on Coolify

The production stack of `docs/v0/runbook.md`, deployed by Coolify from the Git repository with `deploy/compose.coolify.yml` (decision: `docs/adr/0003-coolify-deployment-keeps-the-tunnel.md`). Ingress is still the Cloudflare Tunnel; Coolify builds, starts and redeploys the stack and holds the secrets. Everything in the runbook applies unless this page says otherwise: the three database roles, the Google OAuth client, the Cloudflare edge rules, PostgreSQL version, user deletion.

How it differs from `compose.prod.yml`:

| Thing | Homelab (`compose.prod.yml`) | Coolify (`compose.coolify.yml`) |
| --- | --- | --- |
| Secrets | `deploy/*.env` files | Coolify environment variables, `${NAME:?}` in the compose file |
| Database URLs | typed into two files | built from `POSTGRES_*` and `APP_DB_*`; each password is entered once |
| Tunnel target | `app:8000` | `http://tutor-edge-app:8000` (an alias on `edge` only) |
| `edge` network | `172.30.10.0/24`, cloudflared at `.3` | `10.213.10.0/24`, cloudflared at `10.213.10.3` (outside Docker's default pools) |
| Image | `tutor:latest`, built by `migrate` | built by `migrate` and `app` from the same Dockerfile (cached) |
| Names | project `tutor`, volume `tutor_oauth` | project = the application's uuid: volumes `<uuid>_oauth`, containers found by compose labels (their names change between deploys) |
| Coolify's compose changes | none | none: **Raw Compose Deployment** is on (see step 4) |

## First deploy

1. **VPS and Coolify.** Install Coolify and add the server. The tutor needs no inbound port: the tunnel connects outwards.
2. **Google OAuth client and Cloudflare edge rules** as in the runbook, with one change in "Cloudflare Tunnel, DNS and edge rules", step 1: the public hostname's service is `HTTP`, URL **`tutor-edge-app:8000`**, not `app:8000`. Copy the tunnel token.
3. **Resource.** Create the `production` branch once (`git push origin main:production`); CI moves it from then on (see Releases). New Resource, Application, the repository (GitHub App or deploy key), branch **`production`**. Build Pack **Docker Compose**, Base Directory **`/deploy`**, Docker Compose Location **`/compose.coolify.yml`**, then Load Compose File. The build context `..` is the repository root only with that Base Directory.
4. **Raw mode, no domains, no shared network, no auto deploy.**
   - Turn **Raw Compose Deployment** on (Advanced, Build). Without it, Coolify (checked 2026-10-05) gives every container every variable through `env_file: .env`, attaches every service to its own internet-facing network (undoing `backend`'s `internal: true`), and writes values into the file so `${NAME:?}` no longer stops a deploy. Raw mode deploys the file as written; it needs no proxy labels because the tunnel is the only way in.
   - Leave Domains empty on every service.
   - Keep **Connect To Predefined Network** off; on the shared network `db` could resolve to another stack's database.
   - Turn **Auto Deploy** off (it starts on); CI deploys (see Releases).
5. **Environment variables.** Coolify lists the variables the compose file references. Fill each one (values go in your password manager, never the repository), and leave them runtime-only: the Dockerfile reads none at build time.

   | Variable | Value |
   | --- | --- |
   | `POSTGRES_USER` | `tutor_owner` |
   | `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
   | `POSTGRES_DB` | `tutor` |
   | `APP_DB_USER` | `tutor_login` |
   | `APP_DB_PASSWORD` | `openssl rand -hex 24` |
   | `REPORT_DB_PASSWORD` | `openssl rand -hex 24` |
   | `TUTOR_BASE_URL` | `https://<host>` (no path, no trailing slash) |
   | `TUTOR_SUPPORT_EMAIL` | the support address |
   | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | from the Google console |
   | `TUTOR_JWT_SIGNING_KEY` | `openssl rand -base64 48` |
   | `TUTOR_OAUTH_STORAGE_KEY` | on your machine: `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
   | `TUNNEL_TOKEN` | the tunnel token |

   Every password is different; hex keeps them free of URL-encoding. `TUTOR_ENV=prod`, `FORWARDED_ALLOW_IPS`, `TUTOR_MCP_URL` (`<TUTOR_BASE_URL>/mcp`) and the other fixed settings are literals in the compose file and do not appear in the UI. A missing value stops the deploy with "required variable ... is missing a value"; the server's own startup checks (runbook, "First deploy" step 4) still apply.
6. **Deploy.** Expect both builds, `db` healthy, the `migrate` log ending in `roles ready: tutor_login, tutor_report`, `app` healthy, then `cloudflared` registering its connections. `migrate` stays exited (0); that is its job.
7. **Check what Coolify deployed.** On the host, after the deploy (`<uuid>` is the application's):

   ```sh
   for s in db migrate app cloudflared; do
     c=$(docker ps -aq --filter label=com.docker.compose.project=<uuid> --filter label=com.docker.compose.service=$s)
     echo "$s: $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' $c)"
     docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' $c | cut -d= -f1 | sort | tr '\n' ' '; echo
   done
   ```

   Expect `db` and `migrate` on `<uuid>_backend` only, `app` on `<uuid>_backend` and `<uuid>_edge`, `cloudflared` on `<uuid>_edge` only, and each container holding only its own variable names (plus the image's own `PATH` and similar). The command prints names, never values.
8. **Smoke checks**: runbook, "First deploy" step 8, unchanged.

## Releases

A version tag on `main` deploys; nothing else does. The `deploy` job in `.github/workflows/ci.yml`:
1. runs after `check` passes on the tag;
2. refuses a tag whose commit is not on `main`;
3. force-moves the `production` branch to the tagged commit;
4. calls Coolify (`deploy/coolify_deploy.sh`) and waits until the deployment finishes, failing the job if it fails, is cancelled, or is still running after 25 minutes;
5. smoke-tests the public host: OAuth metadata, and `POST /mcp` answering 401.

Each deploy builds, reruns `migrate` (idempotent: migrations, then role passwords) and only then replaces `app`. Coolify's own automatic deploy stays **off**, so pushes to `production` or anywhere else never deploy by themselves.

To release:

```sh
git switch main && git pull
# release adds a migration? back up first, on the VPS: COMPOSE_PROJECT=<uuid> /opt/tutor/deploy/backup.sh
git tag -a v0.2.0 -m "v0.2.0" && git push origin v0.2.0
```

Roll back by re-running the deploy job of an earlier tag (Actions, that tag's CI run, Re-run all jobs). A rollback across a migration also needs a database restore (runbook, "Upgrades and migrations").

One-time setup:

| Where | What |
| --- | --- |
| Coolify, Keys & Tokens, API Tokens | a token with **read** and **deploy** only (not `write`, `read:sensitive` or `root`) |
| GitHub, Settings, Environments | an environment `production`; under deployment branches and tags allow only tags matching `v*` |
| `production` environment secret | `COOLIFY_TOKEN` |
| `production` environment variables | `COOLIFY_URL` (dashboard origin, https, no trailing slash), `COOLIFY_APP_UUID` (the application's uuid, in its Coolify URL), `TUTOR_BASE_URL` (`https://<host>`) |
| GitHub, branch rules | if `production` is protected, allow GitHub Actions to force-push it |

The job needs Coolify's API reachable from GitHub's runners. Anyone with the token can redeploy (not change settings or read secrets); revoke it in Coolify if it leaks. `deploy/coolify_deploy.sh` runs by hand too, with the same three `COOLIFY_*` variables.

## Backups

Coolify's scheduled database backups cover databases Coolify manages, not one inside a compose application, so `backup.sh` and `check_backup.sh` stay as host cron jobs.

1. Keep a checkout for the scripts only: `git clone <repo> /opt/tutor` (update it when the scripts change). Create `/var/backups/tutor` and the log file as in the runbook, "First deploy" step 2.
2. Cron, as the deploy user (who must be able to run `docker`). `COMPOSE_PROJECT` is the application's uuid: `backup.sh` finds the db container by its compose labels and uses the volume `<uuid>_oauth`.

   ```
   30 3 * * * COMPOSE_PROJECT=<uuid> /opt/tutor/deploy/backup.sh >> /var/log/tutor-backup.log 2>&1
   30 4 * * * /opt/tutor/deploy/check_backup.sh || echo "tutor backup stale" | mail -s tutor <you>
   ```

   Run the first line once by hand. `check_backup.sh` alerts if a backup stops working.

## Runbook commands under Coolify

The runbook's `docker compose -f compose.prod.yml ...` commands need the Coolify names. Container names change between deploys, so look them up first (`<uuid>` is the application's):

```sh
P=<uuid>
db=$(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=db)
app=$(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=app)
```

| Runbook | Coolify |
| --- | --- |
| `... exec -T db sh -c '...'` (restore drill, disaster restore, psql) | `docker exec -i "$db" sh -c '...'` |
| `... exec -e PSQL_HISTORY=/dev/null db sh -c 'psql ...'` | `docker exec -it -e PSQL_HISTORY=/dev/null "$db" sh -c 'psql ...'` |
| `... run --rm migrate` | Redeploy in Coolify |
| `... up -d app` after a secret change | change the variable in Coolify, then Redeploy |
| `... logs app` | `docker logs "$app"` |
| `... run --rm --no-deps ... app` (gate report, oauth volume chown or restore) | `docker run --rm --network ${P}_backend ... "$(docker inspect -f '{{.Config.Image}}' "$app")"` with the same `-e`, `-v` and `--entrypoint` options; the oauth volume is `${P}_oauth` |

In the runbook, `@db:5432` and the volume name `tutor_oauth` become `db` on `<uuid>_backend` and `<uuid>_oauth`. Rotating keys: `APP_DB_PASSWORD` is now one edit and a redeploy (`DATABASE_URL` is built from it). `POSTGRES_PASSWORD` is still `\password` in psql first, then the Coolify variable, then redeploy: the postgres image reads it only when it creates the cluster.

## Troubleshooting

- **`required variable ... is missing a value` for a variable you did fill in.** Raw mode is not passing Coolify's variables to Docker Compose's interpolation. Do not turn raw mode off to work around it (that brings back every secret in every container); report it.
- **Coolify shows the application as degraded, exited or unknown** after a successful deploy. `migrate` exits by design, and in raw mode Coolify tracks the containers less closely. Trust the deploy log and step 7's checks, then the smoke checks.
- **`Pool overlaps with other one on this address space`.** Another network already uses `10.213.10.0/24`. Pick another /24 outside `172.16.0.0/12` and `192.168.0.0/16`, and change `subnet`, `ip_range`, `ipv4_address` and `FORWARDED_ALLOW_IPS` together.
- **Build cannot find `deploy/Dockerfile` or `pyproject.toml`.** Base Directory is not `/deploy`.
- **Cloudflare 502 or 1033.** The tunnel's service URL is not `tutor-edge-app:8000`, or `cloudflared` is waiting for `app` to become healthy (`docker logs "$app"`, see the lookup above).
- **Sign-in or redirects use the wrong scheme or client address.** Requests reach `app` from an address other than `10.213.10.3`; check the tunnel target is the alias, not `app`.
