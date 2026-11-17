# Copilot Gateway

Run Claude Code and Codex on your GitHub Copilot models. A local LiteLLM
gateway listens on `http://localhost:4000` and routes every request to your
Copilot subscription. A private SearXNG answers Claude Code's web searches.
Both run in Docker.

```bash
c-claude        # Claude Code on Copilot
c-codex         # Codex on Copilot
```

That is all you type day to day. Either command starts the gateway in the
background when it is not running, shows its log while it starts, then opens
the tool with the gateway's key and your plan's best models.

## Supply-chain notice: LiteLLM is pinned to 1.106.2

LiteLLM was compromised on PyPI twice in 2026:

| Versions | Date | What shipped |
|---|---|---|
| `1.82.7`, `1.82.8` | 24 March 2026 | Credential stealer that runs on every Python start (`litellm_init.pth`), published with stolen CI credentials |
| `1.107.0` | 21 October 2026 | Credential stealer and backdoor, on PyPI and in the official Docker images for about two hours |

This repository pins `litellm[proxy]==1.106.2` in `requirements.txt` and builds
its own image from PyPI; it never uses LiteLLM's Docker images. 1.106.2 was
published before the October incident. LiteLLM's post-incident guidance, as
reported by The Register (linked below), was to pin 1.106.2 or another explicit
version published before the incident. An OSV query for 1.106.2 on 17 November
2026 returned no advisories, while the same query flags 1.82.8 and 1.107.0 as
malicious (`MAL-2026-2144`).

- **LiteLLM runs in a container.** It sees only the Copilot token folder and
  this repository's `state/` folder, never your SSH keys, cloud credentials or
  home directory. A malicious LiteLLM release would have nothing else to steal.
- **Never loosen the pin to a range.** A range would have installed `1.107.0`
  for anyone who built during those two hours.
- **Change the pin only by pull request**, to an exact version that has been
  public for at least a week with no reports. Check it first:

  ```bash
  curl -s -X POST https://api.osv.dev/v1/query -H 'Content-Type: application/json' \
    -d '{"package":{"name":"litellm","ecosystem":"PyPI"},"version":"X.Y.Z"}'
  ```

  An empty `{}` means no known advisory. Moving the pin also means reviewing
  every patch in `litellm_patches.py`: the image build fails when a patch no
  longer matches the new version.
- The gateway runs with `LITELLM_MODE=PRODUCTION`. In its default `DEV` mode,
  LiteLLM calls `load_dotenv()` at import and loads any `.env` it finds.

Sources:
[Snyk](https://snyk.io/blog/poisoned-security-scanner-backdooring-litellm/),
[LiteLLM security update](https://docs.litellm.ai/blog/security-update-march-2026),
[Datadog Security Labs](https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-docker/),
[The Register](https://www.theregister.com/security/2026/10/26/litellm-supply-chain-attack-started-with-an-exposed-master-key/5305544).

## Install (Linux, WSL, macOS)

Requirements: Docker (Docker Desktop on macOS and Windows, Docker Engine on
Linux), `git`, `curl` and `openssl`.

```bash
git clone https://github.com/the-cloud-clockwork/tcc-copilot-gateway.git ~/tcc-copilot-gateway
cd ~/tcc-copilot-gateway && ./setup.sh
```

Open a new terminal, then run `c-claude` or `c-codex`. The first start builds
the gateway image and opens your browser once for GitHub's device login: enter
the code shown in the terminal and approve. Later starts take seconds.

### What the installer does

`setup.sh` adds one tagged block to your shell RC file: `~/.zshrc` when your
login shell is zsh (the macOS default), otherwise `~/.bashrc`. The gateway key
and a SearXNG secret live in `~/.config/tcc-copilot-gateway/secrets.env`
(mode 600), never in the RC file. The block loads that file, exports the
repository path and the port, then sources `shell/cc-copilot.sh`, which defines
these shell functions in every new terminal:

| Command | What it does |
|---|---|
| `c-claude [args]` | Claude Code on Copilot: the gateway URL, the key, gateway model discovery, the newest Opus, Sonnet and Haiku your plan offers, and the right auto-compact window. |
| `c-codex [args]` | Codex on Copilot: a `copilot-gateway` provider on the Responses API, the gateway's model catalog, and the best Responses model your plan offers. |
| `cc-copilot` | Starts the gateway in this terminal and shows its log. When it is ready a green banner says to keep the terminal open and use `c-claude` or `c-codex` in a new one. Ctrl-C stops it. |
| `cc-copilot -d` | The same in the background (`--detached`): shows the log until it is ready, then returns. |
| `cc-copilot --stop` | Stops the gateway. |
| `cc-copilot --models` | Lists the models the gateway serves right now. |
| `cc-copilot --update` | Pulls this repository, rewrites the RC block and rebuilds the image. |
| `cc-copilot-logs` | Follows the gateway log from any terminal. |

Plain `claude` and `codex` are never changed. Your own `~/.codex/config.toml`
stays untouched: `c-codex` passes its settings with `-c` flags.

Re-running `setup.sh` replaces the block in place. It never duplicates the
block, never touches the rest of the file, keeps the key and the SearXNG secret,
and writes through a symlinked RC file managed by a dotfiles tool. If it finds a
begin marker without its end marker, it stops without changing anything.
Options: `RC_FILE=~/.bashrc ./setup.sh` picks the file, and `PORT=4100
./setup.sh` moves the gateway to another port.

The containers restart with Docker, so after a reboot the gateway is usually
already running when you type `c-claude`.

## Automatic model onboarding

You never edit a model list. On every start, the gateway reads GitHub's live
`/models` metadata for your account and writes `state/config.yaml` and
`state/defaults.env` from it:

- **Which models:** current chat models your plan can call. GitHub marks these
  with a model picker category, and their policy is not `disabled`.
  Embeddings, legacy snapshots and internal models are left out. A model your
  plan disables appears once you enable it in your GitHub Copilot settings and
  restart (`cc-copilot -d`).
- **Which API:** a model that serves `/responses` (GPT-5.x, GPT-6 and Codex
  models) routes in responses mode. Claude models use Copilot's native
  `/v1/messages` through LiteLLM.
- **Names:** Claude models keep their ids (`claude-haiku-4.5`). Every other
  model is served twice: under an `anthropic-` prefix (`anthropic-gpt-6-luna`),
  because Claude Code's `/model` picker only lists names that start with
  `claude` or `anth`, and under its own id (`gpt-6-luna`) for Codex and OpenAI
  clients.
- **Context window:** Copilot enforces its own prompt limits, often far below a
  model's native window: `claude-sonnet-5` accepts 200k prompt tokens there.
  Claude Code cannot see those limits. It assumes 1M for Sonnet 5 and later,
  Opus 4.7 and later, and any model id it does not recognize
  ([Claude Code model configuration](https://code.claude.com/docs/en/model-config#correct-the-window-for-a-gateway-or-custom-model-id)).
  `c-claude` therefore sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to the smaller
  published prompt limit of the Opus and Sonnet defaults, kept within Claude
  Code's 100k to 1M range, so sessions compact before Copilot rejects them. A
  `[1m]` variant appears only for models whose Copilot prompt limit is at least
  one million tokens. When Copilot publishes no prompt limit for a model, its
  context window is used instead. `c-codex` passes the chosen model's prompt
  limit as Codex's `model_context_window`.
- **Defaults:** the newest Opus, Sonnet and Haiku become Claude Code's
  `ANTHROPIC_DEFAULT_*_MODEL`. A plan without one of those families falls back
  to another Claude family, and a plan without Claude falls back to its best
  other model. Codex gets the highest picker category among Responses models,
  preferring codex models, then the newest version.

## Web search

Claude Code's WebSearch is an Anthropic server-side tool that Copilot cannot
run, so through a plain gateway it returns nothing. This gateway turns on
LiteLLM's `websearch_interception` for Copilot models: the search runs on the
bundled SearXNG (`searxng/settings.yml`, no port published to the host) and the
results go back to Claude Code as native search results.

LiteLLM 1.106.2 sends the whole prompt, including Claude Code's
`Perform a web search for the query:` prefix, as the search query.
`litellm_patches.py` strips the prefix when the image is built; the same fix
is upstream in [BerriAI/litellm#43458](https://github.com/BerriAI/litellm/pull/43458),
after 1.106.2.

## Use it from other clients

The gateway speaks the OpenAI API (`/v1/chat/completions`, `/v1/responses`) and
the Anthropic API (`/v1/messages`). Authenticate with `$LITELLM_MASTER_KEY`.

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4.5","messages":[{"role":"user","content":"Hello"}]}'
```

## Troubleshooting

- **Browser did not open:** the log shows the URL and the code
  (`cc-copilot-logs`).
- **"Authenticated but still can't fetch models":** your plan or organization
  policy does not grant Copilot API access, or a proxy blocks
  `api.githubcopilot.com`. Confirm Copilot works in your IDE first.
- **A model returns `model_not_supported`:** your plan does not include it.
  Copilot Free lists more models than it serves; enable the model in your GitHub
  Copilot settings or use one your plan includes (`cc-copilot --models`).
- **Force a new GitHub login:** `cc-copilot --stop`, delete
  `~/.config/litellm/github_copilot`, then run `c-claude`.
- **GHE.com / data residency:** copy `.env.example` to `.env` and uncomment the
  enterprise endpoint block before the first start; the gateway container reads
  it.
