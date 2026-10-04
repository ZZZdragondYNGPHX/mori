"""Private-bus Portal flow: dbus-run-session -- .venv/bin/python tests/manual_portal_smoke.py.

No real desktop permission dialog or input events are generated.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import ClassInfo, QCoreApplication, QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusObjectPath

from presentation.desktop_input import INTERFACE, REQUEST, ROOT, SERVICE, DesktopScroll


@ClassInfo({"D-Bus Interface": REQUEST})
class Request(QObject):
    Response = Signal("uint", "QVariantMap")

    @Slot()
    def Close(self):
        pass


@ClassInfo({"D-Bus Interface": INTERFACE})
class Portal(QObject):
    def __init__(self, bus, client):
        super().__init__()
        self.bus, self.client = bus, client
        self.requests = []
        self.axes = []
        self.methods = []
        self.types = None

    def reply(self, options, results):
        sender = self.client.bus.get_unique_name().lstrip(":").replace(".", "_")
        path = f"{ROOT}/request/{sender}/{options['handle_token']}"
        request = Request(self)
        self.requests.append(request)
        assert self.bus.registerObject(path, request, QDBusConnection.ExportAllSignals | QDBusConnection.ExportAllSlots)
        QTimer.singleShot(10, lambda: request.Response.emit(0, results))
        return QDBusObjectPath(path)

    @Slot("QVariantMap", result="QDBusObjectPath")
    def CreateSession(self, options):
        self.methods.append("CreateSession")
        return self.reply(options, {"session_handle": ROOT + "/session/mori_test"})

    @Slot("QDBusObjectPath", "QVariantMap", result="QDBusObjectPath")
    def SelectDevices(self, session, options):
        self.methods.append("SelectDevices")
        self.types = options["types"]
        return self.reply(options, {})

    @Slot("QDBusObjectPath", str, "QVariantMap", result="QDBusObjectPath")
    def Start(self, session, parent_window, options):
        self.methods.append("Start")
        return self.reply(options, {"devices": 2})

    @Slot("QDBusObjectPath", "QVariantMap", float, float)
    def NotifyPointerAxis(self, session, options, dx, dy):
        self.axes.append((dx, dy, options.get("finish")))


def main():
    os.environ["XDG_SESSION_TYPE"] = "wayland"
    app = QCoreApplication([])
    client = DesktopScroll()
    bus = QDBusConnection.sessionBus()
    if not bus.registerService(SERVICE):
        raise RuntimeError("Use dbus-run-session; never replace a real desktop Portal")
    portal = Portal(bus, client)
    assert bus.registerObject(ROOT, portal, QDBusConnection.ExportAllSlots)
    passed = [False]

    def ready(ok):
        if ok:
            assert portal.types == 2
            assert client.scroll(12.5, finish=True)

    def check():
        if portal.axes:
            assert portal.axes == [(0., 12.5, True)]
            assert portal.methods == ["CreateSession", "SelectDevices", "Start"]
            client.close()
            assert not client.requests and not client.session and not client.available
            assert not client._pump.isActive() and not client._timeout.isActive()
            passed[0] = True
            print("PASS: pointer-only authorization, axis delivery, finish event and session cleanup on private D-Bus")
            app.quit()

    client.ready.connect(ready)
    client.failed.connect(lambda reason: (print("FAIL:", reason), app.quit()))
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(check)
    timer.start()
    QTimer.singleShot(5000, app.quit)
    client.prepare()
    app.exec()
    client.close()
    bus.unregisterService(SERVICE)
    return 0 if passed[0] else 1


if __name__ == "__main__":
    raise SystemExit(main())
