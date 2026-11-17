#!/usr/bin/env bash
# Installer for Linux, WSL and macOS. Writes the COPILOT-GATEWAY block into
# ~/.bashrc or ~/.zshrc: LITELLM_MASTER_KEY, cc-copilot (start the gateway and
# point Claude Code at it, plus --models, --update and --help) and
# cc-copilot-logs. Every run replaces the block in place, keeps the existing
# master key and the rest of the file, and writes through a symlinked RC file.
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

if [[ -z "${RC_FILE:-}" ]]; then
  case "${SHELL:-}" in
    *zsh) RC_FILE="$HOME/.zshrc" ;;
    *)    RC_FILE="$HOME/.bashrc" ;;
  esac
fi
touch "$RC_FILE"

KEY="$(grep -oE 'LITELLM_MASTER_KEY="[^"]+"' "$RC_FILE" 2>/dev/null | head -1 | sed -E 's/.*="([^"]+)"/\1/' || true)"
if [[ -z "$KEY" ]]; then
  KEY="sk-$(openssl rand -hex 24)"
  KEY_MSG="master key: generated"
else
  KEY_MSG="master key: preserved"
fi

KEPT="$(awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '$0 == b {skip = 1; next} $0 == e {skip = 0; next} !skip' "$RC_FILE")"
printf '%s\n' "$KEPT" > "$RC_FILE"

cat >> "$RC_FILE" <<EOF

$MARKER_BEGIN
export LITELLM_MASTER_KEY="$KEY"

function cc-copilot() {
  case "\${1:-}" in
    -h|--help)
      cat <<'HELP'
cc-copilot - run Claude Code on your GitHub Copilot models.

  cc-copilot            Start (or restart) the local gateway and point Claude
                        Code at it in this shell. Then run 'claude'.
  cc-copilot --models   List the models the gateway serves right now.
  cc-copilot --update   Pull the latest gateway code and refresh this block.
                        The next start rebuilds the virtualenv when the
                        pinned dependencies changed.
  cc-copilot --help     This help.
  cc-copilot-logs       Tail the gateway log (any terminal).

Models come from your live Copilot subscription on every start, and the newest
Opus, Sonnet and Haiku become Claude Code's defaults. Nothing to edit by hand.
First start opens a browser once for GitHub auth. Log: ~/.copilot-gateway.log.
Stop the gateway: pkill -f "litellm --config". The env is per shell: open a new
terminal to return Claude Code to normal.
HELP
      return 0
      ;;
    --models)
      curl -s "$BASE_URL/v1/models" -H "Authorization: Bearer \$LITELLM_MASTER_KEY" \\
        | "$REPO_DIR/.venv/bin/python" -c 'import json, sys; print("\n".join(m["id"] for m in json.load(sys.stdin)["data"]))'
      return
      ;;
    --update)
      git -C "$REPO_DIR" pull --ff-only \\
        && PORT="$PORT" RC_FILE="$RC_FILE" "$REPO_DIR/setup.sh" \\
        && source "$RC_FILE" \\
        && echo "Updated. Run cc-copilot to restart the gateway on the new version."
      return
      ;;
  esac
  unset ANTHROPIC_API_KEY ANTHROPIC_MODEL CLAUDE_CODE_EXTRA_BODY
  unset ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL
  export CLAUDE_CODE_USE_BEDROCK=0
  export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
  export ANTHROPIC_BASE_URL="$BASE_URL"
  export ANTHROPIC_AUTH_TOKEN="\$LITELLM_MASTER_KEY"
  export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
  ( cd "$REPO_DIR" && PORT="$PORT" nohup ./run.sh >"\$HOME/.copilot-gateway.log" 2>&1 & )
  local code="" line="" i=""
  printf "Starting Copilot gateway"
  for i in \$(seq 1 300); do
    code="\$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/v1/models" -H "Authorization: Bearer \$LITELLM_MASTER_KEY" 2>/dev/null || true)"
    [[ "\$code" == "200" ]] && break
    printf "."; sleep 1
  done
  echo
  if [[ "\$code" != "200" ]]; then
    echo "Gateway not ready (HTTP \$code) - check: cc-copilot-logs"
    return 1
  fi
  if [[ -f "$REPO_DIR/defaults.env" ]]; then
    while IFS= read -r line; do
      [[ "\$line" == ANTHROPIC_DEFAULT_*=* ]] && export "\$line"
    done < "$REPO_DIR/defaults.env"
  fi
  echo "Copilot gateway ready. Run: claude"
  echo "Defaults: opus=\${ANTHROPIC_DEFAULT_OPUS_MODEL:-none} sonnet=\${ANTHROPIC_DEFAULT_SONNET_MODEL:-none} haiku=\${ANTHROPIC_DEFAULT_HAIKU_MODEL:-none}"
  echo "(logs: cc-copilot-logs)"
}

function cc-copilot-logs() {
  tail -f "\$HOME/.copilot-gateway.log"
}
$MARKER_END
EOF

echo "Setup refreshed -> $RC_FILE  ($KEY_MSG)"
echo ""
echo "  source $RC_FILE   # once"
echo "  cc-copilot                    # start gateway (proxy env + server)"
echo "  claude                        # use your Copilot models"
echo ""
echo "  cc-copilot --help  |  cc-copilot --models  |  cc-copilot --update  |  cc-copilot-logs"
