"""Voice input: collect the microphone stream between long-press start and stop.

Decoding (LC3 -> PCM) and transcription are pluggable: a `Transcriber` takes
the raw LC3 payload bytes and returns text. Until one is configured the hub
falls back to typed questions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from .micstream import MicStats
from .protocol import G1Event

PACKET_SECONDS = 0.1  # one 0xF1 packet = 100 ms of audio (10 x 10 ms LC3 frames)
MIN_UTTERANCE_S = 0.4  # shorter than this is a slip of the finger, not speech
QUIET_RMS = 0.012  # same floor as stt.SILENCE_RMS, measured on a real G1 recording


def packet_rms(payload: bytes) -> float:
    """Loudness of one packet, for deciding when the wearer has stopped talking."""
    from .audio import decode_lc3, rms  # native decoder; imported lazily

    return rms(decode_lc3(payload))


Transcriber = Callable[[bytes], Awaitable[str]]


@dataclass(frozen=True)
class Capture:
    payloads: tuple[bytes, ...] = ()
    stats: MicStats = MicStats()

    def add(self, event: G1Event) -> "Capture":
        return replace(
            self,
            payloads=(*self.payloads, event.payload),
            stats=self.stats.add(event),
        )

    @property
    def audio(self) -> bytes:
        return b"".join(self.payloads)

    @property
    def seconds(self) -> float:
        return len(self.payloads) * PACKET_SECONDS

    @property
    def too_short(self) -> bool:
        return self.seconds < MIN_UTTERANCE_S
