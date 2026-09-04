"""Bookkeeping for the glasses' microphone stream (0xF1 packets from the right arm).

Observed on hardware 2026-09-03: after a long-press on the left temple the right
arm streams 200-byte LC3 payloads, one per 100 ms, sequence byte +1 per packet
(wrapping at 255), with no mic-enable command from the app. Ten packets a second
at 400 hex characters each drown every other event, so `g1 events` folds them
into one summary line per second and can save the raw payloads for offline
decoding.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .protocol import G1Event


@dataclass(frozen=True)
class MicStats:
    packets: int = 0
    bytes: int = 0
    first_seq: int | None = None
    last_seq: int | None = None
    dropped: int = 0

    def add(self, event: G1Event) -> "MicStats":
        """Account for one mic packet; counts sequence gaps as drops."""
        seq = event.seq if event.seq is not None else 0
        dropped = self.dropped
        if self.last_seq is not None:
            gap = (seq - self.last_seq - 1) % 256
            dropped += gap
        return replace(
            self,
            packets=self.packets + 1,
            bytes=self.bytes + len(event.payload),
            first_seq=self.first_seq if self.first_seq is not None else seq,
            last_seq=seq,
            dropped=dropped,
        )

    def summary(self, side: str) -> str:
        if self.packets == 0:
            return ""
        lost = f", {self.dropped} dropped" if self.dropped else ""
        return (
            f"  [{side:>5}] mic_data x{self.packets} "
            f"(seq {self.first_seq}-{self.last_seq}, {self.bytes} B{lost})"
        )


def append_payloads(path: Path, events: list[G1Event]) -> int:
    """Append raw LC3 payloads (header stripped) to `path`; returns bytes written."""
    data = b"".join(event.payload for event in events)
    with path.open("ab") as handle:
        handle.write(data)
    return len(data)
