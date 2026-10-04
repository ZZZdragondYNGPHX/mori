"""Export pointer key poses and a file-friendly local preview manifest."""
import json
from pathlib import Path

from build_motion_assets import BASELINE, SIZE, TARGET, export_gif, export_poses

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "output/mouse-study-v3"
FAMILIES = ("mouse-look", "mouse-curious", "mouse-shy", "mouse-curious-left", "mouse-shy-left")


def clip_definitions():
    clips = {
        "mouse-look": {"family": "mouse-look", "poses": [0, 1, 2, 3],
                       "durations": [600] * 4, "loop": True},
        "mouse-curious": {"family": "mouse-curious", "poses": [0, 1, 2, 3],
                          "durations": [160, 320, 520, 360], "loop": False},
        "mouse-shy": {"family": "mouse-shy", "poses": [0, 1, 2, 3],
                      "durations": [120, 260, 520, 360], "loop": False},
        "mouse-settle": {"family": "mouse-curious", "poses": [3, 0],
                         "durations": [600, 500], "loop": False},
    }
    for name in ("mouse-curious", "mouse-shy", "mouse-settle"):
        clip = clips[name]
        clips[name + "-left"] = {**clip, "family": clip["family"] + "-left"}
    return clips


def build():
    (SOURCE / "gifs").mkdir(parents=True, exist_ok=True)
    poses = {family: export_poses(family, SOURCE) for family in FAMILIES}
    clips = clip_definitions()
    for name, clip in clips.items():
        export_gif(name, clip, poses[clip["family"]], SOURCE)
        clip["frames"] = [f"{clip['family']}-{index}.png" for index in clip.pop("poses")]
    path = TARGET / "clips.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["clips"].update(clips)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    walks = {name: clip for name, clip in manifest["clips"].items() if name.startswith("walk-")}
    preview = {"canvas": list(SIZE), "baseline": BASELINE, "clips": {**walks, **clips},
               "stride": manifest["walk_stride_pixels"], "sway": manifest["walk_sway_degrees"]}
    (PROJECT / "docs/mouse-preview-data.js").write_text(
        "window.MORI_MOUSE = " + json.dumps(preview, ensure_ascii=False) + ";\n", encoding="utf-8")
    print("Exported 20 transparent pointer poses and 7 clips; approved batches retained.")


if __name__ == "__main__":
    build()
