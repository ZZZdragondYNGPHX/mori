"""Inspect owned XWayland window buffers, without forcing QWidget.grab paints."""
import ctypes
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from presentation.platform import configure_desktop_platform
from presentation.speech import SpeechBubble


class Rectangle(ctypes.Structure):
    _fields_ = [('x', ctypes.c_short), ('y', ctypes.c_short),
               ('width', ctypes.c_ushort), ('height', ctypes.c_ushort)]


def main():
    configure_desktop_platform()
    app = QApplication([])
    if app.platformName() != 'xcb':
        print('SKIP: requires xcb/XWayland and a running compositor')
        return 77
    x11 = ctypes.CDLL('libX11.so.6')
    composite = ctypes.CDLL('libXcomposite.so.1')
    shape = ctypes.CDLL('libXext.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                             ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
    x11.XGetImage.restype = ctypes.c_void_p
    x11.XGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    x11.XGetPixel.restype = ctypes.c_ulong
    x11.XDestroyImage.argtypes = [ctypes.c_void_p]
    x11.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XFree.argtypes = [ctypes.c_void_p]
    composite.XCompositeNameWindowPixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    composite.XCompositeNameWindowPixmap.restype = ctypes.c_ulong
    shape.XShapeGetRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    shape.XShapeGetRectangles.restype = ctypes.POINTER(Rectangle)
    display = x11.XOpenDisplay(None)
    assert display, 'No X display'
    active = subprocess.check_output(['xprop', '-root', '_NET_ACTIVE_WINDOW'], text=True)
    owner = QWidget()
    owner.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
    owner.setAttribute(Qt.WA_ShowWithoutActivating)
    bounds = app.primaryScreen().availableGeometry()
    owner.setGeometry(bounds.center().x(), bounds.center().y(), 280, 230)
    bubble = SpeechBubble('测试', owner, interactive=True)

    def rectangles(kind):
        count, ordering = ctypes.c_int(), ctypes.c_int()
        pointer = shape.XShapeGetRectangles(display, int(bubble.winId()), kind,
                                            ctypes.byref(count), ctypes.byref(ordering))
        try:
            return [(r.x, r.y, r.width, r.height) for r in pointer[:count.value]] if pointer else []
        finally:
            if pointer:
                x11.XFree(pointer)

    def check_input(enabled):
        app.sync()
        inputs, bounds = rectangles(2), rectangles(0)
        assert bool(inputs) == enabled, 'Native input does not match hover state'

        def contains(region, px, py):
            return any(x <= px < x + w and y <= py < y + h for x, y, w, h in region)

        # X11 hit testing uses the intersection of the input and bounding shapes.
        assert not (contains(inputs, 0, 0) and contains(bounds, 0, 0)), \
            'Effective native input includes a transparent corner'
        if enabled:
            center = bubble._body_rect().center().toPoint()
            assert contains(inputs, center.x(), center.y()) and contains(bounds, center.x(), center.y()), \
                'Unlocked circle cannot receive clicks'

    def check_buffer(stage):
        # sync publishes pending Qt drawing; XComposite reads the real native
        # storage, including pixels hidden by a bounding mask. QWidget.grab()
        # would repaint into a fresh image and miss opaque black native corners.
        app.sync()
        pixmap = composite.XCompositeNameWindowPixmap(display, int(bubble.winId()))
        image = x11.XGetImage(display, pixmap, 0, 0, bubble.width(), bubble.height(), 0xffffffff, 2)
        assert image, 'Could not read the owned native buffer'
        try:
            w, h = bubble.width(), bubble.height()
            corners = [(0, 0), (4, 4), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
            pixels = [x11.XGetPixel(image, x, y) for x, y in corners]
            assert all(pixel >> 24 == 0 for pixel in pixels), (stage, [hex(p) for p in pixels])
            center = bubble._body_rect().center().toPoint()
            assert x11.XGetPixel(image, center.x(), center.y() + 25) >> 24 > 0, 'Bubble was not drawn'
        finally:
            x11.XDestroyImage(image)
            x11.XFreePixmap(display, pixmap)

    try:
        owner.show()
        bubble.place_above(owner)
        bubble.show()
        QTest.qWait(80)
        bubble._hover.stop()  # Exercise unlock directly without depending on the user's cursor.
        native_id = bubble.winId()
        check_buffer('first show')
        check_input(False)
        bubble._set_input_enabled(True)
        assert not bubble.mask().isEmpty()
        check_buffer('click unlocked')
        check_input(True)
        for index, text in enumerate(('等等，尾巴还没跟上！', '提起来的时候，脑袋里的饭团也晃了一下。',
                                      '稳稳落地！', '测试')):
            bubble.set_text(text)
            owner.move(owner.x() + 12, owner.y() - 20)
            bubble.place_above(owner)
            QTest.qWait(20)
            check_buffer(f'drag / resize {index}')
            check_input(False)
            assert not bubble._input_enabled and bubble.mask().isEmpty()
            assert bubble.winId() == native_id
        bubble.hide()
        bubble.set_text('隐藏后重新拖拽')
        bubble.place_above(owner)
        bubble.show()
        QTest.qWait(20)
        check_buffer('cached show')
        assert bubble.winId() == native_id
        assert subprocess.check_output(['xprop', '-root', '_NET_ACTIVE_WINDOW'], text=True) == active
        print('PASS: native buffer corners alpha=0 on show, click unlock, drag, resize and reuse; '
              'native input empty until unlocked, corners excluded; ID/focus unchanged; no input injection')
    finally:
        bubble.close()
        owner.close()
        app.processEvents()
        x11.XCloseDisplay(display)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
