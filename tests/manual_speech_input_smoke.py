"""Real XWayland shape/click-through check on owned temporary windows only."""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from presentation.platform import configure_desktop_platform
from presentation.speech import SpeechBubble


class Rectangle(ctypes.Structure):
    _fields_ = [('x', ctypes.c_short), ('y', ctypes.c_short),
               ('width', ctypes.c_ushort), ('height', ctypes.c_ushort)]


class Background(QWidget):
    def __init__(self):
        super().__init__()
        self.clicks = 0
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#b5d8ee'))
        painter.end()

    def mouseReleaseEvent(self, event):
        self.clicks += 1


def main():
    configure_desktop_platform()
    app = QApplication([])
    app.setDesktopFileName('mori-companion')
    if app.platformName() != 'xcb':
        print('SKIP: requires XWayland/xcb')
        return 77
    x11 = ctypes.CDLL('libX11.so.6')
    shape = ctypes.CDLL('libXext.so.6')
    xtst = ctypes.CDLL('libXtst.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XFree.argtypes = [ctypes.c_void_p]
    shape.XShapeGetRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    shape.XShapeGetRectangles.restype = ctypes.POINTER(Rectangle)
    xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(None)
    assert display, 'No X display'
    original_cursor = QCursor.pos()
    original_active = subprocess.check_output(['xprop', '-root', '_NET_ACTIVE_WINDOW'], text=True)
    background, owner, bubble = Background(), Background(), None

    def rectangles(kind):
        count, ordering = ctypes.c_int(), ctypes.c_int()
        pointer = shape.XShapeGetRectangles(display, int(bubble.winId()), kind,
                                            ctypes.byref(count), ctypes.byref(ordering))
        result = [(r.x, r.y, r.width, r.height) for r in pointer[:count.value]] if pointer else []
        if pointer:
            x11.XFree(pointer)
        return result

    def move(point):
        assert background.geometry().contains(point), 'Pointer outside owned test surface'
        xtst.XTestFakeMotionEvent(display, -1, point.x(), point.y(), 0)
        x11.XFlush(display)
        QTest.qWait(100)
        assert QCursor.pos() == point, 'Pointer move not confirmed'

    def click():
        assert background.geometry().contains(QCursor.pos())
        xtst.XTestFakeButtonEvent(display, 1, 1, 0)
        xtst.XTestFakeButtonEvent(display, 1, 0, 0)
        x11.XFlush(display)
        QTest.qWait(100)

    def has_theme_shadow(widget):
        native_id = widget.winId()
        app.sync()  # Publish lazy native creation before the external X client reads it.
        properties = subprocess.check_output(['xprop', '-id', hex(int(native_id)),
                                              '_KDE_NET_WM_SHADOW'], text=True)
        return '_KDE_NET_WM_SHADOW(CARDINAL) =' in properties

    try:
        bounds = app.primaryScreen().availableGeometry()
        background.setGeometry(bounds.center().x() - 330, bounds.top() + 100, 660, 760)
        background.show()
        owner.setGeometry(background.x() + 260, background.y() + 620, 140, 90)
        owner.show()
        # Reproduce the theme behavior that the shape/input-only probe missed.
        # The Qt no-shadow hint alone does not stop Breeze's tooltip shadow.
        baseline = QWidget(owner)
        baseline.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                                | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
                                | Qt.WindowTransparentForInput)
        baseline.setAttribute(Qt.WA_TranslucentBackground)
        baseline.setAttribute(Qt.WA_ShowWithoutActivating)
        baseline.setGeometry(background.x() + 40, background.y() + 40, 120, 120)
        baseline.show()
        QTest.qWait(100)
        baseline_shadow = has_theme_shadow(baseline)
        baseline.close()
        bubble = SpeechBubble('气泡测试任务\n悬停三秒后点击', owner, interactive=True)
        bubble.place_above(owner)
        bubble.show()
        QTest.qWait(250)
        native_id, geometry = bubble.winId(), bubble.geometry()
        assert not has_theme_shadow(bubble), 'Theme still installs a rectangular tooltip shadow'
        print(f'THEME: {app.style().objectName()}; no-shadow-hint baseline shadow={baseline_shadow}; bubble shadow=False')
        assert bubble.mask().isEmpty(), 'Pass-through painting must flush the full alpha surface'
        assert not rectangles(2), 'Default native input region is not empty'
        clicked = []
        bubble.clicked.connect(lambda: clicked.append(True))
        center = bubble.mapToGlobal(bubble._body_rect().center().toPoint())
        move(center)
        click()
        assert background.clicks == 1 and not clicked, 'Initial click did not pass through'
        started = time.monotonic()
        while not bubble._input_enabled and time.monotonic() - started < 4:
            QTest.qWait(50)
        assert bubble._input_enabled, 'Three-second hover did not enable input'
        assert time.monotonic() - started >= 2.7, 'Input enabled before the dwell delay'
        assert rectangles(2), 'Native input region stayed empty after hover'
        assert len(rectangles(0)) > 1, 'Unlocked native bounding shape is still rectangular'
        assert not any(x <= 0 < x + w and y <= 0 < y + h for x, y, w, h in rectangles(0)), \
            'Unlocked native bounding shape includes a transparent corner'
        click()
        assert len(clicked) == 1 and background.clicks == 1, 'Unlocked click did not reach bubble'
        move(bubble.mapToGlobal(bubble._body_rect().topLeft().toPoint()))
        assert not bubble._input_enabled and not rectangles(2), 'Exit did not restore click-through'
        click()
        assert background.clicks == 2, 'Circle corner blocked the underlying window'
        assert bubble.winId() == native_id and bubble.geometry() == geometry
        bubble.set_opacity(40)
        assert bubble.winId() == native_id, 'Changing opacity recreated the window'
        assert not has_theme_shadow(bubble), 'Shadow returned after hover / opacity changes'
        daily = SpeechBubble('日常气泡也没有矩形阴影', owner)
        try:
            daily.place_above(owner)
            daily.show()
            QTest.qWait(100)
            assert not has_theme_shadow(daily), 'Daily bubble still has a theme shadow'
        finally:
            daily.close()
        assert subprocess.check_output(['xprop', '-root', '_NET_ACTIVE_WINDOW'], text=True) == original_active
        print('PASS: no KDE shadow on Codex/daily bubbles; native circular mask; clicks pass through initially and at corners; continuous 3s hover enables clicks; exit restores pass-through; native ID/geometry/focus stable')
    finally:
        if bubble:
            bubble.close()
        owner.close()
        background.close()
        app.processEvents()
        xtst.XTestFakeMotionEvent(display, -1, original_cursor.x(), original_cursor.y(), 0)
        x11.XFlush(display)
        x11.XCloseDisplay(display)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
