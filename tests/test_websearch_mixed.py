"""A response that mixes web search calls with other tool calls is answered with search results and the other calls."""

import asyncio
import copy
from types import SimpleNamespace
from typing import Any

import websearch_mixed


class FakeInterceptor:
    """Stands in for LiteLLM's WebSearchInterceptionLogger and records the queries it searched."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def _execute_search(self, query: str, kwargs: Any = None, rich: Any = None) -> str:
        """Return a canned result for the query."""
        self.queries.append(query)
        return f"result for {query}"

    @staticmethod
    def _rich_search_input(tool_input: object) -> None:
        """No objective or query list in these tests."""
        return None

    async def _create_empty_search_result(self) -> str:
        """The result for a call without a query."""
        return ""

    @staticmethod
    def _extract_search_text(result: object) -> str:
        """The text the follow-up would carry for one result."""
        return str(result)


class FakeResponsesResponse(SimpleNamespace):
    """A Responses API response with pydantic's model_copy."""

    def model_copy(self, update: dict[str, Any]) -> "FakeResponsesResponse":
        """A shallow copy with the updated fields."""
        return FakeResponsesResponse(**{**vars(self), **update})


SEARCH_ITEM = {"type": "function_call", "name": "litellm_web_search", "call_id": "s1", "arguments": "{}"}
EXEC_ITEM = {"type": "function_call", "name": "exec_command", "call_id": "e1", "arguments": '{"cmd": "ls"}'}
SEARCH_CALL = {"call_id": "s1", "name": "litellm_web_search", "input": {"query": "litellm release"}}


def test_mixed_responses_output_returns_the_other_calls_and_the_search_results() -> None:
    """The search runs once, its results lead the output as an assistant message, and only exec_command remains."""
    interceptor = FakeInterceptor()
    response = FakeResponsesResponse(id="r1", output=[EXEC_ITEM, SEARCH_ITEM])
    override = asyncio.run(websearch_mixed.responses_override(interceptor, response, [SEARCH_CALL], {}))
    assert interceptor.queries == ["litellm release"]
    assert [item.get("type") for item in override.output] == ["message", "function_call"]
    assert override.output[1] == EXEC_ITEM
    text = override.output[0]["content"][0]["text"]
    assert "litellm release" in text and "result for litellm release" in text
    assert override.output[0]["role"] == "assistant"
    assert response.output == [EXEC_ITEM, SEARCH_ITEM]


def test_search_only_responses_output_is_left_to_the_agentic_loop() -> None:
    """Without another call there is nothing to override, and no search runs here."""
    interceptor = FakeInterceptor()
    response = FakeResponsesResponse(id="r1", output=[SEARCH_ITEM])
    assert asyncio.run(websearch_mixed.responses_override(interceptor, response, [SEARCH_CALL], {})) is None
    assert interceptor.queries == []


def _chat_call(name: str, call_id: str) -> SimpleNamespace:
    """A chat completion tool call object."""
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments="{}"))


def _chat_response(*calls: SimpleNamespace, content: str | None = None) -> SimpleNamespace:
    """A chat completion response with one choice."""
    message = SimpleNamespace(role="assistant", content=content, tool_calls=list(calls))
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")])


def test_mixed_chat_message_keeps_the_other_calls_and_carries_the_search_results() -> None:
    """On the chat bridge the search calls leave tool_calls and their results join the message content."""
    interceptor = FakeInterceptor()
    response = _chat_response(_chat_call("exec_command", "e1"), _chat_call("litellm_web_search", "s1"), content="Doing both.")
    original = copy.deepcopy(response)
    override = asyncio.run(websearch_mixed.chat_override(interceptor, response, [SEARCH_CALL], {}))
    message = override.choices[0].message
    assert [call.function.name for call in message.tool_calls] == ["exec_command"]
    assert message.content.startswith("Doing both.")
    assert "result for litellm release" in message.content
    assert interceptor.queries == ["litellm release"]
    assert [call.function.name for call in response.choices[0].message.tool_calls] == [
        call.function.name for call in original.choices[0].message.tool_calls
    ]


def test_search_only_chat_message_is_left_to_the_agentic_loop() -> None:
    """A message whose only calls are searches is not overridden."""
    interceptor = FakeInterceptor()
    response = _chat_response(_chat_call("web_search", "s1"))
    assert asyncio.run(websearch_mixed.chat_override(interceptor, response, [SEARCH_CALL], {})) is None
    assert interceptor.queries == []
