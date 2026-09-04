"""One-bit 576 x 136 canvas and the BMP file the G1 accepts.

The official demo ships 1-bit BMP files (image_1.bmp: 62-byte header,
palette 0 = white, 1 = black, rows stored bottom-up) and streams the whole
file to the glasses. Its figure is drawn in palette index 0, so on the lens a
0 bit is lit and a 1 bit is dark; a blank screen is all 0xFF. We build the
same file byte for byte, only the picture differs.

Immutable: every drawing call returns a new Canvas. Pixels are kept packed,
one bit each, with 1 = lit (the file inverts them on the way out).
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass

WIDTH = 576
HEIGHT = 136  # official doc; the demo's own sample is 135 rows tall
ROW_BYTES = WIDTH // 8
HEADER_SIZE = 62  # 14 file header + 40 info header + two palette entries
_PALETTE = b"\xff\xff\xff\x00" + b"\x00\x00\x00\x00"  # 0 = white (lit), 1 = black
_PIXELS_PER_METRE = 2834  # 72 dpi, as in the sample

GLYPH_WIDTH = 5
GLYPH_HEIGHT = 7
# A 5 x 7 face for readouts: digits, the clock's colon, a percent sign and the
# dash that stands for "unknown". Rows top to bottom, 1 = lit.
GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    "%": ("11000", "11001", "00010", "00100", "01000", "10011", "00011"),
    "-": ("00000", "00000", "00000", "01110", "00000", "00000", "00000"),
    " ": ("00000",) * GLYPH_HEIGHT,
}


def text_width(text: str, scale: int = 1) -> int:
    """Pixels a string takes at `scale`: glyphs of 5 with a one-pixel gap."""
    if not text:
        return 0
    return (len(text) * GLYPH_WIDTH + (len(text) - 1)) * scale


@dataclass(frozen=True)
class Canvas:
    width: int
    height: int
    bits: bytes  # packed rows, top-down, most significant bit leftmost, 1 = lit

    @classmethod
    def blank(cls, width: int = WIDTH, height: int = HEIGHT) -> "Canvas":
        return cls(width, height, bytes(_row_bytes(width) * height))

    @property
    def row_bytes(self) -> int:
        return _row_bytes(self.width)

    def lit(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        return bool(self.bits[y * self.row_bytes + x // 8] & (0x80 >> (x % 8)))

    def rect(self, x: int, y: int, w: int, h: int) -> "Canvas":
        """A filled rectangle, clipped to the canvas."""
        return self._paint((px, py) for py in range(y, y + h) for px in range(x, x + w))

    def text(self, x: int, y: int, text: str, scale: int = 1) -> "Canvas":
        """Draw `text` with its top-left corner at (x, y), each glyph pixel scaled."""
        missing = sorted({char for char in text if char not in GLYPHS})
        if missing:
            raise ValueError(f"no glyph for {missing}; the face has {''.join(GLYPHS)}")
        advance = (GLYPH_WIDTH + 1) * scale
        pixels = [
            (x + column * advance + gx * scale + dx, y + gy * scale + dy)
            for column, char in enumerate(text)
            for gy, row in enumerate(GLYPHS[char])
            for gx, bit in enumerate(row)
            if bit == "1"
            for dy in range(scale)
            for dx in range(scale)
        ]
        return self._paint(pixels)

    def _paint(self, pixels: Iterable[tuple[int, int]]) -> "Canvas":
        bits = bytearray(self.bits)
        stride = self.row_bytes
        for x, y in pixels:
            if 0 <= x < self.width and 0 <= y < self.height:
                bits[y * stride + x // 8] |= 0x80 >> (x % 8)
        return Canvas(self.width, self.height, bytes(bits))

    def to_bmp(self) -> bytes:
        """The 1-bit BMP file: header as in the official sample, rows bottom-up."""
        stride = self.row_bytes
        image_size = stride * self.height
        file_header = struct.pack(
            "<2sIHHI", b"BM", HEADER_SIZE + image_size, 0, 0, HEADER_SIZE
        )
        info_header = struct.pack(
            "<IiiHHIIiiII",
            40,
            self.width,
            self.height,  # positive: bottom-up
            1,  # planes
            1,  # bits per pixel
            0,  # no compression
            image_size,
            _PIXELS_PER_METRE,
            _PIXELS_PER_METRE,
            0,  # colours used (all)
            0,  # colours important
        )
        rows = [
            bytes(byte ^ 0xFF for byte in self.bits[y * stride : (y + 1) * stride])
            for y in reversed(range(self.height))
        ]
        return file_header + info_header + _PALETTE + b"".join(rows)

    @classmethod
    def from_bmp(cls, data: bytes) -> "Canvas":
        """Read back a file to_bmp() wrote (or the demo's samples)."""
        if len(data) < HEADER_SIZE or data[:2] != b"BM":
            raise ValueError("not a BMP file")
        offset = struct.unpack_from("<I", data, 10)[0]
        width, height = struct.unpack_from("<ii", data, 18)
        planes, bpp = struct.unpack_from("<HH", data, 26)
        if planes != 1 or bpp != 1 or width <= 0 or height == 0:
            raise ValueError(f"expected a 1-bit BMP, got {bpp} bpp {width}x{height}")
        stride = _row_bytes(width)
        rows = abs(height)
        if len(data) < offset + stride * rows:
            raise ValueError("BMP pixel data is truncated")
        stored = [
            data[offset + r * stride : offset + (r + 1) * stride] for r in range(rows)
        ]
        top_down = stored[::-1] if height > 0 else stored
        bits = bytes(byte ^ 0xFF for row in top_down for byte in row)
        return cls(width, rows, bits)

    def preview(self, cols: int = 72, rows: int = 17) -> str:
        """Coarse ASCII picture: '#' where any pixel in the block is lit."""
        block_w, block_h = self.width / cols, self.height / rows
        lines = []
        for row in range(rows):
            y0, y1 = round(row * block_h), round((row + 1) * block_h)
            line = ""
            for col in range(cols):
                x0, x1 = round(col * block_w), round((col + 1) * block_w)
                lit = any(self.lit(x, y) for y in range(y0, y1) for x in range(x0, x1))
                line += "#" if lit else "."
            lines.append(line)
        return "\n".join(lines)


def _row_bytes(width: int) -> int:
    # BMP rows are padded to four bytes; 576 px is 72 bytes, already aligned.
    return ((width + 31) // 32) * 4
