"""Persistent interaction preferences, independent of Qt and window backends."""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import ensure_state_dir
from .transport import atomic_json

MODULES = {
    "basic": ("基本动画", "呼吸眨眼、工作表情、主动触摸、拖拽落地"),
    "movement": ("移动", "散步、小跑、快跑；到达目标后收步"),
    "cursor": ("鼠标互动", "附近注视、好奇、害羞；追逐同时需要移动模块"),
    "window": ("窗口互动", "左右探头、上沿偷看、坐下晃腿、伸手、后台问候"),
    "rest": ("休息", "哈欠、揉眼与打盹；工作时只播几秒短段"),
    "expression": ("主动表达", "日常念头、生活剧情与工作中的短暂小动作"),
    "system": ("系统互动", "统一开启窗口拖动／按钮、页面滚动、临时涂鸦与窗口标记"),
}
OPTIONAL_MODULES = tuple(key for key in MODULES if key != "basic")
MODES = ("focus", "entertainment")


@dataclass
class InteractionPreferences:
    mode: str = "focus"
    walking: bool = True
    window_play: bool = True
    cursor_play: bool = True
    dialogue: bool = True
    minimize_pranks: bool = False  # Legacy migration only; module_enabled("system") is the sole switch.
    interval_seconds: int = 45
    minimize_interval_seconds: int = 180
    top_weight: int = 8
    foreground_weight: int = 3
    background_weight: int = 1
    excluded_apps: list[str] = field(default_factory=list)
    module_profiles: dict[str, dict[str, bool]] = field(default_factory=dict)
    movement_pace: str = "auto"
    rest_seconds: int = 40
    bubble_opacity: int = 95
    life_idle_seconds: int = 60
    life_work_seconds: int = 120

    @property
    def entertaining(self):
        return self.mode == "entertainment"

    def module_enabled(self, name, mode=None):
        if name == "basic":
            return True
        if name not in OPTIONAL_MODULES:
            return False
        mode = self.mode if mode is None else mode
        override = self.module_profiles.get(mode, {}).get(name)
        if isinstance(override, bool):
            return override
        if mode != "entertainment":
            return False
        legacy = {"movement": self.walking, "cursor": self.cursor_play,
                  "window": self.window_play, "system": self.minimize_pranks}
        return legacy.get(name, True)

    @property
    def proactive_dialogue(self):
        return self.module_enabled("expression") and self.dialogue

    @property
    def desktop_enabled(self):
        return self.module_enabled("window") or self.module_enabled("system")

    @property
    def active_modules(self):
        return tuple(name for name in OPTIONAL_MODULES if self.module_enabled(name))

    def with_module(self, name, enabled, mode=None):
        from dataclasses import replace
        mode = self.mode if mode is None else mode
        if name not in OPTIONAL_MODULES or mode not in MODES:
            return self
        profiles = {key: dict(values) for key, values in self.module_profiles.items()}
        profiles.setdefault(mode, {})[name] = bool(enabled)
        return replace(self, module_profiles=profiles)

    def excludes(self, application):
        name = application.casefold()
        return any(item.casefold() in name for item in self.excluded_apps if item.strip())

    @classmethod
    def from_dict(cls, data):
        result = cls()
        if not isinstance(data, dict):
            return result
        if data.get("mode") in ("focus", "entertainment"):
            result.mode = data["mode"]
        for key in ("walking", "window_play", "cursor_play", "dialogue", "minimize_pranks"):
            if isinstance(data.get(key), bool):
                setattr(result, key, data[key])
        for key, minimum, maximum in (("interval_seconds", 5, 300),
                                      ("minimize_interval_seconds", 60, 3600),
                                      ("rest_seconds", 10, 300),
                                      ("bubble_opacity", 20, 100),
                                      ("life_idle_seconds", 30, 300),
                                      ("life_work_seconds", 60, 600),
                                      ("top_weight", 0, 100), ("foreground_weight", 0, 100),
                                      ("background_weight", 0, 100)):
            value = data.get(key)
            if type(value) is int:
                setattr(result, key, max(minimum, min(maximum, value)))
        if data.get("movement_pace") in ("auto", "walk", "trot", "run"):
            result.movement_pace = data["movement_pace"]
        profiles = data.get("module_profiles", {})
        if isinstance(profiles, dict):
            for mode in MODES:
                values = profiles.get(mode)
                if isinstance(values, dict):
                    result.module_profiles[mode] = {name: values[name] for name in OPTIONAL_MODULES
                                                   if isinstance(values.get(name), bool)}
        apps = data.get("excluded_apps", [])
        if isinstance(apps, list):
            result.excluded_apps = [item.strip()[:160] for item in apps[:64]
                                    if isinstance(item, str) and item.strip()]
        return result


class PreferenceStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else ensure_state_dir() / "settings.json"

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def load(self):
        return InteractionPreferences.from_dict(self._read().get("interactions"))

    def save(self, preferences):
        data = self._read()
        data["interactions"] = asdict(preferences)
        data["interactions"].pop("minimize_pranks", None)
        # Materialize both profiles before retiring the legacy permission flag.
        for mode in MODES:
            data["interactions"]["module_profiles"].setdefault(mode, {})["system"] = preferences.module_enabled("system", mode)
        atomic_json(self.path, data)
