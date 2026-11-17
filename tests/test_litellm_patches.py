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
