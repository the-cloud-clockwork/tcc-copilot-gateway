"""Emulates Codex's client-executed tool_search with plain function tools for every Copilot model that is not
one of Codex's own bundled catalog slugs. Copilot rejects tool_search outright for some models and LiteLLM's
Responses-to-chat bridge drops it for chat-mode models, so this callback rewrites the request and replays
Codex's own tool_search_call/tool_search_output wire shapes back on the response.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:
    CustomLogger = object

TOOL_SEARCH_NAME = "tool_search"
RESPONSES_CALL_TYPE = "aresponses"
MAX_PENDING_CALLS = 512

FlatMap = dict[str, tuple[str | None, str]]


@dataclass(frozen=True)
class FlatFunction:
    """One function tool flattened out of a tool_search_output namespace, or kept as a top-level function."""

    flat_name: str
    namespace: str | None
    name: str
    definition: dict[str, Any]


def _get(item: Any, key: str, default: Any = None) -> Any:
    """Read a field from a request dict or a response pydantic item."""
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _set(item: Any, key: str, value: Any) -> None:
    """Write a field on a request dict or a response pydantic item."""
    if isinstance(item, dict):
        item[key] = value
    else:
        setattr(item, key, value)


def is_stock_codex_model(model: str | None, stock_slugs: Any) -> bool:
    """Whether the model is one of Codex's own bundled catalog slugs, which already drives tool_search itself."""
    return model in stock_slugs


def request_has_tool_search(data: dict[str, Any]) -> bool:
    """Whether the request's tools or replayed input carry a tool_search tool or call."""
    if any(_get(tool, "type") == "tool_search" for tool in data.get("tools") or []):
        return True
    return any(_get(item, "type") in ("tool_search_call", "tool_search_output") for item in data.get("input") or [])


def rewrite_tool_search_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """The tool_search tool definition as a plain function tool Copilot accepts."""
    return {
        "type": "function",
        "name": TOOL_SEARCH_NAME,
        "description": tool.get("description"),
        "parameters": tool.get("parameters"),
        "strict": False,
    }


def rewrite_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool, with tool_search replaced by its function form."""
    return [rewrite_tool_search_tool(tool) if tool.get("type") == "tool_search" else tool for tool in tools]


def _function_definition(entry: dict[str, Any], flat_name: str) -> dict[str, Any]:
    """A flat function tool definition for one loaded tool, named by its flat name and without defer_loading."""
    definition = {key: value for key, value in entry.items() if key != "defer_loading"}
    definition["name"] = flat_name
    return definition


def flatten_loaded_tools(output_item: dict[str, Any]) -> list[FlatFunction]:
    """Every function a tool_search_output item loaded: namespaced tools flattened to namespace__name, a
    top-level function kept under its own name, de-duplicated by flat name."""
    flattened: list[FlatFunction] = []
    seen: set[str] = set()
    for entry in output_item.get("tools") or []:
        if entry.get("type") == "namespace":
            namespace = entry.get("name")
            for function in entry.get("tools") or []:
                name = function.get("name")
                flat_name = f"{namespace}__{name}"
                if flat_name in seen:
                    continue
                seen.add(flat_name)
                flattened.append(FlatFunction(flat_name, namespace, name, _function_definition(function, flat_name)))
        else:
            name = entry.get("name")
            if name in seen:
                continue
            seen.add(name)
            flattened.append(FlatFunction(name, None, name, _function_definition(entry, name)))
    return flattened


def loaded_tools_summary(functions: list[FlatFunction]) -> str:
    """The text Codex receives in place of the tool_search_output item's structured tool list."""
    return "Loaded tools: " + (", ".join(function.flat_name for function in functions) or "none")


def rewrite_input(input_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], FlatMap]:
    """The input array with tool_search_call/output and already-namespaced function_call items rewritten to
    plain function_call/function_call_output items, the flat function tools they loaded, and the flat name map
    the response side needs to put namespaces back on the model's own function calls."""
    new_items: list[dict[str, Any]] = []
    extra_tools: list[dict[str, Any]] = []
    flat_map: FlatMap = {}
    added: set[str] = set()
    for item in input_items:
        item_type = item.get("type")
        if item_type == "tool_search_call":
            new_items.append(
                {
                    "type": "function_call",
                    "call_id": item.get("call_id"),
                    "name": TOOL_SEARCH_NAME,
                    "arguments": json.dumps(item.get("arguments") or {}),
                }
            )
        elif item_type == "tool_search_output":
            functions = flatten_loaded_tools(item)
            for function in functions:
                flat_map[function.flat_name] = (function.namespace, function.name)
                if function.flat_name not in added:
                    added.add(function.flat_name)
                    extra_tools.append(function.definition)
            new_items.append(
                {
                    "type": "function_call_output",
                    "call_id": item.get("call_id"),
                    "output": loaded_tools_summary(functions),
                }
            )
        elif item_type == "function_call" and item.get("namespace") is not None:
            namespace = item["namespace"]
            name = item.get("name")
            flat_name = f"{namespace}__{name}"
            flat_map[flat_name] = (namespace, name)
            renamed = {key: value for key, value in item.items() if key != "namespace"}
            renamed["name"] = flat_name
            new_items.append(renamed)
        else:
            new_items.append(item)
    return new_items, extra_tools, flat_map


def rewrite_request(data: dict[str, Any]) -> tuple[dict[str, Any], FlatMap]:
    """A copy of the request with tool_search emulated as plain function tools and calls, and the flat name
    map the response side needs."""
    tools = rewrite_tools(list(data.get("tools") or []))
    new_input, extra_tools, flat_map = rewrite_input(list(data.get("input") or []))
    known_names = {tool.get("name") for tool in tools}
    for tool in extra_tools:
        if tool.get("name") not in known_names:
            known_names.add(tool.get("name"))
            tools.append(tool)
    updated = dict(data)
    updated["tools"] = tools
    updated["input"] = new_input
    return updated, flat_map


def parse_arguments(raw: Any) -> dict[str, Any]:
    """The tool_search call's arguments as a JSON object, {} when raw is empty, not JSON, or not an object, with
    whole-number floats as integers because Codex loads no tools for a float limit."""
    if isinstance(raw, dict):
        parsed: Any = raw
    elif not raw:
        return {}
    else:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(parsed, dict):
        return {}
    return {key: int(value) if isinstance(value, float) and value.is_integer() else value for key, value in parsed.items()}


def rewrite_output_item(item: Any, flat_map: FlatMap) -> None:
    """Rewrite one response output item in place: a tool_search function call back to a tool_search_call item,
    or a flattened function call back to its original name and namespace."""
    if _get(item, "type") != "function_call":
        return
    name = _get(item, "name")
    if name == TOOL_SEARCH_NAME:
        _set(item, "type", "tool_search_call")
        _set(item, "execution", "client")
        _set(item, "arguments", parse_arguments(_get(item, "arguments")))
        if _get(item, "status") is None:
            _set(item, "status", "completed")
        return
    mapped = flat_map.get(name)
    if mapped is not None:
        namespace, original_name = mapped
        _set(item, "name", original_name)
        _set(item, "namespace", namespace)


def rewrite_output_list(output: Any, flat_map: FlatMap) -> None:
    """Rewrite every function_call item of a response output list in place."""
    for item in output or []:
        rewrite_output_item(item, flat_map)


def rewrite_stream_chunk(chunk: Any, flat_map: FlatMap) -> None:
    """Rewrite one Responses API stream event in place: an added/done item, or the completed response's
    output list. Every other event passes through untouched."""
    event_type = _get(chunk, "type")
    if event_type in ("response.output_item.added", "response.output_item.done"):
        item = _get(chunk, "item")
        if item is not None:
            rewrite_output_item(item, flat_map)
    elif event_type == "response.completed":
        rewrite_output_list(_get(_get(chunk, "response"), "output"), flat_map)


_pending_calls: OrderedDict[str, FlatMap] = OrderedDict()


def remember_call(call_id: str, flat_map: FlatMap) -> None:
    """Record that litellm_call_id is an emulated tool_search call, with the flat names it has loaded so far."""
    _pending_calls[call_id] = flat_map
    _pending_calls.move_to_end(call_id)
    while len(_pending_calls) > MAX_PENDING_CALLS:
        _pending_calls.popitem(last=False)


def peek_call(call_id: str | None) -> FlatMap | None:
    """The flat name map for an emulated call, or None when the call is not one this module rewrote."""
    return _pending_calls.get(call_id) if call_id else None


def forget_call(call_id: str | None) -> None:
    """Drop a finished call's flat name map."""
    if call_id:
        _pending_calls.pop(call_id, None)


class CodexToolSearchHandler(CustomLogger):
    """Emulates Codex's tool_search tool with plain function tools for every non-stock Copilot model."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any] | None:
        """Rewrite a /v1/responses request that carries tool_search for a model Codex's own catalog does not
        already drive with it, and remember the flat name map its response needs."""
        if call_type != RESPONSES_CALL_TYPE:
            return None
        from litellm.proxy.common_utils.codex_model_catalog import bundled_codex_models

        if is_stock_codex_model(data.get("model"), bundled_codex_models()):
            return None
        if not request_has_tool_search(data):
            return None
        updated, flat_map = rewrite_request(data)
        call_id = data.get("litellm_call_id")
        if call_id:
            remember_call(call_id, flat_map)
        return updated

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: Any,
        response: Any,
    ) -> Any:
        """Put tool_search_call and namespaced function_call items back on a non-streaming response."""
        call_id = data.get("litellm_call_id")
        flat_map = peek_call(call_id)
        if flat_map is None:
            return response
        rewrite_output_list(_get(response, "output"), flat_map)
        forget_call(call_id)
        return response

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,
        response: Any,
        request_data: dict[str, Any],
    ) -> Any:
        """Put tool_search_call and namespaced function_call items back on every streamed event of an
        emulated call, then forget its flat name map once the stream ends."""
        call_id = request_data.get("litellm_call_id")
        flat_map = peek_call(call_id)
        if flat_map is None:
            async for chunk in response:
                yield chunk
            return
        try:
            async for chunk in response:
                rewrite_stream_chunk(chunk, flat_map)
                yield chunk
        finally:
            forget_call(call_id)


handler = CodexToolSearchHandler()
