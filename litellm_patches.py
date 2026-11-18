"""Source patches applied to the pinned LiteLLM install while the gateway image is built.

Each patch replaces one exact snippet in one installed file. The build fails when a
snippet is not found exactly once, so moving the LiteLLM pin forces a review of every
patch. Applying a patch twice is a no-op.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Patch:
    """One exact snippet replacement in a file relative to the site-packages root, with the reference that explains it."""

    path: str
    old: str
    new: str
    upstream: str


PATCHES = (
    Patch(
        path="litellm/integrations/websearch_interception/handler.py",
        old="        query: Final = get_last_user_message(cast(list[AllMessageValues], messages))\n",
        new=(
            "        query: Final = (get_last_user_message(cast(list[AllMessageValues], messages)) or \"\").removeprefix(\n"
            "            \"Perform a web search for the query:\"\n"
            "        ).strip()\n"
        ),
        upstream="https://github.com/BerriAI/litellm/pull/43458",
    ),
    Patch(
        path="litellm/proxy/common_request_processing.py",
        old=(
            "                elif select_data_generator:\n"
            "                    selected_data_generator = select_data_generator(\n"
        ),
        new=(
            "                elif select_data_generator:\n"
            "                    from litellm.responses.streaming_iterator import CachedResponsesAPIStreamingIterator\n"
            "                    from litellm.types.llms.openai import ResponsesAPIResponse\n"
            "\n"
            "                    if (\n"
            '                        route_type == "aresponses"\n'
            "                        and isinstance(response, ResponsesAPIResponse)\n"
            '                        and not self.data.get("background")\n'
            "                    ):\n"
            "                        response = CachedResponsesAPIStreamingIterator(\n"
            "                            response=response, logging_obj=logging_obj\n"
            "                        )\n"
            "                    selected_data_generator = select_data_generator(\n"
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/issues/10",
    ),
    Patch(
        path="litellm/proxy/common_utils/codex_model_catalog.py",
        old='        "supported_in_api": True,\n        "upgrade": upgrade,\n    }\n',
        new=(
            '        "supported_in_api": True,\n'
            '        "upgrade": upgrade,\n'
            '        "use_responses_lite": False,\n'
            "    }\n"
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/issues/13",
    ),
    Patch(
        path="litellm/proxy/common_utils/codex_model_catalog.py",
        old="    experimental_supported_tools: tuple[()] = ()\n    context_window: int | None\n",
        new=(
            "    experimental_supported_tools: tuple[()] = ()\n"
            '    multi_agent_version: Literal["disabled"] | None = None\n'
            "    context_window: int | None\n"
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/issues/13",
    ),
    Patch(
        path="litellm/proxy/common_utils/codex_model_catalog.py",
        old=(
            "            context_window=row.max_input_tokens,\n"
            "            base_instructions=instructions,\n"
            "        )\n"
        ),
        new=(
            "            context_window=row.max_input_tokens,\n"
            "            base_instructions=instructions,\n"
            '            multi_agent_version="disabled" if "grok" in (row.upstream_model or row.id) else None,\n'
            "        )\n"
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/issues/13",
    ),
    Patch(
        path="litellm/integrations/websearch_interception/handler.py",
        old=(
            "    ) -> AgenticLoopPlan:\n"
            '        tool_calls: Final = tools["tool_calls"]\n'
            '        response_format: Final = tools.get("response_format", "openai")\n'
        ),
        new=(
            "    ) -> AgenticLoopPlan:\n"
            '        tool_calls: Final = tools["tool_calls"]\n'
            "        import websearch_mixed\n"
            "\n"
            "        override = await websearch_mixed.chat_override(self, response, tool_calls, kwargs)\n"
            "        if override is not None:\n"
            "            return AgenticLoopPlan(run_agentic_loop=False, response_override=override, metadata={})\n"
            '        response_format: Final = tools.get("response_format", "openai")\n'
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/issues/17",
    ),
    Patch(
        path="litellm/integrations/websearch_interception/handler.py",
        old=(
            "    ) -> AgenticLoopPlan:\n"
            '        tool_calls: Final = tools["tool_calls"]\n'
            "        request_patch: Final = await self._build_responses_request_patch(\n"
        ),
        new=(
            "    ) -> AgenticLoopPlan:\n"
            '        tool_calls: Final = tools["tool_calls"]\n'
            "        import websearch_mixed\n"
            "\n"
            "        override = await websearch_mixed.responses_override(self, response, tool_calls, kwargs)\n"
            "        if override is not None:\n"
            "            return AgenticLoopPlan(run_agentic_loop=False, response_override=override, metadata={})\n"
            "        request_patch: Final = await self._build_responses_request_patch(\n"
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/issues/17",
    ),
    Patch(
        path="litellm/proxy/common_utils/codex_model_catalog.py",
        old='    return "*" not in row.id and (row.mode is None or row.mode in CODEX_CHAT_MODES)\n',
        new=(
            "    return (\n"
            '        "*" not in row.id\n'
            '        and not row.id.startswith(("claude", "anthropic-"))\n'
            "        and (row.mode is None or row.mode in CODEX_CHAT_MODES)\n"
            "    )\n"
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/pull/21",
    ),
)


def apply(patch: Patch, root: Path) -> str:
    """Apply one patch under root and say whether it was applied now or before; exit when its snippet is missing."""
    target = root / patch.path
    text = target.read_text()
    if patch.new in text:
        return "already applied"
    if text.count(patch.old) != 1:
        raise SystemExit(f"{patch.path}: snippet not found exactly once; review {patch.upstream} for this LiteLLM version")
    target.write_text(text.replace(patch.old, patch.new))
    return "applied"


def site_root() -> Path:
    """The site-packages directory that holds the installed litellm package."""
    spec = importlib.util.find_spec("litellm")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("litellm is not installed")
    return Path(next(iter(spec.submodule_search_locations))).parent


def main() -> int:
    """Apply every patch to the installed LiteLLM and print one line per patch."""
    root = site_root()
    for patch in PATCHES:
        print(f"{patch.path}: {apply(patch, root)} ({patch.upstream})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
