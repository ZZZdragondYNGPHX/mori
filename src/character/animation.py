"""Only generic activities and character data decide animation states."""
from companion.events import EventType

from .contracts import AnimationState
from .timing import ONE_SHOT_MS

ACTIVITY_ANIMATIONS = {
    EventType.TASK_STARTED: "think", EventType.THINKING: "think",
    EventType.GENERATING: "eat", EventType.WAITING_INPUT: "attention",
    EventType.TASK_COMPLETED: "cheerup", EventType.TASK_FAILED: "wrong",
    EventType.IDLE: "nothing",
}
ANIMATION_KEYS = ("nothing", "eat", "think", "attention", "cheerup",
                  "wrong", "touch", "pickup", "true")


class ActivityAnimationStateMachine:
    def resolve(self, activity, emotion):
        activity = EventType(activity)
        key = ACTIVITY_ANIMATIONS[activity]
        return AnimationState(key, duration_ms=ONE_SHOT_MS.get(key, 3000), loop=activity not in (
            EventType.TASK_COMPLETED, EventType.TASK_FAILED))

    def for_interaction(self, key):
        return AnimationState(key, duration_ms=ONE_SHOT_MS.get(key, 3000),
                              loop=key in ("nothing", "think", "eat", "attention", "pickup"))
