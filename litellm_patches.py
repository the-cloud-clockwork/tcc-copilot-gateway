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
            '                    if route_type == "aresponses" and isinstance(response, ResponsesAPIResponse):\n'
            "                        response = CachedResponsesAPIStreamingIterator(\n"
            "                            response=response, logging_obj=logging_obj\n"
            "                        )\n"
            "                    selected_data_generator = select_data_generator(\n"
        ),
        upstream="https://github.com/the-cloud-clockwork/tcc-copilot-gateway/issues/10",
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
