"""Opt-in real XWayland input/stacking regression: python tests/manual_desktop_smoke.py.

Creates two temporary test windows, moves the pointer, then restores it.
Uses system libX11/libXtst for native pointer events, never sendEvent/QTest clicks.
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QPoint
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from presentation.platform import configure_desktop_platform


class NativeInputUnavailable(Exception):
    """The compositor refused test pointer injection; manual input is needed."""


def main():
    temporary = tempfile.TemporaryDirectory()
    os.environ['MORI_COMPANION_HOME'] = temporary.name
    os.environ.pop('QT_QPA_PLATFORM', None)
    configure_desktop_platform()
    from presentation.pet import DesktopPet
    app = QApplication([])
    if app.platformName() != 'xcb':
        raise RuntimeError('This test requires an X11/XWayland desktop and xcb Qt plugin')
    original_cursor = QCursor.pos()
    x11 = ctypes.CDLL('libX11.so.6')
    xtst = ctypes.CDLL('libXtst.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError('Could not connect to XWayland')
    pet = DesktopPet()
    normal = QWidget()
    normal.setWindowTitle('Mori temporary normal-window stacking test')
    try:
        pet.move(300, 300)
        pet.show()
        pet._idle_start()
        normal.setGeometry(240, 240, 400, 350)
        normal.show()
        normal.raise_()
        normal.activateWindow()
        QTest.qWait(1000)
        pet_id = hex(int(pet.winId()))
        normal_id = hex(int(normal.winId()))
        native_state = subprocess.check_output(['xprop', '-id', pet_id, '_NET_WM_STATE'], text=True)
        assert '_NET_WM_STATE_ABOVE' in native_state, native_state
        stacking = subprocess.check_output(['xprop', '-root', '_NET_CLIENT_LIST_STACKING'], text=True)
        ids = re.findall(r'0x[0-9a-f]+', stacking)
        assert ids.index(pet_id) > ids.index(normal_id), stacking

        def button(number, pressed):
            xtst.XTestFakeButtonEvent(display, number, int(pressed), 0)
            x11.XFlush(display)
            QTest.qWait(80)

        def move_pointer(point):
            xtst.XTestFakeMotionEvent(display, -1, point.x(), point.y(), 0)
            x11.XFlush(display)
            QTest.qWait(100)
            print('Pointer target:', point, 'actual:', QCursor.pos(), 'pet geometry:', pet.geometry())
            if QCursor.pos() != point:
                raise NativeInputUnavailable('Compositor did not move the test pointer')

        before = pet.pos()
        point = pet.mapToGlobal(QPoint(50, 60))
        move_pointer(point)
        QTest.qWait(80)
        button(1, True)
        assert pet._pressing, 'Native left-button press did not reach the pet'
        move_pointer(point + QPoint(100, 60))
        QTest.qWait(160)
        assert pet._state == 'pickup', pet._state
        button(1, False)
        assert pet.pos() != before, 'Native drag did not change actual window position'
        delta = pet.pos() - before
        assert abs(delta.x() - 100) <= 2 and abs(delta.y() - 60) <= 2, delta
        assert pet._state == 'idle', pet._state

        move_pointer(pet.mapToGlobal(QPoint(50, 60)))
        QTest.qWait(80)
        button(3, True)
        button(3, False)
        assert pet._context_menu and pet._context_menu.isVisible(), 'Native right-click did not open menu'
        menu = pet._context_menu
        action = next(a for a in menu.actions() if a.text() == '工具状态')
        move_pointer(menu.mapToGlobal(menu.actionGeometry(action).center()))
        QTest.qWait(80)
        button(1, True)
        button(1, False)
        assert pet._status_window and pet._status_window.isVisible(), 'Native menu selection failed'
        print('PASS: xcb backend; native ABOVE property and stacking above raised normal window; '
              f'physical input drag ({delta.x()}, {delta.y()}); right-click and menu action')
    finally:
        xtst.XTestFakeButtonEvent(display, 1, 0, 0)
        xtst.XTestFakeButtonEvent(display, 3, 0, 0)
        x11.XFlush(display)
        pet._quitting = True
        pet._cleanup()
        pet.close()
        normal.close()
        QCursor.setPos(original_cursor)
        x11.XCloseDisplay(display)
        temporary.cleanup()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except NativeInputUnavailable as error:
        print(f'SKIP native pointer injection: {error}; verify drag/right-click manually')
        raise SystemExit(77)
