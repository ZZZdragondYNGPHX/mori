"""Load Mori's packaged system prompt without a provider or UI dependency."""
from importlib.resources import files


def load_system_prompt() -> str:
    """Return the UTF-8 Mori persona for an LLM's system message."""
    return files("character").joinpath("prompts/mori.md").read_text(encoding="utf-8")
