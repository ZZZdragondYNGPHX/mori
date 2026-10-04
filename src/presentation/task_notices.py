"""Task bubble, original-text panel, and visibility-aware reminder scheduler."""
from PySide6.QtCore import QObject, QTimer

from companion.notices import NoticeState

from .speech import SpeechBubble
from .task_feedback import TaskFeedback
from .task_panel import TaskPanel
from .task_windows import TaskWindows


class TaskNotices(QObject):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.state = NoticeState()
        self.windows = TaskWindows(self)
        self.windows.changed.connect(self.visibility_changed)
        self.windows.returned.connect(self.returned)
        self.feedback = TaskFeedback(pet)
        self.panel = TaskPanel()
        self.panel.viewed.connect(self.viewed)
        self.panel.return_requested.connect(self.return_to)
        self.bubble = SpeechBubble('', pet, interactive=True)
        self.bubble.clicked.connect(self.open_bubble)
        self.bubble_key = None
        self.bubble_until = 0
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def ingest(self, notice):
        changed = self.state.ingest(notice)
        if not changed:
            return False
        self.windows.bind(notice.session_id, notice.binding)
        if notice.kind == "resolved" and not self.state.tasks[notice.session_id].pending:
            self.feedback.viewed(notice.session_id)
        if notice.kind == "closed":
            self.windows.bindings.pop(notice.session_id, None)
            self.windows.snapshots.pop(notice.session_id, None)
            self.feedback.viewed(notice.session_id)
        if notice.kind == 'failed':
            self.bubble_key = notice.session_id
            self.bubble_until = self.state.clock() + 15
            task = self.state.tasks[notice.session_id]
            task.messages.setdefault('failure:' + notice.task_id, notice.text or '任务失败，请回到终端查看。')
            task.unread += 1
        # Lifecycle/stream changes affect the badge but never mark input as answered.
        self.sync()
        self.tick()
        return True

    def visibility_changed(self):
        for key, task in self.state.tasks.items():
            previous = task.visibility
            observed = task.observed
            self.state.set_visibility(key, self.windows.strength(key))
            if observed and previous != 'visible' and task.visibility == 'visible':
                self.feedback.viewed(key)
        self.sync()

    def viewed(self, key):
        if key not in self.state.tasks:
            return
        self.state.viewed(key)
        self.feedback.viewed(key)
        self.sync()

    def returned(self, key, ok, reason):
        if self.closed:
            return
        if ok:
            self.viewed(key)
            self.panel.hide()
        else:
            self.panel.status.setText(reason)

    def return_to(self, key):
        self.windows.return_to(key)

    def open_bubble(self):
        if self.bubble_key:
            self.panel.open_task(self.bubble_key)

    def open_panel(self):
        preferred = self.state.preferred()
        key = preferred.key if preferred else next(iter(self.state.tasks), None)
        if key:
            self.panel.open_task(key)
        else:
            self.panel.show()
            self.panel.raise_()
            self.panel.activateWindow()

    def tick(self):
        if self.closed:
            return
        self.state.tick()
        for key, kind, strength in self.state.take_feedback():
            if kind == 'knock' and (key not in self.state.tasks or not self.state.tasks[key].pending):
                continue
            if not self.pet._hidden:
                self.pet.living.cancel()
                self.feedback.request(key, kind, strength)
                if kind in ('knock', 'text', 'failed'):
                    preferred = self.state.preferred()
                    self.bubble_key = preferred.key if preferred and preferred.pending else key
                    self.bubble_until = self.state.clock() + (15 if kind == 'failed' else 8)
        was_working = getattr(self.pet, '_codex_working', False)
        self.pet._codex_working = any(t.connected and (t.status == 'working' or t.pending)
                                      for t in self.state.tasks.values())
        if self.pet._codex_working and not was_working and self.pet.interactions.active:
            self.pet.interactions.cancel(restore_position=False)
        self.pet.living.activity_changed()
        self.sync()

    def sync(self):
        self.panel.sync(self.state.tasks, self.windows.can_return)
        task = self.state.tasks.get(self.bubble_key)
        pending = self.state.preferred()
        if pending and pending.pending:
            task = pending
            self.bubble_key = task.key
        if (not task or self.pet._hidden or task.status == 'closed' or (not task.pending and self.state.clock() > self.bubble_until)):
            self.bubble.hide()
            return
        if self.pet._bubble:
            self.pet._kill_bubble()
        title = task.title
        if len(title) > 26:
            title = title[:25] + '…'
        preview = task.preview.strip()
        # Show the beginning exactly (with line-breaks compacted only in the bubble).
        preview = ' '.join(preview.split())
        preview = preview[:65] + ('…' if len(preview) > 65 else '')
        label = f'{title}任务\n{preview}'
        if task.pending:
            viewed = all(r.viewed for r in task.pending.values())
            label += '\n待选择 · ' + ('已查看' if viewed else '点击查看原文')
            if not task.connected:
                label += ' · 连接中断'
        else:
            label += '\n点击展开原文'
        self.bubble.set_text(label)
        self.bubble.place_above(self.pet)
        if not self.bubble.isVisible():
            self.bubble.show()
        self.bubble.follow(self.pet)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.windows.close()
        self.feedback.close()
        self.bubble.hide()
        self.bubble.deleteLater()
        self.panel.close()
        self.panel.deleteLater()
