"""Checks that the patched LiteLLM in the gateway image sends Copilot no tool_reference part; run with the image's python."""

import sys

from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig

MESSAGES = [
    {"role": "user", "content": "Load the vault tool."},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "ToolSearch", "arguments": "{}"}}],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": [{"type": "tool_reference", "tool_name": "vault_code"}]},
]


def main() -> int:
    """Print the tool message Copilot would receive and exit 1 when it still carries a tool_reference part."""
    request = GithubCopilotConfig().transform_request(
        model="gemini-test", messages=MESSAGES, optional_params={}, litellm_params={}, headers={}
    )
    tool_message = request["messages"][-1]
    print(tool_message)
    content = tool_message.get("content")
    leaked = isinstance(content, list) and any(part.get("type") == "tool_reference" for part in content)
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
