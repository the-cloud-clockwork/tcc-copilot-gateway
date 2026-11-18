#!/usr/bin/env bash
# Read-only TLS diagnosis for the Copilot gateway on networks that inspect HTTPS.
# Usage: scripts/diagnose-tls.sh [host]   (default: api.business.githubcopilot.com)
# Shows the certificate chain the network presents for the host, whether Windows
# (on WSL), state/host-ca.pem and state/ca-bundle.pem hold its issuer, what the
# gateway container trusts, and a VERDICT line. Prints certificate names and
# counts only, never secrets.
set -euo pipefail

HOST="${1:-api.business.githubcopilot.com}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/state"
WINDOWS_PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe

windows_powershell() {
  if command -v powershell.exe > /dev/null 2>&1; then
    command -v powershell.exe
  elif [[ -x "$WINDOWS_PS" ]]; then
    echo "$WINDOWS_PS"
  fi
}

presented_chain() {
  echo | openssl s_client -connect "$HOST:443" -servername "$HOST" -showcerts 2> /dev/null || true
}

verify_with() {
  echo | openssl s_client -connect "$HOST:443" -servername "$HOST" -CAfile "$1" 2> /dev/null \
    | sed -n 's/^ *Verify return code: //p' | head -n 1
}

count_certs() {
  if [[ -s "$1" ]]; then grep -c 'BEGIN CERTIFICATE' "$1"; else echo 0; fi
}

holds() {
  if [[ ! -s "$1" ]]; then
    echo 0
    return
  fi
  openssl crl2pkcs7 -nocrl -certfile "$1" 2> /dev/null | openssl pkcs7 -print_certs -noout 2> /dev/null \
    | grep '^subject' | grep -cF "$2" || true
}

system_bundle() {
  local f
  for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
    if [[ -s "$f" ]]; then
      echo "$f"
      return
    fi
  done
}

echo "Host: $HOST"
echo
echo "== Chain this network presents"
chain="$(presented_chain)"
if ! grep -E '^ *[0-9]+ s:|^ *i:' <<< "$chain"; then
  echo "VERDICT: no TLS answer from $HOST. Check the network, VPN or proxy settings."
  exit 1
fi
cn="$(grep -E '^ *i:' <<< "$chain" | tail -n 1 | sed -E 's/.*CN ?= ?([^,]+).*/\1/')"
echo "Top issuer CN: $cn"

echo
echo "== Verification with OpenSSL on this computer"
bundle="$(system_bundle)"
system_result="$(verify_with "${bundle:-/dev/null}")"
echo "  system bundle ${bundle:-none}: $system_result"
gateway_result="missing"
if [[ -s "$STATE/ca-bundle.pem" ]]; then
  gateway_result="$(verify_with "$STATE/ca-bundle.pem")"
fi
echo "  state/ca-bundle.pem: $gateway_result"

echo
echo "== Windows certificate stores"
ps="$(windows_powershell)"
windows_matches=""
if grep -qi microsoft /proc/version 2> /dev/null; then
  if [[ -z "$ps" ]]; then
    echo "  powershell.exe not found: WSL cannot run Windows programs here"
  else
    windows_matches="$("$ps" -NoProfile -NonInteractive -Command "Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\CA, Cert:\CurrentUser\Root, Cert:\CurrentUser\CA | Where-Object { \$_.Subject.Contains('$cn') } | ForEach-Object { \$_.PSParentPath.Split(':')[-1] + ' | ' + \$_.Subject }" 2>&1 | tr -d '\r')"
    if [[ -n "$windows_matches" ]]; then
      while IFS= read -r line; do echo "  $line"; done <<< "$windows_matches"
    else
      echo "  no certificate named '$cn' in LocalMachine or CurrentUser Root/CA"
    fi
  fi
else
  echo "  not WSL, skipped"
fi

echo
echo "== Gateway files"
env_lines=0
if [[ -f "$ROOT/.env" ]]; then
  env_lines="$(grep -c '^SSL_CERT_FILE' "$ROOT/.env" || true)"
fi
host_matches="$(holds "$STATE/host-ca.pem" "$cn")"
echo "  state/host-ca.pem: $(count_certs "$STATE/host-ca.pem") certificates, $host_matches named '$cn'"
echo "  state/ca-bundle.pem: $(count_certs "$STATE/ca-bundle.pem") certificates, $(holds "$STATE/ca-bundle.pem" "$cn") named '$cn'"
echo "  SSL_CERT_FILE lines in .env: $env_lines"
echo "  COPILOT_CA_FILE in this shell: ${COPILOT_CA_FILE:-unset}"

echo
echo "== Gateway container"
if command -v docker > /dev/null 2>&1 && [[ -n "${LITELLM_MASTER_KEY:-}" ]]; then
  # shellcheck disable=SC2016
  (cd "$ROOT" && COPILOT_UID="$(id -u)" COPILOT_GID="$(id -g)" docker compose run --rm --no-deps --entrypoint sh gateway \
    -c 'b="$(python /app/ca_bundle.py)"; echo "  ca_bundle.py wrote: ${b:-nothing}"; echo "  SSL_CERT_FILE inside: ${SSL_CERT_FILE:-unset}"' \
    2>&1 | grep '^  ') || echo "  could not run the gateway image"
else
  echo "  skipped: docker or LITELLM_MASTER_KEY missing in this shell"
fi

echo
if [[ "$env_lines" != 0 ]]; then
  echo "VERDICT: .env sets SSL_CERT_FILE, so the gateway ignores your computer's certificates. Delete that line from $ROOT/.env, then run cc-copilot -d."
elif [[ "$system_result" == 0* ]]; then
  echo "VERDICT: this computer's system bundle verifies $HOST. Run cc-copilot -d to rebuild state/ca-bundle.pem; if the gateway still fails, send this output."
elif [[ "$gateway_result" == 0* ]]; then
  echo "VERDICT: state/ca-bundle.pem verifies $HOST, so the container is not using it. Check the Gateway container section and send this output."
elif grep -qi microsoft /proc/version 2> /dev/null && [[ -z "$ps" ]]; then
  echo "VERDICT: WSL cannot run powershell.exe, so Windows certificates were never copied. Run scripts/export-company-ca.sh."
elif grep -qi microsoft /proc/version 2> /dev/null && [[ -z "$windows_matches" ]]; then
  echo "VERDICT: Windows does not hold '$cn'. Ask IT for that root certificate as a PEM file, then: export COPILOT_CA_FILE=<file> and cc-copilot -d."
elif [[ "$host_matches" == 0 ]]; then
  echo "VERDICT: '$cn' is not in state/host-ca.pem. Run scripts/export-company-ca.sh."
else
  echo "VERDICT: '$cn' is in state/host-ca.pem but $HOST still fails to verify; an intermediate is likely missing. Run scripts/export-company-ca.sh."
fi
