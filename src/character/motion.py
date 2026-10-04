"""Direction and touch progression policies; independent of the renderer."""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Gait:
    speed: float
    stride: float
    sway: float
    transition: float


GAITS = {"walk": Gait(72, 56, .7, .07),
         "trot": Gait(135, 64, .9, .055),
         "run": Gait(220, 76, 1.1, .04)}


def inward_facing(x, left, right, previous="right"):
    """Face into the screen, with a small quiet zone around its centre."""
    midpoint = (left + right) / 2
    return "left" if x > midpoint + 32 else "right" if x < midpoint - 32 else previous


def choose_gait(distance, pace="auto"):
    if pace in GAITS:
        return pace
    return "walk" if distance < 140 else "trot" if distance < 320 else "run"


def travel_seconds(distance, gait):
    return max(.04, distance / GAITS[gait].speed)


def movement_direction(dx, dy, previous="right"):
    """Choose one of four views, retaining it near diagonal boundaries."""
    if dx == dy == 0:
        return previous
    ax, ay = abs(dx), abs(dy)
    horizontal = "right" if dx >= 0 else "left"
    vertical = "down" if dy >= 0 else "up"
    if previous == horizontal and ax * 1.2 >= ay:
        return horizontal
    if previous == vertical and ay * 1.2 >= ax:
        return vertical
    return horizontal if ax >= ay else vertical


class TouchProgression:
    """Independent affection counters, reset after a quiet interval."""
    def __init__(self):
        self._history = {}

    def next(self, region, now):
        last, count, variant = self._history.get(region, (float("-inf"), 0, 1))
        count = min(3, count + 1) if now - last < 12 else 1
        variant = 1 - variant
        self._history[region] = now, count, variant
        return f"{region}-{count}-{variant}"

    def clear(self):
        self._history.clear()


class DragDynamics:
    """Bounded drag lag with a damped spring; no desktop coordinates change."""
    def __init__(self):
        self.vx = self.vy = 0.0
        self.angle = self.angular_velocity = 0.0
        self.stretch = 1.0

    def push(self, dx, dy, seconds):
        seconds = max(1 / 120, min(.25, seconds))
        self.vx = max(-1800, min(1800, dx / seconds))
        self.vy = max(-1800, min(1800, dy / seconds))

    def advance(self, seconds):
        # Integrate small bounded steps so a delayed Qt frame cannot explode.
        remaining = max(0, min(.08, seconds))
        while remaining > 1e-8:
            dt = min(1 / 120, remaining)
            target = max(-7, min(7, self.vx * .012))
            self.angular_velocity += (110 * (target - self.angle) -
                                      18 * self.angular_velocity) * dt
            self.angle = max(-9, min(9, self.angle + self.angular_velocity * dt))
            target_stretch = 1 - max(-.035, min(.035, self.vy * .00004))
            self.stretch += (target_stretch - self.stretch) * (1 - math.exp(-12 * dt))
            decay = math.exp(-dt / .12)
            self.vx *= decay
            self.vy *= decay
            remaining -= dt

    @property
    def pose(self):
        if max(abs(self.vx), abs(self.vy)) < 45:
            return 0
        if abs(self.vy) > abs(self.vx) * 1.2:
            return 3
        return 1 if self.vx > 0 else 2

    @property
    def settled(self):
        return (abs(self.vx) + abs(self.vy) < 5 and abs(self.angle) < .05 and
                abs(self.angular_velocity) < .05 and abs(self.stretch - 1) < .001)
