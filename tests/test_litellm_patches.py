"""LiteLLM source patches: applied once, idempotent, and refused when the snippet moved."""

from pathlib import Path

import pytest

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
    assert 'if route_type == "aresponses" and isinstance(response, ResponsesAPIResponse):' in text
    assert "response = CachedResponsesAPIStreamingIterator(\n" in text
    assert text.index("CachedResponsesAPIStreamingIterator(") < text.index("selected_data_generator = select_data_generator(")
