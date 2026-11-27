"""LiteLLM source patches: applied once, idempotent, and refused when the snippet moved."""

from pathlib import Path

import pytest

import copilot_auth
import litellm_patches

PATCH = litellm_patches.PATCHES[0]


def _tree(tmp_path: Path, text: str) -> Path:
    """Write text to the patch target under a temporary site-packages root and return the root."""
    target = tmp_path / PATCH.path
    target.parent.mkdir(parents=True)
    target.write_text(text)
    return tmp_path


def test_patch_replaces_its_snippet_once(tmp_path: Path) -> None:
    """The websearch query patch strips Claude Code's prefix from the search query."""
    root = _tree(tmp_path, f"before\n{PATCH.old}after\n")
    assert litellm_patches.apply(PATCH, root) == "applied"
    text = (root / PATCH.path).read_text()
    assert PATCH.old not in text
    assert 'removeprefix(\n            "Perform a web search for the query:"' in text


def test_patch_is_idempotent(tmp_path: Path) -> None:
    """A second build over an already patched file leaves it unchanged."""
    root = _tree(tmp_path, f"before\n{PATCH.old}after\n")
    litellm_patches.apply(PATCH, root)
    patched = (root / PATCH.path).read_text()
    assert litellm_patches.apply(PATCH, root) == "already applied"
    assert (root / PATCH.path).read_text() == patched


def test_patch_refuses_a_moved_snippet(tmp_path: Path) -> None:
    """A LiteLLM version without the exact snippet fails the build instead of shipping unpatched."""
    root = _tree(tmp_path, "query = something_else()\n")
    with pytest.raises(SystemExit):
        litellm_patches.apply(PATCH, root)


UPSTREAM_STREAM_BRANCH = (
    "                elif select_data_generator:\n"
    "                    selected_data_generator = select_data_generator(\n"
    "                        response=response,\n"
)


def test_responses_stream_patch_replays_a_finished_response_as_a_stream(tmp_path: Path) -> None:
    """On /v1/responses a non-streaming response to a streaming request is replayed as an event stream."""
    patch = next(p for p in litellm_patches.PATCHES if p.path == "litellm/proxy/common_request_processing.py")
    target = tmp_path / patch.path
    target.parent.mkdir(parents=True)
    target.write_text(f"before\n{UPSTREAM_STREAM_BRANCH}after\n")
    assert litellm_patches.apply(patch, tmp_path) == "applied"
    text = target.read_text()
    assert 'route_type == "aresponses"\n                        and isinstance(response, ResponsesAPIResponse)\n' in text
    assert 'and not self.data.get("background")' in text
    assert "response = CachedResponsesAPIStreamingIterator(\n" in text
    assert text.index("CachedResponsesAPIStreamingIterator(") < text.index("selected_data_generator = select_data_generator(")


UPSTREAM_CODEX_CATALOG = (
    "    experimental_supported_tools: tuple[()] = ()\n"
    "    context_window: int | None\n"
    "    base_instructions: str\n"
    "        return CodexFallbackModel(\n"
    "            context_window=row.max_input_tokens,\n"
    "            base_instructions=instructions,\n"
    "        )\n"
    "    listing: Final = {\n"
    '        "supported_in_api": True,\n'
    '        "upgrade": upgrade,\n'
    "    }\n"
)


def test_codex_catalog_patches_keep_tools_in_the_tool_list_and_drop_grok_sub_agents(tmp_path: Path) -> None:
    """Stock entries turn responses-lite off, and fallback entries for grok disable the multi-agent namespace."""
    patches = [
        p
        for p in litellm_patches.PATCHES
        if p.path == "litellm/proxy/common_utils/codex_model_catalog.py" and p.upstream.endswith("/issues/13")
    ]
    target = tmp_path / patches[0].path
    target.parent.mkdir(parents=True)
    target.write_text(UPSTREAM_CODEX_CATALOG)
    assert [litellm_patches.apply(patch, tmp_path) for patch in patches] == ["applied"] * 3
    text = target.read_text()
    assert '        "upgrade": upgrade,\n        "use_responses_lite": False,\n    }\n' in text
    assert '    multi_agent_version: Literal["disabled"] | None = None\n    context_window: int | None\n' in text
    assert 'multi_agent_version="disabled" if "grok" in (row.upstream_model or row.id) else None,\n        )' in text
    assert [litellm_patches.apply(patch, tmp_path) for patch in patches] == ["already applied"] * 3


UPSTREAM_CODEX_CAN_DRIVE = (
    "def _codex_can_drive(row: CodexCatalogRow) -> bool:\n"
    '    return "*" not in row.id and (row.mode is None or row.mode in CODEX_CHAT_MODES)\n'
)


def test_codex_catalog_patch_leaves_out_claude_code_names(tmp_path: Path) -> None:
    """Codex's catalog never lists the claude* and anthropic-* names served to Claude Code."""
    patch = next(p for p in litellm_patches.PATCHES if p.upstream.endswith("/pull/21"))
    target = tmp_path / patch.path
    target.parent.mkdir(parents=True)
    target.write_text(UPSTREAM_CODEX_CAN_DRIVE)
    assert litellm_patches.apply(patch, tmp_path) == "applied"
    prefixes = ", ".join(f'"{prefix}"' for prefix in copilot_auth.CLAUDE_CODE_PREFIXES)
    assert f"and not row.id.startswith(({prefixes}))\n" in target.read_text()


UPSTREAM_PLAN_BUILDERS = (
    "    async def async_build_chat_completion_agentic_loop_plan(\n"
    "    ) -> AgenticLoopPlan:\n"
    '        tool_calls: Final = tools["tool_calls"]\n'
    '        response_format: Final = tools.get("response_format", "openai")\n'
    "    async def async_build_responses_agentic_loop_plan(\n"
    "    ) -> AgenticLoopPlan:\n"
    '        tool_calls: Final = tools["tool_calls"]\n'
    "        request_patch: Final = await self._build_responses_request_patch(\n"
)


def test_mixed_search_patches_answer_mixed_responses_before_the_agentic_loop(tmp_path: Path) -> None:
    """Both plan builders hand a response that mixes searches with other calls to websearch_mixed first."""
    patches = [
        p
        for p in litellm_patches.PATCHES
        if p.path == "litellm/integrations/websearch_interception/handler.py" and "websearch_mixed" in p.new
    ]
    target = tmp_path / patches[0].path
    target.parent.mkdir(parents=True)
    target.write_text(UPSTREAM_PLAN_BUILDERS)
    assert [litellm_patches.apply(patch, tmp_path) for patch in patches] == ["applied"] * 2
    text = target.read_text()
    chat = text.index("websearch_mixed.chat_override(self, response, tool_calls, kwargs)")
    responses = text.index("websearch_mixed.responses_override(self, response, tool_calls, kwargs)")
    assert text.index("async_build_chat_completion_agentic_loop_plan") < chat < text.index('response_format: Final')
    assert text.index("async_build_responses_agentic_loop_plan") < responses < text.index("request_patch: Final")
    assert text.count("AgenticLoopPlan(run_agentic_loop=False, response_override=override") == 2


UPSTREAM_COPILOT_MESSAGES = (
    "    def _transform_messages(\n"
    "        self,\n"
    "        messages,\n"
    "        model: str,\n"
    "    ):\n"
    "        import litellm\n"
    "\n"
    "        # Check if system-to-assistant conversion is disabled\n"
)


def test_copilot_messages_patch_drops_tool_reference_parts_first(tmp_path: Path) -> None:
    """Copilot chat requests lose the tool_reference parts of tool messages before any other message change."""
    patch = next(p for p in litellm_patches.PATCHES if p.path == "litellm/llms/github_copilot/chat/transformation.py")
    target = tmp_path / patch.path
    target.parent.mkdir(parents=True)
    target.write_text(UPSTREAM_COPILOT_MESSAGES)
    assert litellm_patches.apply(patch, tmp_path) == "applied"
    text = target.read_text()
    assert "messages = drop_tool_reference_parts_from_tool_messages(messages)\n" in text
    assert text.index("drop_tool_reference_parts_from_tool_messages(messages)") < text.index("# Check if system")
    assert litellm_patches.apply(patch, tmp_path) == "already applied"


UPSTREAM_CODEX_FALLBACK = (
    "    experimental_supported_tools: tuple[()] = ()\n"
    '    multi_agent_version: Literal["disabled"] | None = None\n'
    "    context_window: int | None\n"
)


def test_codex_catalog_patch_offers_tool_search_on_every_fallback_entry(tmp_path: Path) -> None:
    """Fallback catalog entries carry supports_search_tool, so Codex offers tool_search for every gateway model."""
    patch = next(p for p in litellm_patches.PATCHES if "supports_search_tool" in p.new)
    target = tmp_path / patch.path
    target.parent.mkdir(parents=True)
    target.write_text(UPSTREAM_CODEX_FALLBACK)
    assert litellm_patches.apply(patch, tmp_path) == "applied"
    assert "    supports_search_tool: Literal[True] = True\n" in target.read_text()
