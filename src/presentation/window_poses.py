"""Bounded key poses with stable seat support and paint-only occlusion."""
import json
import math

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QPainter, QRegion

from character.window_motion import clip_index

from .keyposes import PoseLibrary


class WindowPoseLibrary(PoseLibrary):
    DIRECTIONAL = {"window-seat", "window-swing", "window-rise", "window-peek-top",
                   "window-consider", "window-press", "window-press-recover"}
    def __init__(self, root):
        super().__init__(root)
        self._bounds_cache = {}
        try:
            self._manifest = json.loads((self.root / "clips.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def has(self, name):
        return bool(self._manifest and name in self._manifest.get("clips", {}))

    @property
    def support(self):
        return QPoint(*self._manifest.get("window_support", [190, 180])) if self._manifest else QPoint(190, 180)

    @property
    def contact(self):
        return QPoint(*self._manifest.get("window_button_contact", [240, 126])) if self._manifest else QPoint(240, 126)

    def anchor_at(self, name, elapsed_ms, facing="right"):
        clip = self.definition(name)
        if clip and "anchors" in clip:
            point = QPoint(*clip["anchors"][clip_index(clip, elapsed_ms)])
            if facing == "left" and name in self.DIRECTIONAL:
                point.setX(280 - point.x())
            return point
        return None

    def footprint(self, name, anchored=False, edge=None, facing="right"):
        """Union every pose's visible outline; never accept a cropped sliver."""
        if name not in self._bounds_cache:
            clip = self.load(name)
            if clip is None:
                return None
            self._bounds_cache[name] = [QRegion(frame.mask()).boundingRect() for frame in self.frames]
        clip = self.definition(name)
        union = QRect()
        for index, raw in enumerate(self._bounds_cache[name]):
            rect = QRect(raw)
            offset = clip.get("offsets", [[0, 0]] * len(clip["frames"]))[index]
            rect.translate(*offset)
            mirrored = facing == "left" and name in self.DIRECTIONAL
            if mirrored:
                rect.moveLeft(280 - rect.x() - rect.width())
            if edge is not None:
                rect = rect.intersected(edge)
            if anchored and "anchors" in clip:
                anchor = QPoint(*clip["anchors"][index])
                if mirrored:
                    anchor.setX(280 - anchor.x())
                rect.translate(-anchor)
            union = union.united(rect)
        return union

    def pose_started(self, name, elapsed_ms):
        clip = self.definition(name)
        index = clip_index(clip, elapsed_ms)
        cycle = int(elapsed_ms // sum(clip["durations"])) if clip.get("loop") else 0
        return (cycle * sum(clip["durations"]) + sum(clip["durations"][:index])) / 1000

    def draw(self, painter, name, elapsed_ms, support, clip_rect=None, facing="right"):
        clip = self.load(name)
        if clip is None:
            return None
        index = clip_index(clip, elapsed_ms)
        frame = self.frames[index]
        mirrored = facing == "left" and name in self.DIRECTIONAL
        if mirrored:
            frame = self.facing.get(frame, "left")
        origin = QPoint(*clip.get("offsets", [[0, 0]] * len(self.frames))[index])
        anchor = None
        if "anchors" in clip:
            anchor = QPoint(*clip["anchors"][index])
            if mirrored:
                anchor.setX(280 - anchor.x())
            origin = support - anchor
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if clip_rect is not None:
            painter.setClipRect(clip_rect)
        if anchor is not None and name == "window-swing":
            painter.translate(support.x(), support.y())
            painter.rotate(math.sin(elapsed_ms / 1000 * 2.4) * .45)
            painter.drawPixmap(-anchor, frame)
        else:
            painter.drawPixmap(origin, frame)
        painter.restore()
        return frame, origin
