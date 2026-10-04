"""Semantic timing, shared by key poses, GIF playback and desktop stories."""

POSE_MS = {
    "mouse-curious": 1600, "mouse-shy": 1550, "mouse-settle": 1400,
    "window-peek-left": 5200, "window-peek-right": 5200, "window-peek-top": 6000,
    "window-seat": 1450, "window-rise": 1250, "window-consider": 1200,
    "window-press": 1050, "window-press-recover": 900,
    "base-eat": 2500, "base-think": 3200, "base-attention": 3400,
    "base-cheerup": 2800, "base-wrong": 3200, "base-true": 2800,
    "base-touch": 2100, "daily-stretch": 2800, "landing": 1220,
}
GIF_SOURCE_MS = {"eat": 2080, "think": 2560, "attention": 2600,
                 "cheerup": 2400, "wrong": 2620, "true": 2360, "touch": 2020}
ONE_SHOT_MS = {"cheerup": 4200, "wrong": 4400, "true": 3600, "touch": 2800}
STORY_SECONDS = {"look": .6, "chase": 3.2, "seat-rest": 12,
                 "background-greet": 3, "background-rest": 7,
                 "minimize-consider": 3, "celebration-settle": 1.4}


def pose_durations(name, durations):
    canonical = name.removesuffix("-left") if name.startswith("mouse-") else name
    target = POSE_MS.get(canonical)
    if name.startswith("head-"):
        target = round(sum(durations) * 1.15)
    elif name.startswith("tail-"):
        target = round(sum(durations) * 1.05)
    elif name.startswith("cheek-"):
        target = round(sum(durations) * .95)
    if target is None:
        return list(durations)
    scale = target / sum(durations)
    result = [max(80, round(ms * scale)) for ms in durations]
    result[-1] += target - sum(result)
    return result


def gif_speed_scale(key):
    return GIF_SOURCE_MS.get(key, 1) / POSE_MS.get("base-" + key, 1)


def quiet_interval(seconds, variation):
    """Give every configured interval 15–35% more quiet time."""
    return seconds * (1.15 + .2 * max(0, min(1, variation)))
