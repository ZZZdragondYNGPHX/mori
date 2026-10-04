"""Temporary KWin scripts for window geometry and reversible minimization.

Uses the KWin 6 scripting API; no clicks, title scraping, or global KWin config.
https://develop.kde.org/docs/plasma/kwin/api/
"""
import configparser
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import ClassInfo, QObject, QPoint, QRect, QTimer, Signal, Slot

MAXIMIZED_JS = """
function isMaximized(w) {
    if (typeof w.maximizeMode === 'number') return w.maximizeMode === 3;
    var g=w.frameGeometry,a=workspace.clientArea(KWin.MaximizeArea,w);
    return Math.abs(g.x-a.x)<2 && Math.abs(g.y-a.y)<2 && Math.abs(g.width-a.width)<2 && Math.abs(g.height-a.height)<2;
}
"""

try:
    from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher
except ImportError:  # QtDBus is optional on non-KDE platforms, including Windows.
    QDBusConnection = QDBusMessage = QDBusPendingCallWatcher = None


@dataclass(frozen=True)
class DesktopWindow:
    identity: str
    application: str
    geometry: QRect
    active: bool = False
    normal: bool = False
    minimizable: bool = False
    minimized: bool = False
    fullscreen: bool = False
    pid: int = 0
    keep_above: bool = False
    stacking_order: int = 0
    current_desktop: bool = True
    movable: bool = True
    resizable: bool = True
    maximized: bool = False
    interactive: bool = False
    titlebar_height: int = 30

    def eligible(self, preferences, include_minimized=False):
        return bool(self.identity and self.application and self.normal and
                    (include_minimized or not self.minimized) and not self.fullscreen and
                    self.current_desktop and self.pid != os.getpid() and
                    self.geometry.width() >= 120 and self.geometry.height() >= 80 and
                    not preferences.excludes(self.application))


@ClassInfo({"D-Bus Interface": "org.mori.Companion.Desktop"})
class KWinDesktop(QObject):
    changed = Signal(object)
    operation_finished = Signal(str, bool)
    availability_changed = Signal()
    geometry_finished = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = QDBusConnection.sessionBus() if QDBusConnection is not None else None
        self.service = "org.mori.Companion.p" + str(os.getpid()) + ".i" + uuid.uuid4().hex
        self.available = False
        self.active_window = None
        self.windows = {}
        self.reason = "窗口互动支持 KDE Plasma 6；当前桌面未提供 KWin 接口。"
        self._directory = None
        self._scripts = {}
        self._registered = False
        self._pending = None
        self.authorize = lambda _window: False
        self.authorize_geometry = lambda _window: False
        self._geometry_pending = None
        self._geometry_queued = None
        self._geometry_timeout = QTimer(self)
        self._geometry_timeout.setSingleShot(True)
        self._geometry_timeout.setInterval(2500)
        self._geometry_timeout.timeout.connect(lambda: self._finish_geometry(False))
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(2500)
        self._timeout.timeout.connect(self._operation_timeout)

    def _call(self, path, interface, method, arguments, callback):
        message = QDBusMessage.createMethodCall("org.kde.KWin", path, interface, method)
        message.setArguments(arguments)
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(message, 1500), self)

        def finished(call):
            reply = call.reply()
            call.deleteLater()
            callback(reply.arguments() if reply.type() != QDBusMessage.ErrorMessage else None)

        watcher.finished.connect(finished)

    def start(self):
        if self._registered:
            return
        if self.bus is None:
            return
        interface = self.bus.interface()
        if not self.bus.isConnected() or interface is None or not interface.isServiceRegistered("org.kde.KWin").value():
            return
        if not self.bus.registerService(self.service):
            return
        if not self.bus.registerObject("/Desktop", self, QDBusConnection.ExportAllSlots):
            self.bus.unregisterService(self.service)
            return
        self._registered = True
        self._directory = tempfile.TemporaryDirectory(prefix="mori-kwin-")
        script = MAXIMIZED_JS + """
function send(data) { callDBus(__SERVICE__, '/Desktop', 'org.mori.Companion.Desktop', 'Report', JSON.stringify(data)); }
function info(w) {
    if (!w) return null;
    var g = w.frameGeometry;
    return {id:String(w.internalId), app:String(w.resourceClass || w.desktopFileName || ''),
        x:g.x,y:g.y,width:g.width,height:g.height,active:w.active,normal:w.normalWindow && !w.specialWindow && !w.dialog && !w.modal,
        minimizable:w.minimizable,minimized:w.minimized,fullscreen:w.fullScreen,pid:w.pid,
        keepAbove:w.keepAbove,stackingOrder:w.stackingOrder,
        movable:w.moveable,resizable:w.resizeable,maximized:isMaximized(w),
        interactive:w.move || w.resize,titlebarHeight:Math.max(0,w.clientGeometry.y-g.y),
        currentDesktop:(w.onAllDesktops || w.desktops.indexOf(workspace.currentDesktop) >= 0) &&
            (!w.activities.length || w.activities.indexOf(workspace.currentActivity) >= 0)};
}
function report(w) { send({window:info(w), active:workspace.activeWindow ? String(workspace.activeWindow.internalId) : ''}); }
function snapshot() { send({windows:workspace.windowList().map(info),
    active:workspace.activeWindow ? String(workspace.activeWindow.internalId) : ''}); }
function watch(w) {
    if (w.pid === __PID__ || String(w.resourceClass).toLowerCase().replace(/ /g,'-') === 'mori-companion') return;
    w.frameGeometryChanged.connect(function(){report(w);});
    w.minimizedChanged.connect(function(){report(w);});
    w.fullScreenChanged.connect(function(){report(w);});
    w.keepAboveChanged.connect(function(){report(w);});
    w.stackingOrderChanged.connect(function(){report(w);});
    w.desktopsChanged.connect(function(){report(w);});
    w.activitiesChanged.connect(function(){report(w);});
    w.moveResizedChanged.connect(function(){report(w);});
    w.maximizedChanged.connect(function(){report(w);});
}
workspace.windowList().forEach(watch);
workspace.windowAdded.connect(function(w){watch(w);report(w);});
workspace.windowRemoved.connect(function(w){send({removed:String(w.internalId)});});
workspace.windowActivated.connect(snapshot);
workspace.currentDesktopChanged.connect(snapshot);
workspace.currentActivityChanged.connect(snapshot);
snapshot();
""".replace("__SERVICE__", json.dumps(self.service)).replace("__PID__", str(os.getpid()))
        self._load(script, "observer", lambda success: self._ready(success))

    def _ready(self, success):
        self.available = success and self._registered
        self.reason = "KDE 窗口互动已就绪。" if success else "KWin 窗口接口暂时不可用；仍可体验桌面动作。"
        self.availability_changed.emit()

    def _load(self, code, kind, done):
        if not self._registered:
            done(False)
            return
        name = "mori-" + kind + "-" + uuid.uuid4().hex
        path = Path(self._directory.name) / (name + ".js")
        path.write_text(code, encoding="utf-8")
        self._scripts[name] = path

        def loaded(arguments):
            if arguments is None or not arguments or int(arguments[0]) < 0 or name not in self._scripts:
                # A load may finish after stop(); unload even if its local entry is gone.
                self._call("/Scripting", "org.kde.kwin.Scripting", "unloadScript", [name], lambda _reply: None)
                self._unload(name)
                done(False)
                return
            self._call("/Scripting/Script" + str(arguments[0]), "org.kde.kwin.Script", "run", [],
                       lambda reply: done(reply is not None))

        self._call("/Scripting", "org.kde.kwin.Scripting", "loadScript", [str(path), name], loaded)
        return name

    def _unload(self, name):
        path = self._scripts.pop(name, None)
        if path:
            self._call("/Scripting", "org.kde.kwin.Scripting", "unloadScript", [name], lambda _reply: None)
            path.unlink(missing_ok=True)

    @Slot(str)
    def Report(self, message):  # noqa: N802
        try:
            data = json.loads(message)
            if "geometryOperation" in data:
                if self._geometry_pending and data["geometryOperation"] == self._geometry_pending[0]:
                    self._finish_geometry(bool(data.get("ok")))
                return
            if "operation" in data:
                if self._pending and data["operation"] == self._pending[0]:
                    self._finish_operation(bool(data.get("ok")))
                return
            removed = data.get("removed")
            if removed:
                self.windows.pop(removed, None)
                if self.active_window and self.active_window.identity == removed:
                    self.active_window = None
                self.changed.emit(None)
                return
            previous = dict(self.windows)
            previous_active = self.active_window
            items = data.get("windows", [data.get("window")])
            if "windows" in data:
                self.windows.clear()
            for item in items[:128]:
                if not item:
                    continue
                window = DesktopWindow(str(item["id"]), str(item["app"])[:160],
                    QRect(round(item["x"]), round(item["y"]), round(item["width"]), round(item["height"])),
                    bool(item["active"]), bool(item["normal"]), bool(item["minimizable"]),
                    bool(item["minimized"]), bool(item["fullscreen"]), int(item["pid"]),
                    bool(item.get("keepAbove", False)), int(item.get("stackingOrder", 0)),
                    bool(item.get("currentDesktop", True)), bool(item.get("movable", True)),
                    bool(item.get("resizable", True)), bool(item.get("maximized", False)),
                    bool(item.get("interactive", False)), int(item.get("titlebarHeight", 30)))
                self.windows[window.identity] = window
                if len(self.windows) > 128:
                    self.windows.pop(next(iter(self.windows)))
            active = data.get("active", "")
            self.windows = {key: replace(window, active=key == active)
                            for key, window in self.windows.items()}
            self.active_window = self.windows.get(active)
            if previous != self.windows or previous_active != self.active_window:
                self.changed.emit(self.active_window)
        except (ValueError, KeyError, TypeError):
            return

    @Slot(str, result=bool)
    def Authorize(self, request):  # noqa: N802
        return bool(self._pending and self._pending[0] == request and
                    self.authorize(self._pending[1]))

    def minimize(self, window):
        return self._operate(window.identity, restore=False)

    def restore(self, identity):
        return self._operate(identity, restore=True)

    def maximize(self, window):
        return self._operate(window.identity, restore=False, maximize=True)

    def _operate(self, identity, restore, maximize=False, activate=False, target_pid=0):
        if not self._registered or self._pending:
            return False
        request = uuid.uuid4().hex
        self._pending = (request, identity, None)
        code = MAXIMIZED_JS + """
var id = __ID__, request = __REQUEST__;
function result(ok) { callDBus(__SERVICE__,'/Desktop','org.mori.Companion.Desktop','Report',JSON.stringify({operation:request,ok:ok})); }
function apply(allowed) {
    if (!allowed) { result(false); return; }
    var w = workspace.windowList().find(function(w){return String(w.internalId) === id;});
    if (!w || !w.normalWindow || w.specialWindow || w.dialog || w.modal || w.pid === __PID__) { result(false); return; }
    if (__ACTIVATE__) {
        if(w.pid!==__TARGETPID__) {result(false);return;}
        if(w.desktops.length && !w.onAllDesktops) workspace.currentDesktop=w.desktops[0];
        if(w.activities.length && w.activities.indexOf(workspace.currentActivity)<0) workspace.currentActivity=w.activities[0];
        w.minimized=false; workspace.activeWindow=w; result(true);
    }
    else if (__RESTORE__) { if (!w.minimized) {result(false);return;} w.minimized=false;result(!w.minimized); }
    else if (__MAXIMIZE__) { if (workspace.activeWindow !== w || !w.maximizable || w.minimized || w.fullScreen) {result(false);return;} var value=!isMaximized(w);w.setMaximize(value,value);result(true); }
    else { if (workspace.activeWindow !== w || !w.minimizable || w.minimized || w.fullScreen) {result(false);return;} w.minimized=true;result(w.minimized); }
}
if (__RESTORE__ || __ACTIVATE__) apply(true);
else callDBus(__SERVICE__,'/Desktop','org.mori.Companion.Desktop','Authorize',request,function(allowed){apply(allowed);});
"""
        for key, value in (("__SERVICE__", self.service), ("__ID__", identity), ("__REQUEST__", request),
                           ("__RESTORE__", restore), ("__MAXIMIZE__", maximize), ("__PID__", os.getpid()),
                           ("__ACTIVATE__", activate), ("__TARGETPID__", target_pid)):
            code = code.replace(key, json.dumps(value))
        name = self._load(code, "operation", lambda success: self._finish_operation(False)
                          if not success and self._pending and self._pending[0] == request else None)
        if self._pending:
            self._pending = (request, identity, name)
            self._timeout.start()
        return True

    def _operation_timeout(self):
        self._finish_operation(False)

    def _finish_operation(self, success):
        if not self._pending:
            return
        _request, identity, name = self._pending
        self._pending = None
        self._timeout.stop()
        if name:
            self._unload(name)
        self.operation_finished.emit(identity, success)

    def cancel_pending(self):
        self._finish_operation(False)
        self._geometry_queued = None
        self._finish_geometry(False, notify=False)

    @Slot(str, result=bool)
    def AuthorizeGeometry(self, request):  # noqa: N802
        return bool(self._geometry_pending and self._geometry_pending[0] == request and
                    self.authorize_geometry(self._geometry_pending[1]))

    def set_geometry(self, window, geometry):
        """One request in flight; coalesce animation frames without accumulating scripts."""
        if not self._registered or not self.authorize_geometry(window.identity):
            return False
        if self._geometry_pending:
            self._geometry_queued = (window, QRect(geometry))
            return True
        request = uuid.uuid4().hex
        self._geometry_pending = (request, window.identity, None)
        code = """
var id=__ID__, request=__REQUEST__, rect=__RECT__;
function result(ok) {callDBus(__SERVICE__,'/Desktop','org.mori.Companion.Desktop','Report',JSON.stringify({geometryOperation:request,ok:ok}));}
callDBus(__SERVICE__,'/Desktop','org.mori.Companion.Desktop','AuthorizeGeometry',request,function(allowed){
    var w=workspace.windowList().find(function(w){return String(w.internalId)===id;});
    if (!allowed || !w || !w.normalWindow || w.specialWindow || w.dialog || w.modal ||
        w.pid===__PID__ || w.minimized || w.fullScreen || w.move || w.resize || !w.moveable) {result(false);return;}
    var g=w.frameGeometry;
    if ((g.width!==rect.width || g.height!==rect.height) && !w.resizeable) {result(false);return;}
    w.frameGeometry=rect;
    result(true);
});
"""
        for key, value in (("__ID__", window.identity), ("__REQUEST__", request),
                           ("__SERVICE__", self.service), ("__PID__", os.getpid()),
                           ("__RECT__", dict(x=geometry.x(), y=geometry.y(), width=geometry.width(), height=geometry.height()))):
            code = code.replace(key, json.dumps(value))
        name = self._load(code, "geometry", lambda ok: self._finish_geometry(False)
                          if not ok and self._geometry_pending and self._geometry_pending[0] == request else None)
        if self._geometry_pending:
            self._geometry_pending = (request, window.identity, name)
            self._geometry_timeout.start()
        return True

    def _finish_geometry(self, success, notify=True):
        if not self._geometry_pending:
            return
        _request, identity, name = self._geometry_pending
        self._geometry_pending = None
        self._geometry_timeout.stop()
        if name:
            self._unload(name)
        queued, self._geometry_queued = self._geometry_queued, None
        if notify:
            self.geometry_finished.emit(identity, success)
        if success and queued:
            self.set_geometry(*queued)

    @property
    def geometry_busy(self):
        return self._geometry_pending is not None

    @staticmethod
    def button_point(window, action="minimize"):
        """Honor configured titlebar side/order; retain a bounded CSD fallback."""
        config = configparser.ConfigParser(interpolation=None)
        try:
            config.read(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kwinrc")
            section = config["org.kde.kdecoration2"] if config.has_section("org.kde.kdecoration2") else {}
            left, right = section.get("ButtonsOnLeft", "MS"), section.get("ButtonsOnRight", "IAX")
        except (OSError, configparser.Error):
            left, right = "MS", "IAX"
        symbol = "I" if action == "minimize" else "A"
        g = window.geometry
        height = max(22, min(44, window.titlebar_height or 30))
        if symbol in right:
            return QPoint(g.right() - 8 - round((len(right) - right.index(symbol) - .5) * height), g.top() + height // 2)
        if symbol in left:
            return QPoint(g.left() + 8 + round((left.index(symbol) + .5) * height), g.top() + height // 2)
        return QPoint(g.right() - 80, g.top() + 16)

    def activate(self, identity, pid, done):
        """Explicit return; wait for the script acknowledgement and actual activation."""
        if (not self._registered or identity not in self.windows
                or self.windows[identity].pid != pid or self._pending):
            done(False)
            return
        def finished(window_id, ok):
            if window_id != identity:
                return
            self.operation_finished.disconnect(finished)
            if not ok:
                done(False)
                return
            timer = QTimer(self)
            timer.setInterval(75)
            deadline = time.monotonic() + 1.5
            def verify():
                window = self.windows.get(identity)
                success = bool(window and window.active and window.current_desktop and not window.minimized)
                if success or not self._registered or time.monotonic() >= deadline:
                    timer.stop()
                    timer.deleteLater()
                    done(success and self._registered)
            timer.timeout.connect(verify)
            timer.start()
        self.operation_finished.connect(finished)
        if not self._operate(identity, restore=False, activate=True, target_pid=pid):
            self.operation_finished.disconnect(finished)
            done(False)

    def stop(self):
        self.cancel_pending()
        for name in list(self._scripts):
            self._unload(name)
        if self._registered:
            self.bus.unregisterObject("/Desktop")
            self.bus.unregisterService(self.service)
            self._registered = False
        if self._directory:
            self._directory.cleanup()
            self._directory = None
        self.available = False
        self.windows.clear()
        self.active_window = None
