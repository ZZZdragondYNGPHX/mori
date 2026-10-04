"""Native xcb speech test: python tests/manual_bubble_smoke.py.

Checks tooltip classification, absence from the managed task list, unchanged
active desktop window, and native-window reuse. No pointer/keyboard injection.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from presentation.pet import SpeechBubble
from presentation.platform import configure_desktop_platform


def property_text(*args):
    return subprocess.check_output(['xprop', *args], text=True)


def managed_windows():
    return set(re.findall(r'0x[0-9a-f]+', property_text('-root', '_NET_CLIENT_LIST')))


def main():
    configure_desktop_platform()
    app = QApplication([])
    if app.platformName() != 'xcb':
        print('SKIP: native speech probe requires xcb/XWayland')
        return 77
    # Keep the owner hidden so the test itself adds no ordinary task window.
    owner = QWidget()
    owner.setGeometry(200, 200, 120, 149)
    bubble = SpeechBubble('Mori 消息气泡验证', owner)
    try:
        clients = managed_windows()
        active = property_text('-root', '_NET_ACTIVE_WINDOW')
        bubble.place_above(owner)
        bubble.show()
        QTest.qWait(150)
        native_id = hex(int(bubble.winId()))
        window_type = property_text('-id', native_id, '_NET_WM_WINDOW_TYPE')
        assert '_NET_WM_WINDOW_TYPE_TOOLTIP' in window_type, window_type
        assert managed_windows() == clients, 'Showing speech changed the managed task list'
        assert native_id not in managed_windows(), 'Speech entered the managed client list'
        assert property_text('-root', '_NET_ACTIVE_WINDOW') == active, 'Speech changed active window'
        for index in range(5):
            bubble.set_text(f'连续消息 {index}')
            bubble.place_above(owner)
            QTest.qWait(20)
            assert hex(int(bubble.winId())) == native_id
        bubble.hide()
        bubble.set_text('隐藏后重新显示')
        bubble.show()
        QTest.qWait(150)
        assert hex(int(bubble.winId())) == native_id
        assert property_text('-root', '_NET_ACTIVE_WINDOW') == active
        assert managed_windows() == clients
        assert bubble.windowFlags() & Qt.WindowTransparentForInput
        print('PASS: native TOOLTIP; no task added; active desktop window unchanged; same window reused')
    finally:
        bubble.hide()
        owner.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
