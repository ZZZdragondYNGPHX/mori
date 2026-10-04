"""Ambient thoughts are independent of poses; only explicit cues bind speech."""
import random
from collections import deque
from functools import lru_cache
from importlib.resources import files

from companion.events import EventType

from .life import MEMORY_EVENTS, THEME_WEIGHTS, THEMES, LifeMemory


def _read_groups(relative):
    text = files("asset_library").joinpath(relative).read_text(encoding="utf-8")
    groups = {}
    current = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            if not current or current in groups:
                raise ValueError(f"{relative}:{number}: empty or duplicate dialogue group")
            groups[current] = []
        elif current is None:
            raise ValueError(f"{relative}:{number}: dialogue needs a group")
        else:
            groups[current].append(line)
    if any(not lines for lines in groups.values()):
        raise ValueError(f"{relative}: empty dialogue pool")
    return {key: tuple(lines) for key, lines in groups.items()}


@lru_cache(maxsize=1)
def fixed_dialogue_groups():
    """Fixed cues, templates and legacy skin pools, with no Qt dependency."""
    groups = _read_groups("talk/fixed.txt")
    for name, filename in (("idle", "nothing"), ("touch", "touch")):
        text = files("asset_library").joinpath(f"talk/{filename}.txt").read_text(encoding="utf-8")
        groups["legacy." + name] = tuple(line.strip() for line in text.splitlines() if line.strip())
    return groups


def fixed_line(name):
    """Resolve an authored fixed cue; a missing key is an authoring error."""
    return fixed_dialogue_groups()["fixed." + name][0]


def template_line(name):
    """Return a dynamic display template, which must be filled before speech."""
    return fixed_dialogue_groups()["template." + name][0]


IDLE_ANIMATION_SPEECH = {
    "eat": fixed_line("animation.eat"),
    "think": fixed_line("animation.think"),
}

AMBIENT_THOUGHTS = tuple(fixed_line("ambient." + name) for name in ("food", "dream", "menu"))
WORK_THOUGHTS = tuple(fixed_line("work." + name) for name in ("food", "menu", "dream"))
AMBIENT_ACTIVITIES = frozenset((
    EventType.IDLE, EventType.TASK_STARTED, EventType.THINKING, EventType.GENERATING,
))


class AmbientDialogue:
    """Choose thoughts by activity, without reading or changing an animation."""

    def __init__(self, rng=None, memory=None):
        self.rng = rng if rng is not None else random.Random()
        self.memory = memory if memory is not None else LifeMemory()
        self._last_line = ""
        self._recent = deque(maxlen=32)

    def line(self, group):
        return self._pick(dialogue_groups().get(group, ()))

    def _pick(self, pool):
        if not pool:
            return ""
        fresh = tuple(line for line in pool if line not in self._recent)
        choices = fresh or tuple(line for line in pool if line != self._last_line) or tuple(pool)
        self._last_line = self.rng.choice(choices)
        self._recent.append(self._last_line)
        return self._last_line

    def choose(self, activity, idle_lines=()):
        activity = EventType(activity)
        if activity not in AMBIENT_ACTIVITIES:
            return ""
        if self.memory.recent("interrupted") and self.rng.random() < .25:
            return self.line("interrupted")
        theme = self.rng.choices(THEMES, weights=THEME_WEIGHTS, k=1)[0]
        if self.memory.recent(MEMORY_EVENTS[theme]):
            return self.line(theme + ".memory")
        context = "idle" if activity == EventType.IDLE else "work"
        groups = dialogue_groups()
        pool = groups[theme + ".common"] + groups[theme + "." + context]
        if activity == EventType.IDLE and idle_lines and self.rng.random() < .125:
            pool = tuple(line for line in idle_lines if line != self._last_line) or pool
        return self._pick(pool)


@lru_cache(maxsize=1)
def dialogue_groups():
    """Read packaged, hand-authored text once; preserve its editable grouping."""
    return _read_groups("talk/life.txt")


class EventDialogue:
    """Keep speech stable during repeated status refreshes; vary new entries."""

    def __init__(self):
        self.dialogue = AmbientDialogue()
        self._activity = None
        self._line = ""

    def resolve(self, activity):
        activity = EventType(activity)
        if activity != self._activity:
            self._activity = activity
            self._line = self.dialogue.line("event." + activity.value)
        return self._line
