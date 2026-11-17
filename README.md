# Copilot Gateway

Expose your GitHub Copilot models as a local OpenAI-compatible API at
`http://localhost:4000/v1`. Point any OpenAI/Anthropic client (SDK, `curl`,
agents, Claude Code) at it.

## ⚡ TL;DR — RUN ME

```bash
unzip copilot-gateway.zip -d copilot-gateway && \
  cd copilot-gateway && ./setup.sh && source ~/.bashrc
```

Then, anytime you want Copilot-powered Claude Code:

```bash
cc-copilot      # points Claude Code at the proxy + (re)starts the gateway
claude          # now runs on your Copilot models
```

That's it. `cc-copilot` is idempotent — run it whenever; it kills any old
gateway and starts a fresh one. Everything below is for the curious.

---

## Setup (one-time)

```bash
./setup.sh
source ~/.bashrc     # or ~/.zshrc — whatever setup.sh reports
```

`setup.sh` writes a tagged block to your shell RC. It's **idempotent by
replace** — every run rewrites the block (so updates land) while **preserving
your existing `LITELLM_MASTER_KEY`** (regenerating it would break live clients).
It installs:

- **`LITELLM_MASTER_KEY`** — the key. The gateway reads it via
  `os.environ/LITELLM_MASTER_KEY`, clients authenticate with it. Change it to
  anything; the gateway uses whatever the variable holds.
- **`cc-copilot()`** / **`cc-copilot-logs()`** — see below.

Skip this if you'd rather manage the key yourself — `run.sh` generates a
throwaway key when `LITELLM_MASTER_KEY` is unset.

## Start it

```bash
./run.sh
```

That's the whole thing. On first run it will:

1. Set up its own virtualenv and install LiteLLM (once).
2. Check whether it can already list your Copilot models.
   - **If yes** → uses the cached token, no login.
   - **If no** → opens your browser to authorize GitHub Copilot. Enter the code
     shown in the terminal, approve, done.
3. Build the model list from *your* subscription and start the gateway.

Leave the terminal open — the gateway runs in the foreground. **Ctrl-C stops
it** (and everything it started shuts down with it).

## Use it

The terminal prints your key on startup (`Master key: ...`). Then:

**curl**
```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4",
       "messages":[{"role":"user","content":"Hello"}]}'
```

**Python (OpenAI SDK)**
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4000/v1", api_key="YOUR_KEY")
r = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}],
)
print(r.choices[0].message.content)
```

**List available models** (exactly what your subscription allows):
```bash
curl http://localhost:4000/v1/models -H "Authorization: Bearer YOUR_KEY"
```

Model names: Claude models keep bare names (`claude-opus-4.8`, `claude-sonnet-5`);
every non-Claude model is prefixed **`anthropic-`** (`anthropic-gpt-5.5`,
`anthropic-gemini-3.1-pro-preview`). Reason: Claude Code's `/model` gateway
discovery only lists ids starting with `claude`/`anthropic`, so the prefix makes
the *whole* subscription show up in the picker. Underneath, each routes to the
real Copilot model. `gpt-5*` and codex are exposed in responses mode
automatically; older Opus/Sonnet (<4.6) are excluded.

## Available models

The generator curates your subscription down to current, chat-capable models
(drops embeddings, non-chat utilities, dated snapshots, and legacy tiers). A
recent run kept 13:

| Family | Models (picker names) |
|---|---|
| **Claude** | `claude-haiku-4.5`, `claude-sonnet-4.6`, `claude-sonnet-5`, `claude-opus-4.6`, `claude-opus-4.7`, `claude-opus-4.8` |
| **GPT** | `anthropic-gpt-5-mini`, `anthropic-gpt-5.3-codex`, `anthropic-gpt-5.4`, `anthropic-gpt-5.4-mini`, `anthropic-gpt-5.5` |
| **Gemini** | `anthropic-gemini-3-flash-preview`, `anthropic-gemini-3.1-pro-preview` |

Pruning rules (in `copilot_auth.py`): exclude embeddings, `trajectory-*`,
`*-picker`, dated snapshots (`-2024-05-13`, `-0613`, …), all pre-5 GPT
(`gpt-3.5*`, `gpt-4*`), `gemini-2*`, and Opus/Sonnet below 4.6.

### 1M context (`[1m]`)

Models with a 1M-token window are **also** exposed with a `[1m]` suffix
(`claude-sonnet-5[1m]`, `anthropic-gpt-5.5[1m]`, …). Pick the `[1m]` variant in
`/model` to lift Claude Code's default 200k context clamp to 1M. Claude Code
strips `[1m]` before sending, so the bare model still routes normally — that's
why each 1M model has both a bare and a `[1m]` entry. 1M models: Opus 4.6/4.7/4.8,
Sonnet 4.6/5, gpt-5.4, gpt-5.5, Gemini 3 Flash, Gemini 3.1 Pro (Haiku, minis,
and codex stay 200k).

This table is a snapshot — your tenant's real, current list is always:

```bash
curl http://localhost:4000/v1/models -H "Authorization: Bearer YOUR_KEY"
```

## How models are added (automatic — not manual)

You never edit the model list by hand. On **every** `./run.sh`, `copilot_auth.py`
queries GitHub for the models your subscription currently grants and rewrites
`config.yaml` from that live list before the gateway starts. So:

- **New model enabled for your tenant** → restart `./run.sh`, it appears.
- **Model removed by policy** → restart, it's gone.
- **Codex-style models** (name contains `codex`) are flagged `mode: responses`
  automatically.

`config.yaml` is a generated artifact — any manual edits are overwritten on the
next run. To change what's exposed, change your Copilot subscription/policy, not
the file.

## Use with Claude Code

`setup.sh` installs one command:

```bash
cc-copilot        # sets proxy env + (re)starts the gateway (idempotent)
claude            # runs on your Copilot models
cc-copilot --help # flags + details
cc-copilot-logs   # tail the gateway log (any terminal)
```

`cc-copilot` sets `ANTHROPIC_BASE_URL=http://localhost:4000` and
`ANTHROPIC_AUTH_TOKEN=$LITELLM_MASTER_KEY` for the current shell only, then
launches the gateway detached (log → `~/.copilot-gateway.log`). Model-override
vars are left empty for now — tune once you've seen how Claude Code behaves
against the proxy.

## A stable key (optional)

By default a throwaway key is generated each start. For a fixed key:

```bash
cp .env.example .env
# set LITELLM_MASTER_KEY=sk-your-own-key in .env
```

You can also change `PORT` there.

## When login is triggered

Only when the gateway **can't fetch your models** with the cached token
(first run, or the token was revoked/expired). A valid token is never
refreshed for no reason. Tokens are cached in
`~/.config/litellm/github_copilot` — outside this repo, nothing secret is
committed.

## Not working?

- **Browser didn't open** — the terminal prints the URL and code; open it
  manually.
- **"Authenticated but still can't fetch models"** — your org/enterprise policy
  may not grant Copilot API access, or a corporate proxy is blocking
  `api.githubcopilot.com`. Confirm Copilot works in your IDE first.
- **GHE.com / data residency** — copy `.env.example` to `.env` and uncomment the
  enterprise endpoint block before running.
