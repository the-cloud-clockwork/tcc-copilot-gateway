"""A network failure keeps the GitHub login and never starts a new device flow; a rejected token still does."""

from pathlib import Path
from typing import Any

import httpx
import pytest

import copilot_auth

TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
TLS_ERROR = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"


def _tls_failure(access_token: str) -> dict[str, Any]:
    """Raise the error a TLS-inspecting proxy causes on the Copilot token exchange."""
    raise httpx.ConnectError(TLS_ERROR, request=httpx.Request("GET", TOKEN_URL))


def _no_device_flow() -> str:
    """Fail the test when a device flow starts."""
    raise AssertionError("a new device flow started")


@pytest.fixture
def token_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the token files at a temporary folder."""
    monkeypatch.setattr(copilot_auth, "TOKEN_DIR", tmp_path)
    monkeypatch.setattr(copilot_auth, "ACCESS_TOKEN_FILE", tmp_path / "access-token")
    monkeypatch.setattr(copilot_auth, "API_KEY_FILE", tmp_path / "api-key.json")
    return tmp_path


def test_existing_token_and_tls_failure_do_not_reauthenticate(
    token_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A saved token plus a TLS failure exits 1 with a hint naming the host, without a new device flow."""
    (token_dir / "access-token").write_text("gho_existing")
    monkeypatch.setattr(copilot_auth, "exchange_api_key", _tls_failure)
    monkeypatch.setattr(copilot_auth, "device_flow", _no_device_flow)
    assert copilot_auth.main() == 1
    out = capsys.readouterr().out
    assert "api.github.com" in out
    assert "COPILOT_CA_FILE" in out
    assert (token_dir / "access-token").read_text() == "gho_existing"


def test_existing_token_and_model_list_failure_do_not_reauthenticate(
    token_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A transport error on the model list also exits 1 without a new device flow."""
    (token_dir / "access-token").write_text("gho_existing")

    def exchange(access_token: str) -> dict[str, Any]:
        """Accept the token."""
        return {"token": "copilot-api", "endpoints": {"api": "https://api.githubcopilot.com"}}

    def unreachable(api_token: str, api_base: str) -> list[dict[str, Any]]:
        """Raise the error an unreachable Copilot API causes."""
        raise httpx.ConnectTimeout("timed out", request=httpx.Request("GET", f"{api_base}/models"))

    monkeypatch.setattr(copilot_auth, "exchange_api_key", exchange)
    monkeypatch.setattr(copilot_auth, "list_models", unreachable)
    monkeypatch.setattr(copilot_auth, "device_flow", _no_device_flow)
    assert copilot_auth.main() == 1
    out = capsys.readouterr().out
    assert "api.githubcopilot.com" in out
    assert "login is kept" in out


def test_device_flow_network_failure_does_not_claim_a_kept_login(
    token_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A transport error before any token exists exits 1 without saying the login is kept."""

    def unreachable() -> str:
        """Raise the error an unreachable github.com causes during the device flow."""
        raise httpx.ConnectError("unreachable", request=httpx.Request("POST", "https://github.com/login/device/code"))

    monkeypatch.setattr(copilot_auth, "device_flow", unreachable)
    assert copilot_auth.main() == 1
    out = capsys.readouterr().out
    assert "github.com" in out
    assert "login is kept" not in out


def test_rejected_token_is_not_reported_as_kept(
    token_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token GitHub rejected is removed, so a network failure in the new device flow claims no kept login."""
    (token_dir / "access-token").write_text("gho_revoked")

    def rejected(access_token: str) -> dict[str, Any]:
        """Reject the saved token."""
        request = httpx.Request("GET", TOKEN_URL)
        raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

    def unreachable() -> str:
        """Raise the error an unreachable github.com causes during the device flow."""
        raise httpx.ConnectError("unreachable", request=httpx.Request("POST", "https://github.com/login/device/code"))

    monkeypatch.setattr(copilot_auth, "exchange_api_key", rejected)
    monkeypatch.setattr(copilot_auth, "device_flow", unreachable)
    assert copilot_auth.main() == 1
    assert "login is kept" not in capsys.readouterr().out
    assert not (token_dir / "access-token").exists()


def test_fresh_login_is_kept_when_the_network_fails(token_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The token from a finished device flow is saved even when the next call fails on the network."""

    def fresh_login() -> str:
        """Return a token as a finished device flow does."""
        return "gho_fresh"

    monkeypatch.setattr(copilot_auth, "device_flow", fresh_login)
    monkeypatch.setattr(copilot_auth, "exchange_api_key", _tls_failure)
    assert copilot_auth.main() == 1
    assert (token_dir / "access-token").read_text() == "gho_fresh"


def test_rejected_token_still_reauthenticates(token_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token GitHub rejects with an HTTP error still leads to a new device flow."""
    (token_dir / "access-token").write_text("gho_revoked")

    def exchange(access_token: str) -> dict[str, Any]:
        """Reject the revoked token, accept the new one."""
        if access_token == "gho_revoked":
            request = httpx.Request("GET", TOKEN_URL)
            raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))
        return {"token": "copilot-api", "endpoints": {"api": "https://api.githubcopilot.com"}}

    def new_login() -> str:
        """Return a new token as a finished device flow does."""
        return "gho_new"

    def models(api_token: str, api_base: str) -> list[dict[str, Any]]:
        """Return one callable model."""
        return [{"id": "gpt-4.1"}]

    def no_config(models: list[dict[str, Any]]) -> None:
        """Skip writing config files."""

    monkeypatch.setattr(copilot_auth, "exchange_api_key", exchange)
    monkeypatch.setattr(copilot_auth, "device_flow", new_login)
    monkeypatch.setattr(copilot_auth, "list_models", models)
    monkeypatch.setattr(copilot_auth, "write_config", no_config)
    assert copilot_auth.main() == 0
    assert (token_dir / "access-token").read_text() == "gho_new"


def test_empty_model_list_still_reauthenticates(token_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token that lists no models leads to a new device flow."""
    (token_dir / "access-token").write_text("gho_no_models")

    def exchange(access_token: str) -> dict[str, Any]:
        """Accept every token."""
        return {"token": access_token, "endpoints": {"api": "https://api.githubcopilot.com"}}

    def models(api_token: str, api_base: str) -> list[dict[str, Any]]:
        """List no models for the old token and one for the new token."""
        return [] if api_token == "gho_no_models" else [{"id": "gpt-4.1"}]

    def new_login() -> str:
        """Return a new token as a finished device flow does."""
        return "gho_new"

    def no_config(models: list[dict[str, Any]]) -> None:
        """Skip writing config files."""

    monkeypatch.setattr(copilot_auth, "exchange_api_key", exchange)
    monkeypatch.setattr(copilot_auth, "list_models", models)
    monkeypatch.setattr(copilot_auth, "device_flow", new_login)
    monkeypatch.setattr(copilot_auth, "write_config", no_config)
    assert copilot_auth.main() == 0
    assert (token_dir / "access-token").read_text() == "gho_new"
