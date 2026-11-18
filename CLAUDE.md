# CLAUDE.md — copilot-gateway

Notes for working on this repo. Read this first.

## What this is

A Docker Compose stack that serves GitHub Copilot models to Claude Code, Codex
and OpenAI/Anthropic clients at `http://localhost:4000`:

- `gateway`: LiteLLM `1.106.2` (exact PyPI pin, see the README supply-chain
  notice) built from `Dockerfile`, with `litellm_patches.py` applied at build
  time. It mounts only the Copilot token folder (`$COPILOT_TOKEN_DIR`, default
  `~/.config/litellm/github_copilot`) and `./state`, runs as the host user, and
  publishes port 4000 on 127.0.0.1 only.
- `searxng`: pinned SearXNG with `searxng/settings.yml` (JSON output on, no
  limiter, no published port). LiteLLM's `websearch_interception` sends Claude
  Code's native web search there.

Posture: a toolchain utility, nothing deployed. `dev` is the working branch;
every change lands by pull request into `dev` with CI green (shellcheck, ruff,
the installer test on Linux and macOS in bash and zsh, the Python tests, and the
compose job that builds the image and checks SearXNG answers JSON).

## How a start works

`cc-copilot` runs `docker compose up --build`, attached or with `-d` detached.
`c-claude` and `c-codex` run the detached start only when the gateway is not
running, and wait for it when it is starting. The container entrypoint
(`entrypoint.sh`):

1. Runs `ca_bundle.py`: certifi plus the host certificates the shell functions
   copied into `state/host-ca.pem` (system bundle, Windows roots on WSL, macOS
   keychains, `$COPILOT_CA_FILE`) become `state/ca-bundle.pem`, exported as
   `SSL_CERT_FILE` unless one is set. httpx and LiteLLM both read it.
2. Runs `copilot_auth.py`: reuses the cached GitHub token if it can list models,
   otherwise runs the GitHub device flow. The code appears in the log, and the
   host shell opens the verification URL it finds there (github.com or GHE).
   The token is saved as soon as the device flow returns it; a transport error
   exits 1 with a hint and never starts a new device flow.
3. Writes `state/config.yaml` and `state/defaults.env` from the live `/models`
   metadata.
4. Execs `litellm --config /state/config.yaml` with `LITELLM_MODE=PRODUCTION`.

The host functions read `state/defaults.env` for Claude Code's
`ANTHROPIC_DEFAULT_*_MODEL`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and Codex's
`COPILOT_CODEX_MODEL` / `COPILOT_CODEX_CONTEXT_WINDOW`.

## Files

| File | Role |
|---|---|
| `setup.sh` | Keeps the key and SearXNG secret in `~/.config/tcc-copilot-gateway/secrets.env` (0600) and writes the COPILOT-GATEWAY RC block that loads it, exports paths and port, and sources `shell/cc-copilot.sh`. Idempotent by replace; refuses unbalanced markers. |
| `shell/cc-copilot.sh` | `c-claude`, `c-codex`, `cc-copilot` (attached, `-d`, `--stop`, `--models`, `--update`, `--help`), `cc-copilot-logs`. bash and zsh. |
| `compose.yaml`, `Dockerfile`, `entrypoint.sh`, `.dockerignore` | The stack. |
| `ca_bundle.py` | certifi plus the host's trusted certificates, valid and deduplicated, for networks that inspect HTTPS. |
| `copilot_auth.py` | Token check, device flow, model onboarding, config and defaults rendering. |
| `litellm_patches.py` | Exact-snippet patches to the installed LiteLLM; the build fails when one no longer matches. |
| `searxng/settings.yml` | SearXNG settings. |
| `requirements.txt` | `litellm[proxy]==1.106.2` + `openai`. Change by PR only. |
| `tests/` | `installer_test.sh` (bash/zsh, fake `docker`, `claude`, `codex`), `test_model_onboarding.py`, `test_litellm_patches.py`, `test_ca_bundle.py`, `test_auth_network.py`. |
| `state/` | Generated `config.yaml`, `defaults.env`, `host-ca.pem` and `ca-bundle.pem`. Gitignored. |

## Models

Rules (`copilot_auth.py`): keep `capabilities.type == "chat"` entries that carry
a `model_picker_category` and whose `policy.state` is not `disabled`; entries
without metadata fall back to name rules. Responses mode when
`supported_endpoints` lists `/responses`. A `[1m]` entry only when
`max_prompt_tokens` is at least 1,000,000. Non-Claude models are served as
`anthropic-<id>` and as `<id>`. Claude defaults: newest Opus/Sonnet/Haiku,
falling back to another Claude family, then the best non-Claude model.
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` = the smaller `max_prompt_tokens` of the Opus
and Sonnet defaults (clamped to 100k..1M). Codex default: highest picker
category among `/responses` models, codex models first, then newest version.
Observed on 2026-11-17 with a Copilot Free account (`sku: free_limited_copilot`):
Copilot lists models it then refuses with `model_not_supported`; only the base
models answered. The code has no plan-specific handling.

## Patches

`litellm_patches.py` holds one patch: the websearch interception query drops
Claude Code's `Perform a web search for the query:` prefix (upstream
BerriAI/litellm#43458, after 1.106.2). Moving the LiteLLM pin means re-checking
every patch; the image build fails loudly when a snippet moved.

## Debug

- Logs: `cc-copilot-logs`, or `docker compose logs -f gateway searxng`.
- Force a re-auth: `cc-copilot --stop`, delete the token folder, start again.
- Live model list: `cc-copilot --models`.
- Port already in use: set `PORT=... ./setup.sh` and open a new terminal.
