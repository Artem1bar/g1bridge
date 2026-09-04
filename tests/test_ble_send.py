"""Dual-arm send: left first, right only after the left's ack (or a timeout)."""

import asyncio

from g1bridge.ble import G1Glasses
from g1bridge.protocol import EventKind, G1Event, ScreenStatus


def make_glasses(record: list[str], ack_left: bool):
    glasses = G1Glasses("L", "R", "left", "right")

    async def left_write(frame: bytes) -> None:
        record.append("left")
        if ack_left:
            glasses._dispatch(G1Event(EventKind.TEXT_ACK, "left", b"\x4e\xc9"))

    async def right_write(frame: bytes) -> None:
        record.append("right")

    glasses.left.write = left_write  # type: ignore[method-assign]
    glasses.right.write = right_write  # type: ignore[method-assign]
    return glasses


def test_right_follows_left_ack():
    record: list[str] = []
    glasses = make_glasses(record, ack_left=True)
    acked = asyncio.run(glasses.send_acked(b"frame", timeout=0.2))
    assert acked is True and record == ["left", "right"]


def test_right_still_sent_when_left_is_mute():
    record: list[str] = []
    glasses = make_glasses(record, ack_left=False)
    acked = asyncio.run(glasses.send_acked(b"frame", timeout=0.05))
    assert acked is False and record == ["left", "right"]


def test_ack_from_the_wrong_arm_does_not_count():
    record: list[str] = []
    glasses = make_glasses(record, ack_left=False)

    async def go():
        waiter = glasses.expect(EventKind.TEXT_ACK, "left")
        glasses._dispatch(G1Event(EventKind.TEXT_ACK, "right", b""))
        assert not waiter.done()
        glasses._dispatch(G1Event(EventKind.HEARTBEAT_ACK, "left", b""))
        assert not waiter.done()
        glasses._dispatch(G1Event(EventKind.TEXT_ACK, "left", b""))
        assert waiter.done()
        assert glasses._waiters == ()

    asyncio.run(go())


def test_text_page_goes_through_ack_path():
    record: list[str] = []
    glasses = make_glasses(record, ack_left=True)
    asyncio.run(
        glasses.send_text_page(
            "hi", page=1, total_pages=1, status=ScreenStatus.TEXT_SHOW
        )
    )
    assert record == ["left", "right"]
