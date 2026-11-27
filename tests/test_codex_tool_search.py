"""codex_tool_search emulates Codex's client-executed tool_search for non-stock Copilot models."""

import asyncio
from types import SimpleNamespace
from typing import Any

import codex_tool_search

STOCK_SLUGS = {"gpt-6-sol", "gpt-5.6-sol"}

TOOL_SEARCH_TOOL = {
    "type": "tool_search",
    "execution": "client",
    "description": "# Tool discovery",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}

TOOL_SEARCH_CALL_ITEM = {
    "type": "tool_search_call",
    "call_id": "call_1",
    "status": "completed",
    "execution": "client",
    "arguments": {"query": "vault code"},
}

NAMESPACE_FUNCTION = {
    "type": "function",
    "name": "zebra_vault_code",
    "description": "Reads the vault code.",
    "strict": False,
    "defer_loading": True,
    "parameters": {"type": "object", "properties": {}},
}

TOOL_SEARCH_OUTPUT_ITEM = {
    "type": "tool_search_output",
    "id": "tso_1",
    "call_id": "call_1",
    "status": "completed",
    "execution": "client",
    "tools": [{"type": "namespace", "name": "mcp__probe", "description": "Tools in mcp__probe.", "tools": [NAMESPACE_FUNCTION]}],
}


def test_request_has_tool_search_detects_the_tool_definition() -> None:
    """A tool_search tool in the request's tools list is detected."""
    assert codex_tool_search.request_has_tool_search({"tools": [TOOL_SEARCH_TOOL], "input": []})
    assert not codex_tool_search.request_has_tool_search({"tools": [{"type": "function", "name": "exec"}], "input": []})


def test_request_has_tool_search_detects_replayed_input_items() -> None:
    """A tool_search_call or tool_search_output already in the replayed input is detected without the tool."""
    assert codex_tool_search.request_has_tool_search({"tools": [], "input": [TOOL_SEARCH_CALL_ITEM]})
    assert codex_tool_search.request_has_tool_search({"tools": [], "input": [TOOL_SEARCH_OUTPUT_ITEM]})
    assert not codex_tool_search.request_has_tool_search({"tools": [], "input": [{"type": "message"}]})


def test_is_stock_codex_model_checks_the_bundled_catalog() -> None:
    """Only a slug in the stock catalog counts as stock."""
    assert codex_tool_search.is_stock_codex_model("gpt-6-sol", STOCK_SLUGS)
    assert not codex_tool_search.is_stock_codex_model("gemini-3-pro", STOCK_SLUGS)


def test_rewrite_tools_turns_tool_search_into_a_function_tool() -> None:
    """The tool_search tool becomes a plain function tool with the same description and parameters."""
    rewritten = codex_tool_search.rewrite_tools([TOOL_SEARCH_TOOL, {"type": "function", "name": "exec_command"}])
    assert rewritten[0] == {
        "type": "function",
        "name": "tool_search",
        "description": TOOL_SEARCH_TOOL["description"],
        "parameters": TOOL_SEARCH_TOOL["parameters"],
        "strict": False,
    }
    assert rewritten[1] == {"type": "function", "name": "exec_command"}


def test_flatten_loaded_tools_namespaces_and_drops_defer_loading() -> None:
    """A namespaced function flattens to namespace__name, without defer_loading, de-duplicated by flat name."""
    output_item = {
        "tools": [
            {"type": "namespace", "name": "mcp__probe", "tools": [NAMESPACE_FUNCTION, NAMESPACE_FUNCTION]},
            {"type": "function", "name": "top_level_fn", "parameters": {"type": "object", "properties": {}}},
        ]
    }
    functions = codex_tool_search.flatten_loaded_tools(output_item)
    assert [f.flat_name for f in functions] == ["mcp__probe__zebra_vault_code", "top_level_fn"]
    assert functions[0].namespace == "mcp__probe"
    assert functions[0].name == "zebra_vault_code"
    assert functions[0].definition["name"] == "mcp__probe__zebra_vault_code"
    assert "defer_loading" not in functions[0].definition
    assert functions[1].namespace is None


def test_loaded_tools_summary_names_every_flat_tool_or_says_none() -> None:
    """The summary lists every flat name, comma separated, or says none when nothing loaded."""
    functions = codex_tool_search.flatten_loaded_tools(TOOL_SEARCH_OUTPUT_ITEM)
    assert codex_tool_search.loaded_tools_summary(functions) == "Loaded tools: mcp__probe__zebra_vault_code"
    assert codex_tool_search.loaded_tools_summary([]) == "Loaded tools: none"


def test_rewrite_input_turns_tool_search_call_into_a_function_call() -> None:
    """A tool_search_call item becomes a function_call naming tool_search, arguments JSON-encoded."""
    new_items, extra_tools, flat_map = codex_tool_search.rewrite_input([TOOL_SEARCH_CALL_ITEM])
    assert new_items == [
        {"type": "function_call", "call_id": "call_1", "name": "tool_search", "arguments": '{"query": "vault code"}'}
    ]
    assert extra_tools == []
    assert flat_map == {}


def test_rewrite_input_turns_tool_search_output_into_a_function_call_output_and_flat_tools() -> None:
    """A tool_search_output item becomes a function_call_output naming the loaded tools, which join the extra
    tools and the flat name map."""
    new_items, extra_tools, flat_map = codex_tool_search.rewrite_input([TOOL_SEARCH_OUTPUT_ITEM])
    assert new_items == [
        {"type": "function_call_output", "call_id": "call_1", "output": "Loaded tools: mcp__probe__zebra_vault_code"}
    ]
    assert [tool["name"] for tool in extra_tools] == ["mcp__probe__zebra_vault_code"]
    assert flat_map == {"mcp__probe__zebra_vault_code": ("mcp__probe", "zebra_vault_code")}


def test_rewrite_input_deduplicates_tools_loaded_by_repeated_tool_search_output_items() -> None:
    """Replayed history carrying the same tool_search_output twice loads its tools only once."""
    new_items, extra_tools, flat_map = codex_tool_search.rewrite_input([TOOL_SEARCH_OUTPUT_ITEM, TOOL_SEARCH_OUTPUT_ITEM])
    assert len(new_items) == 2
    assert len(extra_tools) == 1
    assert flat_map == {"mcp__probe__zebra_vault_code": ("mcp__probe", "zebra_vault_code")}


def test_rewrite_input_flattens_an_already_namespaced_function_call() -> None:
    """A function_call item carrying a namespace from replayed history is renamed flat and loses the field."""
    item = {"type": "function_call", "name": "zebra_vault_code", "namespace": "mcp__probe", "arguments": "{}", "call_id": "call_2"}
    new_items, extra_tools, flat_map = codex_tool_search.rewrite_input([item])
    assert new_items == [{"type": "function_call", "name": "mcp__probe__zebra_vault_code", "arguments": "{}", "call_id": "call_2"}]
    assert extra_tools == []
    assert flat_map == {"mcp__probe__zebra_vault_code": ("mcp__probe", "zebra_vault_code")}


def test_rewrite_input_passes_every_other_item_through_unchanged() -> None:
    """A message item, or a function_call without a namespace, passes through untouched."""
    message = {"type": "message", "role": "user", "content": "hi"}
    plain_call = {"type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": "call_3"}
    new_items, extra_tools, flat_map = codex_tool_search.rewrite_input([message, plain_call])
    assert new_items == [message, plain_call]
    assert extra_tools == []
    assert flat_map == {}


def test_rewrite_request_merges_loaded_tools_into_the_request_tools_without_duplicates() -> None:
    """rewrite_request replaces tool_search, rewrites input, and appends newly loaded tools once."""
    data = {
        "model": "gemini-3-pro",
        "tools": [TOOL_SEARCH_TOOL],
        "input": [TOOL_SEARCH_OUTPUT_ITEM, TOOL_SEARCH_OUTPUT_ITEM],
    }
    updated, flat_map = codex_tool_search.rewrite_request(data)
    names = [tool["name"] for tool in updated["tools"]]
    assert names == ["tool_search", "mcp__probe__zebra_vault_code"]
    assert len(updated["input"]) == 2
    assert flat_map == {"mcp__probe__zebra_vault_code": ("mcp__probe", "zebra_vault_code")}
    assert data["tools"] == [TOOL_SEARCH_TOOL]


def test_parse_arguments_handles_objects_strings_and_junk() -> None:
    """parse_arguments accepts an object as-is, parses a JSON string, and falls back to {} otherwise."""
    assert codex_tool_search.parse_arguments({"query": "x"}) == {"query": "x"}
    assert codex_tool_search.parse_arguments('{"query": "x"}') == {"query": "x"}
    assert codex_tool_search.parse_arguments("") == {}
    assert codex_tool_search.parse_arguments(None) == {}
    assert codex_tool_search.parse_arguments("not json") == {}
    assert codex_tool_search.parse_arguments("[1, 2]") == {}


def test_rewrite_output_item_turns_a_tool_search_function_call_into_a_tool_search_call() -> None:
    """A function_call named tool_search becomes a tool_search_call with parsed object arguments."""
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "tool_search", "arguments": '{"query": "x"}'}
    codex_tool_search.rewrite_output_item(item, {})
    assert item["type"] == "tool_search_call"
    assert item["execution"] == "client"
    assert item["arguments"] == {"query": "x"}
    assert item["status"] == "completed"
    assert item["call_id"] == "call_1"


def test_rewrite_output_item_restores_namespace_on_a_flattened_function_call() -> None:
    """A function_call whose name is in the flat map gets its original name back, plus its namespace."""
    item = {"type": "function_call", "name": "mcp__probe__zebra_vault_code", "arguments": "{}", "call_id": "call_4"}
    flat_map = {"mcp__probe__zebra_vault_code": ("mcp__probe", "zebra_vault_code")}
    codex_tool_search.rewrite_output_item(item, flat_map)
    assert item["name"] == "zebra_vault_code"
    assert item["namespace"] == "mcp__probe"


def test_rewrite_output_item_leaves_an_unmapped_function_call_and_non_function_items_alone() -> None:
    """A plain function call not in the flat map, and a non-function_call item, are untouched."""
    plain_call = {"type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": "call_5"}
    codex_tool_search.rewrite_output_item(plain_call, {})
    assert plain_call == {"type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": "call_5"}
    message = {"type": "message", "role": "assistant"}
    codex_tool_search.rewrite_output_item(message, {})
    assert message == {"type": "message", "role": "assistant"}


def test_rewrite_output_item_works_on_a_pydantic_style_object_too() -> None:
    """Attribute-based items (as the Responses API models really are) are rewritten the same way as dicts."""
    item = SimpleNamespace(type="function_call", name="tool_search", call_id="call_1", arguments="{}", status=None)
    codex_tool_search.rewrite_output_item(item, {})
    assert item.type == "tool_search_call"
    assert item.execution == "client"
    assert item.arguments == {}
    assert item.status == "completed"


def test_rewrite_output_list_rewrites_every_item() -> None:
    """Every function_call item of an output list is rewritten in place; other items are left alone."""
    output: list[Any] = [
        {"type": "message", "role": "assistant"},
        {"type": "function_call", "name": "tool_search", "call_id": "call_1", "arguments": "{}"},
    ]
    codex_tool_search.rewrite_output_list(output, {})
    assert output[0]["type"] == "message"
    assert output[1]["type"] == "tool_search_call"


def test_rewrite_stream_chunk_rewrites_added_and_done_items() -> None:
    """An output_item.added or output_item.done event's item is rewritten in place."""
    item = {"type": "function_call", "name": "tool_search", "call_id": "call_1", "arguments": "{}"}
    chunk = {"type": "response.output_item.added", "item": item}
    codex_tool_search.rewrite_stream_chunk(chunk, {})
    assert item["type"] == "tool_search_call"


def test_rewrite_stream_chunk_rewrites_the_completed_response_output() -> None:
    """A response.completed event's response.output list is rewritten in place."""
    inner_item = {"type": "function_call", "name": "tool_search", "call_id": "call_1", "arguments": "{}"}
    chunk = {"type": "response.completed", "response": {"output": [inner_item]}}
    codex_tool_search.rewrite_stream_chunk(chunk, {})
    assert inner_item["type"] == "tool_search_call"


def test_rewrite_stream_chunk_leaves_every_other_event_untouched() -> None:
    """A delta or lifecycle event, which carries no item, is left exactly as it was."""
    chunk = {"type": "response.output_text.delta", "delta": "hi"}
    before = dict(chunk)
    codex_tool_search.rewrite_stream_chunk(chunk, {})
    assert chunk == before


def test_pending_call_state_round_trips_and_forgets() -> None:
    """A remembered call is found by peek, then gone after forget; an unknown call id finds nothing."""
    codex_tool_search.remember_call("call_x", {"a": (None, "a")})
    assert codex_tool_search.peek_call("call_x") == {"a": (None, "a")}
    codex_tool_search.forget_call("call_x")
    assert codex_tool_search.peek_call("call_x") is None
    assert codex_tool_search.peek_call(None) is None
    assert codex_tool_search.peek_call("never-remembered") is None


def test_pending_call_state_evicts_the_oldest_entry_once_the_cap_is_exceeded() -> None:
    """Remembering past the bound drops the least recently touched call id, not a newer one."""
    cap = codex_tool_search.MAX_PENDING_CALLS
    for index in range(cap + 1):
        codex_tool_search.remember_call(f"evict-{index}", {})
    assert codex_tool_search.peek_call("evict-0") is None
    assert codex_tool_search.peek_call(f"evict-{cap}") == {}
    for index in range(1, cap + 1):
        codex_tool_search.forget_call(f"evict-{index}")


async def _collect(chunks: list[Any], call_id: str) -> list[Any]:
    """Drain the handler's streaming hook over a fake chunk list and return what it yielded."""

    async def source() -> Any:
        for chunk in chunks:
            yield chunk

    handler = codex_tool_search.CodexToolSearchHandler()
    served = []
    async for chunk in handler.async_post_call_streaming_iterator_hook(
        user_api_key_dict=None, response=source(), request_data={"litellm_call_id": call_id}
    ):
        served.append(chunk)
    return served


def test_streaming_hook_rewrites_chunks_of_a_remembered_call_and_then_forgets_it() -> None:
    """Every chunk of a remembered call is rewritten, and the call is forgotten once the stream ends."""
    codex_tool_search.remember_call("call_stream_rewritten", {})
    item = {"type": "function_call", "name": "tool_search", "call_id": "call_1", "arguments": "{}"}
    chunks = [
        {"type": "response.output_item.added", "item": item},
        {"type": "response.output_item.done", "item": item},
        {"type": "response.output_text.delta"},
    ]
    served = asyncio.run(_collect(chunks, "call_stream_rewritten"))
    assert served[0]["item"]["type"] == "tool_search_call"
    assert served[1]["item"]["type"] == "tool_search_call"
    assert served[2] == {"type": "response.output_text.delta"}
    assert codex_tool_search.peek_call("call_stream_rewritten") is None


def test_streaming_hook_passes_through_unchanged_for_a_call_it_never_rewrote() -> None:
    """A call id the pre-call hook never remembered streams through untouched."""
    assert codex_tool_search.peek_call("call_stream_untouched") is None
    item = {"type": "function_call", "name": "tool_search", "call_id": "call_1", "arguments": "{}"}
    chunks = [{"type": "response.output_item.added", "item": item}]
    served = asyncio.run(_collect(chunks, "call_stream_untouched"))
    assert served[0]["item"]["type"] == "function_call"


def test_success_hook_rewrites_the_output_and_forgets_the_call() -> None:
    """The non-streaming hook rewrites a remembered call's output list and then forgets it."""
    codex_tool_search.remember_call("call_success", {"mcp__probe__zebra_vault_code": ("mcp__probe", "zebra_vault_code")})
    output_item = {"type": "function_call", "name": "mcp__probe__zebra_vault_code", "arguments": "{}", "call_id": "c1"}
    response = {"output": [output_item]}
    handler = codex_tool_search.CodexToolSearchHandler()
    result = asyncio.run(
        handler.async_post_call_success_hook(data={"litellm_call_id": "call_success"}, user_api_key_dict=None, response=response)
    )
    assert result["output"][0]["name"] == "zebra_vault_code"
    assert result["output"][0]["namespace"] == "mcp__probe"
    assert codex_tool_search.peek_call("call_success") is None


def test_success_hook_leaves_an_unremembered_call_untouched() -> None:
    """A call the pre-call hook never rewrote is returned exactly as it came in."""
    response = {"output": [{"type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": "c2"}]}
    handler = codex_tool_search.CodexToolSearchHandler()
    result = asyncio.run(
        handler.async_post_call_success_hook(data={"litellm_call_id": "call_never"}, user_api_key_dict=None, response=response)
    )
    assert result is response


def test_pre_call_hook_skips_every_call_type_but_the_responses_one() -> None:
    """A /v1/chat/completions call, or any other call type, is never touched."""
    handler = codex_tool_search.CodexToolSearchHandler()
    data = {"model": "gemini-3-pro", "tools": [TOOL_SEARCH_TOOL], "input": []}
    result = asyncio.run(handler.async_pre_call_hook(user_api_key_dict=None, cache=None, data=data, call_type="acompletion"))
    assert result is None
