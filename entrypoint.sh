#!/bin/sh
# Gateway container entrypoint: trust the host computer's certificates, refresh
# the Copilot token if needed, write the LiteLLM config and Claude Code / Codex
# defaults into the state directory from the live model list, then run LiteLLM
# on port 4000.
set -eu

if [ -z "${SSL_CERT_FILE:-}" ]; then
  bundle="$(python /app/ca_bundle.py)"
  if [ -n "$bundle" ]; then
    export SSL_CERT_FILE="$bundle"
  fi
fi
python /app/copilot_auth.py
exec litellm --config "${COPILOT_STATE_DIR:-/state}/config.yaml" --host 0.0.0.0 --port 4000
