#!/usr/bin/env bash
# Exports the certificates that sign HTTPS on this network from the Windows
# certificate store (WSL) to ~/.config/tcc-copilot-gateway/company-ca.pem:
# every Root and intermediate (CA) certificate named as an issuer in the chain
# the network presents for the host. Checks that the export verifies the host,
# then prints the commands that make the gateway trust it.
# Usage: scripts/export-company-ca.sh [host]   (default: api.business.githubcopilot.com)
set -euo pipefail

HOST="${1:-api.business.githubcopilot.com}"
OUT="$HOME/.config/tcc-copilot-gateway/company-ca.pem"
WINDOWS_PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe

if command -v powershell.exe > /dev/null 2>&1; then
  ps="$(command -v powershell.exe)"
elif [[ -x "$WINDOWS_PS" ]]; then
  ps="$WINDOWS_PS"
else
  echo "powershell.exe not found: this script reads the Windows certificate store from WSL." >&2
  echo "On macOS or Linux, ask IT for the root certificate as a PEM file and set COPILOT_CA_FILE to it." >&2
  exit 1
fi

chain="$(echo | openssl s_client -connect "$HOST:443" -servername "$HOST" -showcerts 2> /dev/null || true)"
issuers="$(grep -E '^ *i:' <<< "$chain" | sed -E 's/.*CN ?= ?([^,]+).*/\1/' | sort -u)"
if [[ -z "$issuers" ]]; then
  echo "No TLS answer from $HOST. Check the network, VPN or proxy settings." >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
: > "$OUT"
while IFS= read -r cn; do
  echo "Issuer: $cn"
  "$ps" -NoProfile -NonInteractive -Command "Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\CA, Cert:\CurrentUser\Root, Cert:\CurrentUser\CA | Where-Object { \$_.Subject.Contains('$cn') } | ForEach-Object { '-----BEGIN CERTIFICATE-----'; [Convert]::ToBase64String(\$_.RawData, 'InsertLineBreaks'); '-----END CERTIFICATE-----' }" \
    < /dev/null 2> /dev/null | tr -d '\r' >> "$OUT"
done <<< "$issuers"

exported="$(grep -c 'BEGIN CERTIFICATE' "$OUT" || true)"
if [[ "$exported" == 0 ]]; then
  echo "Windows holds none of these issuers. Ask IT for the root certificate as a PEM file and set COPILOT_CA_FILE to it." >&2
  exit 1
fi

bundle=""
for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
  if [[ -s "$f" ]]; then
    bundle="$f"
    break
  fi
done
combined="$(mktemp)"
trap 'rm -f "$combined"' EXIT
cat ${bundle:+"$bundle"} "$OUT" > "$combined"
result="$(echo | openssl s_client -connect "$HOST:443" -servername "$HOST" -CAfile "$combined" 2> /dev/null \
  | sed -n 's/^ *Verify return code: //p' | head -n 1)"

echo
echo "Saved $exported certificates to $OUT"
echo "Verify $HOST with them: $result"
if [[ "$result" != 0* ]]; then
  echo "The export does not verify $HOST. Send this output." >&2
  exit 1
fi
echo
echo "Now run these in your terminal:"
echo "  echo 'export COPILOT_CA_FILE=$OUT' >> ${COPILOT_GATEWAY_RC:-$HOME/.bashrc}"
echo "  export COPILOT_CA_FILE=$OUT"
echo "  cc-copilot -d"
