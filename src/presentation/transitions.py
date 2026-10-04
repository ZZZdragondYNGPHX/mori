"""Short, interruptible pose transitions with no idle polling."""
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QImage, QPainter


class FrameTransition(QObject):
    def __init__(self, pet, clock=time.monotonic):
        super().__init__(pet)
        self.pet = pet
        self.clock = clock
        self.token = None
        self.source = self.target = None
        self._started = 0
        self._duration = .12
        self._paused_at = None
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        if self.clock() - self._started >= self._duration:
            self.source = None
            self._timer.stop()
        self.pet.update()

    def _mix(self, target):
        if self.source is None:
            return target
        t = max(0, min(1, (self.clock() - self._started) / self._duration))
        if t >= 1:
            self.source = None
            self._timer.stop()
            return target
        t = t * t * (3 - 2 * t)
        mixed = QImage(target.size(), QImage.Format_ARGB32_Premultiplied)
        mixed.fill(0)
        painter = QPainter(mixed)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.setOpacity(1 - t)
        painter.drawImage(0, 0, self.source)
        weighted = QImage(target.size(), QImage.Format_ARGB32_Premultiplied)
        weighted.fill(0)
        weight_painter = QPainter(weighted)
        weight_painter.setCompositionMode(QPainter.CompositionMode_Source)
        weight_painter.setOpacity(t)
        weight_painter.drawImage(0, 0, target)
        weight_painter.end()
        # Apply opacity before Plus: Qt's global opacity also interpolates
        # operator coverage, which would otherwise dim opaque overlap.
        painter.setOpacity(1)
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.drawImage(0, 0, weighted)
        painter.end()
        return mixed

    def present(self, target, token, duration=.12):
        if self.token != token:
            current = self._mix(self.target) if self.target is not None else None
            self.source = current.copy() if current is not None and current.size() == target.size() else None
            self.token = token
            self._started = self.clock()
            self._duration = max(.04, min(.18, duration))
            if self.source is not None and self._paused_at is None and self.pet.isVisible():
                self._timer.start()
        self.target = target
        return self._mix(target)

    def pause(self):
        self._timer.stop()
        if self.source is not None and self._paused_at is None:
            self._paused_at = self.clock()

    def resume(self):
        if self._paused_at is not None:
            self._started += self.clock() - self._paused_at
            self._paused_at = None
            if self.source is not None:
                self._timer.start()

    def clear(self):
        self._timer.stop()
        self.source = self.target = self.token = self._paused_at = None
