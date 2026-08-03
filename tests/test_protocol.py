import pytest

from g1bridge import protocol
from g1bridge.protocol import EventKind, ScreenStatus, parse_notification


def test_heartbeat_matches_reference_bytes():
    # Known-good constant captured in binarythinktank/eveng1_python_sdk for seq=1.
    assert protocol.heartbeat(1) == bytes([0x25, 0x06, 0x00, 0x01, 0x04, 0x01])


def test_heartbeat_seq_wraps_to_a_byte():
    assert protocol.heartbeat(0x1FF)[3] == 0xFF
    assert protocol.heartbeat(256)[3] == 0x00


def test_text_page_header_layout():
    frame = protocol.text_page(
        "hi", page=2, total_pages=3, status=ScreenStatus.AI_MANUAL, seq=7
    )
    assert frame[0] == 0x4E  # command
    assert frame[1] == 7  # seq
    assert frame[2] == 1  # total_package_num
    assert frame[3] == 0  # current_package_num
    assert frame[4] == 0x51  # manual mode | new content
    assert frame[5:7] == b"\x00\x00"  # new_char_pos
    assert frame[7] == 2  # current page
    assert frame[8] == 3  # max pages
    assert frame[9:] == b"hi"


def test_text_page_plain_text_show_status():
    # Official doc example: "New content + Text Show state is represented as 0x71".
    frame = protocol.text_page(
        "x", page=1, total_pages=1, status=ScreenStatus.TEXT_SHOW
    )
    assert frame[4] == 0x71


def test_text_page_encodes_utf8():
    frame = protocol.text_page(
        "héllo", page=1, total_pages=1, status=ScreenStatus.AI_COMPLETE
    )
    assert frame[9:] == "héllo".encode()


def test_text_page_rejects_oversize_payload():
    with pytest.raises(ValueError):
        protocol.text_page(
            "x" * (protocol.MAX_TEXT_PAYLOAD + 1),
            page=1,
            total_pages=1,
            status=ScreenStatus.AI_COMPLETE,
        )


def test_mic_control():
    assert protocol.mic_control(True) == bytes([0x0E, 0x01])
    assert protocol.mic_control(False) == bytes([0x0E, 0x00])


def test_parse_taps_carry_side():
    single = parse_notification("left", bytes([0xF5, 0x01]))
    assert single.kind is EventKind.SINGLE_TAP
    assert single.side == "left"
    assert parse_notification("right", bytes([0xF5, 0x00])).kind is EventKind.DOUBLE_TAP


def test_parse_even_ai_lifecycle():
    assert parse_notification("left", bytes([0xF5, 0x17])).kind is EventKind.AI_START
    assert parse_notification("left", bytes([0xF5, 0x18])).kind is EventKind.AI_STOP


def test_parse_wear_events():
    assert parse_notification("left", bytes([0xF5, 0x06])).kind is EventKind.WEARING
    assert parse_notification("left", bytes([0xF5, 0x07])).kind is EventKind.TAKEN_OFF


def test_parse_mic_response():
    ok = parse_notification("right", bytes([0x0E, 0xC9, 0x01]))
    fail = parse_notification("right", bytes([0x0E, 0xCA, 0x01]))
    assert ok.kind is EventKind.MIC_OK
    assert fail.kind is EventKind.MIC_FAIL


def test_parse_mic_data_stream():
    event = parse_notification("right", bytes([0xF1, 0x0A]) + b"\x01\x02\x03")
    assert event.kind is EventKind.MIC_DATA
    assert event.seq == 10
    assert event.payload == b"\x01\x02\x03"


def test_parse_unknown_and_empty():
    assert parse_notification("left", b"").kind is EventKind.UNKNOWN
    assert parse_notification("left", bytes([0xF5, 0x7E])).kind is EventKind.UNKNOWN
    assert parse_notification("left", bytes([0x99, 0x01])).kind is EventKind.UNKNOWN
