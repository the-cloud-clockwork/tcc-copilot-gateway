"""Checks codex_tool_search on LiteLLM's own request and response types; run with the gateway image's python."""

import asyncio
import sys

from litellm.proxy.common_utils.codex_model_catalog import bundled_codex_models
from litellm.responses.streaming_iterator import CachedResponsesAPIStreamingIterator
from litellm.types.llms.openai import ResponseFunctionToolCall, ResponsesAPIResponse

import codex_tool_search

NON_STOCK_MODEL = "gemini-3-pro"
FLAT_NAME = "mcp__probe__zebra_vault_code"

TOOL_SEARCH_OUTPUT_ITEM = {
    "type": "tool_search_output",
    "id": "tso_1",
    "call_id": "call_1",
    "tools": [
        {
            "type": "namespace",
            "name": "mcp__probe",
            "tools": [
                {
                    "type": "function",
                    "name": "zebra_vault_code",
                    "description": "Reads the vault code.",
                    "strict": False,
                    "defer_loading": True,
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }
    ],
}


def request_data() -> dict:
    """A /v1/responses request for a non-stock model carrying tool_search and its loaded tools."""
    return {
        "model": NON_STOCK_MODEL,
        "litellm_call_id": "call_1",
        "tools": [{"type": "tool_search", "description": "# Tool discovery", "parameters": {"type": "object"}}],
        "input": [
            {"type": "tool_search_call", "call_id": "call_1", "arguments": {"query": "vault code"}},
            TOOL_SEARCH_OUTPUT_ITEM,
        ],
    }


def pre_call_problems() -> list[str]:
    """What is wrong with the pre-call hook on a real catalog: it must skip every non-responses call type and
    every stock Codex slug, and rewrite tool_search for a model the stock catalog does not cover."""
    found = []
    handler = codex_tool_search.CodexToolSearchHandler()

    async def run(data: dict, call_type: str) -> dict | None:
        return await handler.async_pre_call_hook(user_api_key_dict=None, cache=None, data=data, call_type=call_type)

    if asyncio.run(run(request_data(), "acompletion")) is not None:
        found.append("pre_call: a non-responses call type was rewritten")
    stock_slug = next(iter(bundled_codex_models()))
    stock_request = {**request_data(), "model": stock_slug}
    if asyncio.run(run(stock_request, "aresponses")) is not None:
        found.append(f"pre_call: stock slug {stock_slug} was rewritten")
    rewritten = asyncio.run(run(request_data(), "aresponses"))
    if rewritten is None:
        found.append("pre_call: a non-stock model carrying tool_search was not rewritten")
        return found
    names = [tool["name"] for tool in rewritten["tools"]]
    if names != ["tool_search", FLAT_NAME]:
        found.append(f"pre_call: rewritten tools {names}")
    if codex_tool_search.peek_call("call_1") is None:
        found.append("pre_call: the flat name map was not remembered under litellm_call_id")
    codex_tool_search.forget_call("call_1")
    return found


def _function_call(name: str, call_id: str, arguments: str, namespace: str | None = None) -> ResponseFunctionToolCall:
    """A real ResponseFunctionToolCall output item, as LiteLLM would hand the hooks."""
    return ResponseFunctionToolCall(
        type="function_call", name=name, call_id=call_id, arguments=arguments, namespace=namespace
    )


def non_streaming_problems() -> list[str]:
    """What is wrong with the non-streaming hook on a real ResponsesAPIResponse: the tool_search call comes
    back as tool_search_call, and the flattened call gets its namespace back."""
    handler = codex_tool_search.CodexToolSearchHandler()
    data = request_data()
    rewritten = asyncio.run(
        handler.async_pre_call_hook(user_api_key_dict=None, cache=None, data=data, call_type="aresponses")
    )
    if rewritten is None:
        return ["non_streaming: setup rewrite failed, cannot check the response side"]
    response = ResponsesAPIResponse(
        id="resp_1",
        created_at=0,
        model=NON_STOCK_MODEL,
        object="response",
        status="completed",
        output=[
            _function_call("tool_search", "c1", '{"query": "vault code"}'),
            _function_call(FLAT_NAME, "c2", "{}"),
        ],
    )
    result = asyncio.run(
        handler.async_post_call_success_hook(data=data, user_api_key_dict=None, response=response)
    )
    found = []
    first, second = result.output[0], result.output[1]
    if getattr(first, "type", None) != "tool_search_call" or first.arguments != {"query": "vault code"}:
        found.append(f"non_streaming: tool_search item not restored, got {first}")
    if second.name != "zebra_vault_code" or second.namespace != "mcp__probe":
        found.append(f"non_streaming: flattened call not restored, got {second}")
    if codex_tool_search.peek_call("call_1") is not None:
        found.append("non_streaming: the flat name map was not forgotten after use")
    return found


class ReplayLogging:
    """The few logging calls the stream replay makes, as no-ops."""

    def __init__(self) -> None:
        self.model_call_details: dict = {}

    def __getattr__(self, name: str) -> object:
        """Every other logging method does nothing."""
        return lambda *args, **kwargs: None


def streaming_problems() -> list[str]:
    """What is wrong with the streaming hook once a ResponsesAPIResponse is replayed as an event stream, the
    same way the gateway serves a streamed Codex request."""
    handler = codex_tool_search.CodexToolSearchHandler()
    data = request_data()
    rewritten = asyncio.run(
        handler.async_pre_call_hook(user_api_key_dict=None, cache=None, data=data, call_type="aresponses")
    )
    if rewritten is None:
        return ["streaming: setup rewrite failed, cannot check the response side"]
    response = ResponsesAPIResponse(
        id="resp_2",
        created_at=0,
        model=NON_STOCK_MODEL,
        object="response",
        status="completed",
        output=[
            _function_call("tool_search", "c1", '{"query": "vault code"}'),
            _function_call(FLAT_NAME, "c2", "{}"),
        ],
    )
    source = CachedResponsesAPIStreamingIterator(response=response, logging_obj=ReplayLogging())

    async def run() -> list[object]:
        served = []
        async for chunk in handler.async_post_call_streaming_iterator_hook(
            user_api_key_dict=None, response=source, request_data=data
        ):
            served.append(chunk)
        return served

    events = asyncio.run(run())
    added_items = [getattr(event, "item", None) for event in events if getattr(event, "item", None) is not None]
    names = [getattr(item, "name", None) for item in added_items]
    found = []
    if "tool_search" in names:
        found.append(f"streaming: tool_search function call leaked through, got names {names}")
    if "zebra_vault_code" not in names:
        found.append(f"streaming: flattened call was not restored to its own name, got names {names}")
    namespaces = [getattr(item, "namespace", None) for item in added_items if getattr(item, "name", None) == "zebra_vault_code"]
    if namespaces != ["mcp__probe"]:
        found.append(f"streaming: flattened call namespace {namespaces}")
    if codex_tool_search.peek_call("call_1") is not None:
        found.append("streaming: the flat name map was not forgotten once the stream ended")
    return found


def main() -> int:
    """Print every problem and exit 1 when there is one."""
    found = pre_call_problems() + non_streaming_problems() + streaming_problems()
    for line in found:
        print(line)
    print(f"{len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
