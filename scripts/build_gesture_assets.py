"""Export the second batch while preserving the approved first-batch assets."""
import json
from pathlib import Path

from build_motion_assets import BASELINE, SIZE, TARGET, export_gif, export_poses

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "output/gesture-study-v2"
FAMILIES = ("cheek-left", "cheek-right", "pickup", "landing")


def clip_definitions():
    clips = {}
    for family in ("cheek-left", "cheek-right"):
        for level in (1, 2, 3):
            sequences = {
                1: ([0, 1, 0], [0, 1, 0, 1, 0]),
                2: ([0, 1, 2, 0], [0, 1, 2, 1, 0]),
                3: ([0, 1, 2, 3, 2, 0], [0, 1, 3, 2, 3, 0]),
            }
            for variant, poses in enumerate(sequences[level]):
                durations = [100] + [240] * (len(poses) - 2) + [260]
                durations[-2] = 420
                clips[f"{family}-{level}-{variant}"] = {
                    "family": family, "poses": poses, "durations": durations, "loop": False}
    # During a real drag, velocity selects these poses and a spring carries
    # the lag. This timed order is the portable GIF demonstration only.
    clips["pickup"] = {"family": "pickup", "poses": [0, 1, 2, 3],
                       "durations": [180, 300, 300, 300], "loop": True}
    clips["landing"] = {"family": "landing", "poses": [0, 1, 2, 3],
                        "durations": [120, 180, 460, 260], "loop": False}
    return clips


def build():
    TARGET.mkdir(parents=True, exist_ok=True)
    (SOURCE / "gifs").mkdir(parents=True, exist_ok=True)
    poses = {family: export_poses(family, SOURCE) for family in FAMILIES}
    clips = clip_definitions()
    for name, clip in clips.items():
        export_gif(name, clip, poses[clip["family"]], SOURCE)
        clip["frames"] = [f"{clip['family']}-{index}.png" for index in clip.pop("poses")]
    target = TARGET / "clips.json"
    manifest = json.loads(target.read_text(encoding="utf-8"))
    manifest["clips"].update(clips)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bounds = {}
    for family, frames in poses.items():
        for index, frame in enumerate(frames):
            left, top, right, bottom = frame.getchannel("A").getbbox()
            bounds[f"{family}-{index}.png"] = dict(left=left, top=top, right=right, bottom=bottom)
    preview = {"schema_version": 1, "canvas": list(SIZE), "baseline": BASELINE,
               "clips": clips, "bounds": bounds}
    (PROJECT / "docs/gesture-preview-data.js").write_text(
        "window.MORI_GESTURES = " + json.dumps(preview, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"Exported {len(FAMILIES) * 4} new transparent poses and {len(clips)} clips")


if __name__ == "__main__":
    build()
