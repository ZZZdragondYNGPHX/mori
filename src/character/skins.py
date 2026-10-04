"""Local image resources and skin manifests; independent of Qt and agents."""
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from companion.transport import atomic_json

from .animation import ANIMATION_KEYS
from .contracts import CharacterDefinition
from .defaults import make_character

MAX_ASSET_BYTES = 2 * 1024 * 1024
MAX_SKINS = 32


def load_definition(root):
    root = Path(root).resolve()
    manifest = root / "character.json"
    if manifest.stat().st_size > 65536:
        raise ValueError("角色配置不能超过 64 KiB")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("角色配置需要 schema_version: 1")
    identity, name = data.get("id"), data.get("name")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
        raise ValueError("角色 id 无效")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("角色名称长度应为 1–80 字")
    animations = data.get("animations")
    if not isinstance(animations, dict) or set(animations) != set(ANIMATION_KEYS):
        raise ValueError("角色配置必须包含九种动作")
    if data.get("renderer", "raster") != "raster":
        raise ValueError("此资源导入器目前只支持 PNG / GIF；动画 SDK 使用独立后端接口")
    for relative in animations.values():
        if not isinstance(relative, str):
            raise ValueError("动作素材路径无效")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("素材必须位于角色目录内且存在")
        if path.suffix.lower() not in (".png", ".gif") or path.stat().st_size > MAX_ASSET_BYTES:
            raise ValueError("每个素材必须是 2 MiB 以内的 PNG / GIF")
    return CharacterDefinition(identity, name.strip(), dict(animations))


class SkinRepository:
    def __init__(self, data_root):
        self.data_root = Path(data_root)
        self.root = self.data_root / "characters"
        self.root.mkdir(parents=True, exist_ok=True)
        self.selection = self.data_root / "character-selection.json"

    def list(self):
        result = []
        for path in sorted(self.root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                try:
                    result.append(load_definition(path))
                except (OSError, ValueError, TypeError):
                    continue
        return result

    def get(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
            raise ValueError("角色 id 无效")
        root = (self.root / identity).resolve()
        if not root.is_relative_to(self.root.resolve()):
            raise ValueError("角色目录无效")
        return make_character(root, load_definition(root))

    def selected(self, default):
        if not self.selection.exists():
            return default
        try:
            identity = json.loads(self.selection.read_text(encoding="utf-8"))["id"]
            return default if identity == default.definition.id else self.get(identity)
        except (OSError, ValueError, TypeError, KeyError):
            return default

    def select(self, identity):
        atomic_json(self.selection, {"id": identity})

    def install(self, name, files, validate):
        """Copy a validated snapshot. Existing skins and source files stay intact."""
        if len(list(self.root.iterdir())) >= MAX_SKINS:
            raise ValueError("最多保存 32 个皮肤，请先移除不用的皮肤")
        if set(files) != set(ANIMATION_KEYS):
            raise ValueError("需要完整九种动作")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("角色名称长度应为 1–80 字")
        identity = "skin-" + uuid.uuid4().hex[:16]
        with tempfile.TemporaryDirectory(prefix=".import-", dir=self.root) as temporary:
            root = Path(temporary)
            animations = {}
            for key, source in files.items():
                source = Path(source)
                if source.suffix.lower() not in (".gif", ".png") or source.stat().st_size > MAX_ASSET_BYTES:
                    raise ValueError("素材必须是 2 MiB 以内的 PNG / GIF")
                relative = key + source.suffix.lower()
                target = root / relative
                shutil.copyfile(source, target)
                validate(target)
                animations[key] = relative
            atomic_json(root / "character.json", {
                "schema_version": 1, "id": identity, "name": name.strip(),
                "renderer": "raster", "animations": animations,
            })
            root.rename(self.root / identity)
        return self.get(identity)

    def import_manifest(self, manifest, validate):
        root = Path(manifest).resolve().parent
        definition = load_definition(root)
        return self.install(definition.name, {key: root / path
                            for key, path in definition.animations.items()}, validate)

    def remove(self, identity):
        character = self.get(identity)
        shutil.rmtree(character.assets.root)
