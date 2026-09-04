"""The cockpit reticle: an always-on frame for the lens.

Four 90-degree marks in the corners of the bitmap, a plain cross dead
centre, the time tucked inside the top-left corner and the charge inside
the bottom-right. Nothing else: empty space is the point of a reticle.
Geometry in pixels of the 576 x 136 one-bit bitmap; pure rendering.
"""

from __future__ import annotations

from datetime import datetime

from .bitmap import GLYPH_HEIGHT, HEIGHT, WIDTH, Canvas, text_width

LINE = 2  # stroke thickness
CORNER_INSET = 3  # keep clear of the edge so nothing clips
CORNER_ALONG = 26  # corner mark length along the top and bottom edges
CORNER_DOWN = 16  # and down the sides
CROSS_ARM = 10  # half-width of the centre cross
TEXT_SCALE = 2  # 5 x 7 glyphs drawn at 10 x 14
TEXT_INSET = 8  # from the inner edge of a corner mark to the readout
TEXT_DROP = 5  # readouts sit this far inside the mark's top or bottom arm


def render_cockpit(now: datetime, battery: int | None) -> Canvas:
    """The reticle for one minute: `battery` is a percent, None when unknown."""
    canvas = _cross(_corners(Canvas.blank()))
    clock = f"{now:%H:%M}"
    charge = "--%" if battery is None else f"{max(0, min(100, battery))}%"
    text_height = GLYPH_HEIGHT * TEXT_SCALE
    inner = CORNER_INSET + LINE
    canvas = canvas.text(inner + TEXT_INSET, inner + TEXT_DROP, clock, TEXT_SCALE)
    return canvas.text(
        WIDTH - inner - TEXT_INSET - text_width(charge, TEXT_SCALE),
        HEIGHT - inner - TEXT_DROP - text_height,
        charge,
        TEXT_SCALE,
    )


def _corners(canvas: Canvas) -> Canvas:
    i, along, down, t = CORNER_INSET, CORNER_ALONG, CORNER_DOWN, LINE
    right, bottom = WIDTH - i - t, HEIGHT - i - t
    return (
        canvas.rect(i, i, along, t)
        .rect(i, i, t, down)
        .rect(WIDTH - i - along, i, along, t)
        .rect(right, i, t, down)
        .rect(i, bottom, along, t)
        .rect(i, HEIGHT - i - down, t, down)
        .rect(WIDTH - i - along, bottom, along, t)
        .rect(right, HEIGHT - i - down, t, down)
    )


def _cross(canvas: Canvas) -> Canvas:
    cx, cy = WIDTH // 2, HEIGHT // 2
    return canvas.rect(cx - CROSS_ARM, cy - 1, 2 * CROSS_ARM, LINE).rect(
        cx - 1, cy - CROSS_ARM, LINE, 2 * CROSS_ARM
    )
