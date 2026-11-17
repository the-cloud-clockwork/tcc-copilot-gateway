# CLAUDE.md — copilot-gateway

Operator-facing notes for working on this repo. Read this first.

## What this is

A **local, stateless LiteLLM gateway** that exposes your GitHub Copilot models
over an OpenAI-compatible API at `http://localhost:4000/v1`. It's a **toolchain
utility, not a production service** — no database, no UI, no CI, no deploy. It
runs in a terminal on the operator's machine. Kill the terminal, it's gone.

Project posture: **not live, not prod.** `main` is the working branch. Push to
main is fine here. Standard prod controls (CI gates, release flow,
code-is-source deploy doctrine) **do not apply** — there's nothing deployed.

## How it works (one command)

`./run.sh` does, in order:

1. Loads `.env` if present (all optional).
2. Bootstraps `.venv` + installs `requirements.txt` on first run only.
3. Runs `copilot_auth.py`:
   - Reads the cached GitHub token (`~/.config/litellm/github_copilot/access-token`).
   - **Sanity check**: exchanges it for a Copilot API token and fetches the model
     list. If that works → no refresh, no login.
   - If it fails (no token / revoked / expired) → runs GitHub **device flow**,
     auto-opens the browser, polls, saves the token.
   - Regenerates `config.yaml` from the live model list (codex → responses mode).
4. `exec env -u DATABASE_URL … litellm --config config.yaml --port $PORT`.
   `exec` means the gateway takes over the script's PID → killing the script
   (Ctrl-C, closed terminal) kills the gateway. Nothing is left running.

## Files

| File | Role | Edit by hand? |
|---|---|---|
| `setup.sh` | Idempotent-by-replace RC configurator. Writes `LITELLM_MASTER_KEY` + `cc-copilot()`/`cc-copilot-logs()`. | Yes |
| `run.sh` | Single entrypoint. Bootstrap + auth + launch. | Yes |
| `copilot_auth.py` | Token sanity-check, device flow, browser open, config generation. | Yes |
| `config.yaml` | Model list + master-key env-ref. **Auto-generated every run.** | No — overwritten |
| `requirements.txt` | `litellm[proxy]` (excludes 1.82.7/1.82.8) + `openai`. | Yes |
| `.env.example` | Template. Copy to `.env` to override key/port/token-dir/GHE. | Copy it |
| `.env` | Real overrides. Gitignored. | Yes |
| `README.md` | Consumer/client-facing usage. | Yes |

Secrets never live in the repo: master key is env-only (`os.environ/LITELLM_MASTER_KEY`);
Copilot tokens live in `~/.config/litellm/github_copilot` (outside the repo).
`.env` and `.venv/` are gitignored.

## Setup / master key

`setup.sh` is an RC configurator, **idempotent by replace**. It rewrites a
tagged block between `# ===COPILOT-GATEWAY===` and
`# ===COPILOT-GATEWAY-END===`:

- Every run deletes the old block (via `sed` between the tags) and re-writes a
  fresh one, so function/code updates propagate. No "already configured" skip;
  no duplicate blocks; surrounding RC content is untouched.
- `LITELLM_MASTER_KEY` is **preserved** across re-runs (scraped from the old
  block) — regenerating would break live clients. Generated only if absent.
  The gateway reads it via `os.environ/LITELLM_MASTER_KEY`; clients auth with it.
- Writes one user-facing function `cc-copilot()`: sets proxy env
  (`ANTHROPIC_BASE_URL=http://localhost:4000`, `ANTHROPIC_AUTH_TOKEN=$LITELLM_MASTER_KEY`,
  model-overrides empty) and launches `run.sh` detached (log →
  `~/.copilot-gateway.log`). Supports `-h`/`--help`. Idempotent restart via
  `run.sh`'s own kill. Repo absolute path baked in at setup time.
- Plus `cc-copilot-logs()` (tail the log). The old standalone `claude-copilot()`
  is gone — its env exports are inlined into `cc-copilot`.
- Override RC target with `RC_FILE=...`, non-default port with `PORT=...`.

Without `setup.sh`, `run.sh` falls back to a throwaway key each start.

## Models

Exposed set = whatever your Copilot subscription grants, regenerated on every
`./run.sh`. **New models appear automatically on restart**; nothing is added by
hand. Live list: `curl localhost:4000/v1/models -H "Authorization: Bearer $KEY"`.
See README for the current catalog.

## Debug / troubleshoot playbook

**Auth artifacts:** `ls ~/.config/litellm/github_copilot` → expect `access-token`
(plain GitHub OAuth token) + `api-key.json` (derived Copilot token, has
`expires_at` + `endpoints`). LiteLLM refreshes `api-key.json` on its own when
expired; the `access-token` only changes on a fresh device flow.

**Force a re-auth:** `rm -rf ~/.config/litellm/github_copilot` then `./run.sh`.

**Test auth without launching the proxy:**
```bash
export GITHUB_COPILOT_TOKEN_DIR="$HOME/.config/litellm/github_copilot"
.venv/bin/python copilot_auth.py   # prints "Token valid. N models" and regenerates config.yaml
```

**Gateway won't start — `No module named 'prisma'` / "DATABASE_URL found":**
An inherited `DATABASE_URL` in the shell pushes LiteLLM into DB-backed mode.
`run.sh` already strips it via `env -u DATABASE_URL -u STORE_MODEL_IN_DB -u
LITELLM_DATABASE_URL` on the exec line. If you launch litellm directly, do the
same. This gateway is stateless — it must never touch a DB.

**Authenticated but inference fails (empty/error response):**
1. Confirm Copilot works in your IDE first (rules out entitlement/policy).
2. Corporate proxy may block `api.githubcopilot.com` even when VS Code works.
3. Old LiteLLM missing IDE headers → `copilot_auth.py` already sends
   `editor-version`, `Copilot-Integration-Id`, etc. on its own calls; for the
   proxy path, upgrade LiteLLM (`pip install -U 'litellm[proxy]!=1.82.7,!=1.82.8'`).

**Port already in use:** `pkill -f "litellm --config"` then re-run, or set
`PORT` in `.env`.

**Check what's listening:** `ss -ltn | grep 4000`.

**Logs:** the gateway logs to stdout in its terminal. For a backgrounded run,
redirect: `./run.sh > gw.log 2>&1 &` and `grep -iE "running on|error" gw.log`.

## GHE.com / data residency

Standard github.com works out of the box. For an enterprise host, copy
`.env.example` → `.env` and uncomment the `GITHUB_COPILOT_API_BASE` /
`DEVICE_CODE_URL` / `ACCESS_TOKEN_URL` / `API_KEY_URL` block before the first
`./run.sh`. `copilot_auth.py` reads these env vars directly.

## Gotchas

- `config.yaml` is disposable — don't hand-edit, it's rewritten each run.
- The gateway is tied to the terminal by design (`exec`). No daemon, no
  systemd, no nohup expected. Want it persistent? That's a deliberate change,
  not the default.
- Ephemeral master key: if `LITELLM_MASTER_KEY` is unset, each start prints a
  new throwaway key. Set one in `.env` for a stable key across restarts.
- `litellm[proxy]` pin excludes 1.82.7/1.82.8 (March 2026 supply-chain incident).
