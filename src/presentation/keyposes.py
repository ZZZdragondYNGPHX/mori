"""Small bounded PNG key-pose player shared by movement and touch feedback."""
import json
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap, QRegion, QTransform

from character.motion import DragDynamics
from character.timing import pose_durations


class FacingFrames:
    """Keep one mirrored current frame; move the entire character together."""
    def __init__(self):
        self.clear()

    def clear(self):
        self.key = None
        self.frame = None

    def get(self, frame, facing):
        if facing != "left":
            return frame
        if self.key != frame.cacheKey():
            self.frame = frame.transformed(QTransform().scale(-1, 1))
            self.key = frame.cacheKey()
        return self.frame


class PoseLibrary:
    """Cache only the current clip, including up to six living key poses."""
    def __init__(self, root):
        self.root = Path(root) / "motion"
        self._manifest = None
        self._name = ""
        self.frames = []
        self.clip = None
        self.facing = FacingFrames()

    def load(self, name):
        if name == self._name:
            return self.clip
        self.clear()
        try:
            if self._manifest is None:
                self._manifest = json.loads((self.root / "clips.json").read_text(encoding="utf-8"))
            clip = self.definition(name)
            if clip is None:
                return None
            unique = {f: QPixmap(str(self.root / f)) for f in set(clip["frames"])}
            if any(pixmap.isNull() for pixmap in unique.values()):
                return None
            self.frames = [unique[f] for f in clip["frames"]]
            self.clip = clip
            self._name = name
            return clip
        except (OSError, ValueError, KeyError):
            return None

    def definition(self, name):
        if self._manifest is None:
            return None
        clip = self._manifest.get("clips", {}).get(name)
        return {**clip, "durations": pose_durations(name, clip["durations"])} if clip else None

    def clear(self):
        self._name = ""
        self.clip = None
        self.frames = []
        self.facing.clear()

    def frame_at(self, elapsed_ms, loop=False):
        if self.clip is None:
            return None
        durations = self.clip["durations"]
        total = sum(durations)
        elapsed_ms = elapsed_ms % total if loop else min(elapsed_ms, total - 1)
        for index, duration in enumerate(durations):
            if elapsed_ms < duration:
                return self.frames[index]
            elapsed_ms -= duration
        return self.frames[-1]

    @property
    def stride(self):
        return self._manifest.get("walk_stride_pixels", 56) if self._manifest else 56

    @property
    def sway(self):
        return self._manifest.get("walk_sway_degrees", .7) if self._manifest else .7


class TouchPosePlayer(QObject):
    def __init__(self, pet, asset_root, clock=time.monotonic):
        super().__init__(pet)
        self.pet = pet
        self.library = PoseLibrary(asset_root)
        self.clock = clock
        self.active = False
        self._started = 0
        self._paused_at = None
        self.frame = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.advance)

    def play(self, name):
        self.stop()
        if not self.library.load(name):
            return False
        self.active = True
        self._started = self.clock()
        self._paused_at = None
        self.advance()
        if self.pet.isVisible():
            self._timer.start()
        else:
            self.pause()
        return True

    def advance(self):
        if not self.active or self._paused_at is not None:
            return
        elapsed = (self.clock() - self._started) * 1000
        self.frame = self.library.frame_at(elapsed)
        if elapsed >= sum(self.library.clip["durations"]):
            self._timer.stop()  # Hold the recovery pose until touch timeout.
        self.pet.update()

    def pause(self):
        self._timer.stop()
        if self.active and self._paused_at is None:
            self._paused_at = self.clock()

    def resume(self):
        if not self.active or self._paused_at is None:
            return
        self._started += self.clock() - self._paused_at
        self._paused_at = None
        self.advance()
        if (self.clock() - self._started) * 1000 < sum(self.library.clip["durations"]):
            self._timer.start()

    def stop(self):
        self.active = False
        self._timer.stop()
        self.frame = None
        self._paused_at = None
        self.library.clear()


def drag_transform(pivot, angle, stretch, bounds):
    """Keep the full sprite visible even when grabbed by a foot or tail."""
    canvas = QRectF(1, 1, 278, 228)
    for _ in range(10):
        transform = QTransform()
        transform.translate(pivot.x(), pivot.y())
        transform.rotate(angle)
        transform.scale(1, stretch)
        transform.translate(-pivot.x(), -pivot.y())
        if canvas.contains(transform.mapRect(bounds)):
            return transform
        angle *= .7
        stretch = 1 + (stretch - 1) * .7
    return QTransform()


class DragPosePlayer(TouchPosePlayer):
    """Pose changes follow velocity; the spring keeps reversal continuous."""
    def __init__(self, pet, asset_root, clock=time.monotonic):
        super().__init__(pet, asset_root, clock)
        self.dynamics = DragDynamics()
        self.pivot = QPointF(185, 65)
        self._last_point = QPointF()
        self._last_sample = self._last_tick = 0
        self._bounds = []
        self.pose_index = 0
        self.mirrored = False

    def start(self, pivot, global_point, mirrored=False):
        self.dynamics = DragDynamics()
        self.mirrored = mirrored
        self.pivot = QPointF(pivot)
        self._last_point = QPointF(global_point)
        self._last_sample = self._last_tick = self.clock()
        if not self.play("pickup"):
            return False
        self._bounds = [QRectF(QRegion(frame.mask()).boundingRect()) for frame in self.library.frames]
        return True

    def sample(self, global_point):
        if not self.active:
            return
        point = QPointF(global_point)
        now = self.clock()
        delta = point - self._last_point
        self.dynamics.push(-delta.x() if self.mirrored else delta.x(), delta.y(), now - self._last_sample)
        self._last_point, self._last_sample = point, now
        self.advance()
        if self.pet.isVisible():
            self._timer.start()

    def advance(self):
        if not self.active or self._paused_at is not None:
            return
        now = self.clock()
        self.dynamics.advance(now - self._last_tick)
        self._last_tick = now
        self.pose_index = self.dynamics.pose if now - self._started >= .16 else 0
        self.frame = self.library.frames[self.pose_index]
        if now - self._started >= .16 and self.dynamics.settled:
            self._timer.stop()
        self.pet.update()

    def draw(self, painter):
        if not self.active or self.frame is None:
            return False
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        transform = drag_transform(self.pivot, self.dynamics.angle, self.dynamics.stretch,
                                   self._bounds[self.pose_index])
        painter.setTransform(transform, True)
        painter.drawPixmap(0, 0, self.frame)
        painter.restore()
        return True
