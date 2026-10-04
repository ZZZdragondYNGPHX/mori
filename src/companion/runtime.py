"""Composition root: assembles adapters, bridge, state, and Qt presentation."""
import json
import logging
import sys
import time

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .adapters.catalog import BUILTIN_ADAPTERS, DEFAULT_ADAPTERS
from .adapters.registry import AdapterRegistry
from .bridge import EventBridge
from .events import EventType
from .notices import TaskNotice
from .paths import ensure_state_dir
from .state import PRIORITY, CompanionState
from .transport import BridgeServer


class EventRelay(QObject):
    ready = Signal()


class CompanionController(QObject):
    def __init__(self, pet, enabled_adapters=DEFAULT_ADAPTERS, descriptor_path=None):
        super().__init__(pet)
        self.pet = pet
        self.state = CompanionState()
        from presentation.task_notices import TaskNotices
        self.pet.task_notices = TaskNotices(pet)
        self.notices = self.pet.task_notices
        self.relay = EventRelay(self)
        self.bridge = EventBridge(notify=self.relay.ready.emit)
        self.relay.ready.connect(self.consume, Qt.QueuedConnection)
        registry = AdapterRegistry.load(enabled_adapters, BUILTIN_ADAPTERS)
        self.server = BridgeServer(self.bridge, registry, descriptor_path)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.CoarseTimer)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.render)
        self._closed = False
        self._last_snapshot = None
        try:
            self.server.start()
        except Exception:
            self.server.stop()
            raise
        self.timer.start()

    def consume(self):
        if self._closed:
            return
        changed = False
        for event in self.bridge.drain():
            if isinstance(event, TaskNotice):
                changed = self.notices.ingest(event) or changed
            else:
                changed = self.state.ingest(event) or changed
        if changed:
            self.render()

    def render(self):
        history = [f"{time.strftime('%H:%M:%S', time.localtime(e.timestamp))} · "
                   f"{e.source} · {e.session_id[:12]} · {e.type.value} · {e.summary}"
                   for e in self.state.history]
        quiet = (EventType.TASK_STARTED, EventType.THINKING, EventType.GENERATING)
        self.pet._legacy_codex_working = any(s.source == 'codex' and self.state.effective_status(s) in quiet
                                             for s in self.state.sessions.values())
        if self.pet._legacy_codex_working and self.pet.interactions.active:
            self.pet.interactions.cancel(restore_position=False)
        candidates = [(PRIORITY[self.state.effective_status(s)], s.received_at, self.state.effective_status(s))
                      for s in self.state.sessions.values()
                      if not (s.source == 'codex' and self.state.effective_status(s) in quiet)]
        display_status = max(candidates, default=(0, 0, EventType.IDLE))[2]
        reaction = self.pet.character.react(display_status)
        sessions = self.state.session_lines()
        labels = {"idle": "待机", "working": "进行中", "completed": "本轮完成",
                  "failed": "失败", "interrupted": "已中断", "closed": "会话结束",
                  "disconnected": "连接中断"}
        sessions += [f"Codex · {t.title} · {labels[t.status]}"
                     + (f" · {len(t.pending)} 项待选择" if t.pending else "")
                     for t in self.notices.state.tasks.values()]
        snapshot = (reaction, tuple(sessions), tuple(history))
        if snapshot != self._last_snapshot:
            self._last_snapshot = snapshot
            self.pet.apply_tool_reaction(reaction, sessions, history)

    def stop(self):
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self.notices.close()
        self.server.stop()


def acquire_instance(app):
    """One desktop process per state directory; a second launch restores it."""
    import hashlib
    name = "mori-companion-" + hashlib.sha256(str(ensure_state_dir()).encode()).hexdigest()[:20]
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(250):
        probe.write(b"show")
        probe.waitForBytesWritten(250)
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(name)
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(name):
        raise RuntimeError("Cannot acquire companion instance")
    return server


def load_settings():
    path = ensure_state_dir() / "settings.json"
    if not path.exists():
        return DEFAULT_ADAPTERS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        enabled = data["enabled_adapters"]
        if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
            raise ValueError("Invalid enabled_adapters")
        return tuple(enabled)
    except (OSError, ValueError, KeyError, TypeError):
        logging.warning("Invalid settings.json; using default adapters")
        return DEFAULT_ADAPTERS


def main():
    from presentation.platform import configure_desktop_platform

    configure_desktop_platform()
    from presentation.pet import (
        ASSET_ROOT,
        DesktopPet,
        PetTrayIcon,
        clamp_position_to_screen,
        load_window_position,
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Mori Companion")
    app.setDesktopFileName("mori-companion")
    app.setQuitOnLastWindowClosed(False)
    instance = acquire_instance(app)
    if instance is None:
        return 0
    from character.defaults import default_character
    from character.skins import SkinRepository
    from presentation.skin_panel import validate_image

    default = default_character(ASSET_ROOT)
    character = SkinRepository(ensure_state_dir()).selected(default)
    if character.definition.id != default.definition.id:
        try:
            for key in character.definition.animations:
                validate_image(character.assets.resolve(key))
        except (OSError, ValueError):
            logging.warning("Invalid selected skin; using the original character")
            character = default
    pet = DesktopPet(character)
    from .preferences import PreferenceStore

    pet.interactions.apply(PreferenceStore().load(), persist=False)
    app._pet = pet
    controller = CompanionController(pet, load_settings())
    app._companion = controller
    app.aboutToQuit.connect(controller.stop)
    app.aboutToQuit.connect(pet._cleanup)
    app.aboutToQuit.connect(instance.close)

    def restore():
        while instance.hasPendingConnections():
            connection = instance.nextPendingConnection()
            connection.disconnectFromServer()
            connection.deleteLater()
        pet.show_whale(greet=True)

    instance.newConnection.connect(restore)
    geometry = pet.home_geometry()
    saved = load_window_position()
    if saved is not None:
        x, y = clamp_position_to_screen(*saved, pet.width(), pet.height())
        geometry.moveTopLeft(QPoint(x, y))
    pet.setGeometry(geometry)
    pet.show()
    pet.play_entrance()
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = PetTrayIcon(pet)
        pet.attach_tray(tray)
        app._tray = tray
    return app.exec()
