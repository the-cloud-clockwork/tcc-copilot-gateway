#!/usr/bin/env bash
# Installer test: runs setup.sh twice against a sandboxed HOME for one shell
# (bash or zsh) and checks the RC block, the master key, the surrounding RC
# lines, a symlinked RC file and the cc-copilot functions in that shell.
set -euo pipefail

SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_SHELL="${1:?usage: installer_test.sh bash|zsh}"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
cp -R "$SOURCE" "$SANDBOX/repo"
REPO="$SANDBOX/repo"
export HOME="$SANDBOX"
export PORT=48123

fail() {
  echo "FAIL [$TARGET_SHELL]: $*" >&2
  exit 1
}

mkdir -p "$HOME/dotfiles"
REAL_RC="$HOME/dotfiles/${TARGET_SHELL}rc"
RC="$HOME/.${TARGET_SHELL}rc"
printf 'export KEEP_BEFORE=1\n' > "$REAL_RC"
ln -s "$REAL_RC" "$RC"

RC_FILE="$RC" "$REPO/setup.sh" > /dev/null
printf 'export KEEP_AFTER=1\n' >> "$RC"
first_key="$(grep -oE 'LITELLM_MASTER_KEY="[^"]+"' "$RC")"

RC_FILE="$RC" "$REPO/setup.sh" > /dev/null
second_key="$(grep -oE 'LITELLM_MASTER_KEY="[^"]+"' "$RC")"

[[ -L "$RC" ]] || fail "RC symlink was replaced by a regular file"
[[ "$(grep -c '^# ===COPILOT-GATEWAY===$' "$RC")" == 1 ]] || fail "expected exactly one begin marker"
[[ "$(grep -c '^# ===COPILOT-GATEWAY-END===$' "$RC")" == 1 ]] || fail "expected exactly one end marker"
[[ -n "$first_key" && "$first_key" == "$second_key" ]] || fail "master key not preserved across runs"
grep -q '^export KEEP_BEFORE=1$' "$RC" || fail "line before the block was lost"
grep -q '^export KEEP_AFTER=1$' "$RC" || fail "line after the block was lost"

help_text="$("$TARGET_SHELL" -c "source '$RC'; cc-copilot --help")"
for word in cc-copilot-logs --update --models --detached --stop c-claude c-codex; do
  grep -q -- "$word" <<< "$help_text" || fail "help does not mention $word"
done
for fn in cc-copilot cc-copilot-logs c-claude c-codex; do
  "$TARGET_SHELL" -c "source '$RC'; typeset -f $fn > /dev/null" || fail "$fn not defined"
done

mkdir -p "$HOME/bin" "$HOME/www/v1"
printf '{"data": []}' > "$HOME/www/v1/models"
cat > "$HOME/bin/claude" <<'FAKE'
#!/bin/sh
echo "BASE=${ANTHROPIC_BASE_URL:-none} TOKEN=${ANTHROPIC_AUTH_TOKEN:+set}"
FAKE
cat > "$HOME/bin/codex" <<'FAKE'
#!/bin/sh
echo "CODEX $*"
FAKE
cat > "$HOME/bin/docker" <<'FAKE'
#!/bin/sh
case "$*" in
  *"ps --status running --quiet gateway"*) [ -f "$HOME/.fake-gateway" ] && echo fake-gateway ;;
esac
exit 0
FAKE
chmod +x "$HOME/bin/claude" "$HOME/bin/codex" "$HOME/bin/docker"
alias_run() {
  PATH="$HOME/bin:$PATH" "$TARGET_SHELL" -c "unset ANTHROPIC_BASE_URL; source '$RC'; $1" 2> /dev/null
}
(cd "$HOME/www" && exec python3 -m http.server "$PORT" --bind 127.0.0.1 > /dev/null 2>&1) &
fake=$!
touch "$HOME/.fake-gateway"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null "http://localhost:$PORT/v1/models" && break
  sleep 1
done
[[ "$(alias_run c-claude)" == "BASE=http://localhost:$PORT TOKEN=set" ]] || fail "c-claude did not go to the gateway"
mkdir -p "$REPO/state" "$HOME/.codex"
printf 'COPILOT_CODEX_MODEL=gpt-test\nCOPILOT_CODEX_REASONING_EFFORTS=none low high xhigh\n' > "$REPO/state/defaults.env"
printf 'model_reasoning_effort = "max"\n' > "$HOME/.codex/config.toml"
codex_line="$(alias_run c-codex)"
grep -q -- '-m gpt-test' <<< "$codex_line" || fail "c-codex did not pick the gateway's Codex model: $codex_line"
grep -q -- 'model_reasoning_effort=xhigh' <<< "$codex_line" || fail "c-codex kept an effort the model rejects: $codex_line"
printf 'model_reasoning_effort = "low"\n' > "$HOME/.codex/config.toml"
if grep -q -- 'model_reasoning_effort' <<< "$(alias_run c-codex)"; then
  fail "c-codex overrode an effort the model supports"
fi
grep -q -- "base_url=\"http://localhost:$PORT/v1\"" <<< "$codex_line" || fail "c-codex did not point at the gateway: $codex_line"
grep -q -- 'wire_api="responses"' <<< "$codex_line" || fail "c-codex does not use the responses wire API"
grep -q -- 'model_provider=copilot-gateway' <<< "$codex_line" || fail "c-codex does not select the gateway provider"
kill "$fake"
wait "$fake" 2> /dev/null || true
rm -f "$HOME/.fake-gateway"
[[ "$(alias_run 'cc-copilot --stop')" == "No Copilot gateway is running." ]] || fail "cc-copilot --stop saw a gateway after it stopped"

BROKEN="$HOME/broken_rc"
for content in \
  'export A=1\n# ===COPILOT-GATEWAY===\nexport B=2\n' \
  'export A=1\n# ===COPILOT-GATEWAY-END===\nexport B=2\n# ===COPILOT-GATEWAY===\nexport C=3\n'; do
  printf '%b' "$content" > "$BROKEN"
  cp "$BROKEN" "$BROKEN.orig"
  if RC_FILE="$BROKEN" "$REPO/setup.sh" > /dev/null 2>&1; then
    fail "setup.sh accepted an RC file with unbalanced or out-of-order markers"
  fi
  cmp -s "$BROKEN" "$BROKEN.orig" || fail "setup.sh changed an RC file with unbalanced or out-of-order markers"
done

echo "PASS [$TARGET_SHELL]: installer block, key, symlink and functions"
