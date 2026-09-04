from pathlib import Path

from g1bridge.micstream import MicStats, append_payloads
from g1bridge.protocol import EventKind, G1Event


def mic(seq: int, size: int = 200) -> G1Event:
    return G1Event(EventKind.MIC_DATA, "right", b"", seq=seq, payload=bytes(size))


def test_stats_count_bytes_and_sequence_gaps():
    stats = MicStats()
    for seq in (10, 11, 13, 14):  # 12 missing
        stats = stats.add(mic(seq))
    assert (stats.packets, stats.bytes, stats.dropped) == (4, 800, 1)
    assert (stats.first_seq, stats.last_seq) == (10, 14)
    assert "x4" in stats.summary("right") and "1 dropped" in stats.summary("right")


def test_sequence_wraps_at_255_without_a_false_drop():
    stats = MicStats().add(mic(254)).add(mic(255)).add(mic(0)).add(mic(1))
    assert stats.dropped == 0


def test_stats_are_immutable():
    empty = MicStats()
    empty.add(mic(1))
    assert empty.packets == 0 and empty.summary("right") == ""


def test_append_payloads_strips_headers(tmp_path: Path):
    path = tmp_path / "mic.lc3"
    events = [mic(1, 200), mic(2, 200)]
    assert append_payloads(path, events) == 400
    assert append_payloads(path, [mic(3, 200)]) == 200
    assert path.stat().st_size == 600
