"""Even Realities G1 BLE protocol: frame builders and event parsing.

Pure functions and constants only — no I/O, fully unit-testable.

Byte-level facts verified against:
- the official EvenDemoApp protocol README (github.com/even-realities/EvenDemoApp, BSD-2-Clause)
- community observations (emingenc/even_glasses, binarythinktank/eveng1_python_sdk)
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from enum import Enum, IntEnum

# Nordic UART Service — each G1 arm is its own BLE peripheral exposing one of these.
UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # host -> glasses (write)
UART_RX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # glasses -> host (notify)

# A text page is sent as a single 0x4E package; ATT long writes handle fragmentation.
MAX_TEXT_PAYLOAD = 480
# A bitmap is the whole 1-bit BMP file, streamed in 194-byte pieces to a fixed
# storage address on the glasses (official doc + EvenDemoApp bmp_update_manager).
BMP_CHUNK = 194
BMP_ADDRESS = bytes([0x00, 0x1C, 0x00, 0x00])
BMP_OK = 0xC9


class Cmd(IntEnum):
    BRIGHTNESS = 0x01
    SILENT_MODE = 0x03
    MIC = 0x0E
    BMP_PACKET = 0x15
    BMP_CRC = 0x16
    BMP_END = 0x20  # "all packets sent", fixed payload 0x0D 0x0E
    EXIT = 0x18  # "exit all func" in the official demo app (Proto.exit); not in its doc
    HEARTBEAT = 0x25
    NOTIFICATION = 0x4B
    TEXT = 0x4E
    MIC_DATA = 0xF1
    EVENT = 0xF5


class ScreenStatus(IntEnum):
    """Upper nibble of the 0x4E "newscreen" byte."""

    AI_DISPLAYING = 0x30  # auto mode, more pages coming
    AI_COMPLETE = 0x40  # last page in auto mode
    AI_MANUAL = 0x50  # wearer is paging with the TouchBars
    AI_ERROR = 0x60
    TEXT_SHOW = 0x70  # plain text display


NEW_CONTENT = 0x01  # lower nibble of the "newscreen" byte: display new content


class EventKind(Enum):
    SINGLE_TAP = "single_tap"
    DOUBLE_TAP = "double_tap"
    TRIPLE_TAP = "triple_tap"
    AI_START = "ai_start"  # long-press on the left TouchBar
    AI_STOP = "ai_stop"  # long-press released / recording stopped
    WEARING = "wearing"
    TAKEN_OFF = "taken_off"
    CRADLE = "cradle"
    HEAD_UP = "head_up"
    HEAD_DOWN = "head_down"
    DASHBOARD_OPEN = "dashboard_open"
    DASHBOARD_CLOSE = "dashboard_close"
    BATTERY = "battery"  # F5 0A <percent>; observed draining 1%/4 min on hardware
    MIC_OK = "mic_ok"
    MIC_FAIL = "mic_fail"
    MIC_DATA = "mic_data"
    HEARTBEAT_ACK = "heartbeat_ack"
    TEXT_ACK = "text_ack"
    BMP_END_OK = "bmp_end_ok"
    BMP_END_FAIL = "bmp_end_fail"
    BMP_CRC_OK = "bmp_crc_ok"
    BMP_CRC_FAIL = "bmp_crc_fail"
    UNKNOWN = "unknown"


_F5_EVENTS = {
    0x00: EventKind.DOUBLE_TAP,
    0x01: EventKind.SINGLE_TAP,
    0x02: EventKind.HEAD_UP,  # confirmed 2026-09-03 (precedes dashboard_open)
    0x03: EventKind.HEAD_DOWN,
    0x0A: EventKind.BATTERY,  # value byte follows; see parse_notification
    0x04: EventKind.TRIPLE_TAP,  # silent mode on
    0x05: EventKind.TRIPLE_TAP,  # silent mode off
    0x06: EventKind.WEARING,
    0x07: EventKind.TAKEN_OFF,
    0x08: EventKind.CRADLE,
    0x09: EventKind.CRADLE,  # charged in cradle
    0x0B: EventKind.CRADLE,  # cradle closed
    0x17: EventKind.AI_START,
    0x18: EventKind.AI_STOP,
    0x1E: EventKind.DASHBOARD_OPEN,  # confirmed
    0x1F: EventKind.DASHBOARD_CLOSE,  # confirmed
}


@dataclass(frozen=True)
class G1Event:
    kind: EventKind
    side: str  # "left" | "right"
    raw: bytes
    seq: int | None = None  # mic-data sequence number
    payload: bytes = b""  # mic-data audio chunk (LC3)


def heartbeat(seq: int) -> bytes:
    """0x25 keep-alive; without it the glasses drop the connection."""
    return bytes([Cmd.HEARTBEAT, 0x06, 0x00, seq & 0xFF, 0x04, seq & 0xFF])


def text_page(
    text: str,
    *,
    page: int,
    total_pages: int,
    status: ScreenStatus,
    seq: int = 0,
) -> bytes:
    """One 0x4E frame carrying a full screen of text as a single package."""
    payload = text.encode("utf-8")
    if len(payload) > MAX_TEXT_PAYLOAD:
        raise ValueError(f"page payload {len(payload)}B exceeds {MAX_TEXT_PAYLOAD}B")
    header = bytes(
        [
            Cmd.TEXT,
            seq & 0xFF,
            1,  # total_package_num: the page travels as one package
            0,  # current_package_num
            (status & 0xF0) | NEW_CONTENT,
            0x00,  # new_char_pos0
            0x00,  # new_char_pos1
            page & 0xFF,
            total_pages & 0xFF,
        ]
    )
    return header + payload


def mic_control(enable: bool) -> bytes:
    """0x0E: enable/disable the right-arm microphone (send to the right arm only)."""
    return bytes([Cmd.MIC, 0x01 if enable else 0x00])


def exit_to_dashboard() -> bytes:
    """0x18: what the demo app's Exit button sends after a bitmap (reply 0xC9)."""
    return bytes([Cmd.EXIT])


def bmp_packets(image: bytes) -> tuple[bytes, ...]:
    """The 0x15 frames for one BMP file: seq byte, then the storage address on
    the first frame only, then up to 194 bytes of the file."""
    if not image:
        raise ValueError("empty bitmap")
    frames = []
    for seq, start in enumerate(range(0, len(image), BMP_CHUNK)):
        head = bytes([Cmd.BMP_PACKET, seq & 0xFF]) + (BMP_ADDRESS if seq == 0 else b"")
        frames.append(head + image[start : start + BMP_CHUNK])
    return tuple(frames)


def bmp_end() -> bytes:
    """0x20 0x0D 0x0E: every packet has been sent; the glasses reply 0xC9."""
    return bytes([Cmd.BMP_END, 0x0D, 0x0E])


def bmp_crc(image: bytes) -> bytes:
    """0x16 + CRC-32 (the plain zlib polynomial, big-endian) over address + file."""
    crc = zlib.crc32(BMP_ADDRESS + image) & 0xFFFFFFFF
    return bytes([Cmd.BMP_CRC]) + crc.to_bytes(4, "big")


def parse_notification(side: str, data: bytes) -> G1Event:
    """Decode a UART notify payload from one arm into a G1Event."""
    if not data:
        return G1Event(EventKind.UNKNOWN, side, b"")
    raw = bytes(data)
    cmd = raw[0]
    if cmd == Cmd.EVENT and len(raw) >= 2:
        kind = _F5_EVENTS.get(raw[1], EventKind.UNKNOWN)
        if kind is EventKind.BATTERY and len(raw) >= 3:
            return G1Event(kind, side, raw, payload=raw[2:3])
        return G1Event(kind, side, raw)
    if cmd == Cmd.MIC_DATA and len(raw) >= 2:
        return G1Event(EventKind.MIC_DATA, side, raw, seq=raw[1], payload=raw[2:])
    if cmd == Cmd.MIC and len(raw) >= 2:
        ok = raw[1] == 0xC9
        return G1Event(EventKind.MIC_OK if ok else EventKind.MIC_FAIL, side, raw)
    if cmd == Cmd.HEARTBEAT:
        return G1Event(EventKind.HEARTBEAT_ACK, side, raw)
    if cmd == Cmd.TEXT:
        return G1Event(EventKind.TEXT_ACK, side, raw)
    if cmd == Cmd.BMP_END:
        ok = len(raw) >= 2 and raw[1] == BMP_OK
        return G1Event(
            EventKind.BMP_END_OK if ok else EventKind.BMP_END_FAIL, side, raw
        )
    if cmd == Cmd.BMP_CRC:
        # The demo reads the verdict from the sixth byte of the reply.
        ok = len(raw) >= 6 and raw[5] == BMP_OK
        return G1Event(
            EventKind.BMP_CRC_OK if ok else EventKind.BMP_CRC_FAIL, side, raw
        )
    return G1Event(EventKind.UNKNOWN, side, raw)
