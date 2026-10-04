"""Low-frequency nearby glances, separate from the full cursor story."""
from PySide6.QtCore import QObject, QPoint, QTimer
from PySide6.QtGui import QCursor

from character.cursor import LOOK_INDEX, CursorInterest

from .keyposes import PoseLibrary


class CursorAttention(QObject):
    def __init__(self, director):
        super().__init__(director)
        self.director = director
        self.pet = director.pet
        self.library = PoseLibrary(director.asset_root)
        self.interest = CursorInterest()
        self.frame = None
        self._timer = QTimer(self)
        self._timer.setInterval(240)
        self._timer.timeout.connect(self.advance)

    def eligible(self):
        return (self.director.preferences.module_enabled("cursor") and self.director._can_start() and
                self.pet.character.definition.id == "whale-girl" and
                self.pet._tool_reaction.status == "idle" and self.pet._cur_key == "nothing")

    def sync(self):
        if self.eligible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self.suspend()

    def suspend(self):
        self._timer.stop()
        self.frame = None
        self.library.clear()
        self.interest = CursorInterest()
        if not self.director.active:
            self.director.displayed_frame = None
        self.pet.update()

    def advance(self):
        if not self.eligible():
            self.suspend()
            return
        now = self.director.clock()
        cursor = QCursor.pos()
        self.interest.sample(cursor.x(), cursor.y(), now)
        facing = self.pet.inward_facing()
        delta = cursor - self.pet.pos() - QPoint(95 if facing == "left" else 185, 90)
        direction = self.interest.look(delta.x(), delta.y(), now)
        self.frame = None
        if direction is not None and self.library.load("mouse-look"):
            if facing == "left" and direction in ("left", "right"):
                direction = "right" if direction == "left" else "left"
            self.frame = self.library.frames[LOOK_INDEX[direction]]
            self.frame = self.library.facing.get(self.frame, facing)
        self.director.displayed_frame = self.frame
        self.pet.update()

    def draw(self, painter):
        if self.frame is None or not self.eligible():
            return False
        painter.drawPixmap(0, 0, self.frame)
        self.director.displayed_frame = self.frame
        self.director.displayed_reversed = self.pet.inward_facing() == "left"
        return True
