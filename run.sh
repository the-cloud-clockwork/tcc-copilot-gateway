#!/usr/bin/env bash
# Single entrypoint. Bootstraps deps, ensures a valid Copilot token (refreshing
# only if the model list can't be fetched), regenerates config from your live
# subscription, then runs the LiteLLM gateway in the foreground.
#
# Kill this script (Ctrl-C / close terminal) and the gateway dies with it.
set -euo pipefail

cd "$(dirname "$0")"

# Load .env if present.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Token dir (expand a literal $HOME that may come from .env).
TOKEN_DIR="${GITHUB_COPILOT_TOKEN_DIR:-$HOME/.config/litellm/github_copilot}"
export GITHUB_COPILOT_TOKEN_DIR="${TOKEN_DIR/#\$HOME/$HOME}"

PORT="${PORT:-4000}"

# Idempotent restart: always clear the port before starting, however the old
# gateway was launched. This is what guarantees the fresh gateway binds with the
# CURRENT $LITELLM_MASTER_KEY — a stale instance holding a different key is the
# cause of LiteLLM's misleading "No connected db" (== wrong master key) errors.
pkill -f "litellm .*--port ${PORT}" 2>/dev/null || true
pkill -f "litellm --config"        2>/dev/null || true
for _ in 1 2 3 4 5; do
  ss -ltn 2>/dev/null | grep -q ":${PORT} " || break
  command -v fuser >/dev/null 2>&1 && fuser -k "${PORT}/tcp" 2>/dev/null || true
  pid="$(ss -ltnp 2>/dev/null | grep ":${PORT} " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)"
  [[ -n "$pid" ]] && kill -9 "$pid" 2>/dev/null || true
  sleep 1
done

# Master key resolution — the gateway MUST use the same key clients use, or
# LiteLLM rejects every request with the misleading "No connected db" (== wrong
# master key). Single source of truth, in priority order:
#   1. env LITELLM_MASTER_KEY (set by the sourced RC / cc-copilot)
#   2. the key setup.sh wrote into your shell RC  ← prevents orphan gateways
#   3. generate one (last resort; only when nothing is configured)
if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
  for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    [[ -f "$rc" ]] || continue
    k="$(grep -oE 'LITELLM_MASTER_KEY="[^"]+"' "$rc" | head -1 | sed -E 's/.*="([^"]+)"/\1/' || true)"
    if [[ -n "$k" ]]; then LITELLM_MASTER_KEY="$k"; export LITELLM_MASTER_KEY; break; fi
  done
fi
if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
  LITELLM_MASTER_KEY="sk-$(openssl rand -hex 24)"
  export LITELLM_MASTER_KEY
  echo "No LITELLM_MASTER_KEY configured — generated ephemeral (run ./setup.sh for a stable key):"
  echo "    $LITELLM_MASTER_KEY"
fi

# Bootstrap venv + deps on first run.
if [[ ! -x .venv/bin/litellm ]]; then
  echo "First run — setting up virtualenv..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

# Auth + sanity check + regenerate config. Only refreshes the token on failure.
.venv/bin/python copilot_auth.py

echo ""
echo "Gateway up:  http://localhost:${PORT}/v1"
echo "Master key:  \$LITELLM_MASTER_KEY  (Authorization: Bearer <key>)"
echo "Ctrl-C to stop."
echo ""

# exec => the gateway takes over this PID, so killing the script kills the gateway.
# env -u strips any inherited DATABASE_URL etc. — this is a stateless gateway,
# no DB, no prisma, no UI backend.
exec env -u DATABASE_URL -u STORE_MODEL_IN_DB -u LITELLM_DATABASE_URL \
  .venv/bin/litellm --config ./config.yaml --port "$PORT"
