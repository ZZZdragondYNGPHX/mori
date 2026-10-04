"""Fit generated artwork to the default sprite canvas without editing drawings.

Usage: python scripts/prepare_character_assets.py
Requires Pillow at build time. Prompts and source paths live in move/prompts.json.
Full-resolution sources and upstream originals stay in output/character-refresh.
"""
import json
from pathlib import Path

from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "src" / "asset_library"


def prepare():
    manifest = json.loads((ROOT / "move" / "prompts.json").read_text(encoding="utf-8"))
    width, height = manifest["canvas_size"]
    inset = manifest["inset"]
    baseline = manifest["baseline"]
    prepared = []
    for asset in manifest["assets"]:
        source = PROJECT / asset["source"]
        with Image.open(source) as image:
            image = image.convert("RGBA")
            alpha = image.getchannel("A")
            if alpha.getextrema()[0] != 0:
                raise ValueError(f"Source has no transparent pixels: {source}")
            bounds = alpha.getbbox()
            if bounds is None:
                raise ValueError(f"Empty sprite: {source}")
            # Retain the generated alpha; only trim empty margins and scale.
            sprite = image.crop(bounds)
            sprite.thumbnail((width - 2 * inset, baseline - inset), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (width, height))
            canvas.alpha_composite(sprite, ((width - sprite.width) // 2, baseline - sprite.height))
            prepared.append((ROOT / asset["file"], canvas))
    # Validate all source artwork before replacing any runtime asset.
    for target, canvas in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target, optimize=True)
        print(f"{target.relative_to(ROOT)}: {width}x{height}")


if __name__ == "__main__":
    prepare()
