"""Application adapter catalog; adding a tool never changes the event core."""
from .codex import CodexAdapter

DEFAULT_ADAPTERS = ("codex",)
BUILTIN_ADAPTERS = {"codex": CodexAdapter}
