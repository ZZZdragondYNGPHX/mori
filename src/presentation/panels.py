"""Local companionship and activity panels; no provider-specific networking."""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from character.dialogue import fixed_line


class ChatWindow(QWidget):
    """Preserves the chat interaction with explicitly local, simple replies."""
    def __init__(self, pet=None, parent=None):
        super().__init__(parent)
        self._pet = pet
        self.setWindowTitle("与鲸鱼娘聊天")
        self.resize(420, 520)
        layout = QVBoxLayout(self)
        self._view = QTextEdit(self)
        self._view.setReadOnly(True)
        self._view.document().setMaximumBlockCount(200)
        layout.addWidget(self._view)
        row = QHBoxLayout()
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("试试：状态、资料、休息、你好")
        send = QPushButton("发送", self)
        row.addWidget(self._edit)
        row.addWidget(send)
        layout.addLayout(row)
        hint = QLabel("本地陪伴模式：可查询工具状态和搜索资料；未连接聊天模型。", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._rag_label = QLabel(self)
        layout.addWidget(self._rag_label)
        self._edit.returnPressed.connect(self._on_send)
        send.clicked.connect(self._on_send)
        if pet is not None:
            pet.refresh_rag()
        self.update_rag_label()
        self._view.append("鲸鱼娘：" + fixed_line("chat.open"))

    def update_rag_label(self):
        loaded = getattr(self._pet, "rag_loaded", 0)
        truncated = (getattr(self._pet, "rag_combined_truncated", False) or
                     getattr(self._pet, "rag_doc_truncated", False))
        self._rag_label.setText(f"本地资料：{loaded} / 20000 字" + ("（已截断）" if truncated else ""))

    def _on_send(self):
        question = self._edit.text().strip()[:2000]
        if not question:
            return
        self._edit.clear()
        self._view.append("你：" + escape(question))
        if "状态" in question:
            reply = self._pet.tool_summary_text
        elif question.startswith("搜索 "):
            needle = question[3:].strip().casefold()
            lines = getattr(self._pet, "rag_text", "").splitlines()
            matches = [line[:300] for line in lines if needle and needle in line.casefold()][:5]
            reply = "\n".join(matches) or fixed_line("chat.search_empty")
        elif "资料" in question:
            reply = fixed_line("chat.files")
        elif any(word in question for word in ("休息", "累", "困")):
            reply = fixed_line("chat.rest")
        elif any(word in question.lower() for word in ("你好", "hello", "hi")):
            reply = fixed_line("chat.greet")
        else:
            reply = fixed_line("chat.fallback")
        self._view.append("鲸鱼娘：" + escape(reply).replace("\n", "<br>"))

    def show_and_activate(self, anchor=None):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = anchor.right() + 16 if anchor else geo.center().x() - self.width() // 2
            y = anchor.top() if anchor else geo.center().y() - self.height() // 2
            x = max(geo.left(), min(x, geo.right() - self.width()))
            y = max(geo.top(), min(y, geo.bottom() - self.height()))
            self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus()

    def closeEvent(self, event):
        if getattr(self._pet, "_quitting", False):
            event.accept()
        else:
            event.ignore()
            self.hide()


class StatusWindow(QWidget):
    def __init__(self, pet):
        super().__init__()
        self._pet = pet
        self.setWindowTitle("AI 工具状态")
        self.resize(540, 480)
        layout = QVBoxLayout(self)
        self._sessions = QLabel(self)
        self._sessions.setWordWrap(True)
        self._sessions.setTextFormat(Qt.PlainText)
        layout.addWidget(self._sessions)
        layout.addWidget(QLabel("最近事件（仅保留内存中的 200 条）", self))
        self._events = QTextEdit(self)
        self._events.setReadOnly(True)
        layout.addWidget(self._events)

    def update_status(self, summary, history):
        self._sessions.setText(summary)
        text = "\n".join(history)
        if text != self._events.toPlainText():
            self._events.setPlainText(text)
            bar = self._events.verticalScrollBar()
            bar.setValue(bar.maximum())

    def closeEvent(self, event):
        event.ignore()
        self.hide()
