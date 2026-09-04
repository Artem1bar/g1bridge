import numpy as np
import pytest

from g1bridge.audio import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, decode_lc3, rms

lc3 = pytest.importorskip("lc3")


def encode_tone(seconds: float, hz: float = 440.0) -> tuple[bytes, np.ndarray]:
    encoder = lc3.Encoder(10_000, SAMPLE_RATE)
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    pcm = (np.sin(2 * np.pi * hz * t) * 12_000).astype(np.int16)
    frames = [
        encoder.encode(pcm[i : i + FRAME_SAMPLES].tobytes(), FRAME_BYTES, bit_depth=16)
        for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES)
    ]
    return b"".join(frames), pcm.astype(np.float32) / 32768.0


def test_round_trip_keeps_the_tone():
    payload, original = encode_tone(0.5)
    decoded = decode_lc3(payload)
    assert decoded.dtype == np.float32 and decoded.size == original.size
    # liblc3 adds a small algorithmic delay; compare energy and dominant pitch.
    assert abs(rms(decoded) - rms(original)) < 0.05
    spectrum = np.abs(np.fft.rfft(decoded[SAMPLE_RATE // 10 :]))
    peak_hz = np.argmax(spectrum) * SAMPLE_RATE / (decoded.size - SAMPLE_RATE // 10)
    assert abs(peak_hz - 440) < 10


def test_partial_frame_is_dropped_and_empty_is_empty():
    payload, _ = encode_tone(0.1)
    assert decode_lc3(payload + b"\x01\x02\x03").size == decode_lc3(payload).size
    assert decode_lc3(b"").size == 0
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0


def test_trim_silence_keeps_speech_with_padding():
    from g1bridge.audio import trim_silence

    win = 1600
    quiet = np.zeros(win * 10, dtype=np.float32)
    loud = np.full(win * 3, 0.1, dtype=np.float32)
    pcm = np.concatenate([quiet, loud, quiet])
    trimmed = trim_silence(pcm, threshold=0.01)
    assert trimmed.size == win * (3 + 2 * 2)  # speech + 2 windows of pad each side
    assert trim_silence(quiet, threshold=0.01).size == 0
    assert trim_silence(np.zeros(0, dtype=np.float32), threshold=0.01).size == 0
    assert trim_silence(loud, threshold=0.01).size == loud.size
