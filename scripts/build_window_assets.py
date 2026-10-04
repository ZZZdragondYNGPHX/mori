"""Export complete window-interaction poses and shared attachment metadata."""
import json
from pathlib import Path

from build_motion_assets import BASELINE, SIZE, TARGET, export_gif, export_poses
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "output/window-study-v4"
FAMILIES = ("window-peek-right", "window-peek-left", "window-seat", "window-swing", "window-press")
SUPPORT = [175, 172]
CONTACT = [241, 125]
ANCHORS = {
    "window-seat": [[175, 222], [174, 222], [174, 174], [177, 174]],
    "window-swing": [[175, 172], [175, 172], [174, 175], [177, 176]],
}
OFFSETS = {
    "window-peek-right": [[9, 0], [11, 0], [10, 0], [11, 0]],
    "window-peek-left": [[5, 0], [7, 0], [2, 0], [3, 0]],
}


def clip_definitions():
    clips = {}
    for side in ("right", "left"):
        clips["window-peek-" + side] = {
            "family": "window-peek-" + side, "poses": [0, 1, 2, 3, 2, 1, 0],
            "durations": [480, 640, 1280, 1040, 1280, 1200, 1080], "loop": False}
    clips.update({
        "window-seat": {"family": "window-seat", "poses": [0, 1, 2, 3],
                        "durations": [200, 260, 300, 360], "loop": False},
        "window-swing": {"family": "window-swing", "poses": [0, 1, 0, 2, 0, 3, 0],
                         "durations": [400, 360, 240, 360, 240, 180, 380], "loop": True},
        "window-rise": {"family": "window-seat", "poses": [3, 2, 1, 0],
                        "durations": [200, 280, 300, 340], "loop": False},
        "window-consider": {"family": "window-press", "poses": [0, 1, 0],
                            "durations": [240, 420, 340], "loop": False},
        "window-press": {"family": "window-press", "poses": [0, 1, 2],
                         "durations": [320, 1280, 400], "loop": False},
        "window-press-recover": {"family": "window-press", "poses": [2, 3, 0],
                                 "durations": [120, 340, 300], "loop": False},
    })
    return clips


def build():
    (SOURCE / "gifs").mkdir(parents=True, exist_ok=True)
    poses = {family: export_poses(family, SOURCE) for family in FAMILIES}
    for family, anchors in ANCHORS.items():
        for frame, (anchor_x, anchor_y) in zip(poses[family], anchors):
            left, top, right, bottom = frame.getchannel("A").getbbox()
            if not (0 < left < right < SIZE[0] and 0 < top < bottom < SIZE[1] and
                    0 < anchor_x < SIZE[0] and 0 < anchor_y < SIZE[1]):
                raise ValueError(f"Attachment clips a complete pose: {family}")
    clips = clip_definitions()
    for name, clip in clips.items():
        indices = clip["poses"]
        family = clip["family"]
        previews = []
        for index, frame in enumerate(poses[family]):
            dx, dy = OFFSETS.get(family, [[0, 0]] * 4)[index]
            if family == "window-swing":
                ax, ay = ANCHORS[family][index]
                dx, dy = SUPPORT[0] - ax, SUPPORT[1] - ay
            preview_frame = Image.new("RGBA", SIZE)
            preview_frame.alpha_composite(frame, (dx, dy))
            previews.append(preview_frame)
        export_gif(name, clip, previews, SOURCE)
        if family in ANCHORS:
            clip["anchors"] = [ANCHORS[family][index] for index in indices]
        if family in OFFSETS:
            clip["offsets"] = [OFFSETS[family][index] for index in indices]
        clip["frames"] = [f"{family}-{index}.png" for index in clip.pop("poses")]
    path = TARGET / "clips.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["clips"].update(clips)
    manifest["window_support"] = SUPPORT
    manifest["window_button_contact"] = CONTACT
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    walks = {name: clip for name, clip in manifest["clips"].items() if name.startswith("walk-")}
    preview = {"canvas": list(SIZE), "baseline": BASELINE, "clips": {**walks, **clips},
               "support": SUPPORT, "contact": CONTACT,
               "stride": manifest["walk_stride_pixels"], "sway": manifest["walk_sway_degrees"]}
    (PROJECT / "docs/window-preview-data.js").write_text(
        "window.MORI_WINDOWS = " + json.dumps(preview, ensure_ascii=False) + ";\n", encoding="utf-8")
    print("Exported 20 transparent window poses and 8 clips; approved batches retained.")


if __name__ == "__main__":
    build()
