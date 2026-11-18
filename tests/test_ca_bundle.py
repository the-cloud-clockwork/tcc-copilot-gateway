"""CA bundle: certifi plus the host computer's trusted certificates, valid and deduplicated."""

import re
import ssl
from pathlib import Path

import certifi
import pytest

import ca_bundle

BLOCKS: list[str] = re.findall(
    r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", Path(certifi.where()).read_text(), re.DOTALL
)
BASE = "\n".join(BLOCKS[:-1]) + "\n"
NEW = BLOCKS[-1] + "\n"
MALFORMED = "-----BEGIN CERTIFICATE-----\nnot a certificate\n-----END CERTIFICATE-----\n"


def _loaded(pem: str) -> int:
    """Return how many CA certificates OpenSSL loads from a bundle."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=pem)
    return len(context.get_ca_certs())


def test_new_host_certificates_are_appended_once() -> None:
    """A host certificate certifi lacks is added; one certifi already has is not repeated."""
    bundle = ca_bundle.build(BASE, BLOCKS[0] + "\n" + NEW + NEW)
    assert bundle is not None
    assert bundle.startswith(BASE)
    assert bundle.count("-----BEGIN CERTIFICATE-----") == len(BLOCKS)
    assert _loaded(bundle) == len(BLOCKS)


def test_malformed_blocks_are_dropped() -> None:
    """A block OpenSSL cannot read is skipped, so the bundle still loads."""
    bundle = ca_bundle.build(BASE, MALFORMED + NEW)
    assert bundle is not None
    assert "not a certificate" not in bundle
    assert _loaded(bundle) == len(BLOCKS)


def test_windows_line_endings_are_accepted() -> None:
    """A certificate exported with CRLF line endings is still added."""
    bundle = ca_bundle.build(BASE, NEW.replace("\n", "\r\n"))
    assert bundle is not None
    assert _loaded(bundle) == len(BLOCKS)


def test_nothing_new_means_no_bundle() -> None:
    """When the host adds no certificate, build returns None and certifi stays in use."""
    assert ca_bundle.build(BASE, BLOCKS[0] + MALFORMED) is None
    assert ca_bundle.build(BASE, "") is None


def test_main_writes_the_bundle_and_prints_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main writes state/ca-bundle.pem from state/host-ca.pem and prints the path for the entrypoint."""
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "certifi.pem"))
    (tmp_path / "certifi.pem").write_text(BASE)
    (tmp_path / "host-ca.pem").write_text(NEW)
    assert ca_bundle.main() == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "ca-bundle.pem")
    assert _loaded((tmp_path / "ca-bundle.pem").read_text()) == len(BLOCKS)


def test_main_without_host_certificates_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without state/host-ca.pem, main writes no bundle and prints nothing."""
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    assert ca_bundle.main() == 0
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "ca-bundle.pem").exists()
