"""Answers a model response that mixes web search calls with other tool calls: the searches run
once, their results become an assistant message, and the other calls go to the client. The gateway
image installs this module next to LiteLLM, and the patched web search interception calls it.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from typing import Any


def _field(obj: Any, name: str) -> Any:
    """Read a field from a dict or an object."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _search_ids(tool_calls: list[dict]) -> set[str]:
    """The call ids of the search calls LiteLLM detected in the response."""
    return {call.get("call_id") or call.get("id") for call in tool_calls}


async def _search_text(interceptor: Any, tool_calls: list[dict], kwargs: dict) -> str:
    """Run every search call once with the interceptor's own search and return the results as one text."""
    queries = [interceptor._tool_call_query(call) for call in tool_calls]
    tasks = [
        (
            interceptor._execute_search(query, kwargs=kwargs, rich=interceptor._rich_search_input(call.get("input")))
            if query
            else interceptor._create_empty_search_result()
        )
        for call, query in zip(tool_calls, queries, strict=True)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return "\n\n".join(
        f"Web search results for {query!r}:\n{interceptor._extract_search_text(result)}"
        for query, result in zip(queries, results, strict=True)
    )


async def responses_override(interceptor: Any, response: Any, tool_calls: list[dict], kwargs: dict) -> Any | None:
    """For a Responses API response with search calls and other function calls: a copy whose output is an
    assistant message with the search results followed by the other items. None for any other response."""
    ids = _search_ids(tool_calls)
    output = list(_field(response, "output") or [])
    if not any(_field(item, "type") == "function_call" and _field(item, "call_id") not in ids for item in output):
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
    kept = [item for item in output if _field(item, "type") != "function_call" or _field(item, "call_id") not in ids]
    return response.model_copy(update={"output": [message, *kept]})


async def chat_override(interceptor: Any, response: Any, tool_calls: list[dict], kwargs: dict) -> Any | None:
    """For a chat completion whose message has search calls and other tool calls: a copy whose message keeps
    the other calls and carries the search results in its content. None for any other response."""
    ids = _search_ids(tool_calls)
    calls = _field(_field(_field(response, "choices")[0], "message"), "tool_calls") or []
    if not any(_field(call, "id") not in ids for call in calls):
        return None
    text = await _search_text(interceptor, tool_calls, kwargs)
    copied = copy.deepcopy(response)
    message = copied.choices[0].message
    message.tool_calls = [call for call in message.tool_calls if _field(call, "id") not in ids]
    message.content = "\n\n".join(part for part in (message.content, text) if part)
    return copied
