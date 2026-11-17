"""Model onboarding: a Copilot /models payload becomes LiteLLM routes and Claude Code defaults."""

import json
from pathlib import Path
from typing import Any

import pytest

import copilot_auth

FIXTURE: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "copilot_models.json").read_text()
)["data"]


def _by_id(*model_ids: str) -> list[dict[str, Any]]:
    """Return the fixture entries with the given ids."""
    return [model for model in FIXTURE if model["id"] in model_ids]


def _routes(models: list[dict[str, Any]] = FIXTURE) -> dict[str, copilot_auth.Route]:
    """Return the planned routes for a payload, keyed by public model name."""
    return {route.name: route for route in copilot_auth.plan_routes(models)}


def test_only_current_callable_chat_models_are_exposed() -> None:
    """Disabled, non-picker, non-chat and legacy entries are dropped; current enabled models stay."""
    targets = {route.target.removeprefix("github_copilot/") for route in _routes().values()}
    assert targets == {
        "claude-haiku-4.5",
        "claude-opus-4.8",
        "claude-opus-4.10",
        "claude-sonnet-5.5",
        "gpt-6-luna",
        "gpt-5.4-nano",
        "gpt-4.1",
        "kimi-k3-copilot",
        "mai-code-2-flash",
        "gpt-5-mini",
    }


def test_responses_mode_follows_supported_endpoints() -> None:
    """Models that serve /responses route in responses mode; chat-only and unlisted endpoints do not."""
    routes = _routes()
    assert routes["anthropic-gpt-6-luna"].responses
    assert routes["anthropic-gpt-5.4-nano"].responses
    assert routes["anthropic-mai-code-2-flash"].responses
    assert not routes["claude-haiku-4.5"].responses
    assert not routes["anthropic-kimi-k3-copilot"].responses
    assert not routes["anthropic-gpt-4.1"].responses


def test_one_million_entries_need_a_one_million_token_prompt_limit() -> None:
    """Only models whose prompt limit reaches one million tokens get a [1m] entry."""
    routes = _routes()
    assert routes["claude-opus-4.8[1m]"].target == "github_copilot/claude-opus-4.8"
    assert "claude-opus-4.10[1m]" not in routes
    assert "anthropic-kimi-k3-copilot[1m]" not in routes
    assert "anthropic-mai-code-2-flash[1m]" not in routes


def test_models_without_metadata_use_name_rules() -> None:
    """Entries that carry only an id fall back to the name-based rules."""
    routes = _routes()
    assert routes["anthropic-gpt-5-mini"].responses
    assert "anthropic-gpt-5-mini[1m]" not in routes
    assert "claude-sonnet-4.5" not in routes


def test_claude_defaults_pick_newest_model_per_family() -> None:
    """The newest Opus, Sonnet and Haiku become the defaults, comparing versions numerically."""
    assert copilot_auth.claude_defaults(FIXTURE) == {
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4.10",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5.5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4.5",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
    }


def test_claude_defaults_fall_back_to_another_claude_family() -> None:
    """A plan without Opus or Sonnet still gets every default, from the Claude models it has."""
    models = _by_id("claude-haiku-4.5", "claude-sonnet-5", "gpt-6-luna")
    assert copilot_auth.claude_defaults(models) == {
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-haiku-4.5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-haiku-4.5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4.5",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "128000",
    }


def test_claude_defaults_without_claude_use_the_best_other_model() -> None:
    """A plan without any Claude model points every default at its versatile model."""
    env = copilot_auth.claude_defaults(_by_id("gpt-6-luna", "gpt-5.4-nano", "claude-sonnet-5"))
    assert {v for k, v in env.items() if k.startswith("ANTHROPIC_DEFAULT_")} == {"anthropic-gpt-5.4-nano"}
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "272000"


def test_non_claude_models_also_route_by_their_own_id() -> None:
    """Codex and OpenAI clients reach non-Claude models by their Copilot id as well as the anthropic- name."""
    routes = _routes()
    assert routes["gpt-6-luna"].target == "github_copilot/gpt-6-luna"
    assert routes["gpt-6-luna"].responses
    assert routes["kimi-k3-copilot"].target == "github_copilot/kimi-k3-copilot"
    assert [name for name in routes if routes[name].target == "github_copilot/claude-haiku-4.5"] == ["claude-haiku-4.5"]


def test_codex_defaults_pick_the_best_responses_model() -> None:
    """Codex gets the highest picker category among /responses models, then codex models, then the newest."""
    assert copilot_auth.codex_defaults(FIXTURE) == {
        "COPILOT_CODEX_MODEL": "gpt-5.4-nano",
        "COPILOT_CODEX_CONTEXT_WINDOW": "272000",
    }


def test_codex_defaults_prefer_newer_versions_within_a_category() -> None:
    """Within one picker category, gpt-6.1 outranks gpt-6 and gpt-5.6."""
    models = [
        {"id": f"gpt-{version}", "model_picker_category": "powerful", "supported_endpoints": ["/responses"],
         "capabilities": {"type": "chat", "limits": {"max_prompt_tokens": 272000}}}
        for version in ("5.6", "6.1", "6")
    ]
    assert copilot_auth.codex_defaults(models)["COPILOT_CODEX_MODEL"] == "gpt-6.1"


def test_codex_defaults_list_supported_reasoning_efforts_lowest_first() -> None:
    """Codex learns which reasoning efforts its model accepts, ordered so the last one is the highest."""
    model = {
        "id": "gpt-6-luna",
        "model_picker_category": "lightweight",
        "supported_endpoints": ["/responses"],
        "capabilities": {
            "type": "chat",
            "limits": {"max_prompt_tokens": 272000},
            "supports": {"reasoning_effort": ["max", "none", "high", "low", "xhigh", "medium"]},
        },
    }
    env = copilot_auth.codex_defaults([model])
    assert env["COPILOT_CODEX_REASONING_EFFORTS"] == "none low medium high xhigh max"


def test_codex_defaults_empty_without_a_responses_model() -> None:
    """A plan with no /responses model leaves Codex on its own configuration."""
    assert copilot_auth.codex_defaults(_by_id("claude-haiku-4.5", "kimi-k3-copilot")) == {}


def test_auto_compact_window_stays_within_claude_code_range() -> None:
    """A one million token prompt limit sets the window to Claude Code's 1M maximum."""
    env = copilot_auth.claude_defaults(_by_id("claude-opus-4.8"))
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "1000000"


def test_config_serves_native_web_search_from_searxng() -> None:
    """Claude Code's native web_search on Copilot models is intercepted and answered by the bundled SearXNG."""
    config = copilot_auth.render_config([])
    assert '  callbacks: ["websearch_interception"]\n' in config
    assert '    enabled_providers: ["github_copilot"]\n' in config
    assert "search_tools:\n  - search_tool_name: searxng-search\n" in config
    assert "      search_provider: searxng\n      api_base: http://searxng:8080\n" in config


def test_write_config_writes_config_and_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """write_config writes config.yaml with every route and defaults.env with the family defaults."""
    monkeypatch.setattr(copilot_auth, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(copilot_auth, "DEFAULTS_FILE", tmp_path / "defaults.env")
    copilot_auth.write_config(FIXTURE)
    config = (tmp_path / "config.yaml").read_text()
    assert "  - model_name: claude-opus-4.8[1m]\n" in config
    assert "      model: github_copilot/gpt-6-luna\n" in config
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in config
    defaults = (tmp_path / "defaults.env").read_text().splitlines()
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-5.5" in defaults
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000" in defaults
    assert "COPILOT_CODEX_MODEL=gpt-5.4-nano" in defaults
    assert "  - model_name: gpt-6-luna\n" in config
