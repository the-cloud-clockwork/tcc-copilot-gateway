"""Ensure a working GitHub Copilot token, then regenerate the LiteLLM config.

- Refresh the GitHub token only when it cannot fetch the Copilot model list.
- On failure, run the device flow and open the browser automatically.
- Write the token to the files LiteLLM reads (access-token, api-key.json).
- Build config.yaml and defaults.env from the live /models metadata on every
  start: which models this plan can call, which serve the Responses API, which
  accept a one million token prompt, and the newest Claude model per family.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

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
STATE_DIR = Path(os.getenv("COPILOT_STATE_DIR", str(Path(__file__).parent)))
CONFIG_FILE = STATE_DIR / "config.yaml"
DEFAULTS_FILE = STATE_DIR / "defaults.env"

COPILOT_HEADERS = {
    "editor-version": "vscode/1.85.1",
    "editor-plugin-version": "copilot/1.155.0",
    "Copilot-Integration-Id": "vscode-chat",
    "user-agent": "GithubCopilot/1.155.0",
    "accept": "application/json",
}
MODELS_HEADERS = {
    "copilot-integration-id": "vscode-chat",
    "editor-version": "vscode/1.95.0",
    "editor-plugin-version": "copilot-chat/0.26.7",
    "user-agent": "GitHubCopilotChat/0.26.7",
    "openai-intent": "conversation-panel",
    "x-github-api-version": "2025-04-01",
    "accept": "application/json",
}

ONE_MILLION = 1_000_000
SEARCH_TOOL_NAME = "searxng-search"
MIN_CLAUDE_VERSION = (4, 6)
_DENY_SUBSTR = ("embedding", "trajectory", "picker", "mai-code")
_DATED = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{4})(-preview)?$")
_CLAUDE_TIER = re.compile(r"claude-(?:opus|sonnet)-(\d+(?:\.\d+)*)")
_CLAUDE_FAMILY = re.compile(r"^claude-(opus|sonnet|haiku)-(\d+(?:\.\d+)*)$")
_FAMILY_ORDER = {
    "opus": ("opus", "sonnet", "haiku"),
    "sonnet": ("sonnet", "opus", "haiku"),
    "haiku": ("haiku", "sonnet", "opus"),
}
_CATEGORY_ORDER = ("versatile", "powerful", "lightweight")
_CODEX_CATEGORY_ORDER = ("powerful", "versatile", "lightweight")
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Route:
    """One LiteLLM model_list entry: the public model name, its Copilot target and whether it uses /responses."""

    name: str
    target: str
    responses: bool


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


def list_models(api_token: str, api_base: str) -> list[dict[str, Any]]:
    """Fetch the Copilot /models entries for this token, metadata included, with LiteLLM's request headers."""
    headers = {**MODELS_HEADERS, "authorization": f"Bearer {api_token}"}
    resp = httpx.get(f"{api_base.rstrip('/')}/models", headers=headers, timeout=30)
    resp.raise_for_status()
    return [m for m in resp.json().get("data", []) if isinstance(m, dict) and m.get("id")]


def sanity_check(access_token: str) -> tuple[bool, list[dict[str, Any]], dict | None]:
    """Return (ok, models, api_key_info). ok=False means the token needs refresh."""
    try:
        info = exchange_api_key(access_token)
        api_base = info.get("endpoints", {}).get("api", DEFAULT_API_BASE)
        models = list_models(info["token"], api_base)
        return (bool(models), models, info)
    except Exception as e:  # noqa: BLE001
        _log(f"  sanity check failed: {e}")
        return (False, [], None)


def open_browser(url: str) -> None:
    """Best-effort auto-open of the device login page on WSL, macOS and Linux desktops."""
    candidates = [
        ["wslview", url],
        ["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{url}'"],
        ["xdg-open", url],
    ]
    if sys.platform == "darwin":
        candidates.insert(0, ["open", url])
    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:  # noqa: BLE001, S112
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
    _log(f"  │  {'Authorize GitHub Copilot':<43}│")
    _log(f"  │  Code:  {user_code:<36}│")
    _log(f"  │  URL :  {verify_uri:<36}│")
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


def _version(text: str) -> tuple[int, ...]:
    """Parse a dotted version such as 4.10 into a tuple that compares numerically."""
    return tuple(int(part) for part in text.split("."))


def _excluded_by_name(model_id: str) -> bool:
    """Name rules for entries that carry no picker metadata: drop utilities, snapshots and legacy tiers."""
    ml = model_id.lower()
    if any(s in ml for s in _DENY_SUBSTR) or _DATED.search(ml):
        return True
    tier = _CLAUDE_TIER.match(ml)
    if tier and _version(tier.group(1)) < MIN_CLAUDE_VERSION:
        return True
    return bool(re.match(r"gpt-(3\.5|4)", ml)) or ml.startswith("gemini-2")


def _keep(model: dict[str, Any]) -> bool:
    """Whether a /models entry is a current chat model this plan can call."""
    capabilities = model.get("capabilities")
    if not isinstance(capabilities, dict):
        return not _excluded_by_name(model["id"])
    return (
        capabilities.get("type") == "chat"
        and model.get("model_picker_category") is not None
        and (model.get("policy") or {}).get("state") != "disabled"
    )


def _uses_responses(model: dict[str, Any]) -> bool:
    """Whether the model serves the Responses API, from supported_endpoints or, without it, its name."""
    endpoints = model.get("supported_endpoints")
    if isinstance(endpoints, list):
        return any(str(e).rstrip("/").endswith("/responses") for e in endpoints)
    ml = model["id"].lower()
    return "codex" in ml or ml.startswith("gpt-5")


def _prompt_limit(model: dict[str, Any]) -> int | None:
    """The model's published prompt limit in tokens, or its context window when no prompt limit is published."""
    limits = (model.get("capabilities") or {}).get("limits") or {}
    limit = limits.get("max_prompt_tokens", limits.get("max_context_window_tokens"))
    return limit if isinstance(limit, int) else None


def _accepts_one_million(model: dict[str, Any]) -> bool:
    """Whether the model's published prompt limit reaches one million tokens; no published limit means no."""
    limit = _prompt_limit(model)
    return limit is not None and limit >= ONE_MILLION


def _public_name(model_id: str) -> str:
    """Name clients use: Claude ids stay bare, others get the anthropic- prefix Claude Code's picker lists."""
    return model_id if model_id.startswith(("claude", "anthropic")) else f"anthropic-{model_id}"


def _kept_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate a /models payload by id and keep the callable chat models, sorted by id."""
    by_id = {m["id"]: m for m in models if m.get("id")}
    return [by_id[model_id] for model_id in sorted(by_id) if _keep(by_id[model_id])]


def plan_routes(models: list[dict[str, Any]]) -> list[Route]:
    """Turn a /models payload into LiteLLM routes.

    Each model gets its public name, a [1m] entry when it accepts a 1M token prompt,
    and, for non-Claude models, its bare Copilot id so Codex and OpenAI clients can
    use the name they already know.
    """
    routes: list[Route] = []
    for model in _kept_models(models):
        public = _public_name(model["id"])
        names = [public, f"{public}[1m]"] if _accepts_one_million(model) else [public]
        if public != model["id"]:
            names.append(model["id"])
        responses = _uses_responses(model)
        routes.extend(Route(name, f"github_copilot/{model['id']}", responses) for name in names)
    return routes


def _default_models(kept: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map each Claude Code family (opus, sonnet, haiku) to the kept model that should back it."""
    newest: dict[str, tuple[tuple[int, ...], dict[str, Any]]] = {}
    for model in kept:
        match = _CLAUDE_FAMILY.match(model["id"])
        if match:
            family, version = match.group(1), _version(match.group(2))
            if family not in newest or version > newest[family][0]:
                newest[family] = (version, model)
    if newest:
        return {family: next(newest[f][1] for f in order if f in newest) for family, order in _FAMILY_ORDER.items()}
    if not kept:
        return {}
    ranked = sorted(
        kept,
        key=lambda m: _CATEGORY_ORDER.index(m["model_picker_category"])
        if m.get("model_picker_category") in _CATEGORY_ORDER
        else len(_CATEGORY_ORDER),
    )
    return dict.fromkeys(_FAMILY_ORDER, ranked[0])


def claude_defaults(models: list[dict[str, Any]]) -> dict[str, str]:
    """Claude Code environment for this plan: the Opus, Sonnet and Haiku defaults and the auto-compact window.

    Each family gets its newest kept model; a missing family falls back to another
    Claude family, and a plan with no Claude model falls back to its first model by
    picker category. The auto-compact window is the smaller published prompt limit
    of the Opus and Sonnet defaults, kept within Claude Code's 100k to 1M range,
    because Claude Code cannot see the lower limits Copilot enforces.
    """
    chosen = _default_models(_kept_models(models))
    env = {f"ANTHROPIC_DEFAULT_{family.upper()}_MODEL": _public_name(m["id"]) for family, m in chosen.items()}
    limits = [limit for family in ("opus", "sonnet") if family in chosen if (limit := _prompt_limit(chosen[family]))]
    if limits:
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(min(max(min(limits), 100_000), ONE_MILLION))
    return env


def _padded_version(model_id: str) -> tuple[int, int, int]:
    """First dotted number in a model id, padded to three parts so 6.1 sorts above 6 and 5.6."""
    match = re.search(r"\d+(?:\.\d+)*", model_id)
    parts = [int(part) for part in match.group(0).split(".")[:3]] if match else []
    padded = (parts + [0, 0, 0])[:3]
    return (padded[0], padded[1], padded[2])


def _codex_rank(model: dict[str, Any]) -> tuple[int, bool, tuple[int, int, int]]:
    """Sort key for Codex: picker category (powerful first), codex models first, then newest version."""
    category = model.get("model_picker_category")
    rank = _CODEX_CATEGORY_ORDER.index(category) if category in _CODEX_CATEGORY_ORDER else len(_CODEX_CATEGORY_ORDER)
    major, minor, patch = _padded_version(model["id"])
    return (rank, "codex" not in model["id"].lower(), (-major, -minor, -patch))


def codex_defaults(models: list[dict[str, Any]]) -> dict[str, str]:
    """Codex environment for this plan: the best kept /responses model and its published prompt limit."""
    candidates = [model for model in _kept_models(models) if _uses_responses(model)]
    if not candidates:
        return {}
    best = min(candidates, key=_codex_rank)
    env = {"COPILOT_CODEX_MODEL": best["id"]}
    limit = _prompt_limit(best)
    if limit:
        env["COPILOT_CODEX_CONTEXT_WINDOW"] = str(limit)
    efforts = ((best.get("capabilities") or {}).get("supports") or {}).get("reasoning_effort")
    if isinstance(efforts, list) and efforts:
        ranked = sorted(efforts, key=lambda e: _EFFORT_ORDER.index(e) if e in _EFFORT_ORDER else -1)
        env["COPILOT_CODEX_REASONING_EFFORTS"] = " ".join(ranked)
    return env


def render_config(routes: list[Route]) -> str:
    """Render the LiteLLM config.yaml text for the given routes."""
    lines = ["# AUTO-GENERATED by copilot_auth.py from your live Copilot subscription.", "model_list:"]
    for route in routes:
        lines.append(f"  - model_name: {route.name}")
        if route.responses:
            lines += ["    model_info:", "      mode: responses"]
        lines += ["    litellm_params:", f"      model: {route.target}"]
    lines += [
        "",
        "litellm_settings:",
        '  callbacks: ["websearch_interception"]',
        "  websearch_interception_params:",
        '    enabled_providers: ["github_copilot"]',
        f"    search_tool_name: {SEARCH_TOOL_NAME}",
        "",
        "search_tools:",
        f"  - search_tool_name: {SEARCH_TOOL_NAME}",
        "    litellm_params:",
        "      search_provider: searxng",
        f"      api_base: {os.getenv('SEARXNG_API_BASE', 'http://searxng:8080')}",
        "",
        "general_settings:",
        "  master_key: os.environ/LITELLM_MASTER_KEY",
        "",
    ]
    return "\n".join(lines)


def write_config(models: list[dict[str, Any]]) -> None:
    """Write config.yaml with every route and defaults.env with Claude Code's default models."""
    CONFIG_FILE.write_text(render_config(plan_routes(models)))
    defaults = {**claude_defaults(models), **codex_defaults(models)}
    DEFAULTS_FILE.write_text("".join(f"{key}={value}\n" for key, value in defaults.items()))


def _report(models: list[dict[str, Any]]) -> None:
    """Log what the gateway will expose and which defaults Claude Code gets."""
    kept = _kept_models(models)
    _log(f"  exposing {len(kept)} of {len(models)} models: {', '.join(m['id'] for m in kept)}")
    for key, value in {**claude_defaults(models), **codex_defaults(models)}.items():
        _log(f"  {key}={value}")


def main() -> int:
    """Validate or refresh the Copilot token, then write config.yaml and defaults.env from the live model list."""
    _log(f"Token dir: {TOKEN_DIR}")
    token = read_access_token()

    if token:
        _log("Existing token found — checking it can fetch Copilot models...")
        ok, models, info = sanity_check(token)
        if ok:
            _log(f"✓ Token valid. {len(models)} models available. No refresh needed.")
            save_tokens(token, info)
            write_config(models)
            _report(models)
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
    _report(models)
    return 0


if __name__ == "__main__":
    sys.exit(main())
