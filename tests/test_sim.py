"""Terminal simulator: gesture words become events, pages render as frames."""

import asyncio

from g1bridge.protocol import EventKind, ScreenStatus
from g1bridge.sim import SimGlasses, frame_page, parse_gesture


def test_parse_gesture_words():
    assert parse_gesture("r").kind is EventKind.SINGLE_TAP
    assert parse_gesture("r").side == "right"
    assert parse_gesture(" L ").side == "left"
    assert parse_gesture("ll").kind is EventKind.DOUBLE_TAP
    assert parse_gesture("rrr").kind is EventKind.TRIPLE_TAP
    assert parse_gesture("hold").kind is EventKind.AI_START
    assert parse_gesture("release").kind is EventKind.AI_STOP
    assert parse_gesture("what is the weather") is None
    assert parse_gesture("") is None


def test_frame_page_pads_and_labels():
    frame = frame_page(
        "one\ntwo", page=2, total_pages=3, max_chars=10, lines_per_page=3
    )
    lines = frame.split("\n")
    assert lines[0].startswith("+") and lines[-1].startswith("+")
    body = lines[1:-1]
    assert len(body) == 3
    assert body[0] == "|one       |"
    assert body[2] == "|          |"
    assert "2/3" in lines[-1]


def test_sim_glasses_records_pages_and_dispatches_events():
    shown: list[str] = []
    sim = SimGlasses(out=shown.append, max_chars=12, lines_per_page=2)
    asyncio.run(
        sim.send_text_page("hi", page=1, total_pages=1, status=ScreenStatus.TEXT_SHOW)
    )
    assert sim.pages_shown == ["hi"]
    assert any("|hi" in chunk for chunk in shown)

    got = []
    sim.add_listener(got.append)
    sim.inject(parse_gesture("hold"))
    assert got[0].kind is EventKind.AI_START


def test_mic_word_is_a_fake_audio_packet():
    event = parse_gesture("mic")
    assert event.kind is EventKind.MIC_DATA and len(event.payload) == 200
