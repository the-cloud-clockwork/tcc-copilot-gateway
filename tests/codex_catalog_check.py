"""Checks the Codex model catalog the patched LiteLLM in the gateway image serves; run with the image's python."""

import json
import sys

from litellm.proxy.common_utils.codex_model_catalog import (
    CodexCatalogRow,
    bundled_codex_models,
    codex_models_response_json,
)

GROK_UPSTREAMS = {"grok-4.7": "grok-4.7", "anthropic-grok-4.7": "grok-4.7", "fast-coder": "grok-code-fast-1"}
OTHER_FALLBACKS = ("gpt-4.1", "claude-haiku-4.5")


def rows() -> list[CodexCatalogRow]:
    """One row per stock slug, per grok route (one named only by its upstream) and per other fallback model,
    each routed to `github_copilot/<id>` as copilot_auth.py routes it."""
    upstreams = {slug: slug for slug in bundled_codex_models()} | GROK_UPSTREAMS | {m: m for m in OTHER_FALLBACKS}
    return [
        CodexCatalogRow(
            id=name, mode="responses", max_input_tokens=200_000, upstream_model=f"github_copilot/{upstream}"
        )
        for name, upstream in upstreams.items()
    ]


def problems(entries: dict[str, dict], served: list[str]) -> list[str]:
    """Every way the catalog differs from what Codex on Copilot needs: a served model left out; a stock entry
    whose use_responses_lite is not exactly false (Codex then sends no web_search tool) or whose tool_mode is not
    the stock one (the patches leave tool modes alone); a grok entry whose multi_agent_version is not "disabled"
    (Copilot's grok provider rejects namespace tools); any other fallback entry with multi_agent_version set."""
    stock = bundled_codex_models()
    found = [f"{name}: missing from the catalog" for name in served if name not in entries]
    for slug, entry in entries.items():
        if slug in stock:
            if entry.get("use_responses_lite") is not False:
                found.append(f"{slug}: use_responses_lite is {entry.get('use_responses_lite')!r}, not False")
            if entry.get("tool_mode") != (stock[slug].model_extra or {}).get("tool_mode"):
                found.append(f"{slug}: tool_mode {entry.get('tool_mode')!r} differs from the stock entry")
        elif slug in GROK_UPSTREAMS:
            if entry.get("multi_agent_version") != "disabled":
                found.append(f"{slug}: multi_agent_version is {entry.get('multi_agent_version')!r}, not 'disabled'")
        elif entry.get("multi_agent_version") is not None:
            found.append(f"{slug}: multi_agent_version is {entry.get('multi_agent_version')!r}, not None")
    return found


def main() -> int:
    """Print every problem and exit 1 when there is one."""
    served = [row.id for row in rows()]
    body = codex_models_response_json(rows(), byte_limit=None)
    entries = {entry["slug"]: entry for entry in json.loads(body.json)["models"]}
    found = problems(entries, served)
    for line in found:
        print(line)
    print(f"{len(found)} problems in {len(entries)} catalog entries for {len(served)} served models")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
