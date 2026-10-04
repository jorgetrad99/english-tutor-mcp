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
       service: http://127.0.0.1:8765
     - service: http_status:404
   ```
   In the Cloudflare dashboard for `<spike-host>`: proxied DNS record, a Cache Rule
   "bypass cache", no Cloudflare Access application, and a WAF custom rule *Skip* (all
   remaining custom rules, Bot Fight Mode / Super Bot Fight Mode) for
   `ip.src in {160.79.104.0/21}`. Add a rate-limiting rule for the hostname (e.g. 60
   requests per 10 seconds per IP, action Block) whose expression exempts
   `ip.src in {160.79.104.0/21}`, e.g.
   `(http.host eq "<spike-host>" and not ip.src in {160.79.104.0/21})`.
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
- [ ] A third Google account (not a test user) must get "Access blocked" at the Google
      sign-in.
- [ ] In a text chat on each account: ask Claude to call `get_profile`; check a `kind: tool`
      line with the right tester label in `data/raw/calls-*.jsonl`.
- [ ] Restart check, in the SAME chat: ask Claude to call `get_profile` → restart the
      server → ask again. If Claude cannot recover, record it in the Setup of
      `docs/spike/01` and switch the server to stateless HTTP before run 1.
- [ ] Never restart the server during a run.
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

- Before sending the first message, start the row in `data/raw/runs.csv` (header in
  `runs.template.csv`) and write `start_local` (local HH:MM).
- New chat. Say only "Let's practise English for 15 minutes." Never mention tools or saving.
- Use the next card from `deck.md`. Speak naturally. Phone timer: 15 minutes.
- Close like a person: "OK, I have to go, thanks." No nudge.
- Finish the row: `run_id` (letters, digits, `_` or `-`, at most 32, unique), `account`
  `free|pro`, `mode` `voice|text`, `status` `ok|aborted`, `voice_stayed_active` and
  `continued_with_result` `y|n|na` (`y` or `n` for an ok voice run); set the
  `transcript_file` column to `transcripts/<run_id>.md` (path relative to data/raw).
- Copy the conversation from claude.ai web into `data/raw/transcripts/<run_id>.md`, one turn
  per block, user lines starting `U: ` and Claude lines starting `A: `; leave tool-call
  blocks out.

## Annotation (Sat Oct 10, before looking at payloads)

For each of 10 text runs (5 Free, 5 Pro), print only the learner's turns, numbered, so
Claude's feedback cannot steer you:

```
uv run python -m tutor_spike.analysis.annotate <run_id>
```

From that output only, write `data/raw/annotations/<run_id>.json`:
`[{"said": "<exact words>", "correct": "<correct version>"}]`.

## Analysis and export

Put the names to redact in `.env` as `SPIKE_REDACT_NAMES` (comma-separated), not on the
command line, so they stay out of shell history. List every name that might appear:
nicknames, usernames/handles and joined forms (e.g. "Lucía Fernández,Lucía,luciaf"),
because a name glued to other letters is deliberately not matched. Accents are folded,
so "Lucía" also redacts "Lucia".

```
uv run python -m tutor_spike.analysis --data data/raw --out data/report.md
uv run --env-file .env python -m tutor_spike.redact
```

Paste the tables from `data/report.md` into `docs/spike/01–04`. Before committing the
exported evidence, stage it and manually review the staged `docs/spike/` and
`evals/fixtures/` diff (`git diff --staged -- docs/spike evals/fixtures`) for names,
places and employers; then commit.

If rule 9.2's extension triggers, run the extension series with its own data directory
(`SPIKE_DATA_DIR=data/raw-extension`) and use `--data data/raw-extension` for both analysis and redact commands, so the first 25 runs stay unchanged.
