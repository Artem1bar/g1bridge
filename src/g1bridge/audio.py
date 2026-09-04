"""LC3 audio from the glasses -> PCM samples.

G1 microphone stream (verified against the official EvenDemoApp decoder and
MentraOS's G1 driver, 2026-09-03): 16 kHz mono, 10 ms frames of 20 bytes
(16 kbit/s), ten frames per 0xF1 packet. Decoding uses Google's liblc3 through
the `lc3py` binding; the encoder is only used in tests.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000
FRAME_US = 10_000
FRAME_BYTES = 20
FRAME_SAMPLES = 160  # 10 ms at 16 kHz
INT16_SCALE = 32_768.0


def decode_lc3(payloads: bytes) -> np.ndarray:
    """Decode concatenated 20-byte LC3 frames into float32 samples in -1..1.

    A trailing partial frame is dropped. Returns an empty array for no input.
    """
    import lc3  # heavy native import, kept out of module import time

    whole_frames = len(payloads) // FRAME_BYTES
    if whole_frames == 0:
        return np.zeros(0, dtype=np.float32)
    decoder = lc3.Decoder(FRAME_US, SAMPLE_RATE)
    chunks = [
        np.frombuffer(
            decoder.decode(payloads[i : i + FRAME_BYTES], bit_depth=16), dtype=np.int16
        )
        for i in range(0, whole_frames * FRAME_BYTES, FRAME_BYTES)
    ]
    return (np.concatenate(chunks).astype(np.float32) / INT16_SCALE).copy()


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def trim_silence(
    samples: np.ndarray,
    *,
    threshold: float,
    window: int = FRAME_SAMPLES * 10,
    pad_windows: int = 2,
) -> np.ndarray:
    """Cut leading/trailing stretches quieter than `threshold` (100 ms windows).

    Keeps `pad_windows` of context on each side so clipped consonants survive.
    Returns an empty array when nothing rises above the threshold.
    """
    if samples.size == 0:
        return samples
    count = (samples.size + window - 1) // window
    loud = [
        i
        for i in range(count)
        if rms(samples[i * window : (i + 1) * window]) >= threshold
    ]
    if not loud:
        return samples[:0]
    start = max(loud[0] - pad_windows, 0) * window
    end = min(loud[-1] + 1 + pad_windows, count) * window
    return samples[start:end]
