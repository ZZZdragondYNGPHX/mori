"""Direct-touch choreography sharing the companion's interruption lifecycle."""
import math

from PySide6.QtCore import QObject, QPoint, QPointF, QRect
from PySide6.QtGui import QCursor, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from character.dialogue import fixed_line
from character.window_motion import clip_index

from .desktop_input import DesktopScroll
from .desktop_interaction import KWinDesktop
from .doodles import DoodleLayer, StrokeTrace, drawing_strokes
from .keyposes import PoseLibrary

SYSTEM_SCENES = {
    "move-window": "双手拉动窗口", "resize-window": "拉伸窗口边角",
    "snap-left": "把窗口摆到左半屏", "snap-right": "把窗口摆到右半屏",
    "maximize": "按最大化／还原按钮", "minimize": "按最小化按钮",
    "scroll-up": "向上翻阅页面", "scroll-down": "向下翻阅页面",
    "doodle-screen": "在屏幕上涂鸦", "doodle-window": "给窗口做标记",
}
DIRECT_SCENES = set(SYSTEM_SCENES) - {"minimize", "maximize"}
GEOMETRY_SCENES = {"move-window", "resize-window", "snap-left", "snap-right"}


def geometry_goal(scene, geometry, bounds):
    goal = QRect(geometry)
    if scene.startswith("snap-"):
        width = bounds.width() // 2
        return QRect(bounds.left() if scene == "snap-left" else bounds.left() + width,
                     bounds.top(), width if scene == "snap-left" else bounds.width() - width, bounds.height())
    if scene == "resize-window":
        goal.setSize(goal.size().expandedTo(QRect(0, 0, 240, 180).size()))
        goal.setWidth(min(goal.width() + 100, bounds.right() - goal.left() + 1))
        goal.setHeight(min(goal.height() + 60, bounds.bottom() - goal.top() + 1))
    else:
        delta = -100 if geometry.center().x() > bounds.center().x() else 100
        goal.translate(delta, 20)
        goal.moveLeft(max(bounds.left(), min(goal.left(), bounds.right() - goal.width() + 1)))
        goal.moveTop(max(bounds.top(), min(goal.top(), bounds.bottom() - goal.height() + 1)))
    return goal


class SystemActions(QObject):
    def __init__(self, director):
        super().__init__(director)
        self.director = director
        self.ink = DoodleLayer(self, director.clock)
        self.ink.selection_finished.connect(director._schedule)
        self.input = DesktopScroll(self)
        self.input.ready.connect(self._input_ready)
        self.input.failed.connect(self._input_failed)
        self.library = PoseLibrary(director.asset_root)
        self.bounds = QRect()
        self.doodle = None
        self.contact = QPointF()
        self.origin_geometry = QRect()
        self.goal_geometry = QRect()
        self.pending_scroll = None
        self.scroll_point = QPoint()
        self.scroll_amount = 0.
        self.last_geometry = QRect()
        self.last_send = 0.
        self.epoch = 0
        director.desktop.authorize_geometry = self.authorize_geometry
        if hasattr(director.desktop, "geometry_finished"):
            director.desktop.geometry_finished.connect(self._geometry_finished)

    def allowed(self):
        return self.director.preferences.module_enabled("system") and self.director.pet.isVisible()

    def request(self, scene, kind="star", text=""):
        """Called after settings hide: capture the target, then let the user pick a point."""
        d = self.director
        if not self.allowed() or not d._can_start() or d.active:
            d.message.emit(fixed_line("notice.system_disabled"))
            return False
        d._ambient.stop()
        target = d.desktop.active_window
        if scene in ("doodle-screen", "doodle-window", "scroll-up", "scroll-down"):
            epoch = self.epoch

            def selected(point):
                if epoch != self.epoch or not self.allowed():
                    return
                if scene.startswith("scroll-"):
                    if self.input.available:
                        self.start(scene, target, point)
                    else:
                        self.pending_scroll = (scene, target, point)
                        self.input.prepare()
                else:
                    self.start(scene, target, point, kind, text)

            self.ink.pick(selected)
            return True
        return d.start(scene, target=target)

    def _input_ready(self, success):
        pending, self.pending_scroll = self.pending_scroll, None
        if pending and success and self.allowed():
            scene, target, point = pending
            epoch = self.epoch
            active = self.director.desktop.active_window
            if target and active and active.identity == target.identity and QCursor.pos() == point:
                self.start(scene, target, point)
                return
            # An authorization dialog may have moved focus. Select the region again.
            self.ink.pick(lambda chosen: self.start(scene, target, chosen)
                          if self.allowed() and epoch == self.epoch else None)

    def _input_failed(self, message):
        self.director.message.emit(message)
        if self.director.active and self.director.scene.startswith("scroll-"):
            self.director.cancel(restore_position=False)

    def start(self, scene, target=None, point=None, kind="star", text=""):
        from .interactions import Step

        d, pet = self.director, self.director.pet
        if not self.allowed() or not d._can_start() or d.active:
            return False
        needs_window = scene != "doodle-screen"
        target = (target or d.desktop.active_window) if needs_window else None
        if needs_window and (not target or not target.eligible(d.preferences) or target.interactive):
            d.message.emit(fixed_line("notice.window_unavailable"))
            return False
        if target and scene in GEOMETRY_SCENES:
            if target.maximized or not target.movable or (scene != "move-window" and not target.resizable):
                d.message.emit(fixed_line("notice.geometry_unavailable"))
                return False
        if scene.startswith("scroll-") and not self.input.available:
            return self.request(scene)
        screen = QApplication.screenAt(point or (target.geometry.center() if target else pet.pos())) or pet.screen()
        self.bounds = screen.availableGeometry() if screen else d._bounds()
        self.doodle = None
        if scene.startswith("doodle-"):
            trace = StrokeTrace(drawing_strokes(kind, text))
            if not trace.edges:
                d.message.emit(fixed_line("notice.doodle_empty"))
                return False
            point = point or (target.geometry.center() if target else self.bounds.center())
            origin = QPointF(point)
            region = target.geometry.intersected(self.bounds) if target else self.bounds
            if trace.bounds.width() + 8 > region.width() or trace.bounds.height() + 8 > region.height():
                d.message.emit(fixed_line("notice.ink_space"))
                return False
            origin.setX(max(region.left() + 4, min(origin.x(), region.right() - trace.bounds.right() - 4)))
            origin.setY(max(region.top() + 4, min(origin.y(), region.bottom() - trace.bounds.bottom() - 4)))
            footprint = self._footprint("system-pen", "right")
            span = trace.bounds.adjusted(footprint.left(), footprint.top(), footprint.right(), footprint.bottom())
            low_x, high_x = self.bounds.left() - span.left(), self.bounds.right() - span.right()
            low_y, high_y = self.bounds.top() - span.top(), self.bounds.bottom() - span.bottom()
            if low_x > high_x or low_y > high_y:
                d.message.emit(fixed_line("notice.screen_space"))
                return False
            origin.setX(max(low_x, min(origin.x(), high_x)))
            origin.setY(max(low_y, min(origin.y(), high_y)))
            if target and not QRect(target.geometry).contains(trace.bounds.translated(origin).toAlignedRect()):
                d.message.emit(fixed_line("notice.window_ink_space"))
                return False
            self.doodle = self.ink.add(trace, origin, target)
            self.contact = self.doodle.tip
            duration = max(.8, trace.length / 180)
            steps = [Step(.45, "system-pen-ready"), Step(duration, "system-write"), Step(.55, "system-pen-away")]
        elif scene.startswith("scroll-"):
            point = point or target.geometry.center()
            if not target.geometry.contains(point):
                d.message.emit(fixed_line("notice.scroll_point"))
                return False
            self.scroll_point = QPoint(point)
            self.scroll_amount = 0.
            self.contact = QPointF(point)
            steps = [Step(.4, "system-grab"), Step(1.3, "system-scroll"), Step(.45, "system-release")]
        else:
            self.origin_geometry = QRect(target.geometry)
            self.goal_geometry = geometry_goal(scene, target.geometry, self.bounds)
            if self.goal_geometry == self.origin_geometry:
                d.message.emit(fixed_line("notice.window_at_goal"))
                return False
            self.contact = self._window_contact(target.geometry, scene)
            end_contact = self._window_contact(self.goal_geometry, scene)
            if not self._placeable(end_contact, "system-grip"):
                d.message.emit(fixed_line("notice.goal_space"))
                return False
            self.last_geometry = QRect(target.geometry)
            self.last_send = float("-inf")
            delta = (self.goal_geometry.topLeft() - target.geometry.topLeft()).manhattanLength()
            duration = max(1.2, delta / 100) if scene == "move-window" else 1.8
            steps = [Step(.4, "system-grab"), Step(duration, "system-drag"), Step(.45, "system-release")]
        # The complete character must fit before making any native change.
        if not self._placeable(self.contact, "system-pen" if self.doodle else "system-grip"):
            if self.doodle:
                self.ink.drawings.remove(self.doodle)
                self.ink.advance()
                self.doodle = None
            d.message.emit(fixed_line("notice.stand_space"))
            return False
        d._activate(scene, target)
        d._last_scene = scene
        d.steps = [d._walk_step(self._body_position(), fixed_line("system.start")), *steps]
        d.index = 0
        d._enter_step()
        d.changed.emit()
        return True

    def _window_contact(self, rect, scene):
        if scene == "resize-window":
            return QPointF(rect.right(), rect.bottom())
        if scene.startswith("snap-"):
            return QPointF(rect.center().x(), rect.center().y())
        return QPointF(rect.center().x(), rect.top() + max(12, self.director.target.titlebar_height // 2)
                       if self.director.target else rect.top() + 15)

    def _anchor(self):
        clip = self.library.load("system-pen" if self.doodle else "system-grip")
        return QPointF(*clip["anchors"][1]) if clip else QPointF(240, 126)

    def _footprint(self, family, facing):
        clip = self.library.load(family)
        if not clip:
            return QRect()
        union = QRect()
        for index, frame in enumerate(self.library.frames):
            anchor_index = index if index in (1, 2) else 1
            anchor = QPoint(*clip["anchors"][anchor_index])
            rect = QRegion(frame.mask()).boundingRect()
            if facing == "left":
                rect.moveLeft(280 - rect.x() - rect.width())
                anchor.setX(280 - anchor.x())
            rect.translate(-anchor)
            union = union.united(rect)
        return union

    def _placeable(self, contact, family="system-grip"):
        if self.bounds.width() < 280 or self.bounds.height() < 230:
            return False
        preferred = getattr(self, "facing", "right")
        for facing in (preferred, "left" if preferred == "right" else "right"):
            rect = self._footprint(family, facing).translated(contact.toPoint())
            if not rect.isNull() and self.bounds.contains(rect):
                self.facing = facing
                return True
        return False

    def can_touch_window(self, window):
        screen = QApplication.screenAt(window.geometry.center()) or self.director.pet.screen()
        self.bounds = screen.availableGeometry() if screen else self.director._bounds()
        return self._placeable(self._window_contact(window.geometry, "move-window"))

    def _body_position(self):
        anchor = self._anchor()
        if self.facing == "left":
            anchor.setX(280 - anchor.x())
        return QPoint(round(max(self.bounds.left(), min(self.contact.x() - anchor.x(), self.bounds.right() - 279))),
                      round(max(self.bounds.top(), min(self.contact.y() - anchor.y(), self.bounds.bottom() - 229))))

    def authorize_geometry(self, identity):
        d = self.director
        window = d.desktop.windows.get(identity)
        return bool(self.allowed() and d.active and d.scene in GEOMETRY_SCENES and d.target and
                    d.target.identity == identity and window and window.eligible(d.preferences) and not window.interactive)

    def button_position(self, window, action):
        clip = self.library.load("system-reach")
        anchor = QPoint(*clip["anchors"][2])
        button = KWinDesktop.button_point(window, action)
        if button.x() < window.geometry.center().x():
            anchor.setX(280 - anchor.x())
        return button - anchor

    def button_fits(self, window, action):
        clip = self.library.load("system-reach")
        if not clip:
            return False
        button = KWinDesktop.button_point(window, action)
        facing = "left" if button.x() < window.geometry.center().x() else "right"
        origin = self.button_position(window, action)
        rect = QRect()
        for frame in self.library.frames:
            outline = QRegion(self.library.facing.get(frame, facing).mask()).boundingRect()
            rect = rect.united(outline.translated(origin))
        return self.director._bounds().contains(rect)

    def draw_button(self, painter):
        d = self.director
        pose = d.steps[d.index].pose
        clip = self.library.load("system-reach")
        if not clip:
            return False
        elapsed = (d.clock() - d._started) * 1000
        timing = d._window_poses.definition(pose)
        phase = clip_index(timing, elapsed) if timing else 0
        index = ([0, 1, 2][phase] if pose == "window-press" else
                 [2, 3, 0][phase] if pose == "window-press-recover" else 0)
        frame = self.library.facing.get(self.library.frames[index], d._window_facing)
        painter.drawPixmap(0, 0, frame)
        d.displayed_frame = frame
        d.displayed_reversed = d._window_facing == "left"
        return True

    def _geometry_finished(self, identity, success):
        d = self.director
        if d.active and d.scene in GEOMETRY_SCENES and d.target and d.target.identity == identity:
            if not success:
                d.message.emit(fixed_line("notice.geometry_failed"))
                d.cancel(restore_position=False)
            else:
                d.pet.update()

    def target_changed(self):
        d = self.director
        self.ink.sync_windows(d.desktop.windows)
        if not d.active or d.scene not in DIRECT_SCENES or not d.target:
            return
        window = d.desktop.windows.get(d.target.identity)
        if not window or not window.eligible(d.preferences) or window.interactive:
            d.cancel(restore_position=False)
            return
        d.target = window
        if d.scene.startswith("doodle-"):
            self.contact = self.doodle.tip
        elif d.scene in GEOMETRY_SCENES:
            self.contact = self._window_contact(window.geometry, d.scene)
        family = "system-pen" if self.doodle else "system-grip"
        if not self._placeable(self.contact, family):
            d.cancel(restore_position=False)
            return
        if d.steps and d.steps[d.index].pose != "walk":
            d.pet.move(self._body_position())
            d.pet.update()

    def advance(self, elapsed):
        d = self.director
        step = d.steps[d.index]
        fraction = max(0, min(1, elapsed / step.seconds))
        if step.pose == "system-write":
            self.doodle.progress = self.doodle.trace.length * fraction
            self.contact = self.doodle.tip
            if fraction == 1 and self.doodle.completed is None:
                self.doodle.completed = d.clock()
        elif step.pose == "system-drag":
            f = fraction * fraction * (3 - 2 * fraction)
            a, b = self.origin_geometry, self.goal_geometry
            geometry = QRect(round(a.x() + (b.x() - a.x()) * f), round(a.y() + (b.y() - a.y()) * f),
                             round(a.width() + (b.width() - a.width()) * f), round(a.height() + (b.height() - a.height()) * f))
            now = d.clock()
            if geometry != self.last_geometry and (now - self.last_send >= .04 or fraction == 1):
                if not d.desktop.set_geometry(d.target, geometry):
                    d.cancel(restore_position=False)
                    return
                self.last_geometry = geometry
                self.last_send = now
            # Readback, not the requested rectangle, determines the actual hand position.
            self.contact = self._window_contact(d.target.geometry, d.scene)
            if fraction == 1 and getattr(d.desktop, "geometry_busy", False):
                d.pet.update()
                return
        elif step.pose == "system-scroll":
            active = d.desktop.active_window
            if QCursor.pos() != self.scroll_point or not active or active.identity != d.target.identity:
                d.cancel(restore_position=False)
                return
            amount = (120 if d.scene == "scroll-down" else -120) * fraction
            if not self.input.scroll(amount - self.scroll_amount, finish=fraction == 1):
                d.cancel(restore_position=False)
                return
            self.scroll_amount = amount
            self.contact = QPointF(self.scroll_point) + QPointF(0, math.sin(fraction * math.pi) * (-24 if d.scene == "scroll-down" else 24))
        if not self._placeable(self.contact, "system-pen" if self.doodle else "system-grip"):
            d.cancel(restore_position=False)
            return
        d.pet.move(self._body_position())
        d.pet.update()
        if elapsed >= step.seconds:
            d._next_step()

    def draw(self, painter):
        d = self.director
        pose = d.steps[d.index].pose
        family = "system-pen" if "pen" in pose or pose == "system-write" else "system-grip"
        clip = self.library.load(family)
        if not clip:
            return False
        if pose == "system-write":
            index = 1  # Stable pen contact; hand motion follows the actual stroke.
        elif pose == "system-drag":
            distance = (d.target.geometry.topLeft() - self.origin_geometry.topLeft()).manhattanLength()
            index = 1 + int(distance / 28) % 2
        else:
            index = 0 if pose in ("system-grab", "system-pen-ready") else 3
        frame = self.library.frames[index]
        mirrored = self.facing == "left"
        frame = self.library.facing.get(frame, self.facing)
        anchor = QPointF(*clip["anchors"][index if index in (1, 2) else 1])
        if mirrored:
            anchor.setX(280 - anchor.x())
        local = self.contact - QPointF(d.pet.pos())
        # Move the drawing by its true hand/nib anchor; compensate rather than stretch it.
        origin = local - anchor
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(origin)
        painter.drawPixmap(0, 0, frame)
        painter.restore()
        d.displayed_frame = frame
        d.displayed_origin = origin.toPoint()
        d.displayed_reversed = mirrored
        return True

    def cancel(self, clear=False):
        self.epoch += 1
        self.pending_scroll = None
        self.ink.cancel_pick()
        if self.doodle and self.doodle.completed is None:
            self.doodle.completed = self.director.clock()
        self.doodle = None
        if self.director.scene.startswith("scroll-") and self.input.available:
            self.input.scroll(0, finish=True)
        if self.input.preparing:
            self.input.close()
        if clear:
            self.ink.clear()
            self.input.close()
