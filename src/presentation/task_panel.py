"""Pinned original-text reader; new messages never replace the text being read."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class TaskPanel(QWidget):
    viewed = Signal(str)
    return_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Mori · Codex 原文')
        self.resize(740, 550)
        self.tasks = {}
        self.task_key = None
        self.entry_id = None
        self._entries = []
        self._pin_text = ''
        self._snapshot = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        self.selector = QComboBox()
        self.selector.setAccessibleName('选择 Codex 任务')
        self.selector.currentIndexChanged.connect(self.select_task)
        layout.addWidget(self.selector)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split = QSplitter()
        self.entries = QListWidget()
        self.entries.setAccessibleName('原文和待选择列表')
        self.entries.currentRowChanged.connect(self.select_entry)
        self.original = QTextEdit()
        self.original.setReadOnly(True)
        self.original.setAccessibleName('Codex 完整原文')
        self.original.setPlaceholderText('Codex 的文字描述会显示在这里。')
        split.addWidget(self.entries)
        split.addWidget(self.original)
        split.setSizes([190, 500])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.fresh = QPushButton('查看最新原文')
        self.fresh.clicked.connect(self.show_latest)
        self.back = QPushButton('回到这个任务')
        self.back.clicked.connect(lambda: self.return_requested.emit(self.task_key) if self.task_key else None)
        row.addWidget(self.fresh)
        row.addStretch()
        row.addWidget(self.back)
        layout.addLayout(row)
        self.setStyleSheet('QTextEdit, QListWidget {background:#f8fbfd; color:#273848; '
                          'border:1px solid #cad7e1; border-radius:8px; padding:10px;} '
                          'QPushButton {padding:8px 14px;} QLabel {padding:4px 0;}')

    def sync(self, tasks, can_return=lambda _: False):
        self.tasks = tasks
        key = self.task_key
        self.back.setEnabled(bool(key and can_return(key)))
        snapshot = (key, tuple((t.key, t.title, t.status, t.connected, t.unread,
                    tuple((r, c.text, c.viewed, c.blocking) for r, c in t.pending.items()),
                    tuple(t.messages.items())) for t in tasks.values()))
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self.selector.blockSignals(True)
        self.selector.clear()
        for task in tasks.values():
            badge = ' · 待选择' if task.pending else ''
            badge += f' · {task.unread} 条未读' if task.unread else ''
            # Binding labels disambiguate identical task names in different tabs.
            binding = task.binding
            suffix = (f" · 终端 {binding.get('service', '').rsplit('.', 1)[-1]}"
                      f" / 窗口 {binding.get('window', '').rsplit('/', 1)[-1]}"
                      f" / 标签 {binding.get('session', '').rsplit('/', 1)[-1]}"
                      if binding.get('session') else f' · {task.key[:6]}')
            self.selector.addItem(task.title + badge + suffix, task.key)
        index = self.selector.findData(key)
        self.selector.setCurrentIndex(index if index >= 0 else 0)
        self.selector.blockSignals(False)
        if self.task_key is None and self.selector.count():
            self.task_key = self.selector.currentData()
        self.refresh_entries()
        self.back.setEnabled(bool(self.task_key and can_return(self.task_key)))
        self.back.setToolTip('切换到对应终端窗口与标签页' if self.back.isEnabled()
                            else '窗口尚未定位；请手动切回对应任务一次。')

    def refresh_entries(self):
        task = self.tasks.get(self.task_key)
        if not task:
            return
        old = self.entry_id
        scroll = self.entries.verticalScrollBar().value()
        entries = [(f'q:{key}', ('待选择 · 已查看' if choice.viewed else '待选择')
                    + (' · 暂停任务' if choice.blocking else ' · 任务继续'), choice.text)
                   for key, choice in task.pending.items()]
        entries += [(f'm:{key}', ' '.join(text.split())[:35] or '文字正在输出…', text)
                    for key, text in task.messages.items()]
        self._entries = entries
        self.entries.blockSignals(True)
        self.entries.clear()
        for _, label, _ in entries:
            self.entries.addItem(label)
        index = next((i for i, e in enumerate(entries) if e[0] == old), -1)
        self.entries.setCurrentRow(index)
        self.entries.blockSignals(False)
        self.entries.verticalScrollBar().setValue(scroll)
        status = f'{task.title}任务 · {len(task.pending)} 项待选择'
        if not task.connected:
            status += ' · 终端连接已结束' if task.status == 'closed' else ' · 连接中断，选项状态待确认'
        elif task.status == 'failed':
            status += ' · 任务失败'
        if task.unread:
            status += f' · {task.unread} 条新消息，点击“查看最新原文”'
        self.status.setText(status)
        self.fresh.setEnabled(bool(entries))
        # If a selected streaming message changed, preserve the old readable text
        # until the user explicitly requests the updated original.
        if index >= 0 and entries[index][2] != self._pin_text:
            self.fresh.setText('查看更新后的原文')
        else:
            self.fresh.setText('查看最新原文')

    def select_task(self, _index):
        self.task_key = self.selector.currentData()
        self.entry_id = None
        self.refresh_entries()
        self.show_latest()

    def select_entry(self, row):
        if not 0 <= row < len(self._entries):
            return
        self.entry_id, _, self._pin_text = self._entries[row]
        self.original.setPlainText(self._pin_text)
        self.viewed.emit(self.task_key)

    def show_latest(self):
        if not self._entries:
            self.entry_id = None
            self._pin_text = ''
            self.original.clear()
            return
        task = self.tasks[self.task_key]
        row = 0 if task.pending else len(self._entries) - 1
        self.entries.blockSignals(True)
        self.entries.setCurrentRow(row)
        self.entries.blockSignals(False)
        self.select_entry(row)

    def open_task(self, key):
        self.task_key = key
        self.entry_id = None
        self.selector.blockSignals(True)
        self.selector.setCurrentIndex(self.selector.findData(key))
        self.selector.blockSignals(False)
        self.refresh_entries()
        self.show_latest()
        self.viewed.emit(key)
        self.show()
        self.raise_()
        self.activateWindow()
