from typing import Protocol

from ..events import CompanionEvent


class ToolAdapter(Protocol):
    id: str

    def normalize(self, payload: dict) -> CompanionEvent | None:
        """Return one neutral event, or None for an irrelevant tool event."""
        ...
