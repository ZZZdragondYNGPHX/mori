"""Engine-neutral contracts: no Qt, provider names, or animation SDKs."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

from companion.events import EventType

from .dialogue import EventDialogue


@dataclass(frozen=True)
class CharacterDefinition:
    id: str
    name: str
    animations: Mapping[str, str]
    fallbacks: Mapping[str, str] = field(default_factory=dict)
    renderer: str = "raster"


@dataclass(frozen=True)
class Emotion:
    name: str = "neutral"
    intensity: float = 0.5


@dataclass(frozen=True)
class AnimationState:
    key: str
    loop: bool = False
    duration_ms: int = 3000


class EmotionSystem(Protocol):
    def resolve(self, activity: EventType) -> Emotion: ...


class AnimationStateMachine(Protocol):
    def resolve(self, activity: EventType, emotion: Emotion) -> AnimationState: ...
    def for_interaction(self, key: str) -> AnimationState: ...


class AssetManager(Protocol):
    def resolve(self, animation_key: str) -> Path: ...


class CharacterRenderer(Protocol):
    """Future Live2D/Spine/Rive backends implement this lifecycle contract."""
    def set_animation(self, state: AnimationState) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def set_emotion(self, emotion: Emotion) -> None: ...
    def dispose(self) -> None: ...


@dataclass(frozen=True)
class PetReaction:
    status: str = "idle"
    image: str = "nothing"
    speech: str = ""
    emotion: Emotion = Emotion()
    animation: AnimationState | None = None


@dataclass
class CharacterSystem:
    definition: CharacterDefinition
    emotions: EmotionSystem
    animations: AnimationStateMachine
    assets: AssetManager
    dialogue: EventDialogue = field(default_factory=EventDialogue)

    def react(self, activity: EventType) -> PetReaction:
        activity = EventType(activity)
        emotion = self.emotions.resolve(activity)
        animation = self.animations.resolve(activity, emotion)
        return PetReaction(activity.value, animation.key, self.dialogue.resolve(activity), emotion, animation)
