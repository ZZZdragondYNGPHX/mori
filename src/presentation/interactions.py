"""Interruptible, bounded desktop stories; high-rate timers run only in a story."""
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QImageReader, QPainter, QPixmap

from character.cursor import LOOK_INDEX, CursorFollower, CursorInterest
from character.dialogue import fixed_line
from character.life import LIFE_SCENES
from character.motion import (
    GAITS,
    choose_gait,
    inward_facing,
    movement_direction,
    travel_seconds,
)
from character.timing import POSE_MS, STORY_SECONDS, quiet_interval
from character.window_motion import choose_peek_side, fits_box
from companion.preferences import PreferenceStore

from .cursor_attention import CursorAttention
from .desktop_interaction import KWinDesktop
from .keyposes import PoseLibrary
from .system_actions import DIRECT_SCENES, SYSTEM_SCENES, SystemActions
from .window_poses import WindowPoseLibrary

SCENES = {"wander": "桌面散步", "trot": "小跑到新位置", "run": "快跑到新位置",
          "cursor": "找鼠标玩", "peek": "窗口探头", "press": "伸手试试按钮",
          "peek-left": "左侧悄悄探头", "peek-right": "右侧悄悄探头", "peek-top": "趴在上沿偷看",
          "sit": "坐在标题栏", "dance": "开心庆祝", "background": "问候后台软件",
          "stretch": "伸个懒腰", "nap": "打个小盹",
          "minimize": "最小化恶作剧"}
SCENE_MODULES = {name: "window" for name in ("peek", "peek-left", "peek-right", "peek-top", "sit", "press", "background")}
SCENE_MODULES.update(wander="movement", trot="movement", run="movement", cursor="cursor",
                     stretch="rest", nap="rest", dance="expression", minimize="system")
SCENES.update(SYSTEM_SCENES)
SCENE_MODULES.update({name: "system" for name in SYSTEM_SCENES})
SCENES.update({name: scene.label for name, scene in LIFE_SCENES.items()})
SCENE_MODULES.update({name: scene.module for name, scene in LIFE_SCENES.items()})


@dataclass
class Step:
    seconds: float
    pose: str
    destination: QPoint | None = None
    speech: str = ""
    action: str = ""
    attachment: str = ""
    gait: str = "walk"


class InteractionDirector(QObject):
    changed = Signal()
    message = Signal(str)
    WIDTH, HEIGHT = 280, 230

    def __init__(self, pet, asset_root, preferences, desktop=None, clock=time.monotonic, rng=None):
        super().__init__(pet)
        self.pet = pet
        self.asset_root = Path(asset_root)
        self.preferences = preferences
        self.store = PreferenceStore()
        self.desktop = desktop or KWinDesktop(self)
        self.desktop.authorize = self._authorize_minimize
        self.desktop.changed.connect(self._window_changed)
        self.desktop.operation_finished.connect(self._operation_finished)
        self.desktop.availability_changed.connect(self.changed)
        self.clock = clock
        self.rng = rng or random.Random()
        self.active = False
        self.scene = ""
        self.target = None
        self.steps = []
        self.index = 0
        self._started = 0
        self._path_started = 0
        self._from = QPoint()
        self._home = QPoint()
        self._cache = {}
        self._walk_poses = PoseLibrary(asset_root)
        self._cursor_poses = PoseLibrary(asset_root)
        self._scene_poses = PoseLibrary(asset_root)
        self._window_poses = WindowPoseLibrary(asset_root)
        self._peek_side = "right"
        self._last_peek_side = "left"
        self._window_facing = "right"
        self._seat_goal = None
        self._seat_from = QPoint()
        self._seat_started = 0
        self.attention = CursorAttention(self)
        self._cursor_interest = CursorInterest()
        self._cursor_follower = CursorFollower()
        self._cursor_tick = 0
        self._cursor_position = QPointF()
        self._cursor_distance = 0
        self._cursor_moving = False
        self._cursor_direction = "right"
        self._cursor_side = "right"
        self._cursor_last_move = float("-inf")
        self.direction = "right"
        self._walk_distance = 0
        self.displayed_frame = None
        self.displayed_origin = QPoint()
        self.displayed_reversed = False
        self._clip_rect = None
        self._last_scene = ""
        self._last_minimize = float("-inf")
        self._committing = False
        self._restoring = False
        self.restore_identity = ""
        self._button_action = "minimize"
        self._frame = QTimer(self)
        self._frame.setTimerType(Qt.PreciseTimer)
        self._frame.setInterval(40)
        self._frame.timeout.connect(self.advance)
        self._ambient = QTimer(self)
        self._ambient.setSingleShot(True)
        self._ambient.timeout.connect(self._choose)
        self._follow = QTimer(self)
        self._follow.setSingleShot(True)
        self._follow.setInterval(80)
        self._follow.timeout.connect(self._follow_target)
        self.system = SystemActions(self)

    def apply(self, preferences, persist=True):
        self.cancel()
        self.preferences = preferences
        self.pet.preferences = preferences
        bubbles = [getattr(self.pet, '_speech_bubble_cache', None),
                   getattr(getattr(self.pet, 'task_notices', None), 'bubble', None)]
        for bubble in bubbles:
            if bubble is not None:
                bubble.set_opacity(preferences.bubble_opacity)
        if not preferences.module_enabled("system"):
            self.system.cancel(clear=True)
        self.pet._pending_tool_speech = ""
        if not preferences.proactive_dialogue:
            self.pet._kill_bubble()
            self.pet._dialogue_timer.stop()
        elif self.pet._state == "idle":
            self.pet._start_dialogue_timer()
        if preferences.desktop_enabled:
            if self.pet.isVisible():
                self.desktop.start()
        else:
            self.desktop.stop()
        self._schedule()
        if hasattr(self.pet, "living"):
            self.pet.living.schedule()
        self.attention.sync()
        if persist:
            self.store.save(preferences)
        self.changed.emit()

    def _schedule(self):
        self.attention.sync()
        if self.preferences.active_modules and self.pet.isVisible():
            seconds = self.preferences.interval_seconds
            self._ambient.start(round(quiet_interval(seconds, self.rng.random()) * 1000))
        else:
            self._ambient.stop()

    def _can_start(self):
        system = getattr(self, "system", None)
        return (self.pet.isVisible() and
                not (system and (system.ink.pickers or system.pending_scroll or system.input.preparing)) and
                self.pet._state == "idle" and self.pet._tool_reaction.status == "idle" and not self.pet._pressing and
                not getattr(self.pet, "_codex_working", False) and
                not getattr(self.pet, "_legacy_codex_working", False) and
                not any(w is not None and w.isVisible() for w in (
                    self.pet._chat_window, self.pet._status_window, self.pet._skin_window,
                    self.pet._settings_window, self.pet._context_menu,
                    getattr(getattr(self.pet, "task_notices", None), "panel", None))))

    def _choose(self):
        if not self._can_start():
            self._schedule()
            return
        prefs = self.preferences
        choices = ["dance"] if prefs.module_enabled("expression") else []
        if prefs.module_enabled("rest") and self.pet._tool_reaction.status == "idle":
            choices += ["stretch", "nap"]
        if prefs.module_enabled("movement"):
            choices.append("wander")
            if prefs.module_enabled("cursor") and self.pet._tool_reaction.status == "idle":
                choices.append("cursor")
        target = self.select_target() if prefs.desktop_enabled else None
        if target:
            if prefs.module_enabled("window"):
                choices += (["background"] if target.minimized else
                            [name for name in ("peek", "peek-top", "sit", "press") if self._window_fits(name, target)])
            if (not target.minimized and prefs.entertaining and prefs.module_enabled("system") and target.minimizable and
                    self.desktop.active_window and self.desktop.active_window.identity == target.identity and
                    self._minimize_ready() and self._window_fits("minimize", target)):
                choices += ["minimize"]
            active = self.desktop.active_window
            if (prefs.entertaining and prefs.module_enabled("system") and active and not active.maximized and
                    active.movable and not active.interactive and self.system.can_touch_window(active)):
                choices += ["move-window"]
        choices = [item for item in choices if item != self._last_scene] or choices
        if not choices:
            self._schedule()
            return
        scene = self.rng.choice(choices)
        if not self.start(scene, target=self.desktop.active_window if scene == "move-window" else target):
            self._schedule()

    def target_groups(self):
        windows = dict(self.desktop.windows)
        active = self.desktop.active_window
        if active:
            windows[active.identity] = active
        candidates = [w for w in windows.values() if w.eligible(self.preferences, include_minimized=True)]
        visible = [w for w in candidates if not w.minimized]
        top = max(visible, key=lambda w: (w.keep_above, w.stacking_order, w.active), default=None)
        return ([top] if top else [], [w for w in visible if w is not top],
                [w for w in candidates if w.minimized])

    def select_target(self, minimized=None):
        groups = self.target_groups()
        weights = (self.preferences.top_weight, self.preferences.foreground_weight,
                   self.preferences.background_weight)
        # Normalize by tier: many background windows must not overpower the top one.
        pools = [(group, weight) for group, weight in zip(groups, weights)
                 if group and weight > 0 and (minimized is None or group[0].minimized == minimized)]
        if not pools:
            return None
        group = self.rng.choices([pool for pool, _weight in pools],
                                 weights=[weight for _pool, weight in pools], k=1)[0]
        return self.rng.choice(group)

    def _minimize_ready(self):
        return self.clock() - self._last_minimize >= self.preferences.minimize_interval_seconds

    def _bounds(self):
        screen = self.pet.screen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)

    def _clamp(self, point):
        rect = self._bounds()
        return QPoint(max(rect.left(), min(point.x(), rect.right() - self.WIDTH + 1)),
                      max(rect.top(), min(point.y(), rect.bottom() - self.HEIGHT + 1)))

    def _random_destination(self, origin=None):
        rect = self._bounds()
        origin = self._clamp(self.pet.pos() if origin is None else origin)
        left, right = rect.left(), max(rect.left(), rect.right() - self.WIDTH + 1)
        top, bottom = rect.top(), max(rect.top(), rect.bottom() - self.HEIGHT + 1)
        for _ in range(8):
            point = QPoint(self.rng.randint(max(left, origin.x() - 420), min(right, origin.x() + 420)),
                           self.rng.randint(max(top, origin.y() - 260), min(bottom, origin.y() + 260)))
            if math.hypot(point.x() - origin.x(), point.y() - origin.y()) >= 80:
                return point
        # Even at a screen corner, avoid a walk that never changes position.
        return max((QPoint(x, y) for x in (left, right) for y in (top, bottom)),
                   key=lambda point: (point - origin).manhattanLength())

    def _walk_step(self, destination, speech="", origin=None, gait=None, attachment=""):
        origin = self.pet.pos() if origin is None else origin
        distance = math.hypot(destination.x() - origin.x(), destination.y() - origin.y())
        gait = gait or choose_gait(distance, self.preferences.movement_pace)
        return Step(travel_seconds(distance, gait), "walk", destination, speech,
                    attachment=attachment, gait=gait)

    def _pose_step(self, pose, destination=None, speech="", action="", attachment="", seconds=None):
        clip = self._window_poses.definition(pose)
        if seconds is None:
            seconds = sum(clip["durations"]) / 1000 if clip else POSE_MS.get(pose, 1000) / 1000
        return Step(seconds, pose, destination, speech, action, attachment)

    def start(self, scene, target=None):
        if scene in LIFE_SCENES:
            started = self.pet.living.start(scene)
            if not started:
                self.message.emit(fixed_line("notice.life_unavailable"))
            return started
        if scene not in SCENES or not self._can_start():
            self.message.emit(fixed_line("notice.interaction_busy"))
            return False
        if not self.preferences.module_enabled(SCENE_MODULES[scene]):
            self.message.emit(fixed_line("notice.module_disabled"))
            return False
        if scene in DIRECT_SCENES:
            return self.system.start(scene, target)
        requested = scene
        requested_side = scene.removeprefix("peek-") if scene.startswith("peek-") else ""
        if requested_side:
            scene = "peek"
        prefs = self.preferences
        if scene in ("stretch", "nap") and self.pet._tool_reaction.status != "idle":
            self.message.emit(fixed_line("notice.work_busy"))
            return False
        if scene == "cursor" and not prefs.module_enabled("movement"):
            self.message.emit(fixed_line("notice.walk_disabled"))
            return False
        if scene == "cursor" and self.pet._tool_reaction.status != "idle":
            self.message.emit(fixed_line("notice.cursor_busy"))
            return False
        if scene in ("minimize", "maximize"):
            target = self.desktop.active_window
        elif scene in ("peek", "sit", "press", "background"):
            target = target or self.select_target(minimized=scene == "background")
        else:
            target = None
        if target and (not target.eligible(prefs, include_minimized=scene == "background") or
                       target.minimized != (scene == "background")):
            target = None
        if scene in ("peek", "sit", "press", "minimize", "maximize", "background"):
            if scene not in ("minimize", "maximize") and not prefs.module_enabled("window"):
                self.message.emit(fixed_line("notice.window_disabled"))
                return False
            if not target:
                self.message.emit(fixed_line("notice.no_window"))
                return False
        if scene == "minimize" and (not target.minimizable or not self._minimize_ready()):
            self.message.emit(fixed_line("notice.minimize_unavailable"))
            return False
        self._home = self.pet.pos()
        if target:
            self._window_facing = self._target_facing(target)
        if scene in ("minimize", "maximize", "press"):
            self._button_action = "maximize" if scene == "maximize" else "minimize"
            self._window_facing = ("left" if KWinDesktop.button_point(target, self._button_action).x() < target.geometry.center().x()
                                   else "right")
        if scene == "peek":
            bounds = self._bounds()
            self._peek_side = choose_peek_side(target.geometry.left() - bounds.left(),
                                               bounds.right() - target.geometry.right(), self._last_peek_side)
            candidates = ([requested_side] if requested_side else
                          [self._peek_side, "left" if self._peek_side == "right" else "right", "top"])
            fitting = [side for side in candidates if self._window_fits("peek-" + side, target)]
            if not fitting:
                self.message.emit(fixed_line("notice.edge_space"))
                return False
            self._peek_side = fitting[0]
            self._last_peek_side = self._peek_side
        elif scene in ("sit", "press", "minimize", "maximize") and not self._window_fits(scene, target):
            self.message.emit(fixed_line("notice.body_space"))
            return False
        self._activate(scene, target)
        self._last_scene = requested
        home = self._clamp(self._home)
        if scene in ("wander", "trot", "run"):
            destination = self._random_destination()
            self.steps = [self._walk_step(destination, fixed_line("walk.start"),
                                         gait=scene if scene in GAITS else None),
                          Step(self.rng.uniform(4, 10), "peek", destination, fixed_line("walk.arrive"))]
        elif scene == "cursor":
            self._cursor_follower = CursorFollower()
            self._cursor_position = QPointF(self.pet.pos())
            self._cursor_distance = 0
            self._cursor_last_move = float("-inf")
            self._cursor_tick = self.clock()
            self.steps = [Step(STORY_SECONDS["look"], "mouse-look"),
                          self._pose_step("mouse-curious", speech=fixed_line("cursor.ask"),
                               action="cursor-decide"),
                          Step(STORY_SECONDS["chase"], "cursor-follow", speech=fixed_line("cursor.chase"),
                               action="cursor-follow"),
                          self._pose_step("mouse-settle")]
        elif scene == "dance":
            self.steps = [self._pose_step("base-cheerup", home, fixed_line("dance")),
                          Step(STORY_SECONDS["celebration-settle"], "base-nothing", home)]
        elif scene == "stretch":
            self.steps = [self._pose_step("daily-stretch", home, fixed_line("stretch"))]
        elif scene == "nap":
            self.steps = [Step(.52, "nap-settle", home, fixed_line("nap")),
                          Step(prefs.rest_seconds, "nap-sleep", home), Step(.78, "nap-wake", home)]
        elif scene == "background":
            self.steps = [Step(STORY_SECONDS["background-greet"], "peek", home, fixed_line("background.greet")),
                          Step(STORY_SECONDS["background-rest"], "sit", home, fixed_line("background.rest"))]
        elif scene in ("minimize", "maximize", "press"):
            destination = self._window_position("press", target)
            self.steps = [self._walk_step(destination, attachment="button"),
                          self._pose_step("window-consider", destination,
                               fixed_line("button.consider"), attachment="button",
                               seconds=STORY_SECONDS["minimize-consider"] if scene == "minimize" else None),
                          self._pose_step("window-press", destination, fixed_line("button.press"),
                               scene if scene in ("minimize", "maximize") else "", attachment="button"),
                          self._pose_step("window-press-recover", destination,
                               fixed_line("button.minimized") if scene == "minimize" else fixed_line("button.maximized") if scene == "maximize" else fixed_line("button.touched"),
                               attachment="button"),
                          self._walk_step(self._random_destination(origin=destination), origin=destination)]
        else:
            destination = self._window_position(scene, target)
            if scene == "peek":
                self.steps = [self._walk_step(destination, attachment="edge"),
                              self._pose_step("window-peek-" + self._peek_side, destination,
                                   fixed_line("window.peek"), attachment="edge")]
            else:
                self.steps = [self._walk_step(destination, attachment="seat"),
                              self._pose_step("window-seat", destination, fixed_line("window.sit"), attachment="seat"),
                              Step(STORY_SECONDS["seat-rest"], "window-swing", destination, fixed_line("window.swing"), attachment="seat"),
                              self._pose_step("window-rise", destination, attachment="seat")]
            self.steps.append(self._walk_step(self._random_destination(origin=destination), origin=destination))
        self.index = 0
        self._enter_step()
        self.changed.emit()
        return True

    def _activate(self, scene, target):
        self._home = self.pet.pos()
        self.target = target
        self.scene = scene
        self._cursor_interest = self.attention.interest
        self.attention.suspend()
        self.pet._pause_idle()
        self.pet._state = "scene"
        self.pet._renderer.pause()
        self.pet.resize(self.WIDTH, self.HEIGHT)
        self.active = True
        self._ambient.stop()

    def _target_facing(self, window):
        bounds = self._bounds()
        return inward_facing(window.geometry.center().x(), bounds.left(), bounds.right(), self._window_facing)

    def _eyes(self):
        left = (self.direction == "left" if self.active and self.steps[self.index].pose == "walk"
                else self.pet.inward_facing() == "left")
        return QPoint(95 if left else 185, 90)

    def _window_position(self, scene, window):
        g = window.geometry
        if scene == "peek":
            if self._peek_side == "top":
                anchor = self._window_poses.anchor_at("window-peek-top", 0, self._window_facing)
                return QPoint(g.center().x(), g.top()) - anchor
            edge = g.left() - 218 if self._peek_side == "left" else g.right() - 155
            return QPoint(edge, g.top() + min(140, g.height() // 3))
        if scene == "press":
            if self.scene in ("minimize", "maximize"):
                return self.system.button_position(window, self._button_action)
            contact = self._window_poses.contact
            button = KWinDesktop.button_point(window, self._button_action)
            if button.x() < g.center().x():
                contact.setX(self.WIDTH - contact.x())
            return button - contact
        anchor = self._sit_anchor()
        return QPoint(g.center().x() - anchor.x(), g.top() - anchor.y())

    def _window_fits(self, scene, window):
        if scene in ("minimize", "maximize"):
            return self.system.button_fits(window, "maximize" if scene == "maximize" else "minimize")
        g = window.geometry
        facing = (self._window_facing if self.active and self.target and self.target.identity == window.identity
                  else self._target_facing(window))
        if scene == "peek":
            return any(self._window_fits("peek-" + side, window) for side in ("left", "right", "top"))
        if scene.startswith("peek-"):
            side = scene.removeprefix("peek-")
            name = "window-peek-" + side
            if side == "top":
                if not self._window_poses.has(name):
                    return False
                rect = self._window_poses.footprint(name, anchored=True, facing=facing)
                if rect is None:
                    return False
                rect.translate(g.center().x(), g.top())
                approach = QPoint(g.center().x(), g.top()) - self._window_poses.anchor_at(name, 0, facing)
            else:
                edge = QRect(0, 0, 218, self.HEIGHT) if side == "left" else QRect(155, 0, 125, self.HEIGHT)
                rect = self._window_poses.footprint(name, edge=edge) or edge
                rect.translate(g.left() - 218 if side == "left" else g.right() - 155,
                               g.top() + min(140, g.height() // 3))
                approach = QPoint(g.left() - 218 if side == "left" else g.right() - 155,
                                  g.top() + min(140, g.height() // 3))
                if rect.top() < g.top() + 8 or rect.bottom() > g.bottom() - 8:
                    return False
        elif scene == "sit":
            rect = QRect()
            for name in ("window-seat", "window-swing", "window-rise"):
                part = self._window_poses.footprint(name, anchored=True, facing=facing)
                if part is not None:
                    rect = rect.united(part)
            if rect.isEmpty():
                rect = QRect(-190, -180, self.WIDTH, self.HEIGHT)
            rect.translate(g.center().x(), g.top())
            approach = QPoint(g.center().x(), g.top()) - (self._window_poses.anchor_at("window-seat", 0, facing) or QPoint(190, 180))
        else:
            facing = "left" if KWinDesktop.button_point(window, self._button_action).x() < g.center().x() else "right"
            rect = QRect()
            for name in ("window-consider", "window-press", "window-press-recover"):
                part = self._window_poses.footprint(name, facing=facing)
                if part is not None:
                    rect = rect.united(part)
            if rect.isEmpty():
                rect = QRect(0, 0, self.WIDTH, self.HEIGHT)
            approach = self._window_position("press", window)
            rect.translate(approach)
        if not scene.startswith("peek-") or scene == "peek-top":
            if rect.left() < g.left() + 8 or rect.right() > g.right() - 8 or rect.bottom() > g.bottom() - 8:
                return False
        bounds = self._bounds()
        # The approach and its pose transition must fit too, including the
        # tail on every walking direction before window occlusion begins.
        for direction in ("left", "right", "up", "down"):
            walking = self._window_poses.footprint("walk-" + direction)
            if walking is not None:
                rect = rect.united(walking.translated(approach))
        return fits_box((rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()),
                        (bounds.x(), bounds.y(), bounds.x() + bounds.width(), bounds.y() + bounds.height()))

    def _enter_step(self):
        step = self.steps[self.index]
        self._started = self.clock()
        self._path_started = self._started
        self._from = self.pet.pos()
        self._walk_distance = 0
        self._frame.setInterval(200 if step.pose == "nap-sleep" else 40)
        self._frame.start()
        self._cursor_position = QPointF(self.pet.pos())
        self._cursor_tick = self._started
        self._cursor_moving = False
        if self.scene == "cursor":
            delta = QCursor.pos() - self.pet.pos() - self._eyes()
            self._cursor_direction = movement_direction(delta.x(), delta.y(), self._cursor_direction)
            self._cursor_side = "left" if delta.x() < 0 else "right"
        self.displayed_frame = None
        self.displayed_origin = QPoint()
        self.displayed_reversed = False
        if self.scene == "sit" and step.attachment == "seat" and step.pose != "walk":
            step.destination = self._window_position("sit", self.target)
            self.pet.move(self._seat_position(step))
            self._from = self.pet.pos()
        if step.pose == "walk" and step.destination is not None:
            if self.index > 0 and self.steps[self.index - 1].attachment:
                delta = step.destination - self._from
                step.seconds = travel_seconds(math.hypot(delta.x(), delta.y()), step.gait)
            delta = step.destination - self._from
            self.direction = movement_direction(delta.x(), delta.y(), self.direction)
        self._update_clip()
        if step.speech:
            self.pet._show_speech(step.speech, automatic=True)
        self.pet.update()

    def _seat_position(self, step):
        goal = self._window_position("sit", self.target)
        if goal != self._seat_goal:
            self._seat_from = self.pet.pos()
            self._seat_goal = QPoint(goal)
            elapsed_ms = (self.clock() - self._started) * 1000
            boundary = (self._window_poses.pose_started(step.pose, elapsed_ms)
                        if self._window_poses.has(step.pose) else 0)
            self._seat_started = self._started + boundary
        if (goal - self._seat_from).manhattanLength() <= 8:
            return goal
        t = max(0, min(1, (self.clock() - self._seat_started) / .12))
        t = t * t * (3 - 2 * t)
        return self._seat_from + (goal - self._seat_from) * t

    def _update_clip(self):
        self._clip_rect = None
        step = self.steps[self.index]
        if (self.scene == "peek" and self._peek_side != "top" and
                step.pose.startswith("window-peek") and self.target):
            # Each side aligns its supporting hand with the real window edge.
            edge = (self.target.geometry.left() if self._peek_side == "left" else self.target.geometry.right()) - self.pet.x()
            edge = max(0, min(self.WIDTH, edge))
            # Clip paint only: native window masks can leave stale compositor
            # surfaces when a peek switches back to walking.
            self._clip_rect = QRect(0, 0, edge, self.HEIGHT) if self._peek_side == "left" else QRect(edge, 0, self.WIDTH - edge, self.HEIGHT)

    def advance(self):
        if not self.active:
            return
        if not self.preferences.module_enabled(SCENE_MODULES[self.scene]) or not self.pet.isVisible():
            self.cancel()
            return
        step = self.steps[self.index]
        elapsed = self.clock() - self._started
        if step.pose.startswith("system-"):
            self.system.advance(elapsed)
            return
        if step.pose == "walk":
            elapsed = self.clock() - self._path_started
        if step.attachment and self.target:
            required = "peek-" + self._peek_side if self.scene == "peek" else self.scene
            if not self._window_fits(required, self.target):
                self.cancel(restore_position=False)
                return
        if self.scene == "cursor":
            cursor = QCursor.pos()
            now = self.clock()
            self._cursor_interest.sample(cursor.x(), cursor.y(), now)
            delta = cursor - self.pet.pos() - self._eyes()
            self._cursor_direction = movement_direction(delta.x(), delta.y(), self._cursor_direction)
            if self._cursor_direction in ("left", "right"):
                self._cursor_side = self._cursor_direction
            if step.action == "cursor-follow":
                if self._cursor_interest.shy(math.hypot(delta.x(), delta.y()), now):
                    self._cursor_retreat(delta)
                    return
                dx, dy = self._cursor_follower.advance(delta.x(), delta.y(), now - self._cursor_tick)
                previous = self.pet.pos()
                self._cursor_position += QPointF(dx, dy)
                proposed = self._cursor_position.toPoint()
                clamped = self._clamp(proposed)
                if clamped != proposed:
                    self._cursor_position = QPointF(clamped)
                self.pet.move(clamped)
                moved = self.pet.pos() - previous
                distance = math.hypot(moved.x(), moved.y())
                if distance > 0:
                    self._cursor_last_move = now
                self._cursor_moving = (now - self._cursor_last_move < .08 and
                                       math.hypot(delta.x(), delta.y()) > 112)
                self._cursor_distance += distance
                if self._cursor_moving:
                    self.direction = movement_direction(moved.x(), moved.y(), self.direction)
            self._cursor_tick = now
        if self.scene == "sit" and step.attachment == "seat" and step.pose != "walk":
            # Standing feet and seated palms meet the same world-space ledge.
            # Move the transparent canvas, retaining every complete drawing.
            step.destination = self._window_position("sit", self.target)
            self._from = self._seat_position(step)
        if step.destination is not None:
            previous = self.pet.pos()
            fraction = min(1, elapsed / step.seconds)
            fraction = fraction * fraction * (3 - 2 * fraction)
            point = (self._from if self.scene == "sit" and step.attachment == "seat" and step.pose != "walk"
                     else self._from + (step.destination - self._from) * fraction)
            attached = step.attachment and self.scene in ("peek", "sit", "press", "minimize", "maximize")
            self.pet.move(point if attached else self._clamp(point))
            moved = self.pet.pos() - previous
            if step.pose == "walk":
                self._walk_distance += math.hypot(moved.x(), moved.y())
                self.direction = movement_direction(moved.x(), moved.y(), self.direction)
        self._update_clip()
        self.pet.update()
        contact_time = step.seconds
        if step.action in ("minimize", "maximize"):
            clip = self._window_poses.definition("window-press")
            if clip:
                # Let the existing pose blend reach the fingertip contact before committing.
                contact_time = min(step.seconds, sum(clip["durations"][:-1]) / 1000 + .12)
        if elapsed < contact_time or self._committing:
            return
        if step.action == "cursor-decide":
            delta = QCursor.pos() - self.pet.pos() - self._eyes()
            if self._cursor_interest.shy(math.hypot(delta.x(), delta.y()), self.clock()):
                self._cursor_retreat(delta, after_current=True)
                return
        if step.action in ("minimize", "maximize"):
            self._committing = True
            if not self._authorize_minimize(self.target.identity):
                self._committing = False
                self.cancel()
                return
            operation = self.desktop.maximize if step.action == "maximize" else self.desktop.minimize
            if not operation(self.target):
                self._committing = False
                self.cancel()
            return
        self._next_step()

    def _cursor_retreat(self, delta, after_current=False):
        """One small retreat, a friendly look back, then ordinary idle."""
        distance = math.hypot(delta.x(), delta.y())
        away = QPoint(round(-54 * delta.x() / distance), round(-54 * delta.y() / distance)) if distance else QPoint(-54, 0)
        destination = self._clamp(self.pet.pos() + away)
        first = self.index + 1 if after_current else self.index
        self.steps[first:] = [self._walk_step(destination, gait="walk"),
                              self._pose_step("mouse-shy", speech=fixed_line("cursor.retreat")),
                              self._pose_step("mouse-settle")]
        if after_current:
            self.index += 1
        self._enter_step()

    def _next_step(self):
        self.index += 1
        if self.index >= len(self.steps):
            if self.scene == "nap":
                self.pet._ambient_dialogue.memory.record("rested")
            elif self.scene in ("cursor", "peek", "background"):
                self.pet._ambient_dialogue.memory.record("explored")
            self.cancel(restore_position=False)
        else:
            self._enter_step()

    def _authorize_minimize(self, identity):
        window = self.desktop.active_window
        return bool(self.active and self.scene in ("minimize", "maximize") and self._committing and
                    self.preferences.module_enabled("system") and
                    self.pet.isVisible() and
                    self.target and self.target.identity == identity and window and
                    window.identity == identity and window.active and (self.scene != "minimize" or window.minimizable) and
                    window.eligible(self.preferences))

    def _window_changed(self, _window):
        self.system.target_changed()
        if self.active and self.scene in DIRECT_SCENES:
            return
        if not self.active or not self.target:
            return
        window = self.desktop.windows.get(self.target.identity)
        if self._committing:
            return
        if (self.scene == "minimize" and self.restore_identity == self.target.identity) or (self.scene == "maximize" and self.index >= 3):
            return
        if (not window or not window.eligible(self.preferences, include_minimized=self.scene == "background") or
                window.minimized != (self.scene == "background")):
            self.cancel()
            return
        if self.scene in ("minimize", "maximize") and (not self.desktop.active_window or self.desktop.active_window.identity != window.identity):
            self.cancel()
            return
        if window.geometry != self.target.geometry:
            if self.scene in ("minimize", "maximize"):
                self.cancel()
                return
            if self.scene in ("peek", "sit", "press") and not self._follow.isActive():
                self._follow.start()

    def _follow_target(self):
        if not self.active or not self.target or self.scene not in ("peek", "sit", "press"):
            return
        window = self.desktop.windows.get(self.target.identity)
        if not window or not window.eligible(self.preferences):
            self.cancel()
            return
        self.target = window
        self._window_facing = self._target_facing(window)
        if self.scene == "press":
            self._window_facing = ("left" if KWinDesktop.button_point(window, self._button_action).x() < window.geometry.center().x()
                                   else "right")
        required = "peek-" + self._peek_side if self.scene == "peek" else self.scene
        if not self._window_fits(required, window):
            self.cancel(restore_position=False)
            return
        destination = self._window_position(self.scene, window)
        current = self.steps[self.index]
        if current.attachment and current.pose == "walk" and current.destination != destination:
            # Continue the gait phase, but start the revised path at the actual
            # position so a moved window cannot teleport the character.
            self._from = self.pet.pos()
            self._path_started = self.clock()
            delta = destination - self._from
            current.seconds = travel_seconds(math.hypot(delta.x(), delta.y()), current.gait)
        for step in self.steps:
            if step.attachment:
                step.destination = QPoint(destination)
        # Follow without restarting the phase clock, pose or speech.
        if self.steps[self.index].attachment and self.steps[self.index].pose != "walk":
            self.pet.move(destination)
            self._from = QPoint(destination)
            self._seat_goal = QPoint(destination)
            self._seat_from = QPoint(destination)
            self._update_clip()
            self.pet.update()

    def _operation_finished(self, identity, success):
        if self._restoring:
            self._restoring = False
            if success:
                self.restore_identity = ""
            if not self.preferences.desktop_enabled:
                self.desktop.stop()
            self.message.emit(fixed_line("notice.window_restored") if success else fixed_line("notice.window_restore_skipped"))
            self.changed.emit()
            return
        if not self._committing:
            return
        self._committing = False
        if success:
            if self.scene == "minimize":
                self.restore_identity = identity
                self._last_minimize = self.clock()
            self._next_step()
        else:
            self.cancel()
        self.changed.emit()

    def restore_window(self):
        if not self.restore_identity:
            return
        self.desktop.start()
        self._restoring = True
        if not self.desktop.restore(self.restore_identity):
            self._restoring = False
            if not self.preferences.desktop_enabled:
                self.desktop.stop()
            self.message.emit(fixed_line("notice.window_api"))

    def cancel(self, restore_position=True):
        if hasattr(self.pet, "living"):
            self.pet.living.cancel()
        self.system.cancel()
        self.attention.suspend()
        self._follow.stop()
        self._committing = False
        self.desktop.cancel_pending()
        self._frame.stop()
        if self.active:
            self.active = False
            self.displayed_frame = None
            self.displayed_origin = QPoint()
            self.displayed_reversed = False
            self._walk_poses.clear()
            self._cursor_poses.clear()
            self._scene_poses.clear()
            self._seat_goal = None
            self._window_poses.clear()
            self._clip_rect = None
            self.pet._kill_bubble()
            self.pet._state = "idle"
            self.pet._display_pixmap(self.pet._pixmap)
            if restore_position:
                self.pet.move(self.pet._clamp_pos(self._home))
            self.pet._back_to_idle()
            if self.pet.isVisible():
                self.pet._renderer.resume()
            self.target = None
            self.steps = []
            self.pet.update()
            self.changed.emit()
        self._schedule()

    def visibility_changed(self, visible):
        if visible:
            if self.preferences.desktop_enabled:
                self.desktop.start()
            self._schedule()
        elif not visible:
            self.cancel()
            self.system.cancel(clear=True)
            self._ambient.stop()
            self.attention.suspend()
            self.desktop.stop()

    def _pose_pixmap(self, pose):
        key = (self.pet.character.definition.id, pose)
        if key not in self._cache:
            path = self.asset_root / "interaction" / (pose + ".png")
            if self.pet.character.definition.id != "whale-girl" or not path.is_file():
                path = self.pet.character.assets.resolve({"walk": "nothing", "peek": "think",
                                                         "press": "attention", "sit": "eat",
                                                         "think": "think", "touch": "touch",
                                                         "nothing": "nothing"}.get(pose, "nothing"))
            reader = QImageReader(str(path))
            reader.setScaledSize(reader.size().scaled(260, 210, Qt.KeepAspectRatio))
            image = reader.read()
            self._cache[key] = QPixmap.fromImage(image) if not image.isNull() else self.pet._pixmap
        return self._cache[key]

    def _pose_origin(self, pixmap):
        return QPoint(self.WIDTH // 2 + round(-pixmap.width() / 2), self.HEIGHT - 8 - pixmap.height())

    def _sit_anchor(self):
        if self.pet.character.definition.id == "whale-girl" and self._window_poses.has("window-swing"):
            if self.active and self.scene == "sit" and self.index < len(self.steps):
                name = self.steps[self.index].pose
                anchor = self._window_poses.anchor_at(name, (self.clock() - self._started) * 1000, self._window_facing)
                if anchor is not None:
                    return anchor
            return self._window_poses.anchor_at("window-seat", 0, self._window_facing) or self._window_poses.support
        pixmap = self._pose_pixmap("sit")
        # Mori's hips/palms support her at 70% across and 80% down the source.
        # Include the transparent padding and the actual decoded image scale.
        if self.pet.character.definition.id == "whale-girl" and (self.asset_root / "interaction/sit.png").is_file():
            return self._pose_origin(pixmap) + QPoint(round(pixmap.width() * .70), round(pixmap.height() * .80))
        return QPoint(self.WIDTH // 2, self.HEIGHT - 8)

    def draw(self, painter):
        self.displayed_origin = QPoint()
        self.displayed_reversed = False
        if not self.active:
            return self.attention.draw(painter)
        step = self.steps[self.index]
        if step.pose.startswith("system-") and self.system.draw(painter):
            return True
        if self.scene in ("minimize", "maximize") and step.pose.startswith("window-"):
            if self.system.draw_button(painter):
                return True
        if step.pose.startswith("window-") and self.pet.character.definition.id == "whale-girl":
            support = (self._window_poses.anchor_at("window-peek-top", 0, self._window_facing)
                       if step.pose == "window-peek-top" else self._sit_anchor())
            rendered = self._window_poses.draw(painter, step.pose, (self.clock() - self._started) * 1000,
                                                support, self._clip_rect, self._window_facing)
            if rendered is not None:
                self.displayed_frame, self.displayed_origin = rendered
                self.displayed_reversed = (self._window_facing == "left" and
                                          step.pose in self._window_poses.DIRECTIONAL)
                return True
        cursor_walk = step.pose == "cursor-follow" and self._cursor_moving
        if self.scene == "cursor" and not cursor_walk and self.pet.character.definition.id == "whale-girl":
            name = "mouse-curious" if step.pose == "cursor-follow" else step.pose
            side = ("left" if self._cursor_direction == "left" else "right") if step.pose == "cursor-follow" else self._cursor_side
            facing = self.pet.inward_facing()
            if facing == "left":
                side = "right" if side == "left" else "left"
            if name != "mouse-look" and side == "left":
                name += "-left"
            clip = self._cursor_poses.load(name)
            if clip is not None:
                look = self._cursor_direction
                if facing == "left" and look in ("left", "right"):
                    look = "right" if look == "left" else "left"
                frame = (self._cursor_poses.frames[LOOK_INDEX[look]] if name == "mouse-look"
                         else self._cursor_poses.frames[2] if step.pose == "cursor-follow"
                         else self._cursor_poses.frame_at((self.clock() - self._started) * 1000))
                frame = self._cursor_poses.facing.get(frame, facing)
                self.displayed_frame = frame
                self.displayed_reversed = facing == "left"
                painter.drawPixmap(0, 0, frame)
                return True
        if (step.pose == "walk" or cursor_walk) and self.pet.character.definition.id == "whale-girl":
            clip = self._walk_poses.load("walk-" + self.direction)
            if clip is not None and (step.destination is not None or cursor_walk):
                # Drive footsteps by travelled distance, so slowing at either
                # end of the path also slows the gait instead of skating.
                distance = self._cursor_distance if cursor_walk else self._walk_distance
                gait = GAITS[step.gait]
                cycle = distance / gait.stride
                frame = self._walk_poses.frame_at(cycle * sum(clip["durations"]), loop=True)
                self.displayed_frame = frame
                self.displayed_reversed = self.direction == "left"
                painter.save()
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                # Continuous, tiny weight transfer bridges the drawn key poses.
                # Pivot at the feet rather than making the entire sprite float.
                anchor_x = 95 if self.direction == "left" else 185
                painter.translate(anchor_x, 222)
                painter.rotate(math.sin(cycle * 2 * math.pi) * gait.sway)
                painter.translate(-anchor_x, -222)
                painter.drawPixmap(0, 0, frame)
                painter.restore()
                return True
        fallback_pose = "walk" if cursor_walk else "press" if step.pose == "cursor-follow" else {
            "mouse-look": "think", "mouse-curious": "think", "mouse-shy": "touch",
            "mouse-settle": "nothing", "window-seat": "sit", "window-swing": "sit",
            "window-rise": "sit", "window-peek-left": "peek", "window-peek-right": "peek",
            "window-consider": "peek", "window-press": "press", "window-press-recover": "press"}.get(step.pose, step.pose)
        if self.pet.character.definition.id == "whale-girl":
            name = "daily-nap" if step.pose.startswith("nap-") else step.pose if step.pose.startswith(("base-", "daily-")) else {
                "peek": "base-attention" if self.scene == "background" else "base-think",
                "sit": "base-nothing", "press": "base-attention"}.get(step.pose)
            if name and self._scene_poses.load(name):
                elapsed_ms = (self.clock() - self._started) * 1000
                if step.pose.startswith("nap-"):
                    index = (0 if step.pose == "nap-settle" else 3 if step.pose == "nap-wake"
                             else 1 + int(elapsed_ms // 1400) % 2)
                    frame = self._scene_poses.frames[index]
                else:
                    frame = self._scene_poses.frame_at(elapsed_ms)
                facing = self.pet.inward_facing()
                frame = self._scene_poses.facing.get(frame, facing)
                self.displayed_frame = frame
                self.displayed_reversed = facing == "left"
                painter.drawPixmap(0, 0, frame)
                return True
        fallback_pose = {"base-cheerup": "press", "base-nothing": "nothing",
                         "daily-nap": "nothing", "nap-settle": "nothing", "nap-sleep": "nothing",
                         "nap-wake": "nothing", "daily-stretch": "press"}.get(fallback_pose, fallback_pose)
        pixmap = self._pose_pixmap(fallback_pose)
        phase = (self.clock() - self._started) * (8 if step.pose == "walk" else 2.8)
        bob = -abs(math.sin(phase)) * (3 if step.pose == "walk" else 1)
        angle = math.sin(phase) * (1.5 if step.pose == "walk" else 0.65)
        if self.scene == "dance":
            bob = -abs(math.sin(phase)) * 9
            angle *= 2
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self._clip_rect is not None:
            painter.setClipRect(self._clip_rect)
        if self.scene == "sit" and fallback_pose == "sit":
            anchor = self._sit_anchor()
            painter.translate(anchor.x(), anchor.y())
            painter.rotate(angle)
            painter.drawPixmap(self._pose_origin(pixmap) - anchor, pixmap)
        else:
            painter.translate(self.WIDTH / 2, self.HEIGHT - 8 + bob)
            painter.rotate(angle)
            if step.pose == "walk" and step.destination and step.destination.x() < self._from.x():
                painter.scale(-1, 1)
            painter.drawPixmap(round(-pixmap.width() / 2), -pixmap.height(), pixmap)
        painter.restore()
        return True

    def clear_assets(self):
        self.cancel()
        self._cache.clear()
        self._walk_poses.clear()
        self._cursor_poses.clear()
        self._scene_poses.clear()
        self._window_poses.clear()

    def stop(self):
        self.cancel()
        self.system.cancel(clear=True)
        self._ambient.stop()
        self.attention.suspend()
        self.desktop.stop()
        self._cache.clear()
        self._walk_poses.clear()
        self._cursor_poses.clear()
        self._scene_poses.clear()
        self._window_poses.clear()
