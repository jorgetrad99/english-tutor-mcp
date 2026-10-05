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
- `migrate` carries Coolify's `exclude_from_hc: true`.
- `backup.sh` accepts `DB_CONTAINER` and `OAUTH_VOLUME`. Without them it behaves as before.

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
- **Unverified:** whether Coolify adds its resource network to `db` and `migrate`. If it does, they gain egress (nothing is published). The runbook asks to check the Deployable Compose on the first deploy; record the result here.
- **Unverified:** whether this Coolify version strips `exclude_from_hc` before calling Docker Compose. Plain `docker compose config` rejects the key. The runbook's troubleshooting section covers removing it.
- Backups stay host cron jobs: Coolify's scheduled backups cover only databases Coolify manages.
- The runbook's `docker compose` commands need Coolify's names; `docs/v0/coolify.md` maps them.
