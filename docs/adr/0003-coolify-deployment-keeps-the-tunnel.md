# 0003 — Coolify deployment keeps the Cloudflare Tunnel

- **Status:** proposed
- **Date:** 2026-10-05
- **Deciders:** Jorge
- **Runbook:** `docs/v0/coolify.md`
- **Number note:** 0001 is reserved for the spike go/no-go on Oct 11.

## Context

v0 runs on the homelab from `deploy/compose.prod.yml` (`docs/v0/runbook.md`): secrets in git-ignored `deploy/*.env` files, a backend network with no internet access, and ingress only through a Cloudflare Tunnel. The app trusts `X-Forwarded-*` from exactly one address: cloudflared's fixed address on the edge network. Requirements section 4 allows a single VPS.

The stack is moving to a VPS managed by Coolify. Coolify deploys a Git repository with its Docker Compose build pack, but:
- its checkout has no `deploy/*.env` files;
- it names projects, containers and volumes after its resource id;
- it allocates its networks from Docker's default pools;
- its own ingress is a Traefik proxy, and the proxy container has no fixed address.

Two options:
- **A. Keep the tunnel.** Coolify only builds and runs the stack.
- **B. Use Coolify's proxy.** Traefik with Let's Encrypt, no cloudflared.

## Decision

A, in a separate file, `deploy/compose.coolify.yml`; `compose.prod.yml` is unchanged for the homelab.

- Each service gets an `environment:` mapping with exactly the keys of its `*.env.example`. Secrets are `${NAME:?}` and fixed settings are literals. The owner and app database URLs are built from the same values.
- The edge network moves to `10.213.10.0/24`, outside Docker's default pools. cloudflared is fixed at `10.213.10.3`, the only `FORWARDED_ALLOW_IPS` entry.
- The tunnel targets `tutor-edge-app`, an alias on the edge network only. If Coolify attaches its own network to the services, the app could resolve on two networks; the alias keeps cloudflared's requests on edge, so they come from the trusted address.
- `app` and `migrate` each build the image; there is no shared `image:` name.
- The application runs with **Raw Compose Deployment** on, so Coolify deploys the file as written; the file is plain Compose with no Coolify-only keys. On 2026-10-05, Coolify's normal mode was seen to:
  - give every service `env_file: .env` holding all 13 variables, so cloudflared would hold the database passwords and the app the owner password;
  - attach every service to its own internet-facing network, undoing `backend`'s `internal: true`;
  - drop `app`'s null `backend:` entry (now written `backend: {}`);
  - write whole-value variables into the file, so `${NAME:?}` no longer stopped a deploy.

  It also strips `exclude_from_hc`, which raw mode would pass to Docker Compose as an error; the key is gone.
- `backup.sh` accepts `COMPOSE_PROJECT` (the application uuid). It finds the db container by its compose labels, because Coolify's container names change between deploys, and uses `<uuid>_oauth`. Without it the script behaves as before.

Releases follow the requirements' CI/CD row ("deploy on tag"). A `v*` tag on `main`:
- passes `just check` in CI;
- force-moves the `production` branch, which the Coolify application tracks, to the tagged commit;
- triggers a Coolify deployment through its API with a read and deploy token;
- waits for the result and smoke-tests the public host.

Coolify's automatic deploy stays off. Moving a branch was chosen over pinning a commit through Coolify's API because Coolify's documentation does not confirm an API field for pinning one. `tests/unit/deploy/test_ci_workflow.py` holds the job's order, trigger and permissions.

`tests/unit/deploy/test_coolify_compose.py` holds the file to compose.prod.yml's guarantees:
- the role split and per-service secrets;
- required variables;
- the forwarded-header trust;
- the network split;
- hardening flags and pinned third-party images.

Option B was rejected for three reasons:
- `FORWARDED_ALLOW_IPS` would have to trust an address that is not fixed, or a whole subnet.
- The origin would be reachable directly unless a firewall limited it to Cloudflare's ranges.
- The Cloudflare rate limit, WAF skip rule and HSTS settings from the runbook would need re-validating.

## Consequences

- Same security posture and Cloudflare configuration as the homelab, and no inbound port on the VPS.
- Secrets live in Coolify's database instead of files with mode 600; access to the Coolify dashboard is now access to every secret. Protect it with a strong password and 2FA, and keep the dashboard off the public internet where possible.
- Raw mode makes Coolify track the containers less closely: its status view may show the application as degraded or exited (`migrate` exits by design). The deploy log, the runbook's step 7 host checks and the CI smoke checks are the source of truth.
- **Unverified until the first raw deploy:** that raw mode still passes Coolify's variables to Compose interpolation, and names the project after the application uuid (`<uuid>_backend`, `<uuid>_oauth`). The runbook's step 7 checks both; record the result here.
- Coolify's dashboard also answers on plain HTTP at the server's IP and port 8000. CI uses only the https origin (`deploy/coolify_deploy.sh` refuses anything else); close port 8000 to the internet once the https origin works.
- Backups stay host cron jobs: Coolify's scheduled backups cover only databases Coolify manages.
- The runbook's `docker compose` commands need Coolify's names; `docs/v0/coolify.md` maps them.
