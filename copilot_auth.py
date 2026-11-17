"""Ensure a working GitHub Copilot token, then (re)generate the LiteLLM config.

Design goals (operator intent):
- Refresh the GitHub token ONLY when it can't fetch the Copilot model list.
  A valid token + subscription => no device flow, no refresh, just proceed.
- On failure, run the device flow and open the browser automatically.
- Write the token in the exact files LiteLLM reads (access-token / api-key.json),
  so LiteLLM reuses them and never re-auths on its own.
- Regenerate config.yaml from the live subscription so exposed models never drift.

Run by run.sh; not meant to be called directly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx  # shipped with litellm

# Public GitHub Copilot OAuth client id (same one every Copilot client uses).
CLIENT_ID = os.getenv("GITHUB_COPILOT_CLIENT_ID", "Iv1.b507a08c87ecfe98")
DEVICE_CODE_URL = os.getenv("GITHUB_COPILOT_DEVICE_CODE_URL", "https://github.com/login/device/code")
ACCESS_TOKEN_URL = os.getenv("GITHUB_COPILOT_ACCESS_TOKEN_URL", "https://github.com/login/oauth/access_token")
API_KEY_URL = os.getenv("GITHUB_COPILOT_API_KEY_URL", "https://api.github.com/copilot_internal/v2/token")
DEFAULT_API_BASE = os.getenv("GITHUB_COPILOT_API_BASE", "https://api.githubcopilot.com")

TOKEN_DIR = Path(
    os.path.expandvars(os.getenv("GITHUB_COPILOT_TOKEN_DIR", str(Path.home() / ".config/litellm/github_copilot")))
).expanduser()
ACCESS_TOKEN_FILE = TOKEN_DIR / "access-token"
API_KEY_FILE = TOKEN_DIR / "api-key.json"
CONFIG_FILE = Path(__file__).with_name("config.yaml")

COPILOT_HEADERS = {
    "editor-version": "vscode/1.85.1",
    "editor-plugin-version": "copilot/1.155.0",
    "Copilot-Integration-Id": "vscode-chat",
    "user-agent": "GithubCopilot/1.155.0",
    "accept": "application/json",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def read_access_token() -> str | None:
    try:
        tok = ACCESS_TOKEN_FILE.read_text().strip()
        return tok or None
    except OSError:
        return None


def exchange_api_key(access_token: str) -> dict:
    """Trade the GitHub OAuth token for a short-lived Copilot API token + endpoints."""
    headers = {**COPILOT_HEADERS, "authorization": f"token {access_token}"}
    resp = httpx.get(API_KEY_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def list_models(api_token: str, api_base: str) -> list[str]:
    headers = {**COPILOT_HEADERS, "authorization": f"Bearer {api_token}"}
    resp = httpx.get(f"{api_base.rstrip('/')}/models", headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", data if isinstance(data, list) else [])
    ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
    return sorted(set(ids))


def sanity_check(access_token: str) -> tuple[bool, list[str], dict | None]:
    """Return (ok, model_ids, api_key_info). ok=False means the token needs refresh."""
    try:
        info = exchange_api_key(access_token)
        api_base = info.get("endpoints", {}).get("api", DEFAULT_API_BASE)
        models = list_models(info["token"], api_base)
        return (bool(models), models, info)
    except Exception as e:  # noqa: BLE001 - any failure => refresh
        _log(f"  sanity check failed: {e}")
        return (False, [], None)


def open_browser(url: str) -> None:
    """Best-effort auto-open, WSL2-aware."""
    for cmd in (
        ["wslview", url],
        ["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{url}'"],
        ["xdg-open", url],
    ):
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:  # noqa: BLE001
                continue
    _log(f"  (could not auto-open a browser — open it manually: {url})")


def device_flow() -> str:
    """Run GitHub device flow, auto-open browser, poll, return the access token."""
    resp = httpx.post(
        DEVICE_CODE_URL,
        headers={**COPILOT_HEADERS, "content-type": "application/json"},
        json={"client_id": CLIENT_ID, "scope": "read:user"},
        timeout=30,
    )
    resp.raise_for_status()
    dc = resp.json()
    device_code = dc["device_code"]
    user_code = dc["user_code"]
    verify_uri = dc["verification_uri"]
    interval = int(dc.get("interval", 5))

    _log("")
    _log("  ┌─────────────────────────────────────────────┐")
    _log(f"  │  Authorize GitHub Copilot                     │")
    _log(f"  │  Code:  {user_code:<37}│")
    _log(f"  │  URL :  {verify_uri:<37}│")
    _log("  └─────────────────────────────────────────────┘")
    _log("  Opening your browser...")
    open_browser(verify_uri)

    deadline = time.time() + int(dc.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        poll = httpx.post(
            ACCESS_TOKEN_URL,
            headers={**COPILOT_HEADERS, "content-type": "application/json"},
            json={
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=30,
        ).json()
        if "access_token" in poll:
            return poll["access_token"]
        err = poll.get("error")
        if err == "slow_down":
            interval += int(poll.get("interval", 5))
        elif err not in ("authorization_pending", None):
            raise RuntimeError(f"device flow error: {poll}")
    raise TimeoutError("timed out waiting for GitHub authorization")


def save_tokens(access_token: str, api_key_info: dict | None) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    ACCESS_TOKEN_FILE.write_text(access_token)
    ACCESS_TOKEN_FILE.chmod(0o600)
    if api_key_info:
        API_KEY_FILE.write_text(json.dumps(api_key_info))
        API_KEY_FILE.chmod(0o600)


MIN_CLAUDE_VERSION = 4.6  # drop opus/sonnet below this
# Non-chat / utility / embedding models — not useful as completion models.
_DENY_SUBSTR = ("embedding", "trajectory", "picker", "mai-code")
# Dated snapshots (e.g. -2024-05-13, -0613, -0125-preview) — keep the floating alias.
_DATED = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{4})(-preview)?$")


def _excluded(m: str) -> bool:
    """Prune to a current, chat-capable set (see README 'Available models')."""
    ml = m.lower()
    if any(s in ml for s in _DENY_SUBSTR):          # embeddings, utilities
        return True
    if _DATED.search(ml):                            # dated snapshots
        return True
    cm = re.match(r"claude-(?:opus|sonnet)-(\d+(?:\.\d+)?)", ml)
    if cm and float(cm.group(1)) < MIN_CLAUDE_VERSION:   # old Claude tiers
        return True
    if re.match(r"gpt-(3\.5|4)", ml):                # all pre-5 GPT (3.5, 4, 4.1, 4o, …) — keep only gpt-5*
        return True
    if ml.startswith("gemini-2"):                    # old Gemini (keep gemini-3*)
        return True
    return False


def _has_1m(m: str) -> bool:
    """Models with a 1M-token context window (web-verified July 2026)."""
    ml = m.lower()
    cm = re.match(r"claude-(?:opus|sonnet)-(\d+(?:\.\d+)?)", ml)
    if cm and float(cm.group(1)) >= 4.6:             # opus/sonnet >=4.6 (haiku is 200k)
        return True
    if re.match(r"gpt-5\.\d+$", ml):                 # gpt-5.4, gpt-5.5 (minis/codex unconfirmed)
        return True
    if ml.startswith("gemini-3"):                    # gemini-3* = 1M
        return True
    return False


def write_config(models: list[str]) -> None:
    # Client-facing model_name, routed to the github_copilot/ provider underneath.
    # Claude Code's /model gateway-discovery only lists ids starting with
    # "claude" or "anthropic". Claude models already qualify (kept bare, e.g.
    # claude-opus-4.8); every other model (gpt/gemini/etc.) is prefixed
    # "anthropic-" so the WHOLE subscription shows in the picker. The prefix is
    # just an alias label — litellm_params.model does the real routing.
    lines = ["# AUTO-GENERATED by copilot_auth.py from your live Copilot subscription.", "model_list:"]
    for m in models:
        if _excluded(m):
            continue
        route = f"github_copilot/{m}"
        base = m if m.startswith(("claude", "anthropic")) else f"anthropic-{m}"
        # 1M models get BOTH a bare entry (routes the request Claude Code sends
        # after it strips [1m]) and a [1m] entry (so the picker offers the 1M
        # variant). Non-1M models: bare only.
        names = [base, f"{base}[1m]"] if _has_1m(m) else [base]
        responses = "codex" in m.lower() or m.lower().startswith("gpt-5")
        for name in names:
            lines.append(f"  - model_name: {name}")
            if responses:
                lines.append("    model_info:")
                lines.append("      mode: responses")
            lines.append("    litellm_params:")
            lines.append(f"      model: {route}")
    lines += ["", "general_settings:", "  master_key: os.environ/LITELLM_MASTER_KEY", ""]
    CONFIG_FILE.write_text("\n".join(lines))


def main() -> int:
    _log(f"Token dir: {TOKEN_DIR}")
    token = read_access_token()

    if token:
        _log("Existing token found — checking it can fetch Copilot models...")
        ok, models, info = sanity_check(token)
        if ok:
            _log(f"✓ Token valid. {len(models)} models available. No refresh needed.")
            save_tokens(token, info)
            write_config(models)
            return 0
        _log("✗ Token can't fetch models — re-authenticating.")
    else:
        _log("No token found — authenticating.")

    token = device_flow()
    ok, models, info = sanity_check(token)
    if not ok:
        _log("✗ Authenticated but still can't fetch models (policy/entitlement/proxy?). See README.")
        return 1
    _log(f"✓ Authenticated. {len(models)} models available.")
    save_tokens(token, info)
    write_config(models)
    return 0


if __name__ == "__main__":
    sys.exit(main())
