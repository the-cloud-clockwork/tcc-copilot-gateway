"""SearXNG settings: web search keeps engines that answer when the default ones are suspended."""

import re
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent.parent / "searxng" / "settings.yml"
FALLBACK_ENGINES = ("bing", "yandex", "yahoo", "mwmbl")


def _enabled_engines(text: str) -> set[str]:
    """Names of the engines the settings file switches on with an explicit disabled: false."""
    pattern = re.compile(r"^  - name: (\S+)\n(?:    .*\n)*?    disabled: false$", re.MULTILINE)
    return set(pattern.findall(text))


def test_settings_enable_engines_beyond_the_defaults() -> None:
    """bing, yandex, yahoo and mwmbl answer while brave, duckduckgo, google cse and wikidata are suspended."""
    enabled = _enabled_engines(SETTINGS.read_text())
    assert set(FALLBACK_ENGINES) <= enabled, f"missing: {sorted(set(FALLBACK_ENGINES) - enabled)}"
