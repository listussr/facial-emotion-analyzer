"""Голосовая модель CREMA-D в аудиопайплайне; пропускаются без файлов моделей."""

import numpy as np
import pytest

from src.emotion_kit.backends import MODELS
from src.emotion_labels import NUM_EMOTIONS
from .helpers import chunks
from .helpers import silence
from .helpers import tone

pytestmark = pytest.mark.skipif(
    not (MODELS / "audio_wavlm_base_plus_int8.onnx").exists(),
    reason=f"CREMA-D models are not available in {MODELS}",
)


@pytest.fixture(scope="module")
def recognizer():
    from src.audio_processing.recognizer import VoiceEmotionRecognizer
    return VoiceEmotionRecognizer("crema-wavlm-int8", num_threads=2)


def test_recognizer_equals_kit(recognizer):
    """Обёртка = prepare_audio + AudioONNX.predict из emotion_kit."""
    from src.emotion_kit.backends import AudioONNX
    from src.emotion_kit.preprocess import prepare_audio
    rng = np.random.default_rng(0)
    w = (tone(3000, 180) * (0.5 + 0.5 * np.sin(np.arange(48000) / 2000))
         + 0.01 * rng.standard_normal(48000)).astype(np.float32)
    [ours] = recognizer.predict([w])
    ref    = AudioONNX("int8", threads=2).predict(prepare_audio(w, 16000))
    assert np.allclose(ours["logits"], ref["logits"], atol=1e-4)
    assert ours["emb"].shape == (256,) and np.isclose(ours["probs"].sum(), 1.0)
    assert recognizer.name == "crema-wavlm-int8"


def test_no_speech_by_model_preprocessing(recognizer):
    """Тишина и слишком короткий звук: prepare_audio говорит «речи нет» → None."""
    short = np.concatenate([silence(1400), tone(50), silence(1500)])
    out   = recognizer.predict([silence(3000), short])
    assert out == [None, None]


def test_pipeline_with_crema_model():
    from src.audio_processing.pipeline import AudioPipeline
    from src.session_config import AudioSettings
    p = AudioPipeline(None, AudioSettings(model="crema-wavlm-int8", device="cpu", vad_threshold=0.3,
                                          window_ms=3000, hop_ms=1000), num_threads=2)
    sig = np.concatenate([tone(4000, 200), silence(3000)])
    res = []
    for m in chunks(sig):
        res += p.handle_chunk(m, now=m["capture_wall_ts"])
    speech = [r for r in res if r.is_speech]
    assert speech and all(r.model == "crema-wavlm-int8" for r in res)
    assert all(len(r.logits) == NUM_EMOTIONS and len(r.emb) == 256 for r in speech)
    assert not res[-1].is_speech and res[-1].emb is None
