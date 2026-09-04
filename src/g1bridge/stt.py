"""Speech-to-text for the hub: whisper.cpp on Metal, offline, no API key.

`WhisperTranscriber` satisfies `voice.Transcriber`: raw LC3 payload bytes in,
text out. The model loads lazily (first ever run downloads it) and runs on a
worker thread so the BLE heartbeat keeps going while it thinks.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, Protocol

import numpy as np

from .audio import SAMPLE_RATE, decode_lc3, rms, trim_silence

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "small.en"
MIN_SPEECH_SAMPLES = SAMPLE_RATE // 2  # under 0.5 s cannot hold a question
# Measured on a real G1 recording (2026-09-03): the quiet-room floor sits around
# 0.005-0.01 rms per 100 ms window; speech windows run 0.03-0.1.
SILENCE_RMS = 0.012
MAX_SPEECH_S = 30  # the firmware's own Even AI cap; whisper degrades past it too
# whisper labels non-speech as "[SOUND]", "[BLANK_AUDIO]", "(dog panting)", ...
_NON_SPEECH = re.compile(r"\[[^\]]*\]|\([^)]*\)")


class SpeechModel(Protocol):
    def transcribe(self, media: np.ndarray, **params) -> list: ...


ModelLoader = Callable[[str], SpeechModel]


def load_whisper(model_name: str) -> SpeechModel:
    from pywhispercpp.model import Model

    return Model(model_name, redirect_whispercpp_logs_to=False, print_progress=False)


def transcribe_pcm(
    model: SpeechModel, pcm: np.ndarray, max_seconds: float | None = MAX_SPEECH_S
) -> str:
    """Gate silence and stubs, then run the model. Pure apart from the model."""
    speech = trim_silence(pcm, threshold=SILENCE_RMS)
    if max_seconds is not None:
        speech = speech[: int(max_seconds * SAMPLE_RATE)]
    logger.info(
        "audio %.1fs rms=%.4f -> %.1fs above the floor",
        pcm.size / SAMPLE_RATE,
        rms(pcm),
        speech.size / SAMPLE_RATE,
    )
    if speech.size < MIN_SPEECH_SAMPLES:
        return ""
    segments = model.transcribe(speech)
    raw = " ".join(segment.text.strip() for segment in segments if segment.text)
    text = clean_transcript(raw)
    if raw and not text:
        logger.info("whisper heard only non-speech: %r", raw)
    return text


def clean_transcript(text: str) -> str:
    """Collapse whitespace and drop whisper's bracketed non-speech labels."""
    return " ".join(_NON_SPEECH.sub(" ", text).split())


class WhisperTranscriber:
    def __init__(
        self, model_name: str = DEFAULT_MODEL, loader: ModelLoader = load_whisper
    ):
        self.model_name = model_name
        self._loader = loader
        self._model: SpeechModel | None = None

    def warm(self) -> None:
        """Load the model and run one silent second so the first real request is fast."""
        model = self._get_model()
        model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))

    def _get_model(self) -> SpeechModel:
        if self._model is None:
            logger.info("loading speech model %s", self.model_name)
            self._model = self._loader(self.model_name)
        return self._model

    def transcribe_lc3(self, payloads: bytes) -> str:
        return transcribe_pcm(self._get_model(), decode_lc3(payloads))

    async def __call__(self, payloads: bytes) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.transcribe_lc3, payloads)
