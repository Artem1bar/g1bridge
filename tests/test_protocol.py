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


def test_head_gestures_and_battery_are_distinct_from_dashboard_events():
    from g1bridge.protocol import EventKind, parse_notification

    assert parse_notification("right", bytes([0xF5, 0x02])).kind is EventKind.HEAD_UP
    assert parse_notification("right", bytes([0xF5, 0x03])).kind is EventKind.HEAD_DOWN
    assert (
        parse_notification("left", bytes([0xF5, 0x1E])).kind is EventKind.DASHBOARD_OPEN
    )
    battery = parse_notification("left", bytes([0xF5, 0x0A, 0x21]) + bytes(18))
    assert battery.kind is EventKind.BATTERY and battery.payload == b"\x21"


# --- bitmap upload (official doc + EvenDemoApp bmp_update_manager.dart) ---


def test_bmp_packets_carry_the_storage_address_only_on_the_first():
    image = bytes(range(256)) * 2  # 512 bytes -> 194 + 194 + 124
    packets = protocol.bmp_packets(image)
    assert len(packets) == 3
    assert packets[0][:6] == bytes([0x15, 0x00, 0x00, 0x1C, 0x00, 0x00])
    assert packets[0][6:] == image[:194]
    assert packets[1][:2] == bytes([0x15, 0x01]) and packets[1][2:] == image[194:388]
    assert packets[2][:2] == bytes([0x15, 0x02]) and packets[2][2:] == image[388:]


def test_bmp_packets_seq_wraps_after_255():
    packets = protocol.bmp_packets(bytes(194 * 300))
    assert len(packets) == 300
    assert packets[255][1] == 0xFF and packets[256][1] == 0x00


def test_bmp_packets_reject_an_empty_image():
    with pytest.raises(ValueError):
        protocol.bmp_packets(b"")


def test_bmp_end_is_the_fixed_finish_command():
    assert protocol.bmp_end() == bytes([0x20, 0x0D, 0x0E])


def test_bmp_crc_covers_the_address_and_is_big_endian():
    import zlib

    image = b"reticle"
    expected = zlib.crc32(bytes([0x00, 0x1C, 0x00, 0x00]) + image)
    frame = protocol.bmp_crc(image)
    assert frame[0] == 0x16
    assert frame[1:] == expected.to_bytes(4, "big")


def test_parse_bmp_finish_and_crc_replies():
    assert parse_notification("left", bytes([0x20, 0xC9])).kind is EventKind.BMP_END_OK
    assert (
        parse_notification("left", bytes([0x20, 0xCA])).kind is EventKind.BMP_END_FAIL
    )
    ok = bytes([0x16, 0x12, 0x34, 0x56, 0x78, 0xC9])
    assert parse_notification("right", ok).kind is EventKind.BMP_CRC_OK
    bad = bytes([0x16, 0x12, 0x34, 0x56, 0x78, 0xCA])
    assert parse_notification("right", bad).kind is EventKind.BMP_CRC_FAIL
    assert (
        parse_notification("right", bytes([0x16, 0xC9])).kind is EventKind.BMP_CRC_FAIL
    )
