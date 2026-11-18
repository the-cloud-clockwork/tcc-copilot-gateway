"""Checks websearch_mixed on LiteLLM's own response types; run with the gateway image's python."""

import asyncio
import json
import sys

import websearch_mixed
from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import ModelResponse

SEARCH_CALLS = [{"call_id": "s1", "input": {"query": "litellm release"}}]
SEARCH_ARGUMENTS = json.dumps({"query": "litellm release"})


class Interceptor:
    """Stands in for the web search interception logger with a canned search."""

    async def _execute_search(self, query: str, kwargs: object = None, rich: object = None) -> str:
        """Return a canned result."""
        return f"result for {query}"

    @staticmethod
    def _rich_search_input(tool_input: object) -> None:
        """No objective or query list."""
        return None

    async def _create_empty_search_result(self) -> str:
        """The result for a call without a query."""
        return ""

    @staticmethod
    def _extract_search_text(result: object) -> str:
        """The result as text."""
        return str(result)


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


def main() -> int:
    """Print every problem and exit 1 when there is one."""
    found = responses_problems() + chat_problems()
    for line in found:
        print(line)
    print(f"{len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
