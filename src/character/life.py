"""Authored life scenes and short-lived memories, independent of Qt and tools."""
import time
from dataclasses import dataclass

THEMES = ("food", "dream", "sleep", "curious", "friend", "ocean")
THEME_WEIGHTS = (3, 3, 3, 1, 1, 1)
MEMORY_EVENTS = dict(zip(THEMES, ("ate", "dreamed", "rested", "explored", "touched", "ocean")))
MEMORY_SECONDS = {"ate": 900, "dreamed": 360, "rested": 420, "explored": 480,
                  "touched": 300, "ocean": 480, "interrupted": 120}


class LifeMemory:
    """Only completed actions make facts; remembered events expire naturally."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._events = {}

    def record(self, event):
        if event in MEMORY_SECONDS:
            self._events[event] = self.clock()

    def recent(self, event):
        at = self._events.get(event)
        if at is None:
            return False
        if self.clock() - at >= MEMORY_SECONDS[event]:
            del self._events[event]
            return False
        return True


@dataclass(frozen=True)
class LifeScene:
    id: str
    label: str
    theme: str
    poses: tuple[int, ...]
    durations: tuple[int, ...]
    working: bool = False
    effect: str = ""
    requires: str = ""
    excludes: str = ""

    @property
    def module(self):
        return "rest" if self.theme == "sleep" else "expression"

    @property
    def seconds(self):
        return sum(self.durations) / 1000

    def eligible(self, memory):
        return ((not self.requires or memory.recent(self.requires))
                and (not self.excludes or not memory.recent(self.excludes)))


def _scene(key, label, theme, poses, durations, **options):
    return LifeScene("life-" + key, label, theme, tuple(poses), tuple(durations), **options)


_SCENES = (
    _scene("hungry", "摸摸肚子", "food", (0, 1, 0, 5), (500, 1100, 800, 700), working=True, excludes="ate"),
    _scene("menu", "脑袋里的菜单", "food", (1, 0, 1, 5), (800, 700, 1400, 600), working=True, excludes="ate"),
    _scene("resist", "先忍住嘴馋", "food", (0, 1, 5), (1200, 1400, 800), working=True, excludes="ate"),
    _scene("snack", "偷偷吃饭团", "food", (2, 3, 3, 4, 5), (1600, 1900, 1700, 2600, 1200), effect="ate", excludes="ate"),
    _scene("share", "珍惜最后一口", "food", (2, 3, 4, 3, 4, 5), (1500, 1500, 1700, 1600, 2200, 1300), effect="ate", excludes="ate"),
    _scene("crumbs", "收拾小碎屑", "food", (4, 5, 5), (1800, 2000, 1200), requires="ate"),
    _scene("blank", "原地放空", "dream", (0, 2, 0, 5), (700, 1400, 600, 800), working=True, effect="dreamed"),
    _scene("cloud", "想象一朵小云", "dream", (1, 3, 5), (900, 1800, 800), working=True, effect="dreamed"),
    _scene("remember", "突然想起来了", "dream", (0, 4, 5), (1000, 1400, 900), working=True, effect="dreamed"),
    _scene("daydream", "念头去散步", "dream", (0, 2, 3, 4, 5), (1400, 2600, 3600, 1800, 1600), effect="dreamed"),
    _scene("idea", "奇妙的小计划", "dream", (1, 4, 3, 5), (2100, 2200, 3000, 1500), effect="dreamed"),
    _scene("forgot", "想起了又忘了", "dream", (4, 0, 2, 5), (1800, 2000, 2400, 1400), effect="dreamed"),
    _scene("yawn", "遮住小哈欠", "sleep", (0, 1, 5), (700, 1900, 800), working=True, excludes="rested"),
    _scene("nod", "差点点头睡着", "sleep", (0, 2, 3, 5), (700, 1200, 800, 900), working=True, excludes="rested"),
    _scene("rub", "揉揉眼睛", "sleep", (0, 4, 5), (700, 1800, 1000), working=True, excludes="rested"),
    _scene("doze", "一小段浅眠", "sleep", (0, 1, 2, 2, 3, 4, 5), (1200, 1600, 7000, 7000, 800, 2000, 1800), effect="rested", excludes="rested"),
    _scene("rest", "静静歇一会儿", "sleep", (0, 2, 2, 4, 5), (1600, 4000, 5000, 2000, 1800), effect="rested", excludes="rested"),
    _scene("awake", "醒来迷糊一会儿", "sleep", (3, 4, 5), (1200, 2300, 1800), requires="rested"),
    _scene("look", "小小张望", "curious", (0, 1, 5), (800, 1700, 900), working=True),
    _scene("listen", "听听旁边", "curious", (0, 2, 0), (1000, 1600, 900), working=True),
    _scene("tiptoe", "踮脚看一眼", "curious", (0, 2, 5), (700, 1700, 900), working=True),
    _scene("arrow", "试探小箭头", "curious", (0, 1, 3, 4, 5), (1300, 1600, 2100, 1300, 1800), effect="explored"),
    _scene("investigate", "认真小侦察", "curious", (1, 2, 0, 5), (2100, 2200, 2300, 1800), effect="explored"),
    _scene("discover", "发现小秘密", "curious", (0, 1, 5, 5), (1700, 2300, 2300, 1200), effect="explored"),
    _scene("wave", "轻轻打招呼", "friend", (0, 5), (1700, 1200), working=True),
    _scene("wait", "偷偷等你一眼", "friend", (1, 2, 5), (1100, 1500, 900), working=True),
    _scene("giggle", "偷笑一下", "friend", (4, 5), (1900, 1200), working=True),
    _scene("pout", "假装有点生气", "friend", (0, 3, 4, 5), (1000, 2600, 2100, 1700)),
    _scene("pat", "把脑袋递过来", "friend", (1, 2, 1, 5), (2200, 2000, 2400, 1500)),
    _scene("tidy", "整理围裙站好", "friend", (2, 5, 5), (1800, 2400, 1600)),
    _scene("tail", "照顾一下尾巴", "ocean", (0, 5), (1900, 1200), working=True),
    _scene("hum", "小声哼海风", "ocean", (4, 5), (2100, 1100), working=True, effect="ocean"),
    _scene("shell-listen", "听一小阵浪声", "ocean", (1, 5), (2200, 1200), working=True, effect="ocean"),
    _scene("shell", "擦擦小贝壳", "ocean", (1, 2, 5), (2400, 3100, 1800), effect="ocean"),
    _scene("proud-tail", "尾巴的小得意", "ocean", (0, 3, 5), (1900, 2700, 1800)),
    _scene("sea", "回一趟小海湾", "ocean", (1, 4, 2, 5), (2400, 3300, 2400, 1900), effect="ocean"),
)
LIFE_SCENES = {scene.id: scene for scene in _SCENES}
