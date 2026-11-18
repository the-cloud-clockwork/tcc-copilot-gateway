"""Checks the Codex model catalog the patched LiteLLM in the gateway image serves; run with the image's python."""

import json
import sys

from litellm.proxy.common_utils.codex_model_catalog import CodexCatalogRow, codex_models_response_json

MODELS = ("gpt-6-sol", "gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-luna", "grok-4.7", "anthropic-grok-4.7", "gpt-4.1")


def catalog() -> dict[str, dict]:
    """The catalog entries for MODELS by slug, each routed to its Copilot upstream."""
    rows = [
        CodexCatalogRow(
            id=name,
            mode="responses",
            max_input_tokens=200_000,
            upstream_model=f"github_copilot/{name.removeprefix('anthropic-')}",
        )
        for name in MODELS
    ]
    return {entry["slug"]: entry for entry in json.loads(codex_models_response_json(rows).json)["models"]}


def problems(entries: dict[str, dict]) -> list[str]:
    """What breaks Codex on Copilot in these entries: tools sent outside the request's tool list, or sub-agent
    namespace tools for a model whose provider rejects them."""
    found = [f"{slug}: use_responses_lite is on" for slug, entry in entries.items() if entry.get("use_responses_lite")]
    found += [
        f"{slug}: multi_agent_version is {entry.get('multi_agent_version')!r}, not 'disabled'"
        for slug, entry in entries.items()
        if "grok" in slug and entry.get("multi_agent_version") != "disabled"
    ]
    found += [
        f"{slug}: multi_agent_version is {entry.get('multi_agent_version')!r}"
        for slug, entry in entries.items()
        if "grok" not in slug and entry.get("multi_agent_version") == "disabled"
    ]
    if entries["gpt-6-sol"].get("tool_mode") != "code_mode_only":
        found.append("gpt-6-sol: lost its stock tool_mode code_mode_only")
    return found


def main() -> int:
    """Print every problem and exit 1 when there is one."""
    found = problems(catalog())
    for line in found:
        print(line)
    print(f"{len(found)} problems in {len(MODELS)} catalog entries")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
