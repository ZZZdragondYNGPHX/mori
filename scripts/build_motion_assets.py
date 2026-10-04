"""Export generated key poses, without repainting or warping the artwork.

Build-time Pillow only. Sources/prompts: output/motion-study-v1.
Runtime consumes transparent PNGs; GIFs are portable preview exports.
"""
import json
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "output/motion-study-v1"
TARGET = PROJECT / "src/asset_library/motion"
SIZE = (280, 230)
BASELINE = 222
FAMILIES = ("walk-left", "walk-right", "walk-up", "walk-down", "head", "tail")


def clip_definitions():
    clips = {}
    for direction in ("left", "right", "up", "down"):
        name = "walk-" + direction
        clips[name] = {"family": name, "poses": [0, 1, 2, 3],
                       "durations": [180, 180, 180, 180], "loop": True}
    for family in ("head", "tail"):
        for level in (1, 2, 3):
            # Each level reaches a distinct hand/face/tail pose. Variants change
            # the anticipation and recovery, rather than simply playback speed.
            sequences = {
                1: ([0, 1, 0], [0, 1, 0, 1, 0]),
                2: ([0, 1, 2, 0], [0, 1, 2, 1, 0]),
                3: ([0, 1, 2, 3, 2, 0], [0, 1, 3, 2, 3, 0]),
            }
            for variant, poses in enumerate(sequences[level]):
                durations = [160] + [300] * (len(poses) - 2) + [280]
                durations[-2] = 500  # Let the main reaction read before release.
                clips[f"{family}-{level}-{variant}"] = {
                    "family": family, "poses": poses, "durations": durations, "loop": False}
    return clips


def export_poses(family, source_root=SOURCE, target_root=TARGET):
    with Image.open(source_root / "sources" / f"{family}.png") as source:
        sheet = source.convert("RGBA")
    # Generated sheets need not honor the mathematical cell edges. Extract
    # each complete connected drawing instead of cutting a tail/hair at 50%.
    cells = extract_sprites(sheet)
    opposite_path = source_root / "sources" / f"{family}-opposite.png"
    if family in ("walk-left", "walk-right") and opposite_path.is_file():
        # A separately drawn alternate contact makes the opposite arm swing
        # readable. Normalize it to the sheet's contact height before fitting.
        with Image.open(opposite_path) as image:
            opposite = image.convert("RGBA")
        box = opposite.getchannel("A").point(lambda a: 255 if a >= 32 else 0).getbbox()
        if box is None:
            raise ValueError(f"Empty alternate contact: {family}")
        opposite = opposite.crop(box)
        ratio = cells[2].height / opposite.height
        cells[2] = opposite.resize((round(opposite.width * ratio), cells[2].height),
                                   Image.Resampling.LANCZOS)
    # One scale per family, never resize individual poses independently.
    max_w = max(cell.width for cell in cells)
    max_h = max(cell.height for cell in cells)
    scale = min(232 / max_w, 202 / max_h)
    canvases = []
    for index, cell in enumerate(cells):
        # Trim transparent padding only; preserve the generated alpha exactly.
        sprite = cell.crop(cell.getchannel("A").getbbox())
        sprite = sprite.resize((round(sprite.width * scale), round(sprite.height * scale)),
                               Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", SIZE)
        # Feet share a baseline. Keep a fixed body-side edge while the tail
        # changes silhouette, avoiding sideways jumps caused by bbox centering.
        x = 36 if family == "walk-left" else 244 - sprite.width
        baseline = BASELINE - 8 if family == "pickup" else BASELINE
        canvas.alpha_composite(sprite, (x, baseline - sprite.height))
        canvas.save(target_root / f"{family}-{index}.png", optimize=True)
        canvases.append(canvas)
    return canvases


def extract_sprites(sheet, count=4, columns=2):
    """Scanline connected components; retain the original RGBA of each pose."""
    width, height = sheet.size
    original_alpha = sheet.getchannel("A")
    # Ignore near-transparent generated speckles when identifying characters;
    # otherwise a faint bridge can join two entirely separate drawings.
    alpha = original_alpha.point(lambda value: 255 if value >= 32 else 0).tobytes()
    if 0 not in alpha:
        raise ValueError("Sprite sheet needs true transparency")
    parents, runs = [], []

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    previous = []
    for y in range(height):
        current = []
        for match in re.finditer(rb"[^\x00]+", alpha[y * width:(y + 1) * width]):
            left, right = match.span()
            index = len(parents)
            parents.append(index)
            runs.append((y, left, right, index))
            current.append((left, right, index))
            for old_left, old_right, old_index in previous:
                if old_left > right:
                    break
                if old_right >= left:
                    parents[root(index)] = root(old_index)
        previous = current
    groups = {}
    for y, left, right, index in runs:
        groups.setdefault(root(index), []).append((y, left, right))
    ordered = sorted(groups.values(), key=lambda group: sum(right - left for _, left, right in group),
                     reverse=True)
    if len(ordered) < count or sum(right - left for _, left, right in ordered[count - 1]) < width * height * .12 / count:
        raise ValueError(f"Expected {count} separate complete character drawings")
    if len(ordered) > count and sum(right - left for _, left, right in ordered[count]) > width * height * .01:
        raise ValueError("A large detached body part needs artwork correction")
    sprites = []
    for group in ordered[:count]:
        left, right = min(start for _, start, _ in group), max(end for _, _, end in group)
        top, bottom = min(y for y, _, _ in group), max(y for y, _, _ in group) + 1
        left, right = max(0, left - 4), min(width, right + 4)
        top, bottom = max(0, top - 4), min(height, bottom + 4)
        box = left, top, right, bottom
        sprite = sheet.crop(box)
        mask = bytearray(sprite.width * sprite.height)
        for y, start, end in group:
            offset = (y - top) * sprite.width + start - left
            mask[offset:offset + end - start] = bytes([255]) * (end - start)
        silhouette = Image.frombytes("L", sprite.size, bytes(mask)).filter(ImageFilter.MaxFilter(9))
        # Retain source alpha around each full silhouette, including its soft
        # edges; discard neighboring drawings and disconnected faint speckles.
        sprite.putalpha(ImageChops.multiply(original_alpha.crop(box), silhouette))
        sprites.append((box, sprite))
    # Sort by row first, then column, regardless of small pose height changes.
    sprites.sort(key=lambda item: (item[0][1] + item[0][3]) / 2)
    result = []
    for start in range(0, count, columns):
        row = sprites[start:start + columns]
        result.extend(sprite for _, sprite in sorted(row, key=lambda item: item[0][0]))
    return result


def export_gif(name, clip, poses, source_root=SOURCE):
    frames = [poses[index] for index in clip["poses"]]
    strip = Image.new("RGB", (SIZE[0] * len(frames), SIZE[1]), (30, 40, 65))
    for index, frame in enumerate(frames):
        strip.paste(frame.convert("RGB"), (SIZE[0] * index, 0))
    palette = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette.putpalette(palette.getpalette()[:765] + [0, 0, 0])
    encoded = []
    for frame in frames:
        indexed = frame.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)
        indexed.paste(255, mask=frame.getchannel("A").point(lambda a: 255 if a < 128 else 0))
        encoded.append(indexed)
    # Repeated playback is for comparison in a preview; runtime reactions end.
    encoded[0].save(source_root / "gifs" / f"{name}.gif", save_all=True,
                    append_images=encoded[1:], duration=clip["durations"],
                    loop=0, transparency=255, disposal=2, optimize=False)


def build():
    TARGET.mkdir(parents=True, exist_ok=True)
    (SOURCE / "gifs").mkdir(parents=True, exist_ok=True)
    poses = {family: export_poses(family) for family in FAMILIES}
    clips = clip_definitions()
    for name, clip in clips.items():
        export_gif(name, clip, poses[clip["family"]])
        clip["frames"] = [f"{clip['family']}-{index}.png" for index in clip.pop("poses")]
    manifest = {"schema_version": 1, "canvas": list(SIZE), "baseline": BASELINE,
                "walk_stride_pixels": 56, "walk_sway_degrees": .7, "clips": clips}
    target_manifest = dict(manifest)
    target = TARGET / "clips.json"
    if target.is_file():
        existing = json.loads(target.read_text(encoding="utf-8"))
        retained = {key: clip for key, clip in existing["clips"].items()
                    if clip["family"] not in FAMILIES}
        target_manifest["clips"] = {**retained, **clips}
    target.write_text(json.dumps(target_manifest, ensure_ascii=False, indent=2) + "\n")
    (PROJECT / "docs/motion-preview-data.js").write_text(
        "window.MORI_MOTION = " + json.dumps(manifest, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"Exported {len(FAMILIES) * 4} transparent poses and {len(clips)} clips")


if __name__ == "__main__":
    build()
