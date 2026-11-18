#!/usr/bin/env bash
# Headless smoke test of every model the gateway serves, through the real
# c-claude and c-codex functions, plus the LiteLLM log of the run.
# Usage: scripts/smoke-models.sh [claude|codex|all] [model regex]
# Needs the gateway environment of a terminal opened after setup.sh
# (LITELLM_MASTER_KEY, SEARXNG_SECRET); starts the gateway when it is down.
# Each call must read a random token from a file with a tool and run a web
# search: the token in the output proves tool use, SearXNG traffic during the
# call shows a search ran. Claude Code and Codex run with throwaway config
# folders, so your hooks, MCP servers and history are untouched; Codex keeps
# your model_reasoning_effort (override with SMOKE_CODEX_EFFORT). Writes
# state/smoke/<UTC time>/ with results.tsv, one log per call and gateway.log.
# Exits 1 when a call fails for any reason other than the model missing from
# your Copilot plan.
set -uo pipefail

TOOLS="${1:-all}"
FILTER="${2:-.}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN_FILE=smoke-token.txt
PROMPT="Read the file $TOKEN_FILE in the current directory with your tools, then use your web search tool to find the latest LiteLLM release. Reply with the file's exact content on the first line and the release on the second line."
CALL_TIMEOUT="${SMOKE_TIMEOUT:-180}"
# shellcheck disable=SC2016
WRAP='. "$COPILOT_GATEWAY_DIR/shell/cc-copilot.sh" && "$0" "$@"'

export COPILOT_GATEWAY_DIR="${COPILOT_GATEWAY_DIR:-$ROOT}"
export COPILOT_GATEWAY_PORT="${COPILOT_GATEWAY_PORT:-4000}"
export COPILOT_TOKEN_DIR="${COPILOT_TOKEN_DIR:-$HOME/.config/litellm/github_copilot}"
: "${LITELLM_MASTER_KEY:?open a terminal after setup.sh, or export the gateway key}"
: "${SEARXNG_SECRET:?open a terminal after setup.sh, or export the SearXNG secret}"
# shellcheck source=shell/cc-copilot.sh
. "$COPILOT_GATEWAY_DIR/shell/cc-copilot.sh"

OUT="$COPILOT_GATEWAY_DIR/state/smoke/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT/claude-config" "$OUT/codex-home" "$OUT/work" "$OUT/calls"
effort="${SMOKE_CODEX_EFFORT:-$(sed -n 's/^model_reasoning_effort *= *"\(.*\)".*/\1/p' "$HOME/.codex/config.toml" 2> /dev/null | head -n 1)}"
if [[ -n "$effort" ]]; then
  printf 'model_reasoning_effort = "%s"\n' "$effort" > "$OUT/codex-home/config.toml"
fi
printf 'tool\tmodel\tresult\tseconds\tdetail\n' > "$OUT/results.tsv"

__cc_copilot_ensure || exit 1
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
models="$(cc-copilot --models | grep -E -- "$FILTER" || true)"
if [[ -z "$models" ]]; then
  echo "The gateway lists no model matching '$FILTER'." >&2
  exit 1
fi
searxng="$(__cc_copilot_compose ps -q searxng)"

searxng_bytes() {
  docker exec "$searxng" cat /sys/class/net/eth0/statistics/rx_bytes 2> /dev/null
}

if [[ -z "$searxng" ]] || ! searxng_bytes > /dev/null; then
  echo "Cannot read the SearXNG container's traffic counter, so web search cannot be checked." >&2
  exit 1
fi

classify() {
  local status="$1" log="$2" token="$3" searched="$4"
  if [[ "$status" == 0 ]] && grep -qF "$token" "$log" && [[ "$searched" -gt 0 ]]; then
    echo "PASS"
  elif [[ "$status" != 0 ]] && grep -qiE 'model_not_supported|requested model is not supported' "$log"; then
    echo "PLAN"
  elif [[ "$status" == 0 ]] && grep -qF "$token" "$log"; then
    echo "NOSEARCH"
  elif [[ "$status" == 0 ]]; then
    echo "NOTOOL"
  else
    echo "FAIL"
  fi
}

detail() {
  case "$1" in
    NOSEARCH) echo "the token was read, but SearXNG got no traffic during the call" ;;
    NOTOOL) echo "the answer does not hold the token from $TOKEN_FILE" ;;
    *) grep -iE 'error|exception|status|not supported|invalid|unexpected' "$2" | head -n 1 | cut -c 1-240 | tr '\t' ' ' ;;
  esac
}

run_call() {
  local tool="$1" model="$2" log start status result seconds token before searched
  log="$OUT/calls/$tool-${model//[^A-Za-z0-9._-]/_}.log"
  token="SMOKE-$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
  printf '%s\n' "$token" > "$OUT/work/$TOKEN_FILE"
  start="$(date +%s)"
  before="$(searxng_bytes)"
  if [[ "$tool" == claude ]]; then
    (cd "$OUT/work" && CLAUDE_CONFIG_DIR="$OUT/claude-config" CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
      timeout "$CALL_TIMEOUT" bash -c "$WRAP" c-claude -p "$PROMPT" --model "$model" --output-format text \
      --allowedTools Read,WebSearch) > "$log" 2>&1 < /dev/null
  else
    (cd "$OUT/work" && CODEX_HOME="$OUT/codex-home" \
      timeout "$CALL_TIMEOUT" bash -c "$WRAP" c-codex --search exec --skip-git-repo-check --sandbox read-only \
      -m "$model" "$PROMPT") > "$log" 2>&1 < /dev/null
  fi
  status=$?
  searched=$(($(searxng_bytes) - before))
  seconds=$(($(date +%s) - start))
  result="$(classify "$status" "$log" "$token" "$searched")"
  printf '%s\t%s\t%s\t%s\t%s\n' "$tool" "$model" "$result" "$seconds" "$([[ "$result" == PASS ]] || detail "$result" "$log")" >> "$OUT/results.tsv"
  printf '%-6s %-8s %4ss  %s\n' "$tool" "$result" "$seconds" "$model"
}

while IFS= read -r model; do
  if [[ "$TOOLS" != codex && ( "$model" == claude* || "$model" == anthropic-* ) ]]; then
    run_call claude "$model"
  fi
  if [[ "$TOOLS" != claude && "$model" != anthropic-* && "$model" != *"[1m]"* ]]; then
    run_call codex "$model"
  fi
done <<< "$models"

__cc_copilot_compose logs --no-color --since "$since" gateway > "$OUT/gateway.log" 2>&1 || true

echo
echo "Results: $OUT/results.tsv"
echo "LiteLLM log: $OUT/gateway.log ($(grep -ciE 'error|exception|traceback' "$OUT/gateway.log" || true) error lines)"
for result in PASS PLAN NOSEARCH NOTOOL FAIL; do
  printf '%s: %s\n' "$result" "$(cut -f 3 "$OUT/results.tsv" | grep -cx "$result" || true)"
done
if cut -f 3 "$OUT/results.tsv" | grep -qxE 'FAIL|NOTOOL|NOSEARCH'; then
  echo
  echo "Failures:"
  awk -F '\t' '$3 == "FAIL" || $3 == "NOTOOL" || $3 == "NOSEARCH" { printf "  %s %s %s: %s\n", $1, $2, $3, $5 }' "$OUT/results.tsv"
  exit 1
fi
