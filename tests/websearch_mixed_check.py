"""Checks websearch_mixed on LiteLLM's own response types; run with the gateway image's python."""

import asyncio
import json
import sys

from litellm.integrations.websearch_interception.handler import WebSearchInterceptionLogger
from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import ModelResponse

import websearch_mixed

SEARCH_CALLS = [{"call_id": "s1", "input": {"query": "litellm release"}}]
SEARCH_ARGUMENTS = json.dumps({"query": "litellm release"})


class Interceptor:
    """The real interception logger's helpers, with a canned search in place of the network call."""

    _tool_call_query = staticmethod(WebSearchInterceptionLogger._tool_call_query)
    _rich_search_input = staticmethod(WebSearchInterceptionLogger._rich_search_input)
    _extract_search_text = staticmethod(WebSearchInterceptionLogger._extract_search_text)
    _create_empty_search_result = WebSearchInterceptionLogger._create_empty_search_result

    async def _execute_search(self, query: str, kwargs: object = None, rich: object = None) -> tuple[str, None]:
        """Return a canned result as LiteLLM's (text, SearchResponse) pair."""
        return f"result for {query}", None


def responses_problems() -> list[str]:
    """What is wrong with the Responses API override of a mixed response."""
    response = ResponsesAPIResponse(
        id="resp_1",
        created_at=0,
        model="m",
        object="response",
        status="completed",
        output=[
            {"type": "function_call", "id": "fc_1", "call_id": "e1", "name": "exec_command", "arguments": "{}"},
            {"type": "function_call", "id": "fc_2", "call_id": "s1", "name": "litellm_web_search",
             "arguments": SEARCH_ARGUMENTS},
        ],
    )
    override = asyncio.run(websearch_mixed.responses_override(Interceptor(), response, SEARCH_CALLS, {}))
    items = [(item.get("type"), item.get("name")) for item in json.loads(override.model_dump_json())["output"]]
    found = [] if isinstance(override, ResponsesAPIResponse) else [f"responses: got {type(override).__name__}"]
    if items != [("message", None), ("function_call", "exec_command")]:
        found.append(f"responses: output items {items}")
    if len(response.output) != 2:
        found.append("responses: the original response changed")
    return found


def chat_problems() -> list[str]:
    """What is wrong with the chat completion override of a mixed response."""
    calls = [
        {"id": "e1", "type": "function", "function": {"name": "exec_command", "arguments": "{}"}},
        {"id": "s1", "type": "function", "function": {"name": "litellm_web_search", "arguments": SEARCH_ARGUMENTS}},
    ]
    response = ModelResponse(
        choices=[{"index": 0, "finish_reason": "tool_calls",
                  "message": {"role": "assistant", "content": None, "tool_calls": calls}}]
    )
    override = asyncio.run(websearch_mixed.chat_override(Interceptor(), response, SEARCH_CALLS, {}))
    message = override.choices[0].message
    found = [] if isinstance(override, ModelResponse) else [f"chat: got {type(override).__name__}"]
    if [call.function.name for call in message.tool_calls] != ["exec_command"]:
        found.append(f"chat: tool calls {[call.function.name for call in message.tool_calls]}")
    if "result for litellm release" not in (message.content or ""):
        found.append("chat: the search results are missing from the message")
    if len(response.choices[0].message.tool_calls) != 2:
        found.append("chat: the original response changed")
    return found


def builder_problems() -> list[str]:
    """What is wrong with the patched plan builders on mixed responses: each must hand the response to
    websearch_mixed and return its override without running LiteLLM's agentic loop."""
    responses = ResponsesAPIResponse(
        id="resp_2",
        created_at=0,
        model="m",
        object="response",
        status="completed",
        output=[
            {"type": "function_call", "id": "fc_3", "call_id": "e1", "name": "exec_command", "arguments": "{}"},
            {"type": "function_call", "id": "fc_4", "call_id": "s1", "name": "litellm_web_search",
             "arguments": SEARCH_ARGUMENTS},
        ],
    )
    chat = ModelResponse(
        choices=[{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                  "tool_calls": [
                      {"id": "e1", "type": "function", "function": {"name": "exec_command", "arguments": "{}"}},
                      {"id": "s1", "type": "function",
                       "function": {"name": "litellm_web_search", "arguments": SEARCH_ARGUMENTS}},
                  ]}}]
    )
    found = []
    for name, builder, response in (
        ("responses builder", WebSearchInterceptionLogger.async_build_responses_agentic_loop_plan, responses),
        ("chat builder", WebSearchInterceptionLogger.async_build_chat_completion_agentic_loop_plan, chat),
    ):
        plan = asyncio.run(
            builder(Interceptor(), tools={"tool_calls": SEARCH_CALLS}, model="m", messages=[], response=response,
                    optional_params={}, logging_obj=None, stream=False, kwargs={})
        )
        if plan.run_agentic_loop or plan.response_override is None:
            found.append(f"{name}: no override, the agentic loop would run")
        elif "litellm_web_search" in plan.response_override.model_dump_json():
            found.append(f"{name}: the override still holds litellm_web_search")
    return found


def main() -> int:
    """Print every problem and exit 1 when there is one."""
    found = responses_problems() + chat_problems() + builder_problems()
    for line in found:
        print(line)
    print(f"{len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
