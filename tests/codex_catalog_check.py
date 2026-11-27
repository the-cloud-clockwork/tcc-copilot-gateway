"""Checks the Codex model catalog the patched LiteLLM in the gateway image serves; run with the image's python."""

import json
import sys
from pathlib import Path

from litellm.proxy.common_utils.codex_model_catalog import (
    CodexCatalogRow,
    bundled_codex_models,
    codex_models_response_json,
)

import copilot_auth

FIXTURE = Path(__file__).parent / "fixtures" / "copilot_models.json"
CLAUDE_CODE_ONLY = copilot_auth.CLAUDE_CODE_PREFIXES
GROK_ROUTES = {
    "grok-4.7": "grok-4.7",
    "anthropic-grok-4.7": "grok-4.7",
    "upstream-only-grok": "grok-code-fast-1",
}


def rows() -> list[CodexCatalogRow]:
    """The gateway's routes for the Copilot fixture, as copilot_auth.plan_routes builds them, plus one row per
    stock slug and the grok routes, one of them a synthetic name that carries grok only in its upstream."""
    routes = copilot_auth.plan_routes(json.loads(FIXTURE.read_text())["data"])
    served = {route.name: (route.target, "responses" if route.responses else "chat") for route in routes}
    extra = [*bundled_codex_models(), *GROK_ROUTES]
    for name in extra:
        served.setdefault(name, (f"github_copilot/{GROK_ROUTES.get(name, name)}", "responses"))
    return [
        CodexCatalogRow(id=name, mode=mode, max_input_tokens=200_000, upstream_model=target)
        for name, (target, mode) in served.items()
    ]


def problems(entries: dict[str, dict], listed: list[CodexCatalogRow]) -> list[str]:
    """Every way the catalog differs from what Codex on Copilot needs: a served model left out; an entry built
    from a stock slug whose use_responses_lite is not exactly false (Codex then sends no web_search tool) or whose
    tool_mode is not the stock one (the patches leave tool modes alone); a grok entry whose multi_agent_version is
    not "disabled" (Copilot's grok provider rejects namespace tools); any other entry with it set; an entry without
    supports_search_tool (Codex then never offers tool_search for that model)."""
    stock = bundled_codex_models()
    found = []
    for row in listed:
        entry = entries.get(row.id)
        upstream = (row.upstream_model or "").partition("/")[2]
        stock_slug = row.id if row.id in stock else upstream if upstream in stock else None
        if row.id.startswith(CLAUDE_CODE_ONLY):
            if entry is not None:
                found.append(f"{row.id}: served to Claude Code only, but listed for Codex")
        elif entry is None:
            found.append(f"{row.id}: missing from the catalog")
        elif entry.get("supports_search_tool") is not True:
            found.append(f"{row.id}: supports_search_tool is {entry.get('supports_search_tool')!r}, not True")
        elif stock_slug is not None:
            if entry.get("use_responses_lite") is not False:
                found.append(f"{row.id}: use_responses_lite is {entry.get('use_responses_lite')!r}, not False")
            if entry.get("tool_mode") != (stock[stock_slug].model_extra or {}).get("tool_mode"):
                found.append(f"{row.id}: tool_mode {entry.get('tool_mode')!r} differs from stock {stock_slug}")
        elif "grok" in upstream:
            if entry.get("multi_agent_version") != "disabled":
                found.append(f"{row.id}: multi_agent_version is {entry.get('multi_agent_version')!r}, not 'disabled'")
        elif entry.get("multi_agent_version") is not None:
            found.append(f"{row.id}: multi_agent_version is {entry.get('multi_agent_version')!r}, not None")
    return found


def main() -> int:
    """Print every problem and exit 1 when there is one."""
    listed = rows()
    body = codex_models_response_json(listed, byte_limit=None)
    entries = {entry["slug"]: entry for entry in json.loads(body.json)["models"]}
    found = problems(entries, listed)
    for line in found:
        print(line)
    print(f"{len(found)} problems in {len(entries)} catalog entries for {len(listed)} served models")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
