"""Versioned, intentionally small event contract shared by all adapters."""
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class EventType(StrEnum):
    TASK_STARTED = "task_started"
    THINKING = "thinking"
    GENERATING = "generating"
    WAITING_INPUT = "waiting_input"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    IDLE = "idle"


def short_text(value, name, limit=160, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Invalid {name}")
    if required and not value.strip():
        raise ValueError(f"Missing {name}")
    if any(ord(c) < 32 for c in value):
        raise ValueError(f"Control characters in {name}")
    return value


@dataclass(frozen=True)
class CompanionEvent:
    source: str
    session_id: str
    type: EventType
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)
    schema_version: int = 1
    task_id: str = ""
    summary: str = ""

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported schema_version")
        object.__setattr__(self, "type", EventType(self.type))
        for name in ("source", "session_id", "event_id", "task_id"):
            short_text(getattr(self, name), name, required=name in ("source", "session_id", "event_id"))
        short_text(self.summary, "summary", limit=240)
        if type(self.timestamp) not in (int, float) or not math.isfinite(self.timestamp):
            raise ValueError("Invalid timestamp")
        if self.timestamp < 0 or self.timestamp > time.time() + 60:
            raise ValueError("Timestamp is outside the allowed range")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Event must be an object")
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise ValueError("Unknown event fields")
        if not {"source", "session_id", "type"} <= set(data):
            raise ValueError("Missing event fields")
        return cls(**data)
