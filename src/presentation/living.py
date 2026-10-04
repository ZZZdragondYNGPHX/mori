"""Quiet life vignettes: short at work, complete at leisure, always interruptible."""
import random
import time
from collections import deque

from PySide6.QtCore import QObject, QTimer

from character.life import LIFE_SCENES, THEME_WEIGHTS, THEMES

from .keyposes import PoseLibrary

WORKING = frozenset(("task_started", "thinking", "generating"))


class LivingDirector(QObject):
    def __init__(self, pet, asset_root, clock=time.monotonic, rng=None):
        super().__init__(pet)
        self.pet = pet
        self.clock = clock
        self.rng = rng if rng is not None else random.Random()
        self.memory = pet._ambient_dialogue.memory
        self.dialogue = pet._ambient_dialogue
        self.library = PoseLibrary(asset_root)
        self.scene = None
        self.frame = None
        self._started = 0
        self._recent = deque(maxlen=6)
        self._working = False
        self._ambient = QTimer(self)
        self._ambient.setSingleShot(True)
        self._ambient.timeout.connect(self.choose)
        self._frame = QTimer(self)
        self._frame.setInterval(80)
        self._frame.timeout.connect(self.advance)

    @property
    def active(self):
        return self.scene is not None

    def working(self):
        return (self.pet._tool_reaction.status in WORKING
                or getattr(self.pet, "_codex_working", False)
                or getattr(self.pet, "_legacy_codex_working", False))

    def schedule(self):
        prefs = self.pet.preferences
        if (self.active or self.pet._hidden or self.pet._quitting
                or not any(prefs.module_enabled(name) for name in ("expression", "rest"))):
            self._ambient.stop()
            return
        self._working = self.working()
        seconds = prefs.life_work_seconds if self._working else prefs.life_idle_seconds
        self._ambient.start(round(seconds * self.rng.uniform(.5, 1.5) * 1000))

    def can_start(self):
        pet = self.pet
        notices = getattr(pet, "task_notices", None)
        system = pet.interactions.system
        return (not self.active and not pet._hidden and pet.isVisible()
                and pet._state == "idle" and not pet._pressing and not pet._idle_paused
                and pet._tool_reaction.status in WORKING | {"idle"}
                and pet._bubble is None and not pet._pending_tool_speech
                and not pet.interactions.active
                and not (system.ink.pickers or system.pending_scroll or system.input.preparing)
                and not (notices and (notices.bubble.isVisible() or notices.feedback.kind
                                     or notices.feedback.queue
                                     or any(task.pending for task in notices.state.tasks.values())))
                and not any(w is not None and w.isVisible() for w in (
                    pet._chat_window, pet._status_window, pet._skin_window,
                    pet._settings_window, pet._context_menu, getattr(notices, "panel", None))))

    def available(self):
        working = self.working()
        return [scene for scene in LIFE_SCENES.values()
                if self.pet.preferences.module_enabled(scene.module)
                and (not working or scene.working) and scene.eligible(self.memory)]

    def choose(self):
        if self.can_start():
            choices = self.available()
            choices = [s for s in choices if s.id not in self._recent] or choices
            themes = [theme for theme in THEMES if any(s.theme == theme for s in choices)]
            if themes:
                theme = self.rng.choices(themes, weights=[THEME_WEIGHTS[THEMES.index(t)] for t in themes], k=1)[0]
                if self.start(self.rng.choice([s.id for s in choices if s.theme == theme])):
                    return
        self.schedule()

    def start(self, name):
        scene = LIFE_SCENES.get(name)
        if scene is None or not self.can_start() or scene not in self.available():
            return False
        if self.pet.character.definition.id == "whale-girl" and not self.library.load(name):
            return False
        self.pet._pause_idle()
        self.pet._state = "life"
        self.pet._renderer.pause()
        self.scene = scene
        self._started = self.clock()
        self._recent.append(name)
        self._ambient.stop()
        self.advance()
        group = scene.theme + ".memory" if scene.requires else "scene." + scene.theme + ".start"
        if self.rng.random() < .55:
            self.pet._show_speech(self.dialogue.line(group), automatic=True)
        self._frame.start()
        return True

    def advance(self):
        if not self.scene:
            return
        if (self.pet._hidden or not self.pet.preferences.module_enabled(self.scene.module)
                or (self.working() and not self.scene.working)
                or self.pet._tool_reaction.status not in WORKING | {"idle"}):
            self.cancel()
            return
        elapsed = (self.clock() - self._started) * 1000
        if elapsed >= sum(self.scene.durations):
            scene = self.scene
            if scene.effect:
                self.memory.record(scene.effect)
            self.cancel(interrupted=False)
            if self.pet._bubble is None and not self.pet._pending_tool_speech and self.rng.random() < .35:
                group = scene.theme + ".memory" if scene.effect else "scene." + scene.theme + ".end"
                self.pet._show_speech(self.dialogue.line(group), automatic=True)
            return
        self.frame = (self.library.frame_at(elapsed) if self.pet.character.definition.id == "whale-girl"
                      else self.pet._pixmap)
        self.pet.update()

    def cancel(self, interrupted=True):
        self._frame.stop()
        if self.scene:
            if interrupted and self.scene.theme == "dream":
                self.memory.record("interrupted")
            self.scene = None
            self.frame = None
            self.library.clear()
            self.pet._kill_bubble()
            self.pet._back_to_idle()
            self.pet.update()
        self.schedule()

    def activity_changed(self):
        working = self.working()
        if self.active and (self.pet._tool_reaction.status not in WORKING | {"idle"}
                            or (working and not self.scene.working)):
            self.cancel()
        elif working != self._working:
            self.schedule()

    def draw(self, painter):
        if not self.active or self.frame is None:
            return False
        facing = self.pet.inward_facing() if self.pet.character.definition.id == "whale-girl" else "right"
        frame = self.library.facing.get(self.frame, facing)
        self.pet._visible_base_frame = frame
        self.pet._visible_base_reversed = facing == "left"
        painter.drawPixmap(0, 0, frame)
        return True

    def stop(self):
        self.cancel()
        self._ambient.stop()
        self._frame.stop()
