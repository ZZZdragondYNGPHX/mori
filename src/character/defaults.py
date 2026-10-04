"""Default character assets with generic emotion and animation policies."""
from pathlib import Path

from .animation import ActivityAnimationStateMachine
from .contracts import CharacterDefinition, CharacterSystem
from .emotions import ActivityEmotionSystem

PNG_ANIMATIONS = {
    "nothing": "move/idle/nothing/nothing.png",
    "eat": "move/idle/eat/eat.png",
    "think": "move/idle/think/think.png",
    "pickup": "move/PickUp/pickup.png",
    "touch": "move/touch/touch.png",
    "attention": "move/attention/attention.png",
    "cheerup": "move/cheerup/cheerup.png",
    "true": "move/file/true/true.png",
    "wrong": "move/file/wrong/wrong.png",
}
GIF_ANIMATIONS = {key: path.removesuffix(".png") + ".gif"
                  for key, path in PNG_ANIMATIONS.items()}

# Compatibility names for third-party skins built against 0.1.
BasicEmotionSystem = ActivityEmotionSystem
PngAnimationStateMachine = ActivityAnimationStateMachine


class PngAssetManager:
    def __init__(self, root, definition):
        self.root = Path(root).resolve()
        self.definition = definition

    def resolve_path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Skin assets must stay inside the asset root")
        return path

    def resolve(self, animation_key):
        relative = self.definition.animations.get(animation_key)
        if relative is None:
            raise ValueError("Unknown animation state")
        path = self.resolve_path(relative)
        fallback = self.definition.fallbacks.get(animation_key)
        if not path.is_file() and fallback:
            return self.resolve_path(fallback)
        return path


def make_character(root, definition):
    return CharacterSystem(definition, ActivityEmotionSystem(), ActivityAnimationStateMachine(),
                           PngAssetManager(root, definition))


def default_character(asset_root):
    definition = CharacterDefinition("whale-girl", "鲸鱼娘", dict(GIF_ANIMATIONS),
                                     dict(PNG_ANIMATIONS))
    return make_character(asset_root, definition)
