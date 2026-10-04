"""Bounded mailbox; adapters publish, the presentation runtime drains."""
from collections import deque
from threading import Lock

from .events import CompanionEvent
from .notices import TaskNotice


class BridgeBusy(Exception):
    pass


class EventBridge:
    def __init__(self, notify=lambda: None, capacity=256):
        self._notify = notify
        self._capacity = capacity
        self._events = deque()
        self._lock = Lock()

    def publish(self, event: CompanionEvent):
        if not isinstance(event, (CompanionEvent, TaskNotice)):
            raise ValueError("Bridge accepts CompanionEvent or TaskNotice only")
        with self._lock:
            if len(self._events) >= self._capacity:
                raise BridgeBusy("Event queue is full")
            wake = not self._events
            self._events.append(event)
        if wake:
            self._notify()

    def drain(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events
