# shellcheck shell=bash
# c-claude, c-codex and cc-copilot for bash and zsh on Linux, WSL and macOS.
# The COPILOT-GATEWAY block that setup.sh writes into ~/.bashrc or ~/.zshrc
# sources the 0600 secrets file (LITELLM_MASTER_KEY, SEARXNG_SECRET), exports
# COPILOT_GATEWAY_DIR, COPILOT_GATEWAY_PORT, COPILOT_GATEWAY_RC and
# COPILOT_TOKEN_DIR, then sources this file, so a git pull updates it.

__cc_copilot_url() {
  printf 'http://localhost:%s' "${COPILOT_GATEWAY_PORT:-4000}"
}

__cc_copilot_compose() {
  (cd "$COPILOT_GATEWAY_DIR" && COPILOT_UID="$(id -u)" COPILOT_GID="$(id -g)" docker compose "$@")
}

__cc_copilot_running() {
  [[ -n "$(__cc_copilot_compose ps --status running --quiet gateway 2> /dev/null)" ]]
}

__cc_copilot_ready() {
  local code
  code="$(printf 'Authorization: Bearer %s\n' "$LITELLM_MASTER_KEY" \
    | curl -s -o /dev/null -w '%{http_code}' -H @- "$(__cc_copilot_url)/v1/models" 2> /dev/null)"
  [[ "$code" == "200" ]]
}

__cc_copilot_defaults() {
  local line file="$COPILOT_GATEWAY_DIR/state/defaults.env"
  [[ -f "$file" ]] || return 0
  while IFS= read -r line; do
    [[ "$line" == ANTHROPIC_DEFAULT_*=* || "$line" == CLAUDE_CODE_AUTO_COMPACT_WINDOW=* || "$line" == COPILOT_CODEX_*=* ]] \
      && printf '%s\n' "$line"
  done < "$file"
}

__cc_copilot_env() {
  __cc_copilot_defaults | sed -n "s/^$1=//p"
}

__cc_copilot_open() {
  if command -v wslview > /dev/null 2>&1; then
    (wslview "$1" > /dev/null 2>&1 &)
  elif [[ "$(uname -s)" == Darwin ]]; then
    open "$1"
  elif command -v xdg-open > /dev/null 2>&1; then
    (xdg-open "$1" > /dev/null 2>&1 &)
  fi
}

__cc_copilot_banner() {
  local green=$'\033[1;32m' reset=$'\033[0m' rule="================================================================" line
  printf '\n%s%s\n' "$green" "$rule"
  printf '  COPILOT GATEWAY READY   %s\n\n' "$(__cc_copilot_url)"
  if [[ "$1" == attached ]]; then
    printf '  KEEP THIS TERMINAL OPEN. Ctrl-C or closing it stops the gateway.\n'
    printf '  Open a NEW terminal and run:   c-claude   or   c-codex\n'
  else
    printf '  Running in the background. Run:   c-claude   or   c-codex\n'
    printf '  Logs: cc-copilot-logs      Stop: cc-copilot --stop\n'
  fi
  printf '\n'
  while IFS= read -r line; do
    [[ -n "$line" ]] && printf '  %s\n' "$line"
  done <<< "$(__cc_copilot_defaults)"
  printf '%s%s\n\n' "$rule" "$reset"
}

__cc_copilot_started_at() {
  local id
  id="$(__cc_copilot_compose ps --quiet gateway 2> /dev/null)"
  [[ -n "$id" ]] && docker inspect --format '{{.State.StartedAt}}' "$id" 2> /dev/null
}

__cc_copilot_wait() {
  local mode="$1" since="${2:-}" shown=0 total=0 i=0 logs="" url="" opened=""
  while (( i < 1800 )); do
    if [[ -n "$since" ]]; then
      logs="$(__cc_copilot_compose logs --no-color --no-log-prefix --since "$since" gateway 2> /dev/null)"
      if [[ "$mode" != attached && -n "$logs" ]]; then
        total="$(printf '%s\n' "$logs" | wc -l | tr -d ' ')"
        if (( total > shown )); then
          printf '%s\n' "$logs" | sed -n "$((shown + 1)),${total}p"
          shown="$total"
        fi
      fi
      if [[ -z "$opened" ]]; then
        url="$(printf '%s\n' "$logs" | grep 'URL :' | grep -oE 'https://[A-Za-z0-9._~:/?#@%=&+-]+' | tail -1)"
        if [[ -n "$url" ]]; then
          __cc_copilot_open "$url"
          opened=1
        fi
      fi
    fi
    if __cc_copilot_ready; then
      [[ "$mode" == quiet ]] || __cc_copilot_banner "$mode"
      return 0
    fi
    if (( i > 30 )) && ! __cc_copilot_running; then
      printf 'Copilot gateway stopped before it was ready. See: cc-copilot-logs\n' >&2
      return 1
    fi
    sleep 1
    i=$((i + 1))
  done
  printf 'Copilot gateway not ready after 30 minutes. See: cc-copilot-logs\n' >&2
  return 1
}

__cc_copilot_prepare() {
  if ! command -v docker > /dev/null 2>&1; then
    printf 'Docker is required: install Docker Desktop (macOS, Windows) or Docker Engine (Linux).\n' >&2
    return 1
  fi
  mkdir -p "$COPILOT_TOKEN_DIR" "$COPILOT_GATEWAY_DIR/state"
}

__cc_copilot_start_detached() {
  local since
  __cc_copilot_prepare || return
  since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  __cc_copilot_compose up -d --build || return
  __cc_copilot_wait detached "$since"
}

__cc_copilot_stop() {
  if __cc_copilot_running; then
    __cc_copilot_compose down > /dev/null 2>&1 && printf 'Copilot gateway stopped.\n'
  else
    printf 'No Copilot gateway is running.\n'
  fi
}

__cc_copilot_ensure() {
  if __cc_copilot_running && __cc_copilot_ready; then
    return 0
  fi
  if __cc_copilot_running; then
    printf 'Waiting for the Copilot gateway at %s ...\n' "$(__cc_copilot_url)" >&2
    __cc_copilot_wait quiet "$(__cc_copilot_started_at)" >&2
    return
  fi
  printf 'Starting the Copilot gateway in the background ...\n' >&2
  __cc_copilot_start_detached >&2
}

__cc_copilot_help() {
  cat << 'HELP'
c-claude / c-codex - run Claude Code and Codex on your GitHub Copilot models.

  c-claude [args]         Claude Code on Copilot. Starts the gateway in the
                          background first when it is not running.
  c-codex [args]          Codex on Copilot, the same way.

  cc-copilot              Start (or restart) the gateway in this terminal and
                          show its log. Keep the terminal open; when the green
                          READY banner appears, use c-claude or c-codex in a
                          new terminal.
  cc-copilot -d           Same, detached (--detached): runs in the background,
                          shows the log until it is ready, then returns.
  cc-copilot --stop       Stop the gateway.
  cc-copilot --models     List the models the gateway serves right now.
  cc-copilot --update     Pull the latest gateway code, refresh the RC block
                          and rebuild the gateway image.
  cc-copilot --help       This help.
  cc-copilot-logs         Follow the gateway log (any terminal).

The gateway runs in Docker: LiteLLM (pinned, patched) and a private SearXNG
that answers Claude Code's web searches. c-claude and c-codex use the gateway
key and the models your Copilot plan offers: the newest Opus, Sonnet and Haiku
for Claude Code, the best Responses model for Codex. Plain 'claude' and
'codex' are never changed. The first start opens a browser once for GitHub's
device login; the code is shown while the gateway starts.
HELP
}

function cc-copilot() {
  case "${1:-}" in
    -h|--help) __cc_copilot_help; return 0 ;;
    --stop) __cc_copilot_stop; return ;;
    -d|--detached) __cc_copilot_start_detached; return ;;
    --models)
      printf 'Authorization: Bearer %s\n' "$LITELLM_MASTER_KEY" \
        | curl -s -H @- "$(__cc_copilot_url)/v1/models" \
        | grep -oE '"id": ?"[^"]+"' | sed -E 's/.*"([^"]+)"$/\1/'
      return
      ;;
    --update)
      git -C "$COPILOT_GATEWAY_DIR" pull --ff-only \
        && PORT="$COPILOT_GATEWAY_PORT" RC_FILE="$COPILOT_GATEWAY_RC" "$COPILOT_GATEWAY_DIR/setup.sh" \
        && . "$COPILOT_GATEWAY_DIR/shell/cc-copilot.sh" \
        && __cc_copilot_compose build \
        && printf 'Updated. Run cc-copilot -d (or c-claude / c-codex) to restart on the new version.\n'
      return
      ;;
    "") ;;
    *) printf 'cc-copilot: unknown option %s (see cc-copilot --help)\n' "$1" >&2; return 2 ;;
  esac
  __cc_copilot_prepare || return
  local since
  since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  (__cc_copilot_wait attached "$since" &)
  __cc_copilot_compose up --build
}

function cc-copilot-logs() {
  __cc_copilot_compose logs -f gateway
}

function c-claude() {
  __cc_copilot_ensure || return
  local line
  local -a vars=(
    CLAUDE_CODE_USE_BEDROCK=0
    CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
    CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
    "ANTHROPIC_BASE_URL=$(__cc_copilot_url)"
    "ANTHROPIC_AUTH_TOKEN=$LITELLM_MASTER_KEY"
  )
  while IFS= read -r line; do
    [[ "$line" == ANTHROPIC_DEFAULT_*=* || "$line" == CLAUDE_CODE_AUTO_COMPACT_WINDOW=* ]] && vars+=("$line")
  done <<< "$(__cc_copilot_defaults)"
  command env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u CLAUDE_CODE_EXTRA_BODY \
    -u ANTHROPIC_DEFAULT_OPUS_MODEL -u ANTHROPIC_DEFAULT_SONNET_MODEL -u ANTHROPIC_DEFAULT_HAIKU_MODEL \
    -u CLAUDE_CODE_AUTO_COMPACT_WINDOW "${vars[@]}" claude "$@"
}

function c-codex() {
  __cc_copilot_ensure || return
  local base model window efforts wanted
  base="$(__cc_copilot_url)/v1"
  model="$(__cc_copilot_env COPILOT_CODEX_MODEL)"
  window="$(__cc_copilot_env COPILOT_CODEX_CONTEXT_WINDOW)"
  local -a args=(
    -c model_provider=copilot-gateway
    -c "model_providers.copilot-gateway={name=\"Copilot gateway\",base_url=\"$base\",env_key=\"LITELLM_MASTER_KEY\",wire_api=\"responses\",model_catalog_url=\"$base/models\"}"
    -c features.api_key_model_discovery=true
  )
  [[ -n "$model" ]] && args+=(-m "$model")
  [[ -n "$window" ]] && args+=(-c "model_context_window=$window")
  efforts="$(__cc_copilot_env COPILOT_CODEX_REASONING_EFFORTS)"
  wanted="$(sed -n 's/^model_reasoning_effort *= *"\(.*\)".*/\1/p' "${CODEX_HOME:-$HOME/.codex}/config.toml" 2> /dev/null | head -1)"
  if [[ -n "$efforts" && -n "$wanted" && " $efforts " != *" $wanted "* ]]; then
    args+=(-c "model_reasoning_effort=${efforts##* }")
  fi
  command codex "${args[@]}" "$@"
}
