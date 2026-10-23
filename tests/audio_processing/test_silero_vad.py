"""Silero VAD на синтетических сигналах; пропускается без файла модели."""

import numpy as np
import pytest

from pathlib import Path

from src.audio_processing.vad import SileroVAD

MODEL = Path(__file__).resolve().parents[2] / "models" / "vad" / "silero_vad.onnx"

pytestmark = pytest.mark.skipif(not MODEL.exists(), reason="Silero VAD model not downloaded")


@pytest.fixture(scope="module")
def vad():
    return SileroVAD(str(MODEL))


def test_noise_tone_and_silence_are_not_speech(vad):
    rng = np.random.default_rng(0)
    t   = np.arange(48000) / 16000
    for signal in (rng.normal(0, 0.1, 48000), 0.3 * np.sin(2 * np.pi * 440 * t), np.zeros(48000)):
        assert vad.speech_prob(signal.astype(np.float32)) < 0.1


def test_frames_and_short_input(vad):
    assert vad.frame_probs(np.zeros(1600, np.float32)).shape == (3,)
    assert vad.speech_prob(np.zeros(100, np.float32)) == 0.0
    with pytest.raises(ValueError):
        SileroVAD(str(MODEL), sample_rate=8000)
