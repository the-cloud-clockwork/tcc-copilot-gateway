"""Reasoning levels for the Codex catalog entries the gateway builds for models Codex does not know.

copilot_auth.py writes reasoning_efforts.json into the state folder from Copilot's /models metadata, one
list of efforts per served model name, weakest first. The patched LiteLLM catalog adds each list to the
model's entry, so Codex offers only those efforts and clamps a configured effort the model rejects.
"""

import json
import os
from pathlib import Path

FILE_NAME = "reasoning_efforts.json"


def _efforts(model: str) -> list[str]:
    """The efforts written for model, or an empty list when the file or the model is missing."""
    path = Path(os.getenv("COPILOT_STATE_DIR", "/state")) / FILE_NAME
    try:
        efforts = json.loads(path.read_text()).get(model)
    except (OSError, ValueError):
        return []
    return [effort for effort in efforts if isinstance(effort, str)] if isinstance(efforts, list) else []


def catalog_fields(model: str) -> dict[str, object]:
    """supported_reasoning_levels and default_reasoning_level for model's catalog entry, or nothing."""
    efforts = _efforts(model)
    if not efforts:
        return {}
    return {
        "supported_reasoning_levels": tuple(
            {"effort": effort, "description": f"{effort} reasoning effort"} for effort in efforts
        ),
        "default_reasoning_level": "medium" if "medium" in efforts else efforts[-1],
    }
