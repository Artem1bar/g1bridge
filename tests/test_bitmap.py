"""One-bit canvas and the BMP file the glasses accept (576 x 136, 62-byte header)."""

import struct

import pytest

from g1bridge.bitmap import HEADER_SIZE, HEIGHT, ROW_BYTES, WIDTH, Canvas, text_width


def test_blank_canvas_is_dark_everywhere():
    canvas = Canvas.blank()
    assert canvas.width == WIDTH and canvas.height == HEIGHT
    assert not canvas.lit(0, 0) and not canvas.lit(WIDTH - 1, HEIGHT - 1)


def test_rect_lights_exactly_its_pixels_and_leaves_the_original_alone():
    blank = Canvas.blank()
    drawn = blank.rect(10, 20, 3, 2)
    assert not blank.lit(10, 20)  # immutable: a new canvas came back
    assert drawn.lit(10, 20) and drawn.lit(12, 21)
    assert not drawn.lit(13, 20) and not drawn.lit(10, 22) and not drawn.lit(9, 20)


def test_rect_is_clipped_to_the_canvas():
    canvas = Canvas.blank().rect(WIDTH - 2, HEIGHT - 2, 10, 10)
    assert canvas.lit(WIDTH - 1, HEIGHT - 1)
    assert canvas.to_bmp()  # no IndexError, still encodes


def test_lit_outside_the_canvas_is_false_not_an_error():
    canvas = Canvas.blank().rect(0, 0, 1, 1)
    assert (
        not canvas.lit(-1, 0) and not canvas.lit(WIDTH, 0) and not canvas.lit(0, HEIGHT)
    )


def test_bmp_header_matches_the_official_sample_layout():
    data = Canvas.blank().to_bmp()
    assert data[:2] == b"BM"
    assert len(data) == HEADER_SIZE + ROW_BYTES * HEIGHT == 9854
    assert struct.unpack_from("<I", data, 2)[0] == len(data)  # file size
    assert struct.unpack_from("<I", data, 10)[0] == HEADER_SIZE  # pixel offset
    assert struct.unpack_from("<I", data, 14)[0] == 40  # BITMAPINFOHEADER
    assert struct.unpack_from("<ii", data, 18) == (WIDTH, HEIGHT)  # bottom-up
    assert struct.unpack_from("<HH", data, 26) == (1, 1)  # planes, bits per pixel
    assert struct.unpack_from("<I", data, 34)[0] == ROW_BYTES * HEIGHT  # image size
    assert data[54:58] == b"\xff\xff\xff\x00"  # palette 0: white = lit
    assert data[58:62] == b"\x00\x00\x00\x00"  # palette 1: black = dark


def test_dark_pixels_are_ones_and_lit_pixels_are_zeros_like_the_sample():
    # The official sample draws its figure with palette index 0 on an index-1 ground.
    blank = Canvas.blank().to_bmp()
    assert set(blank[HEADER_SIZE:]) == {0xFF}
    top_left = Canvas.blank().rect(0, 0, 1, 1).to_bmp()
    last_row = top_left[-ROW_BYTES:]  # rows are stored bottom-up: top row last
    assert last_row[0] == 0x7F and set(last_row[1:]) == {0xFF}


def test_bmp_round_trips_through_from_bmp():
    canvas = Canvas.blank().rect(100, 50, 7, 3).rect(575, 135, 1, 1)
    again = Canvas.from_bmp(canvas.to_bmp())
    assert again == canvas
    assert again.lit(106, 52) and again.lit(575, 135) and not again.lit(107, 52)


def test_from_bmp_rejects_other_formats():
    with pytest.raises(ValueError):
        Canvas.from_bmp(b"PNG rubbish")
    with pytest.raises(ValueError):
        Canvas.from_bmp(Canvas.blank().to_bmp()[:100])


def test_text_draws_scaled_glyphs_and_reports_its_width():
    canvas = Canvas.blank().text(10, 10, "1", scale=2)
    assert text_width("1", scale=2) == 10  # 5 px wide glyph, doubled
    assert text_width("17:43", scale=2) == 5 * 10 + 4 * 2  # 2 px gaps at scale 2
    assert text_width("", scale=2) == 0
    lit_pixels = sum(canvas.lit(x, y) for x in range(10, 20) for y in range(10, 24))
    assert lit_pixels > 0
    # Nothing outside the glyph box.
    assert not any(canvas.lit(x, 9) for x in range(10, 20))
    assert not any(canvas.lit(x, 24) for x in range(10, 20))


def test_text_refuses_glyphs_it_does_not_have():
    with pytest.raises(ValueError):
        Canvas.blank().text(0, 0, "A", scale=1)


def test_preview_is_a_coarse_ascii_picture():
    canvas = Canvas.blank().rect(0, 0, 8, 8)
    preview = canvas.preview(cols=72, rows=17)
    lines = preview.split("\n")
    assert len(lines) == 17 and all(len(line) == 72 for line in lines)
    assert lines[0][0] == "#" and lines[0][1] == "." and lines[1][0] == "."
