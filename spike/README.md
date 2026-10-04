# Spike runbook (throwaway)

Design: `docs/superpowers/specs/2026-10-03-spike-design.md`. Experiments: `docs/spike/01–04`.
This code is deleted at the go/no-go (ADR 0001). Raw data in `data/raw/` never leaves this machine.

## One-time setup

1. **Google Cloud** (console.cloud.google.com): new project → OAuth consent screen: External,
   Testing, scopes `openid` and `.../auth/userinfo.email`, test users = the two Google
   accounts used by the Free and Pro Claude accounts → Credentials → OAuth client ID, type
   Web application, authorized redirect URI `https://<spike-host>/auth/callback`.
2. **Cloudflare Tunnel** (PowerShell):
   ```
   winget install --id Cloudflare.cloudflared
   cloudflared tunnel login
   cloudflared tunnel create tutor-spike
   cloudflared tunnel route dns tutor-spike <spike-host>
   ```
   `%USERPROFILE%\.cloudflared\config.yml`:
   ```
   tunnel: tutor-spike
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
   ingress:
     - hostname: <spike-host>
       service: http://localhost:8765
     - service: http_status:404
   ```
   In the Cloudflare dashboard for `<spike-host>`: proxied DNS record, a Cache Rule
   "bypass cache", no Cloudflare Access application, and a WAF custom rule *Skip* (all
   remaining custom rules, Bot Fight Mode / Super Bot Fight Mode) for
   `ip.src in {160.79.104.0/21}`.
3. **Settings**: copy `env.example` to `.env` and fill it in (Claude never edits `.env`).
4. **Install**: from the repo root, `uv sync --directory spike`.

All commands below run from the `spike/` directory (`cd spike`), so `data/raw` and `.env`
resolve there.

## Start (every session day)

```
powercfg /change standby-timeout-ac 0
cloudflared tunnel run tutor-spike
uv run --env-file .env python -m tutor_spike.server
```

Check: `curl -s https://<spike-host>/.well-known/oauth-protected-resource/mcp` returns JSON
whose `resource` is exactly `https://<spike-host>/mcp`.

## Setup acceptance (once, before run 1)

- [ ] On claude.ai web, Free account: Settings → Connectors → Add custom connector →
      URL `https://<spike-host>/mcp` → sign in with the Free Google account.
- [ ] Same for the Pro account with the Pro Google account.
- [ ] In a text chat on each account: ask Claude to call `get_profile`; check a `kind: tool`
      line with the right tester label in `data/raw/calls-*.jsonl`.
- [ ] Restart the server; call `get_profile` again without reconnecting (persistence).
- [ ] Open the Claude app on the phone, confirm the connector is listed for both accounts.
- [ ] Delete the test lines: move `data/raw/` to `data/setup/` so runs start clean.
      The server recreates `data/raw/` on the next request (JsonlLog/SessionRegistry mkdir on write).
- [ ] Freeze: `git tag spike-instructions-v1` and record `git rev-parse --short HEAD` in the
      Setup of `docs/spike/01–04`.

## Pre-run checklist (every run)

- Laptop on mains power, sleep disabled; tunnel and server running; the curl check passes.
- Only this connector enabled in the chat; note the model the client shows.
- Voice runs: start the phone screen recording first.

## During and after a run (spec §7)

- New chat. Say only "Let's practise English for 15 minutes." Never mention tools or saving.
- Use the next card from `deck.md`. Speak naturally. Phone timer: 15 minutes.
- Close like a person: "OK, I have to go, thanks." No nudge.
- Add the row to `data/raw/runs.csv` (header in `runs.template.csv`); set the `transcript_file` column to `transcripts/<run_id>.md` (path relative to data/raw).
- Copy the conversation from claude.ai web into `data/raw/transcripts/<run_id>.md`, one turn
  per block, user lines starting `U: ` and Claude lines starting `A: `; leave tool-call
  blocks out.

## Annotation (Sat Oct 10, before looking at payloads)

For each of 10 text runs (5 Free, 5 Pro) write `data/raw/annotations/<run_id>.json`:
`[{"said": "<exact words>", "correct": "<correct version>"}]`, from the transcript only.

## Analysis and export

```
uv run python -m tutor_spike.analysis --data data/raw
uv run python -m tutor_spike.redact --names "<your name>,<other names>"
```

In `--names`, list every spelling of each name that might appear: with and without accents (e.g. "José,Jose"), nicknames, usernames/handles and joined forms (e.g. "jorgeperez"), because a name glued to other letters is deliberately not matched.

Paste the tables into `docs/spike/01–04`, review the exported files for anything personal,
then commit them.

If rule 9.2's extension triggers, run the extension series with its own data directory
(`SPIKE_DATA_DIR=data/raw-extension`) and use `--data data/raw-extension` for both analysis and redact commands, so the first 25 runs stay unchanged.
