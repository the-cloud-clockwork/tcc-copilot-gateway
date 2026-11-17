#!/usr/bin/env bash
# Installer test: runs setup.sh twice against a sandboxed HOME for one shell
# (bash or zsh) and checks the RC block, the master key, the surrounding RC
# lines, a symlinked RC file and the cc-copilot functions in that shell.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_SHELL="${1:?usage: installer_test.sh bash|zsh}"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
export HOME="$SANDBOX"

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
for word in cc-copilot-logs --update --models; do
  grep -q -- "$word" <<< "$help_text" || fail "help does not mention $word"
done
"$TARGET_SHELL" -c "source '$RC'; typeset -f cc-copilot-logs > /dev/null" || fail "cc-copilot-logs not defined"

echo "PASS [$TARGET_SHELL]: installer block, key, symlink and functions"
