from g1bridge.protocol import EventKind, G1Event
from g1bridge.voice import Capture


def packet(seq: int) -> G1Event:
    return G1Event(
        EventKind.MIC_DATA, "right", b"", seq=seq, payload=bytes([seq]) * 200
    )


def test_capture_accumulates_immutably():
    empty = Capture()
    five = empty
    for seq in range(5):
        five = five.add(packet(seq))
    assert empty.seconds == 0 and empty.too_short
    assert five.seconds == 0.5 and not five.too_short
    assert five.audio[:200] == bytes(200) and five.audio[-200:] == bytes([4]) * 200
    assert five.stats.packets == 5 and five.stats.dropped == 0
