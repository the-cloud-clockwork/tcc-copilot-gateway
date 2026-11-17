#!/usr/bin/env bash
# Setup. Idempotent by REPLACE: on every run it removes the existing
# COPILOT-GATEWAY block from your shell RC and re-writes a fresh one,
# so function/code updates always propagate. The master key is PRESERVED across
# re-runs (regenerating it would break live clients) — generated only if absent.
#
# Writes to your RC:
#   LITELLM_MASTER_KEY   — read by the gateway (config.yaml) and by clients.
#   cc-copilot()         — start gateway + point Claude Code at it.
#   cc-copilot-logs()    — tail the gateway log.
#
# Usage:
#   ./setup.sh                     # auto-detect RC (~/.bashrc or ~/.zshrc)
#   RC_FILE=~/.bashrc ./setup.sh   # force a specific RC file
#   PORT=4000 ./setup.sh           # match a non-default gateway port
set -euo pipefail

MARKER_BEGIN="# ===COPILOT-GATEWAY==="
MARKER_END="# ===COPILOT-GATEWAY-END==="
PORT="${PORT:-4000}"
BASE_URL="http://localhost:${PORT}"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Pick the RC file: explicit override, else by shell, else ~/.bashrc.
if [[ -z "${RC_FILE:-}" ]]; then
  case "${SHELL:-}" in
    *zsh) RC_FILE="$HOME/.zshrc" ;;
    *)    RC_FILE="$HOME/.bashrc" ;;
  esac
fi
touch "$RC_FILE"

# Preserve an existing master key; generate one only if none is present yet.
KEY="$(grep -oE 'LITELLM_MASTER_KEY="[^"]+"' "$RC_FILE" 2>/dev/null | head -1 | sed -E 's/.*="([^"]+)"/\1/' || true)"
if [[ -z "$KEY" ]]; then
  KEY="sk-$(openssl rand -hex 24)"
  KEY_MSG="master key: generated"
else
  KEY_MSG="master key: preserved"
fi

# Remove any prior block (idempotent replace), then re-append a fresh one below.
if grep -qF "$MARKER_BEGIN" "$RC_FILE"; then
  sed -i "/^${MARKER_BEGIN}$/,/^${MARKER_END}$/d" "$RC_FILE"
fi

cat >> "$RC_FILE" <<EOF

$MARKER_BEGIN
# Master key for the local LiteLLM Copilot gateway. Change it to anything you
# like — the gateway reads whatever is in this variable.
export LITELLM_MASTER_KEY="$KEY"

# Start the local Copilot gateway and point Claude Code at it. Idempotent —
# run.sh always kills any previous gateway, so call it any time.
function cc-copilot() {
  if [[ "\${1:-}" == "-h" || "\${1:-}" == "--help" ]]; then
    cat <<'HELP'
cc-copilot — run Claude Code on your GitHub Copilot models.

  cc-copilot            Start (or restart) the local gateway + set proxy env,
                        then run 'claude'.
  cc-copilot -h|--help  This help.
  cc-copilot-logs       Tail the gateway log (any terminal).

Gateway runs detached (log: ~/.copilot-gateway.log). First run opens a browser
once for GitHub auth; later runs are silent. Stop it: pkill -f "litellm --config".
Env is per-shell — open a new terminal to return Claude Code to normal.
HELP
    return 0
  fi
  # --- point Claude Code at the local proxy (this shell only) ---
  # ANTHROPIC_API_KEY must be cleared: with both it and ANTHROPIC_AUTH_TOKEN set,
  # Claude Code conflicts. AUTH_TOKEN (the gateway master key) is the one we want.
  unset ANTHROPIC_API_KEY ANTHROPIC_MODEL CLAUDE_CODE_EXTRA_BODY
  export CLAUDE_CODE_USE_BEDROCK=0
  export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
  export ANTHROPIC_BASE_URL="$BASE_URL"
  export ANTHROPIC_AUTH_TOKEN="\$LITELLM_MASTER_KEY"
  # Show ALL proxy models in the /model picker (calls GET /v1/models on startup,
  # labeled "From gateway").
  export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
  # Set only the Sonnet default; let discovery surface the rest in /model.
  export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-5"
  # --- (re)start the gateway, detached ---
  ( cd "$REPO_DIR" && nohup ./run.sh >"\$HOME/.copilot-gateway.log" 2>&1 & )
  # Wait until it actually serves. Launching 'claude' before the gateway binds
  # causes ConnectionRefused AND fails model discovery (GET /v1/models), which is
  # why the /model picker sometimes shows only the tier defaults, not all models.
  local code=""
  printf "Starting Copilot gateway"
  for _ in \$(seq 1 60); do
    code="\$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/v1/models" -H "Authorization: Bearer \$LITELLM_MASTER_KEY" 2>/dev/null || true)"
    [[ "\$code" == "200" ]] && break
    printf "."; sleep 1
  done
  echo
  if [[ "\$code" == "200" ]]; then
    echo "Copilot gateway ready → run:  claude"
    echo "(logs: cc-copilot-logs)"
  else
    echo "Gateway not ready (HTTP \$code) — check: cc-copilot-logs"
  fi
}

# Watch the gateway logs live from any terminal (Ctrl-C stops watching, not the
# gateway).
function cc-copilot-logs() {
  tail -f "\$HOME/.copilot-gateway.log"
}
$MARKER_END
EOF

echo "Setup refreshed → $RC_FILE  ($KEY_MSG)"
echo ""
echo "  source $RC_FILE   # once"
echo "  cc-copilot                    # start gateway (proxy env + server)"
echo "  claude                        # use your Copilot models"
echo ""
echo "  cc-copilot --help  ·  cc-copilot-logs"
