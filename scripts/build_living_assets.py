"""Normalize six generated sheets, preserving alpha and original drawings."""
import json
import sys
from pathlib import Path

from build_motion_assets import extract_sprites
from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from character.life import LIFE_SCENES, THEMES  # noqa: E402

SOURCE = PROJECT / "output/life-library-v7"
TARGET = PROJECT / "src/asset_library/motion"


def build():
    sprites = {}
    preview = Image.new("RGB", (840, 6 * 250), "#e7edf4")
    draw = ImageDraw.Draw(preview)
    for row, theme in enumerate(THEMES):
        with Image.open(SOURCE / "sources" / f"{theme}.png") as source:
            source = source.convert("RGBA")
            cells = extract_sprites(source, count=6, columns=3)
        boxes = [cell.getchannel("A").getbbox() for cell in cells]
        if any(box is None for box in boxes):
            raise ValueError(f"Empty sprite cell: {theme}")
        # Shared scale, feet baseline and body-side edge across all six poses.
        scale = min(232 / max(b[2] - b[0] for b in boxes),
                    202 / max(b[3] - b[1] for b in boxes))
        sprites[theme] = []
        for index, (cell, box) in enumerate(zip(cells, boxes)):
            sprite = cell.crop(box)
            sprite = sprite.resize((round(sprite.width * scale), round(sprite.height * scale)),
                                   Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (280, 230))
            canvas.alpha_composite(sprite, (244 - sprite.width, 222 - sprite.height))
            canvas.save(TARGET / f"life-{theme}-{index}.png", optimize=True)
            sprites[theme].append(canvas)
            # Contact sheet is diagnostic only; runtime keeps transparent PNGs.
            small = canvas.resize((140, 115), Image.Resampling.LANCZOS)
            preview.paste(small, (index * 140, row * 250 + 30), small)
        draw.text((12, row * 250 + 8), theme, fill="#193554")
    path = TARGET / "clips.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    (SOURCE / "gifs").mkdir(exist_ok=True)
    for name, scene in LIFE_SCENES.items():
        frames = [f"life-{scene.theme}-{pose}.png" for pose in scene.poses]
        manifest["clips"][name] = {"family": "life-" + scene.theme, "frames": frames,
                                   "durations": list(scene.durations), "loop": False}
        # Preview exports preserve complete clip timing; not used by runtime.
        images = [sprites[scene.theme][pose] for pose in scene.poses]
        images[0].save(SOURCE / "gifs" / f"{name}.gif", save_all=True,
                       append_images=images[1:], duration=list(scene.durations),
                       disposal=2, loop=0)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    preview.save(SOURCE / "poses.png")
    print(f"Exported {sum(map(len, sprites.values()))} transparent poses and {len(LIFE_SCENES)} clips.")


if __name__ == "__main__":
    build()
