"""Small policies shared by attached window animations."""


def choose_peek_side(left_space, right_space, previous="left"):
    """Alternate when both edges fit, otherwise favor the available edge."""
    left_fits, right_fits = left_space >= 218, right_space >= 125
    if left_fits and right_fits:
        return "right" if previous == "left" else "left"
    if left_fits:
        return "left"
    if right_fits:
        return "right"
    return "left" if left_space > right_space else "right"


def clip_index(clip, elapsed_ms):
    total = sum(clip["durations"])
    elapsed = elapsed_ms % total if clip.get("loop") else min(elapsed_ms, total - 1)
    for index, duration in enumerate(clip["durations"]):
        if elapsed < duration:
            return index
        elapsed -= duration
    return len(clip["durations"]) - 1


def fits_box(box, container, margin=8):
    """Half-open drawing bounds must fit completely, with a little air."""
    left, top, right, bottom = box
    x0, y0, x1, y1 = container
    return (left < right and top < bottom and x0 + margin <= left and
            y0 + margin <= top and right <= x1 - margin and bottom <= y1 - margin)
