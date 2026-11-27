"""Reasoning levels: Codex catalog entries list the efforts each Copilot model accepts."""

import json
from pathlib import Path

import pytest

import reasoning_levels


def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, efforts: dict[str, list[str]]) -> None:
    """Write a reasoning efforts file into a temporary state folder and point the module at it."""
    (tmp_path / reasoning_levels.FILE_NAME).write_text(json.dumps(efforts))
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))


def test_catalog_fields_list_the_model_levels_with_a_supported_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that accepts low, medium and high lists exactly those, so Codex clamps a configured xhigh."""
    _state(tmp_path, monkeypatch, {"mai-code-1.1-flash": ["low", "medium", "high"]})
    fields = reasoning_levels.catalog_fields("mai-code-1.1-flash")
    assert [level["effort"] for level in fields["supported_reasoning_levels"]] == ["low", "medium", "high"]
    assert all(level["description"] for level in fields["supported_reasoning_levels"])
    assert fields["default_reasoning_level"] == "medium"


def test_catalog_fields_default_to_the_strongest_level_without_medium(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model without medium defaults to its strongest level."""
    _state(tmp_path, monkeypatch, {"kimi-k3": ["low", "high", "max"]})
    assert reasoning_levels.catalog_fields("kimi-k3")["default_reasoning_level"] == "max"


def test_catalog_fields_are_empty_for_an_unknown_model_or_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model without reported efforts, or a state folder without the file, adds nothing to the entry."""
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    assert reasoning_levels.catalog_fields("gemini-4-argon") == {}
    _state(tmp_path, monkeypatch, {"kimi-k3": ["low", "high"]})
    assert reasoning_levels.catalog_fields("gemini-4-argon") == {}
