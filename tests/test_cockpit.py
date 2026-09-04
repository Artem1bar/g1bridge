"""The cockpit reticle: corner marks, a centre cross, the time and the charge."""

from datetime import datetime

from g1bridge.bitmap import HEIGHT, WIDTH
from g1bridge.cockpit import (
    CORNER_ALONG,
    CORNER_DOWN,
    CORNER_INSET,
    CROSS_ARM,
    LINE,
    render_cockpit,
)

NOW = datetime(2026, 9, 4, 17, 43)


def test_corner_marks_sit_in_all_four_corners():
    canvas = render_cockpit(NOW, battery=58)
    i, along, down, t = CORNER_INSET, CORNER_ALONG, CORNER_DOWN, LINE
    # Top-left: horizontal arm and vertical arm, both LINE pixels thick.
    assert (
        canvas.lit(i, i)
        and canvas.lit(i + along - 1, i)
        and canvas.lit(i, i + down - 1)
    )
    assert canvas.lit(i + along - 1, i + t - 1) and not canvas.lit(i + along, i)
    assert not canvas.lit(i, i + down) and not canvas.lit(i + t, i + t)
    # Top-right, bottom-left, bottom-right mirror it.
    assert canvas.lit(WIDTH - 1 - i, i) and canvas.lit(WIDTH - i - along, i)
    assert canvas.lit(WIDTH - 1 - i, i + down - 1)
    assert canvas.lit(i, HEIGHT - 1 - i) and canvas.lit(i, HEIGHT - i - down)
    assert canvas.lit(WIDTH - 1 - i, HEIGHT - 1 - i) and canvas.lit(
        WIDTH - i - along, HEIGHT - 1 - i
    )
    # The very edge stays dark so nothing clips on the lens.
    assert not canvas.lit(0, 0) and not canvas.lit(WIDTH - 1, HEIGHT - 1)


def test_cross_is_centred_and_plain():
    canvas = render_cockpit(NOW, battery=58)
    cx, cy = WIDTH // 2, HEIGHT // 2
    assert canvas.lit(cx, cy) and canvas.lit(cx - 1, cy - 1)
    assert canvas.lit(cx - CROSS_ARM, cy) and canvas.lit(cx + CROSS_ARM - 1, cy)
    assert canvas.lit(cx, cy - CROSS_ARM) and canvas.lit(cx, cy + CROSS_ARM - 1)
    assert not canvas.lit(cx - CROSS_ARM - 1, cy) and not canvas.lit(cx + CROSS_ARM, cy)
    assert not canvas.lit(cx - 5, cy - 5)  # no ring, no diagonals


def test_clock_top_left_and_battery_bottom_right_are_the_only_readouts():
    canvas = render_cockpit(NOW, battery=58)
    assert _lit_in(canvas, 10, 8, 80, 26) > 40  # "17:43" inside the top-left corner
    assert _lit_in(canvas, 520, 108, 566, 128) > 20  # "58%" inside the bottom-right
    assert _lit_in(canvas, 100, 30, 470, 55) == 0  # the band between them is empty
    assert _lit_in(canvas, 10, 108, 300, 128) == 0  # nothing bottom-left but the corner


def test_unknown_battery_reads_as_dashes_and_renders():
    known = render_cockpit(NOW, battery=58)
    unknown = render_cockpit(NOW, battery=None)
    assert unknown != known
    assert _lit_in(unknown, 520, 108, 566, 128) > 10


def test_clock_changes_with_the_minute():
    assert render_cockpit(NOW, battery=58) != render_cockpit(
        datetime(2026, 9, 4, 17, 44), battery=58
    )


def test_bmp_is_the_size_the_glasses_expect():
    assert len(render_cockpit(NOW, battery=100).to_bmp()) == 9854


def _lit_in(canvas, x0, y0, x1, y1) -> int:
    return sum(canvas.lit(x, y) for x in range(x0, x1) for y in range(y0, y1))
