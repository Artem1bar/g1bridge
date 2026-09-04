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


def make_mic_glasses(record: list[tuple[str, bytes]], reply: EventKind | None):
    glasses = G1Glasses("L", "R", "left", "right")

    async def left_write(frame: bytes) -> None:
        record.append(("left", frame))

    async def right_write(frame: bytes) -> None:
        record.append(("right", frame))
        if reply is not None:
            glasses._dispatch(G1Event(reply, "right", b"\x0e\xc9\x01"))

    glasses.left.write = left_write  # type: ignore[method-assign]
    glasses.right.write = right_write  # type: ignore[method-assign]
    return glasses


def test_set_mic_goes_to_the_right_arm_only_and_reads_the_ack():
    record: list[tuple[str, bytes]] = []
    glasses = make_mic_glasses(record, EventKind.MIC_OK)
    assert asyncio.run(glasses.set_mic(True, timeout=0.2)) is True
    assert record == [("right", b"\x0e\x01")]


def test_set_mic_reports_failure_and_timeout():
    record: list[tuple[str, bytes]] = []
    failing = make_mic_glasses(record, EventKind.MIC_FAIL)
    assert asyncio.run(failing.set_mic(False, timeout=0.2)) is False
    assert record[-1] == ("right", b"\x0e\x00")
    mute = make_mic_glasses(record, None)
    assert asyncio.run(mute.set_mic(False, timeout=0.05)) is False


def test_set_mic_retries_once_after_a_refusal():
    record: list[tuple[str, bytes]] = []
    glasses = G1Glasses("L", "R", "left", "right")
    replies = iter([EventKind.MIC_FAIL, EventKind.MIC_OK])

    async def right_write(frame: bytes) -> None:
        record.append(("right", frame))
        glasses._dispatch(G1Event(next(replies), "right", b""))

    glasses.right.write = right_write  # type: ignore[method-assign]
    from g1bridge import ble

    ble.MIC_RETRY_PAUSE_S = 0.01
    assert asyncio.run(glasses.set_mic(True, timeout=0.2)) is True
    assert len(record) == 2


def test_connect_survives_a_failing_mic_reset(monkeypatch):
    glasses = G1Glasses("L", "R", "left", "right")

    async def ok() -> None:
        return None

    async def boom(frame: bytes) -> None:
        raise OSError("write failed")

    monkeypatch.setattr(glasses.left, "connect", ok)
    monkeypatch.setattr(glasses.right, "connect", ok)
    glasses.right.write = boom  # type: ignore[method-assign]

    async def go():
        await glasses.connect()  # must not raise
        await glasses.disconnect()

    asyncio.run(go())


def make_one_armed(record: list[str], down: str):
    glasses = G1Glasses("L", "R", "left", "right")

    async def left_write(frame: bytes) -> None:
        if down == "left":
            raise RuntimeError("left: not connected")
        record.append("left")

    async def right_write(frame: bytes) -> None:
        if down == "right":
            raise RuntimeError("right: not connected")
        record.append("right")

    glasses.left.write = left_write  # type: ignore[method-assign]
    glasses.right.write = right_write  # type: ignore[method-assign]
    return glasses


def test_a_down_left_arm_does_not_starve_the_right_one():
    record: list[str] = []
    glasses = make_one_armed(record, down="left")
    asyncio.run(glasses.send_both(b"beat", gap_s=0))
    acked = asyncio.run(glasses.send_acked(b"page", timeout=0.05))
    assert record == ["right", "right"] and acked is False


def test_both_arms_down_is_an_error():
    import pytest

    glasses = G1Glasses("L", "R", "left", "right")

    async def boom(frame: bytes) -> None:
        raise RuntimeError("not connected")

    glasses.left.write = boom  # type: ignore[method-assign]
    glasses.right.write = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="both arms"):
        asyncio.run(glasses.send_both(b"beat", gap_s=0))
    with pytest.raises(RuntimeError, match="both arms"):
        asyncio.run(glasses.send_acked(b"page", timeout=0.05))


def test_set_mic_survives_a_down_right_arm():
    record: list[str] = []
    glasses = make_one_armed(record, down="right")
    assert asyncio.run(glasses.set_mic(False, timeout=0.05)) is False


# --- bitmap upload: packets, finish handshake, CRC, per arm ---


def make_bitmap_glasses(frames: dict[str, list[bytes]], *, ack: bool):
    glasses = G1Glasses("L", "R", "left", "right")

    def writer(side: str):
        async def write(frame: bytes) -> None:
            frames[side].append(frame)
            if not ack:
                return
            if frame[0] == 0x20:
                glasses._dispatch(G1Event(EventKind.BMP_END_OK, side, b"\x20\xc9"))
            elif frame[0] == 0x16:
                glasses._dispatch(
                    G1Event(EventKind.BMP_CRC_OK, side, b"\x16\x00\x00\x00\x00\xc9")
                )

        return write

    glasses.left.write = writer("left")  # type: ignore[method-assign]
    glasses.right.write = writer("right")  # type: ignore[method-assign]
    return glasses


def test_send_bitmap_streams_packets_then_finish_then_crc_to_both_arms():
    frames: dict[str, list[bytes]] = {"left": [], "right": []}
    glasses = make_bitmap_glasses(frames, ack=True)
    image = bytes(range(256)) * 4  # 1024 bytes -> 6 packets
    ok = asyncio.run(glasses.send_bitmap(image, packet_gap_s=0))
    assert ok is True
    for side in ("left", "right"):
        sent = frames[side]
        assert [f[0] for f in sent] == [0x15] * 6 + [0x20, 0x16]
        assert sent[0][2:6] == bytes([0x00, 0x1C, 0x00, 0x00])
        assert [f[1] for f in sent[:6]] == [0, 1, 2, 3, 4, 5]
        assert b"".join([sent[0][6:], *(f[2:] for f in sent[1:6])]) == image
        assert sent[6] == bytes([0x20, 0x0D, 0x0E])
        assert sent[7][0] == 0x16 and len(sent[7]) == 5


def test_send_bitmap_gives_up_when_the_finish_is_never_acknowledged():
    frames: dict[str, list[bytes]] = {"left": [], "right": []}
    glasses = make_bitmap_glasses(frames, ack=False)
    ok = asyncio.run(
        glasses.send_bitmap(
            bytes(300), packet_gap_s=0, ack_timeout=0.01, end_attempts=2
        )
    )
    assert ok is False
    # Two finish attempts, no CRC without the finish ack.
    assert [f[0] for f in frames["left"]] == [0x15, 0x15, 0x20, 0x20]
    assert all(f[0] != 0x16 for f in frames["left"])
