"""Rich, provider-neutral task notices and deterministic reminder policy."""
import math
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field

from .events import short_text

KINDS = {'bound', 'title', 'message', 'input', 'resolved', 'started',
         'completed', 'failed', 'interrupted', 'disconnected', 'closed'}


@dataclass(frozen=True)
class TaskNotice:
    source: str
    session_id: str
    kind: str
    task_id: str = ''
    title: str = ''
    text: str = ''
    item_id: str = ''
    request_id: str = ''
    blocking: bool = True
    complete: bool = True
    binding: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)
    schema_version: int = 2

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 2 or self.kind not in KINDS:
            raise ValueError('Invalid notice kind/version')
        for name in ('source', 'session_id', 'task_id', 'item_id', 'request_id', 'event_id'):
            short_text(getattr(self, name), name, required=name in ('source', 'session_id', 'event_id'))
        short_text(self.title, 'title', 240)
        if not isinstance(self.text, str) or len(self.text) > 2 * 1024 * 1024:
            raise ValueError('Invalid original text')
        if type(self.blocking) is not bool or type(self.complete) is not bool:
            raise ValueError('Invalid flags')
        if (type(self.timestamp) not in (int, float) or not math.isfinite(self.timestamp)
                or not 0 <= self.timestamp <= time.time() + 60):
            raise ValueError('Invalid timestamp')
        if not isinstance(self.binding, dict) or set(self.binding) - {'service', 'session', 'window', 'native_window'}:
            raise ValueError('Invalid terminal binding')
        for value in self.binding.values():
            short_text(value, 'binding', 240)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError('Invalid notice fields')
        return cls(**data)


@dataclass
class PendingChoice:
    text: str
    blocking: bool
    due: float
    viewed: bool = False
    knocks: int = 0


@dataclass
class Task:
    key: str
    source: str
    title: str = '未命名任务'
    binding: dict = field(default_factory=dict)
    messages: OrderedDict = field(default_factory=OrderedDict)
    pending: OrderedDict = field(default_factory=OrderedDict)
    status: str = 'idle'
    unread: int = 0
    visibility: str = 'unknown'
    unread_items: set = field(default_factory=set)
    connected: bool = True
    observed: bool = False
    last_text_animation: float = -math.inf
    finished_turns: set = field(default_factory=set)
    turn_id: str = ''

    @property
    def preview(self):
        if self.pending:
            return next(reversed(self.pending.values())).text
        return next(reversed(self.messages.values()), '')


class NoticeState:
    """Pending inputs outlive turns; only explicit resolution clears them."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.tasks = OrderedDict()
        self.seen = OrderedDict()
        self.feedback = []

    def ingest(self, notice):
        if notice.event_id in self.seen:
            return False
        self.seen[notice.event_id] = None
        while len(self.seen) > 4096:
            self.seen.popitem(last=False)
        task = self.tasks.setdefault(notice.session_id, Task(notice.session_id, notice.source))
        now = self.clock()
        if notice.title:
            task.title = notice.title
        if notice.binding:
            task.binding = dict(notice.binding)
        if notice.kind not in ('disconnected', 'closed'):
            task.connected = True
        if notice.kind == 'message':
            key = notice.item_id or notice.event_id
            previous = task.messages.get(key)
            task.messages[key] = notice.text
            # Full original messages are retained; streaming snapshots never duplicate them.
            if previous != notice.text and notice.text and key not in task.unread_items:
                task.unread_items.add(key)
                task.unread += 1
            if (notice.text and previous != notice.text and task.visibility in ('covered', 'background')
                    and now - task.last_text_animation >= 10):
                task.last_text_animation = now
                self.feedback.append((task.key, 'text', task.visibility))
        elif notice.kind == 'input':
            if notice.request_id not in task.pending:
                task.pending[notice.request_id] = PendingChoice(
                    notice.text, notice.blocking, now if notice.blocking else now + 20)
                task.unread += 1
                task.unread_items.add('q:' + notice.request_id)
        elif notice.kind == 'resolved':
            task.pending.pop(notice.request_id, None)
        elif notice.kind == 'started':
            task.status = 'working'
            task.turn_id = notice.task_id
        elif notice.kind in ('completed', 'failed', 'interrupted'):
            if not task.turn_id or task.turn_id == notice.task_id:
                task.status = notice.kind
            if notice.task_id not in task.finished_turns:
                task.finished_turns.add(notice.task_id)
                if notice.kind != 'interrupted':
                    self.feedback.append((task.key, notice.kind, task.visibility))
        elif notice.kind in ('disconnected', 'closed'):
            task.connected = False
            task.status = notice.kind
        return True

    def viewed(self, key):
        task = self.tasks[key]
        task.unread = 0
        task.unread_items.clear()
        for request in task.pending.values():
            request.viewed = True

    def set_visibility(self, key, visibility):
        task = self.tasks[key]
        # Already foreground when a new question arrives is not a viewing action.
        if task.observed and visibility == 'visible' and task.visibility != 'visible':
            self.viewed(key)
        if (not task.observed and task.unread and task.messages
                and visibility in ('covered', 'background')):
            task.last_text_animation = self.clock()
            self.feedback.append((key, 'text', visibility))
        task.visibility = visibility
        task.observed = True

    def tick(self):
        now = self.clock()
        for task in self.tasks.values():
            if not task.connected:
                continue
            for request in task.pending.values():
                if request.viewed or request.due > now:
                    continue
                self.feedback.append((task.key, 'knock', task.visibility if task.observed else 'background'))
                request.knocks += 1
                request.due = now + {'visible': 90, 'covered': 60, 'background': 35}.get(task.visibility, 35)

    def take_feedback(self):
        items, self.feedback = self.feedback, []
        return items

    def preferred(self):
        tasks = list(self.tasks.values())
        return next((t for t in reversed(tasks) if t.pending and t.connected),
                    next((t for t in reversed(tasks) if t.unread), None))
