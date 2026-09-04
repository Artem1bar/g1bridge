import asyncio
import os

import numpy as np
import pytest

from g1bridge.audio import SAMPLE_RATE
from g1bridge.stt import SILENCE_RMS, WhisperTranscriber, transcribe_pcm


class Segment:
    def __init__(self, text: str):
        self.text = text


class StubModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, media, **params):
        self.calls += 1
        return [Segment("  Testing "), Segment("one two   three. ")]


def test_gate_skips_short_and_silent_audio():
    model = StubModel()
    assert transcribe_pcm(model, np.zeros(SAMPLE_RATE // 4, dtype=np.float32)) == ""
    quiet = np.full(SAMPLE_RATE, SILENCE_RMS / 2, dtype=np.float32)
    assert transcribe_pcm(model, quiet) == ""
    assert model.calls == 0
    loud = np.full(SAMPLE_RATE, 0.1, dtype=np.float32)
    assert transcribe_pcm(model, loud) == "Testing one two three."


def test_gate_trims_silence_and_caps_length():
    class Recorder(StubModel):
        def transcribe(self, media, **params):
            self.last = media
            return super().transcribe(media)

    model = Recorder()
    silence = np.zeros(SAMPLE_RATE * 5, dtype=np.float32)
    speech = np.full(SAMPLE_RATE, 0.1, dtype=np.float32)
    transcribe_pcm(model, np.concatenate([silence, speech, silence]))
    assert model.last.size < SAMPLE_RATE * 2  # the 10 s of silence is gone
    transcribe_pcm(model, np.full(SAMPLE_RATE * 40, 0.1, dtype=np.float32))
    assert model.last.size == SAMPLE_RATE * 30


def test_transcriber_loads_lazily_and_runs_off_the_loop():
    loads: list[str] = []

    def loader(name: str):
        loads.append(name)
        return StubModel()

    transcriber = WhisperTranscriber("tiny.en", loader=loader)
    assert loads == []
    lc3 = pytest.importorskip("lc3")
    encoder = lc3.Encoder(10_000, SAMPLE_RATE)
    tone = (np.sin(np.arange(160) / 3) * 12000).astype(np.int16).tobytes()
    payload = encoder.encode(tone, 20, bit_depth=16) * 100  # 1 s of tone
    assert asyncio.run(transcriber(payload)) == "Testing one two three."
    assert loads == ["tiny.en"]


@pytest.mark.skipif(
    not os.environ.get("G1_STT_TESTS"), reason="needs the whisper model"
)
def test_real_model_transcribes_recorded_clip():
    from pathlib import Path

    clip = Path.home() / "g1-mic.lc3"
    if not clip.exists():
        pytest.skip("no recording")
    text = WhisperTranscriber().transcribe_lc3(clip.read_bytes())
    assert text


def test_non_speech_labels_are_dropped():
    from g1bridge.stt import clean_transcript

    assert clean_transcript("[SOUND]") == ""
    assert clean_transcript(" (dog panting) [BLANK_AUDIO] ") == ""
    assert clean_transcript("Hello [clicking] there  world.") == "Hello there world."


def test_keep_warm_runs_silent_inferences_and_yields_to_real_ones():
    model = StubModel()
    transcriber = WhisperTranscriber("tiny.en", loader=lambda name: model)

    async def go():
        task = asyncio.create_task(transcriber.keep_warm(interval_s=0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert transcriber.warm_ups >= 3 and model.calls == transcriber.warm_ups


def test_metal_keep_alive_is_set_before_the_model_loads(monkeypatch):
    import os
    import sys
    import types

    from g1bridge import stt

    monkeypatch.delenv("GGML_METAL_RESIDENCY_KEEP_ALIVE_S", raising=False)
    seen: dict[str, str | None] = {}

    class FakeModel:
        def __init__(self, name, **kwargs):
            seen["value"] = os.environ.get("GGML_METAL_RESIDENCY_KEEP_ALIVE_S")

    fake = types.ModuleType("pywhispercpp.model")
    fake.Model = FakeModel
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", fake)
    stt.load_whisper("tiny.en")
    assert seen["value"] == stt.RESIDENCY_KEEP_ALIVE_S
