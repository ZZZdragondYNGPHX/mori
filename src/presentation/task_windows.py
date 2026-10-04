"""Read-only Konsole tab tracking, plus an explicit user-click return action."""
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from .desktop_interaction import KWinDesktop


@dataclass(frozen=True)
class TerminalSnapshot:
    service: str
    session: int
    window: str
    main_window: str
    pid: int
    selected: bool
    active: bool


def dbus_call(service, path, method, *args):
    command = shutil.which('qdbus6')
    if not command:
        raise OSError('需要 qdbus6 才能定位 Konsole 标签页')
    argv = [command, service, path, method, *map(str, args)] if method else [command, service]
    reply = subprocess.run(argv,
                           capture_output=True, text=True, timeout=.7, check=True)
    return reply.stdout.strip()


def inspect_terminal(binding, metadata=None, cycle=None):
    service = binding.get('service', '')
    session_path = binding.get('session', '')
    native = binding.get('native_window', '')
    owner = binding.get('window', '')
    if not re.fullmatch(r':[0-9]+\.[0-9]+|org\.kde\.konsole[-.\w]*', service):
        raise ValueError('无法定位此终端')
    match = re.fullmatch(r'/Sessions/(\d+)', session_path)
    if not match or not native.isdigit() or not re.fullmatch(r'/Windows/\d+', owner):
        raise ValueError('此终端没有提供窗口与标签页标识')
    session = int(match[1])
    metadata = {} if metadata is None else metadata
    cycle = {} if cycle is None else cycle
    fingerprint = service, native
    if fingerprint not in metadata:
        objects = dbus_call(service, '/', '').splitlines()
        mains = [p for p in objects if re.fullmatch(r'/konsole/MainWindow_\d+', p)]
        main = next((path for path in mains if
                     dbus_call(service, path, 'org.kde.KMainWindow.winId') == native), None)
        if not main:
            raise ValueError('对应终端窗口已关闭')
        pid = int(dbus_call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                            'org.freedesktop.DBus.GetConnectionUnixProcessID', service))
        metadata[fingerprint] = main, pid
    main, pid = metadata[fingerprint]
    # All tasks in the same native window share these three reads in each cycle.
    window_key = service, owner, main
    if window_key not in cycle:
        try:
            sessions = dbus_call(service, owner, 'org.kde.konsole.Window.sessionList').splitlines()
            selected = dbus_call(service, owner, 'org.kde.konsole.Window.currentSession')
            active = dbus_call(service, main, 'org.qtproject.Qt.QWidget.isActiveWindow') == 'true'
        except (OSError, subprocess.SubprocessError):
            metadata.pop(fingerprint, None)
            raise
        cycle[window_key] = sessions, selected, active
    sessions, selected, active = cycle[window_key]
    if str(session) not in sessions:
        raise ValueError('标签页已关闭或移入其他窗口，请重新连接 Codex')
    return TerminalSnapshot(service, session, owner, main, pid, selected == str(session), active)


def visibility(snapshot, target, windows, own_pid):
    if snapshot is None or not snapshot.selected or not snapshot.active:
        return 'background'
    if target is None:
        return 'covered'  # Unknown occlusion must not silently suppress ordinary text.
    if target.minimized or not target.current_desktop:
        return 'background'
    for other in windows:
        if (other.identity != target.identity and other.pid != own_pid
                and not other.minimized and other.current_desktop
                and other.stacking_order > target.stacking_order
                and other.geometry.intersects(target.geometry)):
            return 'covered'
    return 'visible'


class TaskWindows(QObject):
    changed = Signal()
    returned = Signal(str, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.desktop = KWinDesktop(self)
        self.desktop.changed.connect(self.desktop_changed)
        self.generation = 0
        self.active_identity = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='mori-terminal')
        self.bindings = {}
        self.snapshots = {}
        self.identities = {}
        self.errors = {}
        self.metadata = {}
        self.future = None
        self.return_future = None
        self.returning = set()
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.poll)

    def bind(self, key, binding):
        if self.bindings.get(key) == binding:
            return
        self.bindings[key] = binding
        self.identities.pop(key, None)
        self.snapshots.pop(key, None)
        self.desktop.start()
        self.timer.start()
        self.poll()

    def poll(self):
        if self.closed:
            return
        if self.return_future and self.return_future.done():
            key, result, phase = self.return_future.result()
            self.return_future = None
            if result is None:
                self.returning.discard(key)
                self.errors[key] = '窗口或标签页切换未确认，请手动切回。'
                self.returned.emit(key, False, self.errors[key])
            elif phase == 'selected':
                self.returning.discard(key)
                self.returned.emit(key, True, '')
            else:
                identity = self.identities.get(key)
                self.returning.add(key)
                binding = dict(self.bindings[key])
                def activated(ok, key=key, binding=binding, snapshot=result):
                    if self.closed or not ok:
                        self.returning.discard(key)
                        if not self.closed:
                            self.returned.emit(key, False, '窗口激活未确认，请手动切回。')
                        return
                    self.return_future = self.executor.submit(self.confirm_selection, key, binding, snapshot)
                self.desktop.activate(identity, result.pid, activated)
        if self.future and self.future.done():
            snapshots, errors, generation = self.future.result()
            self.future = None
            self.snapshots = {key: value for key, value in snapshots.items() if key in self.bindings}
            self.errors = {key: value for key, value in errors.items() if key in self.bindings}
            self.refresh_visibility(associate=generation == self.generation)
        if self.future is None and self.bindings:
            bindings = dict(self.bindings)
            self.future = self.executor.submit(self.inspect_all, bindings, self.generation, self.metadata)

    @staticmethod
    def inspect_all(bindings, generation, metadata=None):
        snapshots, errors, cache, cycle = {}, {}, {}, {}
        metadata = {} if metadata is None else metadata
        for key, binding in bindings.items():
            fingerprint = tuple(sorted(binding.items()))
            if fingerprint not in cache:
                try:
                    cache[fingerprint] = (inspect_terminal(binding, metadata, cycle), '')
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    cache[fingerprint] = (None, str(error))
            snapshot, error = cache[fingerprint]
            if snapshot:
                snapshots[key] = snapshot
            else:
                errors[key] = error
        return snapshots, errors, generation

    def desktop_changed(self, _window):
        active = self.desktop.active_window
        identity = active.identity if active else None
        if identity != self.active_identity:
            self.active_identity = identity
            self.generation += 1
        self.changed.emit()

    def refresh_visibility(self, associate=True):
        for key, snapshot in self.snapshots.items():
            candidates = [w for w in self.desktop.windows.values()
                          if w.pid == snapshot.pid and w.normal and 'konsole' in w.application.lower()]
            active = self.desktop.active_window
            if associate and snapshot.active and active and active.pid == snapshot.pid and active in candidates:
                self.identities[key] = active.identity
            elif self.identities.get(key) not in self.desktop.windows and len(candidates) == 1:
                self.identities[key] = candidates[0].identity
        self.changed.emit()

    def strength(self, key):
        import os
        return visibility(self.snapshots.get(key), self.desktop.windows.get(self.identities.get(key)),
                          self.desktop.windows.values(), os.getpid())

    def can_return(self, key):
        return (key in self.bindings and key in self.snapshots and self.identities.get(key) in self.desktop.windows
                and self.return_future is None and not self.returning)

    def return_to(self, key):
        if not self.can_return(key):
            self.returned.emit(key, False, self.errors.get(key, '尚未定位到对应窗口，请手动切回一次。'))
            return
        binding = dict(self.bindings[key])
        snapshot = self.snapshots[key]
        def switch():
            try:
                fresh = inspect_terminal(binding)
                if (fresh.window, fresh.pid, fresh.main_window) != (snapshot.window, snapshot.pid, snapshot.main_window):
                    return key, None, 'activate'
                dbus_call(fresh.service, fresh.window, 'org.kde.konsole.Window.setCurrentSession', fresh.session)
                # Background Konsole may keep its old active controller until focus
                # arrives, even though the requested tab is already displayed.
                return key, fresh, 'activate'
            except (OSError, ValueError, subprocess.SubprocessError):
                return key, None, 'activate'
        self.return_future = self.executor.submit(switch)

    @staticmethod
    def confirm_selection(key, binding, expected):
        try:
            fresh = inspect_terminal(binding)
            if ((fresh.window, fresh.pid, fresh.main_window) !=
                    (expected.window, expected.pid, expected.main_window)):
                return key, None, 'selected'
            deadline = time.monotonic() + 1.2
            while dbus_call(fresh.service, fresh.main_window, 'org.qtproject.Qt.QWidget.isActiveWindow') != 'true':
                if time.monotonic() >= deadline:
                    return key, None, 'selected'
                time.sleep(.04)
            # Repeat once after activation so Konsole's focused controller and tab
            # agree; currentSession alone is stale while the window is inactive.
            dbus_call(fresh.service, fresh.window, 'org.kde.konsole.Window.setCurrentSession', fresh.session)
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                current = dbus_call(fresh.service, fresh.window, 'org.kde.konsole.Window.currentSession')
                if current == str(fresh.session):
                    return key, fresh, 'selected'
                time.sleep(.04)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return key, None, 'selected'

    def close(self):
        self.closed = True
        self.timer.stop()
        self.desktop.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
