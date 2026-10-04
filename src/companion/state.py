"""Bounded, source-agnostic aggregation of the seven universal event types."""
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from .events import EventType

LABELS = {
    "task_started": "任务开始", "thinking": "思考中", "generating": "工作中",
    "waiting_input": "等待输入", "task_completed": "本轮结束",
    "task_failed": "任务出错", "idle": "待命",
}
PRIORITY = {"waiting_input": 6, "task_failed": 5, "generating": 4,
            "thinking": 3, "task_started": 3, "task_completed": 2, "idle": 0}
TERMINAL = {EventType.TASK_COMPLETED, EventType.TASK_FAILED, EventType.IDLE}


@dataclass
class SessionState:
    source: str
    session_id: str
    status: EventType = EventType.IDLE
    task_id: str = ""
    timestamp: float = 0
    received_at: float = 0
    summary: str = ""
    retired_tasks: deque = field(default_factory=lambda: deque(maxlen=16))


class CompanionState:
    def __init__(self, clock=time.monotonic, session_limit=128, stale_seconds=1800):
        self.clock = clock
        self.session_limit = session_limit
        self.stale_seconds = stale_seconds
        self.sessions = OrderedDict()
        self.history = deque(maxlen=200)
        self._seen = OrderedDict()

    def ingest(self, event):
        if event.event_id in self._seen:
            return False
        self._seen[event.event_id] = None
        if len(self._seen) > 1024:
            self._seen.popitem(last=False)
        key = (event.source, event.session_id)
        session = self.sessions.get(key)
        if session is not None:
            if event.timestamp < session.timestamp or event.task_id in session.retired_tasks:
                return False
            if (event.task_id and event.task_id == session.task_id and
                    session.status in TERMINAL and event.type not in TERMINAL and
                    event.type != EventType.TASK_STARTED):
                return False
        else:
            session = SessionState(event.source, event.session_id)
            self.sessions[key] = session
        if event.task_id and event.task_id != session.task_id:
            if session.task_id:
                session.retired_tasks.append(session.task_id)
            session.task_id = event.task_id
        session.timestamp = event.timestamp
        session.received_at = self.clock()
        session.summary = event.summary
        session.status = event.type
        self.sessions.move_to_end(key)
        while len(self.sessions) > self.session_limit:
            self.sessions.popitem(last=False)
        self.history.append(event)
        return True

    def is_stale(self, session):
        return self.clock() - session.received_at > self.stale_seconds

    def effective_status(self, session):
        age = self.clock() - session.received_at
        if self.is_stale(session):
            return EventType.IDLE
        if session.status in (EventType.TASK_COMPLETED, EventType.TASK_FAILED) and age > 8:
            return EventType.IDLE
        return session.status

    def dominant_status(self):
        if not self.sessions:
            return EventType.IDLE
        session = max(self.sessions.values(),
                      key=lambda s: (PRIORITY[self.effective_status(s)], s.received_at))
        return self.effective_status(session)

    def session_lines(self):
        return [f"{s.source} · {s.session_id[:12]} · "
                f"{'状态已过期' if self.is_stale(s) else LABELS[self.effective_status(s)]}"
                for s in reversed(self.sessions.values())]
