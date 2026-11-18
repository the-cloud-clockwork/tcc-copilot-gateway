"""Answers a model response that mixes web search calls with other tool calls: the searches run
once, their results become an assistant message, and the other calls go to the client. The gateway
image installs this module next to LiteLLM, and the patched web search interception calls it.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from typing import Any

SEARCH_NAMES = frozenset({"litellm_web_search", "web_search"})


def _field(obj: Any, name: str) -> Any:
    """Read a field from a dict or an object."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _is_search_item(item: Any) -> bool:
    """Whether a Responses API output item is a web search function call."""
    return _field(item, "type") == "function_call" and _field(item, "name") in SEARCH_NAMES


def _is_search_call(call: Any) -> bool:
    """Whether a chat completion tool call is a web search."""
    return _field(_field(call, "function") or {}, "name") in SEARCH_NAMES


async def _search_text(interceptor: Any, tool_calls: list[dict], kwargs: dict) -> str:
    """Run every search call once with the interceptor's own search and return the results as one text."""
    tasks = [
        (
            interceptor._execute_search(
                call["input"]["query"], kwargs=kwargs, rich=interceptor._rich_search_input(call["input"])
            )
            if isinstance(call.get("input"), dict) and call["input"].get("query")
            else interceptor._create_empty_search_result()
        )
        for call in tool_calls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return "\n\n".join(
        f"Web search results for {(call.get('input') or {}).get('query', '')!r}:\n"
        f"{interceptor._extract_search_text(result)}"
        for call, result in zip(tool_calls, results, strict=True)
    )


async def responses_override(interceptor: Any, response: Any, tool_calls: list[dict], kwargs: dict) -> Any | None:
    """For a Responses API response with search calls and other function calls: a copy whose output is an
    assistant message with the search results followed by the other items. None for any other response."""
    output = list(_field(response, "output") or [])
    if not any(_field(item, "type") == "function_call" and not _is_search_item(item) for item in output):
        return None
    message = {
        "type": "message",
        "id": f"msg_{uuid.uuid4().hex}",
        "role": "assistant",
        "status": "completed",
        "content": [
            {"type": "output_text", "text": await _search_text(interceptor, tool_calls, kwargs), "annotations": []}
        ],
    }
    return response.model_copy(update={"output": [message, *(item for item in output if not _is_search_item(item))]})


async def chat_override(interceptor: Any, response: Any, tool_calls: list[dict], kwargs: dict) -> Any | None:
    """For a chat completion whose message has search calls and other tool calls: a copy whose message keeps
    the other calls and carries the search results in its content. None for any other response."""
    calls = _field(_field(_field(response, "choices")[0], "message"), "tool_calls") or []
    if not any(not _is_search_call(call) for call in calls):
        return None
    text = await _search_text(interceptor, tool_calls, kwargs)
    copied = copy.deepcopy(response)
    message = copied.choices[0].message
    message.tool_calls = [call for call in message.tool_calls if not _is_search_call(call)]
    message.content = "\n\n".join(part for part in (message.content, text) if part)
    return copied
