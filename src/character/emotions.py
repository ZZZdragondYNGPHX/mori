"""Activity-driven emotions with a short, deterministic return to neutral."""
import time

from companion.events import EventType

from .contracts import Emotion


class ActivityEmotionSystem:
    TARGETS = {
        EventType.TASK_STARTED: Emotion("focused", 0.6),
        EventType.THINKING: Emotion("curious", 0.55),
        EventType.GENERATING: Emotion("focused", 0.8),
        EventType.WAITING_INPUT: Emotion("expectant", 0.85),
        EventType.TASK_COMPLETED: Emotion("happy", 1.0),
        EventType.TASK_FAILED: Emotion("concerned", 0.9),
    }

    def __init__(self, clock=time.monotonic, recovery_seconds=6):
        self.clock = clock
        self.recovery_seconds = max(1, recovery_seconds)
        self._activity = EventType.IDLE
        self._emotion = Emotion("neutral", 0.0)
        self._idle_at = clock()

    def resolve(self, activity):
        activity = EventType(activity)
        now = self.clock()
        if activity != self._activity:
            self._activity = activity
            if activity == EventType.IDLE:
                self._idle_at = now
            else:
                self._emotion = self.TARGETS[activity]
        if activity != EventType.IDLE:
            return self._emotion
        intensity = round(self._emotion.intensity * max(
            0, 1 - (now - self._idle_at) / self.recovery_seconds), 2)
        return Emotion(self._emotion.name, intensity) if intensity > 0 else Emotion("neutral", 0.0)
