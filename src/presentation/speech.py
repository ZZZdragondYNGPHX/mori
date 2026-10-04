"""A reusable, non-focusing thought bubble with stable native geometry."""
import math
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget


def wrap_text(text, font, max_width):
    metrics = QFontMetrics(font)
    lines = []
    for paragraph in (text or "").splitlines():
        line = ""
        for character in paragraph:
            if line and metrics.horizontalAdvance(line + character) > max_width:
                lines.append(line)
                line = ""
            line += character
        lines.append(line)
    return lines or [""]


def head_anchor(frame, origin, canvas, clip=None):
    """Use the upper visible silhouette, including a window pose's offset."""
    visible = QRegion(frame.mask()).translated(origin).intersected(QRegion(canvas))
    if clip is not None:
        visible = visible.intersected(QRegion(clip))
    bounds = visible.boundingRect()
    if bounds.isEmpty():
        return QPoint(canvas.center().x(), canvas.top())
    # The highest band belongs to the head; the full silhouette includes a
    # large sideways tail, which must not pull the bubble away from the face.
    band = QRect(bounds.left(), bounds.top(), bounds.width(), max(8, bounds.height() // 7))
    head = visible.intersected(QRegion(band)).boundingRect()
    return QPoint(head.center().x(), bounds.top())


class SpeechBubble(QWidget):
    """Circular thought bubble; two small dots point toward Mori's head."""

    MARGIN = 4
    CONNECTOR = 32
    GAP = 8
    HOVER_SECONDS = 3
    clicked = Signal()

    def __init__(self, text, owner, interactive=False):
        super().__init__(owner)
        # Breeze installs a rectangular compositor shadow on Qt.ToolTip windows
        # independently of Qt.NoDropShadowWindowHint and the native shape mask.
        # Set its opt-out before style polishing / native window creation.
        self.setProperty('_KDE_NET_WM_SKIP_SHADOW', True)
        self._interactive = interactive
        self._input_enabled = False
        self._hover_since = None
        self._opacity = getattr(getattr(owner, 'preferences', None), 'bubble_opacity', 95) / 100
        flags = (Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
                 | Qt.WindowTransparentForInput)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._font = QFont("Microsoft YaHei", 11)
        self._font.setStyleStrategy(QFont.PreferAntialias)
        self.setFont(self._font)
        self._fm = QFontMetrics(self._font)
        self._text = None
        self._direction = "above"
        self._head = QPoint()
        self._owner = owner
        self._placement = QTimer(self)
        self._placement.setSingleShot(True)
        self._placement.setInterval(0)
        self._placement.timeout.connect(lambda: self.place_above(self._owner))
        # A pass-through window receives no enter/move events. Poll only while
        # visible; observe cursor position without intercepting underlying input.
        self._hover = QTimer(self)
        self._hover.setInterval(100)
        self._hover.timeout.connect(self._poll_hover)
        self.set_text(text)

    def set_opacity(self, percent):
        opacity = max(20, min(100, percent)) / 100
        if opacity != self._opacity:
            self._opacity = opacity
            self.update()

    def set_text(self, text):
        text = text or "……"
        if text == self._text:
            return
        # Remove any click-only shape before resizing / painting new content.
        self._hover_since = None
        self._set_input_enabled(False)
        self._text = text
        self.setAccessibleName(text)
        self._lines = wrap_text(text, self._font, 220)
        width = max(self._fm.horizontalAdvance(line) for line in self._lines)
        height = len(self._lines) * self._fm.lineSpacing()
        # Fit the entire text rectangle inside the circle, including corners.
        self._diameter = max(112, math.ceil(math.hypot(width + 36, height + 32)))
        self._preferred_size = QSize(self._diameter + self.CONNECTOR + 2 * self.MARGIN,
                                     self._diameter + self.CONNECTOR + 2 * self.MARGIN)
        if not self.isVisible():
            self.resize(self._preferred_size)
            self._update_shape()
        else:
            self.follow(owner=self._owner)
        self.update()

    def follow(self, owner):
        """Coalesce pose changes; do not move another surface inside paintEvent."""
        self._owner = owner
        if self.isVisible() and not self._placement.isActive():
            self._placement.start()

    def place_above(self, owner):
        self._placement.stop()
        self._owner = owner
        pet = owner.frameGeometry()
        anchor = getattr(owner, "speech_anchor", None)
        head = owner.mapToGlobal(anchor()) if callable(anchor) else QPoint(pet.center().x(), pet.top())
        screen = owner.screen() or QApplication.primaryScreen()
        bounds = screen.availableGeometry().adjusted(2, 2, -2, -2) if screen else None
        width, height = self._preferred_size.width(), self._preferred_size.height()
        above = QRect(head.x() - self.MARGIN - self._diameter // 2,
                      min(head.y() - height - self.GAP, pet.top() - height - self.GAP), width, height)
        director = getattr(owner, "interactions", None)
        target = (director.target.geometry if director and director.active and director.target
                  and director.scene in ("peek", "sit", "press", "minimize", "maximize") else None)
        candidates = [(above, "above")]
        if target is not None:
            # At a vertical edge, keep the bubble beside the software window
            # instead of repeatedly damaging its content as Mori moves.
            for x in (target.right() + self.GAP, target.left() - width - self.GAP):
                if abs(x - above.x()) <= self._diameter:
                    candidates.append((QRect(x, above.y(), width, height), "above"))
        candidates += [
            (QRect(pet.right() + self.GAP, head.y() - height // 2, width, height), "right"),
            (QRect(pet.left() - width - self.GAP, head.y() - height // 2, width, height), "left"),
            (QRect(above.x(), pet.bottom() + self.GAP, width, height), "below"),
        ]
        # Geometry must not overlap the pet's native transparent surface.
        # Keep clear of the attached software window whenever space permits.
        choice = next(((rect, side) for rect, side in candidates
                       if (bounds is None or bounds.contains(rect)) and not rect.intersects(pet)
                       and (target is None or not rect.intersects(target))), None)
        if choice is None:
            choice = next(((rect, side) for rect, side in candidates
                           if (bounds is None or bounds.contains(rect)) and not rect.intersects(pet)), None)
        if choice is None:
            rect = QRect(above)
            if bounds is not None:
                rect.moveLeft(max(bounds.left(), min(rect.left(), bounds.right() - width + 1)))
                rect.moveTop(max(bounds.top(), min(rect.top(), bounds.bottom() - height + 1)))
            choice = rect, "above"
        rect, direction = choice
        changed = direction != self._direction or head - rect.topLeft() != self._head
        self._direction = direction
        self._head = head - rect.topLeft()
        moved = self.geometry() != rect
        if moved:
            self._hover_since = None
            self._set_input_enabled(False)
            # One native configure instead of a resize followed by a move.
            self.setGeometry(rect)
        if changed or moved:
            self._update_shape()
            self.update()

    def _body_rect(self):
        x = self.MARGIN + (self.CONNECTOR if self._direction == "right" else 0)
        y = self.MARGIN + (self.CONNECTOR if self._direction == "below" else 0)
        return QRectF(x, y, self._diameter, self._diameter)

    def _dots(self):
        center = self._body_rect().center()
        delta = QPointF(self._head) - center
        direction = delta / (math.hypot(delta.x(), delta.y()) or 1)
        return [(center + direction * (self._diameter / 2 + distance), radius)
                for radius, distance in ((5.5, 9), (3, 23))]

    def _update_shape(self):
        # A permanent bounding mask clips Qt's backing-store flush. On XWayland
        # its untouched corners contain opaque black, exposed when a compositor
        # effect temporarily ignores the shape during movement / resizing.
        # Paint the entire alpha surface while pass-through; only use a native
        # shape after hover unlock so transparent corners never receive clicks.
        if not self._input_enabled:
            if not self.mask().isEmpty():
                self.clearMask()
            return
        shape = QRegion(self._body_rect().adjusted(-1, -1, 1, 1).toAlignedRect(), QRegion.Ellipse)
        for point, radius in self._dots():
            dot = QRectF(point.x() - radius - 1, point.y() - radius - 1,
                         2 * radius + 2, 2 * radius + 2)
            shape = shape.united(QRegion(dot.toAlignedRect(), QRegion.Ellipse))
        shape = shape.intersected(QRegion(self.rect()))
        if shape != self.mask():
            self.setMask(shape)

    def _set_input_enabled(self, enabled):
        enabled = bool(enabled and self._interactive)
        if enabled == self._input_enabled:
            return
        self._input_enabled = enabled
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not enabled)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        handle = self.windowHandle()
        if handle:
            # QWidget.setWindowFlags hides/recreates a top-level widget. Updating
            # this existing QWindow flag preserves its geometry and native ID.
            handle.setFlag(Qt.WindowTransparentForInput, not enabled)
        self._update_shape()

    def _poll_hover(self, now=None):
        if not self.isVisible() or not self._interactive:
            return
        now = time.monotonic() if now is None else now
        point = self.mapFromGlobal(QCursor.pos())
        delta = QPointF(point) - self._body_rect().center()
        inside = math.hypot(delta.x(), delta.y()) <= self._diameter / 2
        if not inside:
            self._hover_since = None
            if self._input_enabled:
                self._set_input_enabled(False)
        elif QApplication.mouseButtons() == Qt.NoButton:
            if self._hover_since is None:
                self._hover_since = now
            if not self._input_enabled and now - self._hover_since >= self.HOVER_SECONDS:
                self._set_input_enabled(True)
        elif not self._input_enabled:
            self._hover_since = None  # Never intercept a held click or drag.

    def showEvent(self, event):
        self._hover_since = None
        self._set_input_enabled(False)
        self._update_shape()
        if self._interactive:
            self._hover.start()
        super().showEvent(event)

    def leaveEvent(self, event):
        self._hover_since = None
        self._set_input_enabled(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._input_enabled and event.button() == Qt.LeftButton:
            body = self._body_rect()
            delta = event.position() - body.center()
            if math.hypot(delta.x(), delta.y()) <= self._diameter / 2:
                self.clicked.emit()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        # Clear the complete backing store so resizing/changing the dots never
        # leaves an old translucent outline over an underlying application.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(self._opacity)
        body = self._body_rect()
        center = body.center()
        painter.setPen(QPen(QColor(121, 151, 176), 1.25))
        painter.setBrush(QColor(250, 253, 255))
        painter.drawEllipse(body)
        for point, radius in self._dots():
            painter.drawEllipse(point, radius, radius)
        painter.setFont(self._font)
        painter.setPen(QColor(39, 56, 72))
        line_height = self._fm.lineSpacing()
        top = center.y() - len(self._lines) * line_height / 2
        for index, line in enumerate(self._lines):
            x = center.x() - self._fm.horizontalAdvance(line) / 2
            painter.drawText(QPointF(x, top + index * line_height + self._fm.ascent()), line)
        painter.end()

    def hideEvent(self, event):
        self._placement.stop()
        self._hover.stop()
        self._hover_since = None
        self._set_input_enabled(False)
        super().hideEvent(event)
