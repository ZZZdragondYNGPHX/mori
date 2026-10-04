"""Native bubble/window damage probe using only temporary, owned windows."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from presentation.pet import ASSET_ROOT
from presentation.platform import configure_desktop_platform
from presentation.speech import SpeechBubble, head_anchor


def active_window():
    return subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"], text=True)


class ProbeWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.paints = 0
        self.setWindowTitle("Mori · 临时气泡验证")
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def paintEvent(self, event):
        self.paints += 1
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#e8edf3"))
        painter.setPen(QColor("#263b51"))
        painter.drawText(30, 50, "仅用于验证气泡；不操作其他软件窗口。")
        painter.end()


class ProbePet(QWidget):
    def __init__(self, target):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(280, 230)
        self.frames = [QPixmap(str(Path(ASSET_ROOT) / "motion" / f"window-peek-right-{i}.png"))
                       for i in range(4)]
        self.frame_index = 0
        self.clip = QRect(155, 0, 125, 230)
        self.anchor = head_anchor(self.frames[0], QPoint(), self.rect(), self.clip)
        self.interactions = SimpleNamespace(active=True, scene="peek",
                                            target=SimpleNamespace(geometry=target.frameGeometry()))

    def speech_anchor(self):
        return self.anchor

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setClipRect(self.clip)
        painter.drawPixmap(0, 0, self.frames[self.frame_index])
        painter.end()


def main():
    configure_desktop_platform()
    app = QApplication([])
    if app.platformName() != "xcb":
        print("SKIP: requires xcb/XWayland")
        return 77
    original_active = active_window()
    target = ProbeWindow()
    bounds = app.primaryScreen().availableGeometry()
    target.setGeometry(bounds.left() + bounds.width() // 4, bounds.top() + 300, 540, 300)
    owner = bubble = None
    try:
        target.show()
        QTest.qWait(400)
        owner = ProbePet(target)
        owner.move(target.frameGeometry().right() - 155, target.frameGeometry().top() + 90)
        owner.show()
        bubble = SpeechBubble("任务有新的进度。", owner, interactive="--interactive" in sys.argv)
        bubble.place_above(owner)
        bubble.show()
        QTest.qWait(300)
        geometry, native_id = bubble.geometry(), bubble.winId()
        assert not geometry.intersects(target.frameGeometry()), "Speech overlaps the target window"
        assert not geometry.intersects(owner.geometry()), "Transparent native surfaces overlap"
        paints = target.paints
        for index in range(24):
            owner.frame_index = index % 4
            owner.update()
            bubble.follow(owner)
            QTest.qWait(40)
            assert bubble.geometry() == geometry, "Speech jumps between pose frames"
            assert bubble.winId() == native_id, "Speech native window was recreated"
        assert target.paints == paints, "Pet/speech repeatedly repainted the target window"
        assert active_window() == original_active, "The probe changed desktop focus"
        print("PASS: native peek and bubble; stable geometry/id; zero extra target paints; focus unchanged")
    finally:
        if bubble:
            bubble.hide()
        if owner:
            owner.close()
        target.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
