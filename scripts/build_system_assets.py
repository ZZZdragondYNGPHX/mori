"""Mechanical atlas export; preserve generated RGBA and the approved prior poses."""
import json
from pathlib import Path

from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "output/system-study-v6/sources/system-poses-v2.png"
TARGET = PROJECT / "src/asset_library/motion"
# Source-cell hand / nib coordinates, checked against the transparent atlas.
ANCHORS = {
    "system-pen": [(326, 254), (435, 280), (402, 291), (375, 210)],
    "system-grip": [(382, 217), (366, 246), (361, 249), (378, 219)],
}


def build():
    sheet = Image.open(SOURCE).convert("RGBA")
    cells = []
    for row in range(2):
        for column in range(4):
            cells.append(sheet.crop((round(column * sheet.width / 4), round(row * sheet.height / 2),
                                     round((column + 1) * sheet.width / 4), round((row + 1) * sheet.height / 2))))
    boxes = [cell.getchannel("A").point(lambda alpha: 255 if alpha >= 32 else 0).getbbox() for cell in cells]
    if any(not box or box[0] <= 0 or box[1] <= 0 or box[2] >= cell.width or box[3] >= cell.height
           for cell, box in zip(cells, boxes)):
        raise ValueError("Atlas cell clips a character; correct the source artwork")
    scale = min(232 / max(b[2] - b[0] for b in boxes), 202 / max(b[3] - b[1] for b in boxes))
    manifest_path = TARGET / "clips.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row, family in enumerate(ANCHORS):
        anchors = []
        for index, raw_anchor in enumerate(ANCHORS[family]):
            cell, box = cells[row * 4 + index], boxes[row * 4 + index]
            sprite = cell.crop(box)
            sprite = sprite.resize((round(sprite.width * scale), round(sprite.height * scale)), Image.Resampling.LANCZOS)
            origin = (244 - sprite.width, 222 - sprite.height)
            canvas = Image.new("RGBA", (280, 230))
            canvas.alpha_composite(sprite, origin)
            canvas.save(TARGET / f"{family}-{index}.png", optimize=True)
            anchors.append([round(origin[0] + (raw_anchor[0] - box[0]) * scale),
                            round(origin[1] + (raw_anchor[1] - box[1]) * scale)])
        manifest["clips"][family] = dict(family=family, frames=[f"{family}-{i}.png" for i in range(4)],
                                         durations=[300, 300, 300, 300], loop=False, anchors=anchors)
    reach = Image.open(SOURCE.parent / "system-reach.png").convert("RGBA")
    cells = [reach.crop((round(i * reach.width / 4), 0, round((i + 1) * reach.width / 4), reach.height)) for i in range(4)]
    boxes = [cell.getchannel("A").point(lambda alpha: 255 if alpha >= 32 else 0).getbbox() for cell in cells]
    if any(not box or box[0] <= 0 or box[1] <= 0 or box[2] >= cell.width or box[3] >= cell.height
           for cell, box in zip(cells, boxes)):
        raise ValueError("Reach atlas clips a character")
    scale = min(232 / max(b[2] - b[0] for b in boxes), 214 / max(b[3] - b[1] for b in boxes))
    anchors = []
    for i, (cell, box, raw_anchor) in enumerate(zip(cells, boxes, [(410, 361), (484, 243), (482, 59), (415, 360)])):
        sprite = cell.crop(box)
        sprite = sprite.resize((round(sprite.width * scale), round(sprite.height * scale)), Image.Resampling.LANCZOS)
        origin = (244 - sprite.width, 222 - sprite.height)
        canvas = Image.new("RGBA", (280, 230))
        canvas.alpha_composite(sprite, origin)
        canvas.save(TARGET / f"system-reach-{i}.png", optimize=True)
        anchors.append([round(origin[0] + (raw_anchor[0] - box[0]) * scale),
                        round(origin[1] + (raw_anchor[1] - box[1]) * scale)])
    manifest["clips"]["system-reach"] = dict(family="system-reach", frames=[f"system-reach-{i}.png" for i in range(4)],
                                             durations=[300, 300, 300, 300], loop=False, anchors=anchors)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Exported twelve complete system poses; prior artwork retained.")


if __name__ == "__main__":
    build()
