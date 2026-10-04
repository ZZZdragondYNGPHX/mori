"""Local KWin integration on one isolated child window; never target existing apps."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QRect, QTimer
from PySide6.QtWidgets import QApplication

from presentation.platform import configure_desktop_platform


def main():
    os.environ.pop("QT_QPA_PLATFORM", None)
    configure_desktop_platform()
    from presentation.desktop_interaction import KWinDesktop

    app = QApplication([])
    desktop = KWinDesktop()
    child = None
    state = dict(stage="observer", identity="", goal=None, success=False)

    def fail(message):
        print("FAIL:", message)
        app.exit(1)

    def ready():
        nonlocal child
        if not desktop.available:
            fail(desktop.reason)
            return
        child = subprocess.Popen([sys.executable, "-c", """
import sys
sys.path.insert(0, 'src')
from presentation.platform import configure_desktop_platform
configure_desktop_platform()
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import QTimer
app=QApplication([])
w=QWidget();w.setWindowTitle('Mori isolated system smoke');w.setGeometry(420,300,500,360)
w.show();w.raise_();w.activateWindow();QTimer.singleShot(15000,app.quit);app.exec()
"""], cwd=Path(__file__).resolve().parents[1])
        state["stage"] = "window"

    def poll():
        if not child:
            return
        window = next((w for w in desktop.windows.values() if w.pid == child.pid), None)
        if not window:
            return
        state["identity"] = window.identity
        if state["stage"] == "window" and window.active:
            state["goal"] = QRect(window.geometry).translated(60, 20)
            state["stage"] = "move"
            if not desktop.set_geometry(window, state["goal"]):
                fail("geometry request rejected")
        elif state["stage"] == "move" and window.geometry == state["goal"] and not desktop.geometry_busy:
            print("PASS: native window geometry readback", window.geometry)
            state["stage"] = "maximize"
            if not desktop.maximize(window):
                fail("maximize request rejected")
        elif state["stage"] == "wait-maximized" and window.maximized:
            print("PASS: native maximization and state readback")
            state["stage"] = "unmaximize"
            desktop.maximize(window)
        elif state["stage"] == "wait-unmaximized" and not window.maximized:
            print("PASS: native unmaximization and state readback")
            state["stage"] = "minimize"
            desktop.minimize(window)

    def finished(identity, success):
        if identity != state["identity"]:
            fail("unexpected target")
            return
        if not success:
            fail("native operation failed")
            return
        if state["stage"] == "maximize":
            state["stage"] = "wait-maximized"
        elif state["stage"] == "unmaximize":
            state["stage"] = "wait-unmaximized"
        elif state["stage"] == "minimize":
            print("PASS: native minimization")
            state["stage"] = "restore"
            desktop.restore(identity)
        elif state["stage"] == "restore":
            print("PASS: restoration by the same window identity")
            state["success"] = True
            app.quit()

    desktop.authorize = desktop.authorize_geometry = lambda identity: bool(child and any(
        w.identity == identity and w.pid == child.pid for w in desktop.windows.values()))
    desktop.availability_changed.connect(ready)
    desktop.operation_finished.connect(finished)
    desktop.geometry_finished.connect(lambda identity, ok: None if ok else fail("geometry operation failed"))
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(poll)
    timer.start()
    QTimer.singleShot(12000, lambda: fail("KWin smoke timed out"))
    desktop.start()
    try:
        app.exec()
    finally:
        desktop.stop()
        if child:
            child.terminate()
            child.wait(timeout=3)
    return 0 if state["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
