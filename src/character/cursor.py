"""Small, renderer-independent policies for gentle pointer attention."""
import math

from .motion import movement_direction

LOOK_INDEX = {"left": 0, "right": 1, "up": 2, "down": 3}


class CursorInterest:
    def __init__(self):
        self.direction = "right"
        self._candidate = "right"
        self._candidate_at = 0
        self._near_at = None
        self._last_sample = None
        self.startled_until = float("-inf")

    def sample(self, x, y, now):
        if self._last_sample is not None:
            px, py, previous = self._last_sample
            dt = now - previous
            if 0 < dt <= .5 and math.hypot(x - px, y - py) / dt > 700:
                self.startled_until = now + .65
        self._last_sample = x, y, now

    def shy(self, distance, now):
        return distance < 85 or (distance < 180 and now < self.startled_until)

    def look(self, dx, dy, now):
        distance = math.hypot(dx, dy)
        if distance > (340 if self._near_at is not None else 280):
            self._near_at = None
            return None
        if self._near_at is None:
            self._near_at = now
            self.direction = movement_direction(dx, dy, self.direction)
            self._candidate = self.direction
        candidate = movement_direction(dx, dy, self.direction)
        if candidate != self._candidate:
            self._candidate, self._candidate_at = candidate, now
        if candidate != self.direction and now - self._candidate_at >= .18:
            self.direction = candidate
        # Leave room for the ordinary breathing/blinking idle between glances.
        phase = (now - self._near_at) % 6.4
        return self.direction if .24 <= phase < 2.64 else None


class CursorFollower:
    """Slow acceleration, personal space and a finite travel budget."""
    def __init__(self):
        self.vx = self.vy = self.travelled = 0.0

    def advance(self, dx, dy, seconds):
        dt = max(0, min(.08, seconds))
        distance = math.hypot(dx, dy)
        gap = max(0, distance - 110)
        speed = min(100, gap * 3)
        target_x = dx / distance * speed if distance else 0
        target_y = dy / distance * speed if distance else 0
        mix = 1 - math.exp(-8 * dt)
        self.vx += (target_x - self.vx) * mix
        self.vy += (target_y - self.vy) * mix
        x, y = self.vx * dt, self.vy * dt
        length = math.hypot(x, y)
        permitted = min(gap, max(0, 260 - self.travelled))
        if length > permitted and length:
            x, y = x * permitted / length, y * permitted / length
        self.travelled += math.hypot(x, y)
        return x, y
