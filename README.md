# Copilot Gateway

Run Claude Code, or any OpenAI- or Anthropic-compatible client, on your GitHub
Copilot models. A local LiteLLM gateway listens on `http://localhost:4000` and
routes every request to your Copilot subscription.

## Supply-chain notice: LiteLLM is pinned to 1.106.2

LiteLLM was compromised on PyPI twice in 2026:

| Versions | Date | What shipped |
|---|---|---|
| `1.82.7`, `1.82.8` | 24 March 2026 | Credential stealer that runs on every Python start (`litellm_init.pth`), published with stolen CI credentials |
| `1.107.0` | 21 October 2026 | Credential stealer and backdoor, on PyPI and in the official Docker images for about two hours |

This repository pins `litellm[proxy]==1.106.2` in `requirements.txt`. It was
published before the October incident, LiteLLM named it as a safe pin after that
incident, and OSV lists no advisory for it (checked 17 November 2026).

- **Never loosen the pin to a range.** A range would have installed `1.107.0`
  for anyone who ran the installer during those two hours.
- **Change the pin only by pull request**, to an exact version that has been
  public for at least a week with no reports. Check it first:

  ```bash
  curl -s -X POST https://api.osv.dev/v1/query \
    -d '{"package":{"name":"litellm","ecosystem":"PyPI"},"version":"X.Y.Z"}'
  ```

  An empty `{}` means no known advisory.
- **`run.sh` rebuilds `.venv` from scratch whenever `requirements.txt`
  changes.** A pin change replaces the whole environment, including any startup
  file a bad version left behind, instead of upgrading in place. The startup
  line prints the LiteLLM version actually running.
- **If you ever installed `1.82.7`, `1.82.8` or `1.107.0`**, treat every
  credential on that machine as exposed. Rotate them and delete the old
  virtualenv. To check an existing install, run
  `.venv/bin/pip show litellm | grep Version` and
  `find .venv -name 'litellm_init.pth'`.
- The gateway runs with `LITELLM_MODE=PRODUCTION`. This stops LiteLLM from
  loading a stray `.env` from a parent directory, such as `~/.env`, into the
  gateway process.

Sources:
[Snyk](https://snyk.io/blog/poisoned-security-scanner-backdooring-litellm/),
[LiteLLM security update](https://docs.litellm.ai/blog/security-update-march-2026),
[Datadog Security Labs](https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-docker/),
[The Register](https://www.theregister.com/security/2026/10/26/litellm-supply-chain-attack-started-with-an-exposed-master-key/5305544).

## Install (Linux, WSL, macOS)

```bash
git clone https://github.com/the-cloud-clockwork/tcc-copilot-gateway.git ~/tcc-copilot-gateway
cd ~/tcc-copilot-gateway && ./setup.sh
source ~/.bashrc        # or: source ~/.zshrc   (macOS uses zsh by default)
```

Then, whenever you want Claude Code on Copilot:

```bash
cc-copilot      # starts the gateway and points Claude Code at it (this shell only)
claude          # now runs on your Copilot models
```

The first `cc-copilot` installs LiteLLM into a private virtualenv. It then opens
your browser once for GitHub's device login: enter the code shown in
`cc-copilot-logs` and approve. Later starts are silent.

Requirements: `python3` 3.10 to 3.14, `git`, `curl` and `openssl`.

### What the installer does

`setup.sh` adds one tagged block to your shell RC file: `~/.zshrc` when your
login shell is zsh (the macOS default), otherwise `~/.bashrc`. The block holds:

| Name | What it does |
|---|---|
| `LITELLM_MASTER_KEY` | The key the gateway accepts and Claude Code sends. Generated once and kept on every re-run. |
| `cc-copilot` | Restarts the gateway in the background, points Claude Code at it in the current shell, and exports the default models the gateway picked. |
| `cc-copilot --models` | Lists the models the gateway serves right now. |
| `cc-copilot --update` | Runs `git pull` on this repository and rewrites the block. The next start rebuilds the virtualenv if the pinned dependencies changed. |
| `cc-copilot --help` | Shows the commands. |
| `cc-copilot-logs` | Tails the gateway log (`~/.copilot-gateway.log`) from any terminal. |

Re-running `setup.sh` replaces the block in place. It never duplicates the
block, never touches the rest of the file, and writes through a symlinked RC
file managed by a dotfiles tool. Options: `RC_FILE=~/.bashrc ./setup.sh` picks
the file, and `PORT=4100 ./setup.sh` moves the gateway to another port.

The environment is per shell. Open a new terminal to use Claude Code normally
again. To stop the gateway, run `pkill -f "litellm --config"`.

## Automatic model onboarding

You never edit a model list. On every start, `copilot_auth.py` reads GitHub's
live `/models` metadata for your account and writes `config.yaml` and
`defaults.env` from it:

- **Which models:** current chat models your plan can call. GitHub marks these
  with a model picker category, and their policy is not `disabled`.
  Embeddings, legacy snapshots and internal models are left out. A model your
  plan disables appears once you enable it in your GitHub Copilot settings and
  restart.
- **Which API:** a model that serves `/responses` (GPT-5.x, GPT-6 and Codex
  models) routes in responses mode. Claude models use Copilot's native
  `/v1/messages` through LiteLLM.
- **1M context:** a `[1m]` variant appears only when Copilot's published prompt
  limit for that model is at least one million tokens. Copilot caps many models
  below their native window; `claude-sonnet-5`, for example, accepts 200k
  prompt tokens there. Picking a `[1m]` variant in `/model` raises Claude
  Code's context to 1M.
- **Defaults:** the newest Opus, Sonnet and Haiku become
  `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` and
  `ANTHROPIC_DEFAULT_HAIKU_MODEL`. A plan without one of those families falls
  back to another Claude family, and a plan without Claude falls back to its
  best other model.
- **Names:** Claude models keep their ids (`claude-haiku-4.5`). Every other
  model gets an `anthropic-` prefix (`anthropic-gpt-6-luna`), because Claude
  Code's `/model` picker only lists names that start with `claude` or `anth`.

When GitHub adds or retires a model, run `cc-copilot` again. When this
repository changes, run `cc-copilot --update`.

## Use it from other clients

The gateway speaks the OpenAI API (`/v1/chat/completions`, `/v1/responses`) and
the Anthropic API (`/v1/messages`). Authenticate with `$LITELLM_MASTER_KEY`.

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4.5","messages":[{"role":"user","content":"Hello"}]}'
```

```python
import os
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4000/v1", api_key=os.environ["LITELLM_MASTER_KEY"])
r = client.chat.completions.create(
    model="claude-haiku-4.5",
    messages=[{"role": "user", "content": "Hello"}],
)
print(r.choices[0].message.content)
```

## Run without the installer

`./run.sh` runs the same gateway in the foreground; Ctrl-C stops it. Without
`LITELLM_MASTER_KEY` set, it generates a throwaway key and prints it. For a
fixed key or another port, copy `.env.example` to `.env`.

## Troubleshooting

- **Browser did not open:** `cc-copilot-logs` shows the URL and the code.
- **"Authenticated but still can't fetch models":** your plan or organization
  policy does not grant Copilot API access, or a proxy blocks
  `api.githubcopilot.com`. Confirm Copilot works in your IDE first.
- **A model returns an error:** enable it in your GitHub Copilot settings, then
  run `cc-copilot` again.
- **Force a new GitHub login:** `rm -rf ~/.config/litellm/github_copilot`, then
  run `cc-copilot`.
- **GHE.com / data residency:** copy `.env.example` to `.env` and uncomment the
  enterprise endpoint block before the first start.
