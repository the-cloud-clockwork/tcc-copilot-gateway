# AGENTS.md: tcc-copilot-gateway

Read this before changing, testing or troubleshooting the repository. It is the
single source for agents; `CLAUDE.md` imports it.

## What this is

A local gateway that runs Claude Code and Codex on a GitHub Copilot
subscription. A Docker Compose stack serves every model the Copilot plan offers
at `http://localhost:4000`, in both API shapes:

- OpenAI: `/v1/chat/completions`, `/v1/responses`, `/v1/models`
- Anthropic: `/v1/messages`

The user installs it once with `setup.sh`. After that, `c-claude` and `c-codex`
in any new terminal start the stack when it is down and launch the tool against
it. Plain `claude` and `codex` are never changed. Nothing is deployed; this is a
local developer tool.

## Hard rules

- **No personal details** in files or in anything published around them. See
  "Issues, pull requests and commits".
- **LiteLLM is pinned.** The exact pin is `litellm[proxy]==1.106.2`, because
  LiteLLM was compromised on PyPI twice in 2026 (the README supply-chain notice
  has the details). Never loosen it to a range. Change it only by pull request,
  after an OSV check, and re-check every patch in `litellm_patches.py`.
- **Secrets stay out of output.** Never print or read `LITELLM_MASTER_KEY`,
  `SEARXNG_SECRET`, `~/.config/tcc-copilot-gateway/secrets.env`, `.env`, the
  RC files or the Copilot token folder. Pass the key by reference:
  `printf 'Authorization: Bearer %s\n' "$LITELLM_MASTER_KEY" | curl -H @- ...`.
- **Branches.** `dev` is the working branch. Changes land by pull request into
  `dev` with CI green and review closed. `main` is moved only by a maintainer.
- **Code style.** No comments in code (docstrings and one file header only), full
  type annotations on Python functions, `ruff` clean, shellcheck clean. Shell
  sourced by users must work in bash 3.2, bash 5 and zsh.

## Architecture

```
terminal                          docker compose project "tcc-copilot-gateway"
--------                          --------------------------------------------
c-claude / c-codex / cc-copilot   gateway  (image tcc-copilot-gateway:local)
  shell/cc-copilot.sh               entrypoint.sh
  |  starts the stack if down         1. ca_bundle.py    -> state/ca-bundle.pem
  |  copies host CA certificates      2. copilot_auth.py -> token, state/config.yaml,
  |    -> state/host-ca.pem                                  state/defaults.env
  |  reads state/defaults.env         3. litellm --config /state/config.yaml
  v                                   127.0.0.1:4000 only, runs as the host user
claude / codex  --HTTP-->  gateway --> api.githubcopilot.com (or the plan's endpoint)
                              |
                              +-- web search --> searxng (no published port)
```

- **gateway:** built from `Dockerfile`, a `python:3.12.13-slim-bookworm` base
  with `requirements.txt` installed and `litellm_patches.py` applied at build
  time. `LITELLM_MODE=PRODUCTION`, so LiteLLM never loads a `.env` itself. It
  mounts only the Copilot token folder (`$COPILOT_TOKEN_DIR` at
  `/copilot-token`) and `./state` (at `/state`).
- **searxng:** a pinned image with `searxng/settings.yml` (JSON output, no
  limiter). LiteLLM's `websearch_interception` callback answers Claude Code's
  and Codex's web-search tool from it.
- **Startup**, in `entrypoint.sh`:
  1. `ca_bundle.py` merges certifi with the host certificates in
     `state/host-ca.pem` into `state/ca-bundle.pem`, and the entrypoint exports
     it as `SSL_CERT_FILE` unless one is set. This makes networks that inspect
     HTTPS work.
  2. `copilot_auth.py` reuses the saved GitHub token if it can list models.
     Otherwise it runs the device flow: the code goes to the log and the host
     shell opens the URL. A new token is saved as soon as GitHub returns it. A
     network error (TLS, connect, timeout) exits 1 with a hint and never starts
     a new device flow. Any other failure of the saved token, such as an HTTP
     error, an unreadable answer or an empty model list, removes the token and
     runs the device flow.
  3. `copilot_auth.py` writes `state/config.yaml`, the LiteLLM model list built
     from Copilot's live `/models` metadata, and `state/defaults.env`, the
     Claude Code and Codex defaults.
  4. `exec litellm`.
- **Model routing** (`copilot_auth.py`):
  - Exposed: chat models with a `model_picker_category` whose `policy.state` is
    not `disabled`. An entry without `capabilities` metadata falls back to
    name rules: ids containing `embedding`, `trajectory`, `picker` or
    `mai-code`, dated snapshots, Claude below 4.6, GPT-3.5/4 and Gemini 2 are
    excluded.
  - Responses mode when `supported_endpoints` lists `/responses`, chat mode
    when it lists other endpoints. Without `supported_endpoints`, ids that
    contain `codex` or start with `gpt-5` use responses mode.
  - Names: non-Claude models are served as `anthropic-<id>` (for Claude Code's
    `/model` picker) and as `<id>`. A `[1m]` variant exists only when the
    prompt limit (`max_prompt_tokens`, else `max_context_window_tokens`) is at
    least 1,000,000.
  - `drop_params: true`: unsupported parameters are dropped instead of failing.
    Codex sends a reasoning effort, which LiteLLM rejected with
    `UnsupportedParamsError` for chat models without reasoning support
    (issue #10 has the probe).
- **Defaults** (`state/defaults.env`):
  - `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL`: the newest model per family,
    with fallbacks.
  - `CLAUDE_CODE_AUTO_COMPACT_WINDOW`: the smaller prompt limit of the Opus and
    Sonnet defaults, clamped to 100k..1M.
  - `COPILOT_CODEX_MODEL`, `COPILOT_CODEX_CONTEXT_WINDOW` and
    `COPILOT_CODEX_REASONING_EFFORTS`.

## Commands (shell functions)

`setup.sh` writes one marked block (`# ===COPILOT-GATEWAY===` to
`# ===COPILOT-GATEWAY-END===`) into `~/.zshrc` or `~/.bashrc`. The block
sources the 0600 secrets file, exports the paths and port, and sources
`shell/cc-copilot.sh`.

| Command | What it does |
|---|---|
| `c-claude [args]` | Starts the gateway if needed, then runs `claude` with `ANTHROPIC_BASE_URL`, the gateway key, model discovery, the family defaults and the auto-compact window. |
| `c-codex [args]` | Starts the gateway if needed, then runs `codex` with a `copilot-gateway` provider (Responses API, the gateway's `/models` catalog), the default model and context window. When the reasoning effort in `~/.codex/config.toml` is not one the default model supports, it passes the highest effort that model supports instead. |
| `cc-copilot` | Runs the stack attached with live logs and a green READY banner. Ctrl-C stops it for every terminal. |
| `cc-copilot -d` | Runs it detached, showing the log until ready. |
| `cc-copilot --stop` | `docker compose down`. |
| `cc-copilot --models` | Lists the model names the gateway serves. |
| `cc-copilot --update` | `git pull`, re-runs `setup.sh`, re-sources the functions in this shell, rebuilds the image. Then run `cc-copilot -d` to restart on it. |
| `cc-copilot-logs` | Follows the gateway log. |

A terminal opened before an update keeps the old functions. Open a new terminal
after `git pull` or `--update`.

## Environment

| Variable | Set by | Meaning |
|---|---|---|
| `LITELLM_MASTER_KEY`, `SEARXNG_SECRET` | `setup.sh` (secrets file) | Gateway key and SearXNG secret. Never print. |
| `COPILOT_GATEWAY_DIR`, `COPILOT_GATEWAY_PORT`, `COPILOT_GATEWAY_RC` | RC block | Clone path, port (default 4000), RC file. |
| `COPILOT_TOKEN_DIR` | RC block | Copilot token folder, default `~/.config/litellm/github_copilot`. `setup.sh` takes it from `GITHUB_COPILOT_TOKEN_DIR` when that is set. |
| `COPILOT_CA_FILE` | user | Extra root CA (PEM) for networks that inspect HTTPS. |
| `SMOKE_TIMEOUT`, `SMOKE_CODEX_EFFORT` | user | Per-call timeout (default 300 s) and Codex effort for `scripts/smoke-models.sh`. |

`.env` (copied from `.env.example`, gitignored) is passed to the gateway
container. It is only needed for GHE.com data-residency endpoints.

## Files

| Path | Role |
|---|---|
| `setup.sh` | Installer: secrets file, RC block (idempotent, refuses unbalanced markers). |
| `shell/cc-copilot.sh` | All user-facing functions. |
| `compose.yaml`, `Dockerfile`, `entrypoint.sh`, `.dockerignore` | The stack. `.dockerignore` allow-lists the files the image needs. |
| `copilot_auth.py` | Token check, device flow, model onboarding, config and defaults. |
| `ca_bundle.py` | certifi plus host certificates, valid and deduplicated. |
| `litellm_patches.py` | Exact-snippet patches to the installed LiteLLM. |
| `searxng/settings.yml` | SearXNG settings. |
| `requirements.txt` | `litellm[proxy]==1.106.2` and `openai`, exact pins. Change by PR only. |
| `.env.example` | GHE.com endpoint overrides, copied to `.env` when needed. |
| `ruff.toml`, `pytest.ini` | Lint settings (line length 120) and the pytest path. |
| `scripts/smoke-models.sh` | Headless call to every model through `c-claude` and `c-codex`. |
| `scripts/diagnose-tls.sh`, `scripts/export-company-ca.sh` | TLS diagnosis and Windows CA export for networks that inspect HTTPS. |
| `tests/` | `installer_test.sh` (fake docker, claude, codex, powershell.exe) and the pytest suites. |
| `state/` | Generated at runtime and gitignored: `config.yaml`, `defaults.env`, `host-ca.pem`, `ca-bundle.pem`, `smoke/`. |

## LiteLLM patches

`litellm_patches.py` replaces exact snippets in the installed LiteLLM during the
image build. A snippet not found exactly once fails the build, and applying a
patch twice is a no-op.

1. `websearch_interception/handler.py`: strips Claude Code's `Perform a web
   search for the query:` prefix from the search query (BerriAI/litellm#43458).
2. `proxy/common_request_processing.py`: on `/v1/responses`, a finished
   `ResponsesAPIResponse` returned to a streaming request (web-search
   interception runs without a stream) is replayed as an event stream through
   `CachedResponsesAPIStreamingIterator`. Background requests are skipped
   (issue #10). Without it, Codex on chat-mode models fails with
   `'ResponsesAPIResponse' object has no attribute '__aiter__'`.
3. `proxy/common_utils/codex_model_catalog.py`, the Codex catalog LiteLLM
   serves on `/v1/models?client_version=` (issue #13):
   - Stock entries get `use_responses_lite: false`, so Codex sends their tools,
     `web_search` included, in the request's tool list.
   - Fallback entries get `multi_agent_version`: `disabled` when `grok` is in
     the upstream model or id, so grok gets no `namespace` tool and no
     sub-agents. Every other fallback entry gets `null`.

   CI runs `tests/codex_catalog_check.py` in the built image against this
   catalog.

## Testing

Tests run in CI, not locally. Lint and shellcheck may run locally:

```bash
shellcheck setup.sh entrypoint.sh shell/cc-copilot.sh tests/*.sh scripts/*.sh
ruff check .
```

The CI jobs, all required through `Gate — Required`:

- **lint:** shellcheck and ruff.
- **installer:** `tests/installer_test.sh` on ubuntu and macos, in bash and zsh.
- **onboarding:** `pytest -q tests`.
- **compose:**
  - builds the image, which applies the patches;
  - imports the patched modules;
  - builds a CA bundle from a planted certificate;
  - checks that SearXNG answers JSON.

A behaviour change starts with a failing test pushed to a draft PR into `dev`
and seen red in CI. The fix then turns it green.

### Live check: every model, both tools

```bash
scripts/smoke-models.sh [claude|codex|all] [model regex]
```

The script sources the real functions and starts the gateway if needed. It
uses throwaway `CLAUDE_CONFIG_DIR` and `CODEX_HOME` folders and makes one
headless call per model name:

- `anthropic-*` names, including `[1m]`: `c-claude -p ... --model M
  --allowedTools Read,WebSearch` only.
- `claude*` names: both `c-claude` and `c-codex`, except `[1m]` names, which
  go to `c-claude` only.
- Every other bare id: `c-codex --search exec -m M` only.

Each call is asked to read a random value from `smoke-check.txt` with a tool
and to run a web search. The script counts the bytes the SearXNG container
receives during the call. Each result is one of:

- `PASS`: the call succeeded, the output holds the value and SearXNG received
  traffic;
- `PLAN`: the output contains `model_not_supported` or `requested model is not
  supported`, so the model is not in the plan;
- `NOSEARCH`: the value was read, but no search ran;
- `NOTOOL`: the call succeeded without the value, so the model had no working
  tools;
- `FAIL`: anything else.

The script exits 1 on any `FAIL`, `NOTOOL` or `NOSEARCH`. A model can skip a
search it was asked for, so rerun one `NOSEARCH` before treating it as a defect.
SearXNG traffic from another session during a call counts as that call's
search, so run the script while no other session uses the gateway.
Output goes to
`state/smoke/<UTC time>/`:

- `results.tsv`: tool, model, result, seconds, first error line;
- `calls/<tool>-<model>.log`: the full output of each call;
- `gateway.log`: the LiteLLM log for the run.

To run it as an agent without the user's key, export a throwaway key and
secret (`LITELLM_MASTER_KEY=sk-<random>`, `SEARXNG_SECRET=<random>`) together
with `COPILOT_TOKEN_DIR`. Stop any gateway started with another key first
(`cc-copilot --stop`). Otherwise the script finds the gateway running but never
answering its key, waits up to 30 minutes, then exits 1 before any call.

### Direct probes

```bash
printf 'Authorization: Bearer %s\n' "$LITELLM_MASTER_KEY" \
  | curl -sS -N -H @- -H 'Content-Type: application/json' http://localhost:4000/v1/responses \
    -d '{"model":"gpt-4.1","input":"Reply PONG","stream":true,"tools":[{"type":"web_search"}]}'
```

Vary `stream`, `tools` and `reasoning` one at a time to isolate a failure.
Claude Code uses `/v1/messages` and Codex uses `/v1/responses`.

## Issues, pull requests and commits

Everything published to the repository host is public: issue and pull request
titles and bodies, comments, reviews, commit messages, branch names, files and
CI logs.

- **Neutral content only.** Describe the software, the change and the evidence,
  never the people or their setup:
  - no names, signatures, employers or teams;
  - nothing about which computer, network or subscription plan something ran
    on;
  - no local paths, hostnames, usernames or certificate names.

  Write "verified against a gateway built from `<sha>`", not where or by whom.
  Commands use repository-relative paths.
- **Check before publishing.** Read every title, body, comment and commit
  message for the items above before creating or editing it. For files, run
  `git grep -niE '<terms>'` after `git add` and before each commit.
- **Issues.** One problem per issue, with:
  - what happens and what should happen;
  - the evidence: command and output, run id, SHA;
  - acceptance criteria that a test or command can check.

  Label anything not yet proven as a hypothesis.
- **Branches and pull requests.**
  - Branch off `dev` and open a draft pull request into `dev` at the first
    push.
  - Follow "Testing" for red, then green.
  - The body states the change, the evidence (run ids, SHAs, command output)
    and what was not verified. Use `Refs #n` while work on the issue remains
    and `Closes #n` when the pull request completes it.
  - Squash-merge into `dev` once every check is green and review is closed,
    then delete the branch. Its commits stay visible on the pull request.
- **Commits.** An imperative subject and one logical change; the body says why.
  Stage files by name.
- **If something private was published:**
  - Deleting a comment removes it completely.
  - An edited comment or issue or pull request body keeps its old text in the
    edit history. A maintainer removes each old revision with "Delete revision
    from history" in the web page.
  - A title change stays visible in the timeline.
  - Commits pushed to `dev` or `main` are never rewritten (no force-push) and
    stay reachable by SHA, so check commit messages and staged files before
    pushing.

## Reading logs

```bash
cc-copilot-logs                                     # follow the gateway
docker compose logs --since 10m gateway searxng     # from the clone, recent window
docker logs tcc-copilot-gateway-gateway-1 2>&1 | sed -E 's/\x1b\[[0-9;]*m//g'
```

| Log line | Meaning |
|---|---|
| `Authorize GitHub Copilot` box with `Code:` | The device flow is waiting. The URL opens on the host. |
| `✓ Token valid. N models available.` | The start is healthy. |
| `the HTTPS certificate is not trusted ... CERTIFICATE_VERIFY_FAILED` | The network inspects HTTPS. See Troubleshooting. |
| `model_not_supported` | The model is not in the Copilot plan. This is not a bug. |
| `UnsupportedParamsError` | A parameter is not supported for that model. `drop_params` should prevent it. |
| `POST /v1/responses ... 500` with `__aiter__` | The stream replay patch is missing or no longer matches. |
| `exited with code 1 (restarting)` | `copilot_auth.py` failed. The lines just above it give the reason. |

To summarize errors in a saved log:
`grep -iE 'error|exception' gateway.log | sed -E 's/\x1b\[[0-9;]*m//g' | sort | uniq -c | sort -rn | head`

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A new device code on every restart | An older version discarded the token on network errors. | Update: `cc-copilot --update`, then `cc-copilot -d`. |
| `CERTIFICATE_VERIFY_FAILED` in the gateway log | The network re-signs HTTPS with a company root CA. | `bash scripts/diagnose-tls.sh` prints a VERDICT. Usually: open a new terminal, then `cc-copilot --stop; cc-copilot -d`. Otherwise run `scripts/export-company-ca.sh` (WSL only) and set `COPILOT_CA_FILE`; on macOS or Linux, set `COPILOT_CA_FILE` to the company root CA from the network administrator. |
| `state/host-ca.pem` is empty | The shell that started the gateway had functions from before the update. | Open a new terminal, then `cc-copilot --stop; cc-copilot -d`. |
| `Authenticated but still can't fetch models` | The plan or organization policy blocks API access, or a proxy blocks it. | Confirm Copilot works in the IDE. |
| `PLAN` results in the smoke test | The model is listed but not in the plan. | Enable it in GitHub Copilot settings or use another model. |
| Codex `Reconnecting... 1/5` | The gateway returned 5xx. | Read `gateway.log` for the traceback. Probe `/v1/responses` directly. |
| `curl` gets 401, or `c-claude` waits and prints `not ready after 30 minutes` while the container runs | The shell's key differs from the one the running gateway started with. | `cc-copilot --stop`, then start it again from the same shell. |
| Port 4000 already in use | Another service holds the port. | `PORT=4100 ./setup.sh`, then open a new terminal. |
| A new login is needed | The token is gone or revoked. | `cc-copilot --stop`, delete the token folder, run `c-claude`. |

## Gotchas for scripts and agents

- The functions in `shell/cc-copilot.sh` are written for interactive shells
  and are not `set -e` safe. Scripts that source them use `set -uo pipefail`
  and check return codes explicitly.
- `timeout` cannot run a shell function. Wrap it:
  `timeout 120 bash -c '. "$COPILOT_GATEWAY_DIR/shell/cc-copilot.sh" && "$0" "$@"' c-claude -p ...`.
- `powershell.exe` reads stdin. Inside a `while read` loop, give it
  `< /dev/null`.
- Copilot Free lists models that it then refuses. Plan-specific behaviour is
  classified as `PLAN`, not handled in code.
- The compose project name is fixed, so a second clone or a worktree controls
  the same containers. Stop the gateway before switching clones.
