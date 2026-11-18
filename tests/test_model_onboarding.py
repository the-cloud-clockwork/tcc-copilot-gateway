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
    assert routes["gpt-6-luna"].responses
    assert routes["gpt-5.4-nano"].responses
    assert routes["anthropic-mai-code-2-flash"].responses
    assert not routes["claude-haiku-4.5"].responses
    assert not routes["anthropic-kimi-k3-copilot"].responses
    assert not routes["gpt-4.1"].responses


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
    assert routes["gpt-5-mini"].responses
    assert "gpt-5-mini[1m]" not in routes
    assert "anthropic-gpt-5-mini" not in routes
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


def test_claude_defaults_without_claude_use_the_best_model_claude_code_may_run() -> None:
    """A plan without any Claude model points every default at its best non-OpenAI model, and gets no default
    when only OpenAI models are left."""
    env = copilot_auth.claude_defaults(_by_id("gpt-6-luna", "gpt-5.4-nano", "kimi-k3-copilot", "claude-sonnet-5"))
    assert {v for k, v in env.items() if k.startswith("ANTHROPIC_DEFAULT_")} == {"anthropic-kimi-k3-copilot"}
    assert copilot_auth.claude_defaults(_by_id("gpt-6-luna", "gpt-5.4-nano")) == {}


def test_claude_code_gets_every_model_but_openai_and_codex_every_model_but_anthropic() -> None:
    """Anthropic models keep their id for Claude Code only, OpenAI models keep their id for Codex only, and every
    other vendor's model is served as anthropic-<id> for Claude Code and as <id> for Codex."""
    routes = _routes()
    names_by_target: dict[str, list[str]] = {}
    for name, route in routes.items():
        names_by_target.setdefault(route.target.removeprefix("github_copilot/"), []).append(name)
    assert names_by_target["claude-haiku-4.5"] == ["claude-haiku-4.5"]
    assert names_by_target["gpt-6-luna"] == ["gpt-6-luna"]
    assert names_by_target["gpt-4.1"] == ["gpt-4.1"]
    assert names_by_target["kimi-k3-copilot"] == ["anthropic-kimi-k3-copilot", "kimi-k3-copilot"]
    assert not any(name.startswith("anthropic-gpt") for name in routes)


def test_vendor_metadata_decides_which_tool_gets_a_model() -> None:
    """The /models vendor field wins over the name: an OpenAI or Azure OpenAI model gets no anthropic- name and
    an Anthropic model gets no Codex name, whatever their ids look like."""
    def model(model_id: str, vendor: str) -> dict[str, Any]:
        """A current chat model of one vendor."""
        return {"id": model_id, "vendor": vendor, "model_picker_category": "versatile",
                "capabilities": {"type": "chat"}, "policy": {"state": "enabled"}}

    routes = _routes([model("o9-mini", "OpenAI"), model("tuned-41", "Azure OpenAI"),
                      model("sonnet-next", "Anthropic"), model("gemini-4-argon", "Google")])
    assert sorted(routes) == ["anthropic-gemini-4-argon", "gemini-4-argon", "o9-mini", "sonnet-next", "tuned-41"]


def test_codex_defaults_never_pick_an_anthropic_model() -> None:
    """An Anthropic model that serves /responses is still left to Claude Code."""
    claude = {"id": "claude-next", "vendor": "Anthropic", "model_picker_category": "powerful",
              "capabilities": {"type": "chat"}, "supported_endpoints": ["/responses"]}
    assert copilot_auth.codex_defaults([claude, *_by_id("gpt-5.4-nano")])["COPILOT_CODEX_MODEL"] == "gpt-5.4-nano"


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


def test_config_drops_parameters_a_model_does_not_support() -> None:
    """LiteLLM drops any parameter a model does not support, such as Codex's reasoning effort, instead of failing."""
    config = copilot_auth.render_config([])
    assert "litellm_settings:\n  drop_params: true\n" in config


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
