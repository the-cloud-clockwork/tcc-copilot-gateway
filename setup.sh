#!/usr/bin/env bash
# Installer for Linux, WSL and macOS. Writes the COPILOT-GATEWAY block into
# ~/.bashrc or ~/.zshrc: LITELLM_MASTER_KEY and the gateway settings, then a
# line that sources shell/cc-copilot.sh (cc-copilot, cc-copilot-logs and the
# claude wrapper). Every run replaces the block in place, keeps the existing
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
SECRET="$(grep -oE 'SEARXNG_SECRET="[^"]+"' "$RC_FILE" 2>/dev/null | head -1 | sed -E 's/.*="([^"]+)"/\1/' || true)"
if [[ -z "$SECRET" ]]; then
  SECRET="$(openssl rand -hex 32)"
fi
TOKEN_DIR="${GITHUB_COPILOT_TOKEN_DIR:-$HOME/.config/litellm/github_copilot}"

if ! awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '$0 == b {if (open) bad = 1; open = 1} $0 == e {if (!open) bad = 1; open = 0} END {exit (bad || open)}' "$RC_FILE"; then
  echo "setup.sh: $RC_FILE has unbalanced or out-of-order COPILOT-GATEWAY markers. Fix them by hand; nothing was changed." >&2
  exit 1
fi

KEPT="$(awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '$0 == b {skip = 1; next} $0 == e {skip = 0; next} !skip' "$RC_FILE")"
printf '%s\n' "$KEPT" > "$RC_FILE"

cat >> "$RC_FILE" <<EOF

$MARKER_BEGIN
export LITELLM_MASTER_KEY="$KEY"
export SEARXNG_SECRET="$SECRET"
export COPILOT_GATEWAY_DIR="$REPO_DIR"
export COPILOT_GATEWAY_PORT="$PORT"
export COPILOT_GATEWAY_RC="$RC_FILE"
export COPILOT_TOKEN_DIR="$TOKEN_DIR"
[ -f "\$COPILOT_GATEWAY_DIR/shell/cc-copilot.sh" ] && . "\$COPILOT_GATEWAY_DIR/shell/cc-copilot.sh"
$MARKER_END
EOF

echo "Setup refreshed -> $RC_FILE  ($KEY_MSG)"
command -v docker > /dev/null 2>&1 || echo "WARNING: Docker not found. Install Docker Desktop (macOS, Windows) or Docker Engine (Linux)."
echo ""
echo "  Open a new terminal (or: source $RC_FILE), then:"
echo "  c-claude          # Claude Code on your Copilot models (starts the gateway when needed)"
echo "  c-codex           # Codex on your Copilot models"
echo ""
echo "  cc-copilot  |  cc-copilot -d  |  cc-copilot --stop  |  cc-copilot --models  |  cc-copilot --update  |  cc-copilot --help"
