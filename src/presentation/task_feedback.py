"""Short authored poses with a chalkboard prop painted on Mori's own surface."""
import math
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPen, QPixmap


class TaskFeedback(QObject):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.kind = None
        self.task_key = None
        self.strength = 'background'
        self.queue = []
        self.cache = {}
        self.started = 0
        self.duration = 0
        self.frame = None
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.advance)

    def request(self, key, kind, strength):
        if kind in ('text', 'knock') and any(item[0] == key and item[1] == kind for item in self.queue):
            return
        self.queue.append((key, kind, strength))
        self.queue.sort(key=lambda item: {'knock': 3, 'failed': 2, 'completed': 1, 'text': 0}[item[1]], reverse=True)
        self.timer.start()
        self.advance()

    def viewed(self, key):
        self.queue = [item for item in self.queue if not (item[0] == key and item[1] == 'knock')]
        if self.task_key == key and self.kind == 'knock':
            self.finish()

    def poses(self, kind):
        if kind not in self.cache:
            family = {'knock': 'window-press', 'text': 'system-pen',
                      'completed': 'base-cheerup', 'failed': 'base-wrong'}[kind]
            if self.pet.character.definition.id == 'whale-girl':
                root = self.pet.character.assets.root
                frames = [QPixmap(str(root / 'motion' / f'{family}-{i}.png')) for i in range(4)]
            else:
                key = {'knock': 'attention', 'text': 'true', 'completed': 'cheerup', 'failed': 'wrong'}[kind]
                frames = [QPixmap(str(self.pet.character.assets.resolve(key)))]
            self.cache[kind] = [p for p in frames if not p.isNull()]
        return self.cache[kind]

    def advance(self):
        if self.pet._hidden:
            self.finish()
            self.queue.clear()
            self.timer.stop()
            return
        # Let direct touches and dragging finish before playing a queued response.
        if self.pet._state != 'idle' or self.pet._pressing:
            if self.kind:
                self.finish()
            return
        now = time.monotonic()
        if self.kind and now - self.started >= self.duration:
            self.finish()
        if not self.kind and self.queue:
            self.task_key, self.kind, self.strength = self.queue.pop(0)
            self.started = now
            self.duration = ({'visible': 1.0, 'covered': 1.7, 'background': 2.4}[self.strength]
                             if self.kind == 'knock' else 1.8 if self.kind == 'completed' else 2.4)
            if self.pet.interactions.active:
                self.pet.interactions.cancel(restore_position=False)
        if self.kind:
            poses = self.poses(self.kind)
            index = int((now - self.started) / .16) % max(1, len(poses))
            self.frame = poses[index] if poses else None
            self.pet.update()
        elif not self.queue:
            self.timer.stop()

    def finish(self):
        self.kind = None
        self.frame = None
        self.pet.update()

    def draw(self, painter):
        if not self.kind or self.frame is None:
            return False
        width, height = self.pet.width(), self.pet.height()
        painter.save()
        painter.scale(width / 256, height / 256)
        left = self.pet.inward_facing() == "left" and self.pet.character.definition.id == "whale-girl"
        if left:
            painter.translate(256, 0)
            painter.scale(-1, 1)
        if self.kind == 'knock':
            board = QRectF(207, 82, 46, 62)
            painter.setPen(QPen(QColor('#c79e6c'), 4))
            painter.setBrush(QColor('#204d46'))
            painter.drawRoundedRect(board, 3, 3)
            painter.setPen(QColor('#f4faf0'))
            painter.setFont(QFont('sans-serif', 14))
            painter.save()
            if left:
                painter.translate(2 * board.center().x(), 0)
                painter.scale(-1, 1)
            painter.drawText(board.adjusted(0, 3, 0, -22), Qt.AlignCenter, '选')
            painter.restore()
            phase = (time.monotonic() - self.started) % .55 / .55
            painter.setPen(QPen(QColor(247, 231, 150, round(230 * (1 - phase))), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(237 - phase * 8, 116 - phase * 8, 12 + phase * 16, 12 + phase * 16))
        elif self.kind == 'text':
            painter.setPen(QPen(QColor('#b7cbdc'), 1.2))
            painter.setBrush(QColor('#fbfdff'))
            painter.drawRoundedRect(QRectF(218, 134, 30, 35), 2, 2)
            painter.setPen(QColor('#6088a6'))
            for line in range(3):
                painter.drawLine(222, 142 + line * 7, 240, 142 + line * 7)
        elif self.kind == 'failed':
            pulse = .5 + .5 * math.sin((time.monotonic() - self.started) * 9)
            painter.setPen(QPen(QColor(192, 65, 57, round(160 + 80 * pulse)), 3))
            painter.setBrush(QColor('#fff7ee'))
            painter.drawEllipse(QRectF(216, 58, 30, 30))
            painter.setFont(QFont('sans-serif', 17, QFont.Bold))
            painter.drawText(QRectF(216, 58, 30, 30), Qt.AlignCenter, '!')
        painter.drawPixmap(0, 0, 256, 256, self.frame)
        painter.restore()
        self.pet._visible_base_frame = self.frame
        return True

    def reset_assets(self):
        self.cache.clear()
        self.finish()

    def close(self):
        self.timer.stop()
        self.queue.clear()
        self.finish()
