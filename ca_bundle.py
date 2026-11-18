"""Builds the gateway's CA bundle: certifi plus the host computer's trusted certificates from state/host-ca.pem."""

from __future__ import annotations

import os
import re
import ssl
from pathlib import Path

import certifi

PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]*?-----END CERTIFICATE-----")


def _certificates(pem: str) -> list[bytes]:
    """Return the DER bytes of every PEM block OpenSSL accepts as a CA certificate."""
    found: list[bytes] = []
    for block in PEM_BLOCK.findall(pem):
        try:
            der = ssl.PEM_cert_to_DER_cert(block.replace("\r", ""))
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=der)
        except (ValueError, ssl.SSLError):
            continue
        found.append(der)
    return found


def build(base_pem: str, extra_pem: str) -> str | None:
    """Return base_pem followed by the valid extra certificates it lacks, or None when there are none."""
    known = set(_certificates(base_pem))
    added: list[str] = []
    for der in _certificates(extra_pem):
        if der not in known:
            known.add(der)
            added.append(ssl.DER_cert_to_PEM_cert(der))
    if not added:
        return None
    return base_pem.rstrip("\n") + "\n" + "".join(added)


def main() -> int:
    """Write state/ca-bundle.pem when the host adds certificates and print its path; print nothing otherwise."""
    state = Path(os.getenv("COPILOT_STATE_DIR", "/state"))
    host = state / "host-ca.pem"
    if not host.is_file():
        return 0
    bundle = build(Path(certifi.where()).read_text(), host.read_text(errors="replace"))
    if bundle is None:
        return 0
    target = state / "ca-bundle.pem"
    target.write_text(bundle)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
