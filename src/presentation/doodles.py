"""Transient, click-through laser strokes in logical screen coordinates."""
import math
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QTransform,
)
from PySide6.QtWidgets import QApplication, QWidget


def drawing_strokes(kind, text=""):
    """Return bounded polylines; the pen follows exactly the rendered geometry."""
    if kind == "circle":
        return [[QPointF(58 + 54 * math.cos(t * math.tau / 80),
                         45 + 39 * math.sin(t * math.tau / 80)) for t in range(81)]]
    if kind == "arrow":
        return [[QPointF(5, 55), QPointF(110, 15)],
                [QPointF(80, 9), QPointF(110, 15), QPointF(94, 42)]]
    if kind == "star":
        return [[QPointF(48 + (43 if i % 2 == 0 else 19) * math.sin(i * math.pi / 5),
                         47 - (43 if i % 2 == 0 else 19) * math.cos(i * math.pi / 5)) for i in range(11)]]
    if kind == "whale":
        path = QPainterPath(QPointF(100, 36))
        path.cubicTo(74, -5, 9, 4, 8, 43)
        path.cubicTo(6, 79, 90, 87, 100, 48)
        path.lineTo(128, 25)
        path.lineTo(130, 51)
        path.lineTo(100, 36)
        return [list(p) for p in path.toSubpathPolygons()] + [[QPointF(34, 33), QPointF(35, 34)]]
    if kind == "text":
        text = " ".join(text.split())[:32]
        if not text:
            return []
        font = QFont("LXGW WenKai")
        font.setPixelSize(32)
        path = QPainterPath()
        metrics = QFontMetricsF(font)
        line, baseline = "", 32
        for char in text:
            if line and metrics.horizontalAdvance(line + char) > 300:
                path.addText(0, baseline, font, line)
                line, baseline = "", baseline + 42
            line += char
        if line:
            path.addText(0, baseline, font, line)
        rect = path.boundingRect()
        scale = min(1, 360 / max(1, rect.width()))
        transform = QTransform()
        transform.scale(scale, scale)
        transform.translate(-rect.left() + 4, -rect.top() + 4)
        return [list(p) for p in transform.map(path).toSubpathPolygons() if len(p) > 1]
    return []


@dataclass
class StrokeTrace:
    strokes: list
    edges: list = field(init=False, default_factory=list)
    length: float = field(init=False, default=0)

    def __post_init__(self):
        previous = None
        for stroke in self.strokes:
            if not stroke:
                continue
            if previous is not None and previous != stroke[0]:
                travel = math.hypot(stroke[0].x() - previous.x(), stroke[0].y() - previous.y())
                self.edges.append((self.length, travel, previous, stroke[0], False))
                self.length += travel
            for a, b in zip(stroke, stroke[1:]):
                length = math.hypot(b.x() - a.x(), b.y() - a.y())
                if length:
                    self.edges.append((self.length, length, a, b, True))
                    self.length += length
            previous = stroke[-1]

    @property
    def bounds(self):
        points = [p for stroke in self.strokes for p in stroke]
        if not points:
            return QRectF()
        return QRectF(QPointF(min(p.x() for p in points), min(p.y() for p in points)),
                      QPointF(max(p.x() for p in points), max(p.y() for p in points)))

    def partial(self, distance):
        path = QPainterPath()
        tip = QPointF()
        previous = None
        for start, length, a, b, ink in self.edges:
            if start > distance:
                break
            fraction = max(0, min(1, (distance - start) / length))
            tip = a + (b - a) * fraction
            if ink:
                if previous != a:
                    path.moveTo(a)
                path.lineTo(tip)
            previous = b if ink else None
            if fraction < 1:
                break
        return path, tip


@dataclass
class Doodle:
    trace: StrokeTrace
    origin: QPointF
    identity: str = ""
    reference: QRectF = field(default_factory=QRectF)
    geometry: QRectF = field(default_factory=QRectF)
    progress: float = 0
    completed: float | None = None
    retiring: float | None = None
    _cached_progress: float = field(init=False, default=-1)
    _cached_partial: tuple | None = field(init=False, default=None)

    def partial(self):
        if self.progress != self._cached_progress:
            self._cached_partial = self.trace.partial(self.progress)
            self._cached_progress = self.progress
        return self._cached_partial

    def transform(self):
        transform = QTransform()
        if self.identity and self.reference.width() and self.reference.height():
            transform.translate(self.geometry.x(), self.geometry.y())
            transform.scale(self.geometry.width() / self.reference.width(),
                            self.geometry.height() / self.reference.height())
            transform.translate(self.origin.x() - self.reference.x(), self.origin.y() - self.reference.y())
        else:
            transform.translate(self.origin.x(), self.origin.y())
        return transform

    @property
    def tip(self):
        return self.transform().map(self.partial()[1])

    def opacity(self, now):
        start = self.retiring if self.retiring is not None else self.completed + 6 if self.completed is not None else None
        return 1 if start is None else max(0, min(1, 1 - (now - start)))


class InkWindow(QWidget):
    picked = Signal(object)
    dismissed = Signal()

    def __init__(self, manager, screen, picking=False):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if not picking:
            flags |= Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        super().__init__(None, flags)
        self.manager = manager
        self.picking = picking
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, not picking)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not picking)
        self.setWindowTitle("Mori · 临时笔迹" if not picking else "Mori · 点击选择位置，右键或 Esc 取消")
        self.setGeometry(screen.geometry())
        if picking:
            self.setCursor(Qt.CrossCursor)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.picking:
            painter.fillRect(self.rect(), QColor(15, 27, 44, 28))
            hint = QRectF((self.width() - min(460, self.width() - 24)) / 2, 24, min(460, self.width() - 24), 44)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(20, 35, 51, 225))
            painter.drawRoundedRect(hint, 12, 12)
            painter.setPen(QColor("#e7faff"))
            painter.drawText(hint.toRect(), Qt.AlignCenter,
                             "点击选择位置 · 右键或 Esc 取消")
            return
        painter.translate(-self.x(), -self.y())
        for doodle in self.manager.drawings:
            opacity = doodle.opacity(self.manager.clock())
            if opacity <= 0:
                continue
            path = doodle.transform().map(doodle.partial()[0])
            painter.setOpacity(opacity)
            for width, color in ((8, QColor(87, 207, 255, 28)), (4, QColor(110, 220, 255, 80)),
                                 (3, QColor(19, 119, 151, 230)),
                                 (1.8, QColor("#bef2ff"))):
                painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.picked.emit(event.globalPosition().toPoint())
        elif event.button() == Qt.RightButton:
            self.dismissed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.dismissed.emit()


class DoodleLayer(QObject):
    selection_finished = Signal()
    def __init__(self, parent, clock):
        super().__init__(parent)
        self.clock = clock
        self.drawings = []
        self.windows = []
        self.pickers = []
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.advance)

    def pick(self, callback):
        self.cancel_pick()

        def picked(point):
            self.cancel_pick()
            QTimer.singleShot(300, lambda: callback(point))

        for screen in QApplication.screens():
            window = InkWindow(self, screen, picking=True)
            window.picked.connect(picked)
            window.dismissed.connect(self.cancel_pick)
            self.pickers.append(window)
            window.show()
        if self.pickers:
            self.pickers[0].activateWindow()
            self.pickers[0].setFocus()

    def cancel_pick(self):
        had_pickers = bool(self.pickers)
        for window in self.pickers:
            window.hide()
            window.deleteLater()
        self.pickers.clear()
        if had_pickers:
            self.selection_finished.emit()

    def add(self, trace, origin, target=None):
        doodle = Doodle(trace, QPointF(origin))
        if target:
            doodle.identity = target.identity
            doodle.reference = QRectF(target.geometry)
            doodle.geometry = QRectF(target.geometry)
        self.drawings = self.drawings[-7:] + [doodle]
        if not self.windows:
            for screen in QApplication.screens():
                window = InkWindow(self, screen)
                self.windows.append(window)
                window.show()
        self._timer.start()
        return doodle

    def sync_windows(self, windows):
        for doodle in self.drawings:
            if not doodle.identity:
                continue
            target = windows.get(doodle.identity)
            if target and not target.minimized and target.current_desktop:
                doodle.geometry = QRectF(target.geometry)
            elif doodle.retiring is None:
                doodle.retiring = self.clock()
        self.advance()

    def advance(self):
        now = self.clock()
        self.drawings = [d for d in self.drawings if d.opacity(now) > 0]
        if not self.drawings:
            self._dispose_windows()
            return
        changing = any(d.completed is None or d.retiring is not None or now >= d.completed + 6 for d in self.drawings)
        self._timer.setInterval(40 if changing else 200)
        for window in self.windows:
            window.update()

    def clear(self):
        self.cancel_pick()
        self.drawings.clear()
        self._dispose_windows()

    def _dispose_windows(self):
        self._timer.stop()
        for window in self.windows:
            window.hide()
            window.deleteLater()
        self.windows.clear()
