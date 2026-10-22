import numpy as np
import pytest

from src.audio_processing.contracts import AudioAnalytics
from src.audio_processing.recognizer import STUB_MODEL
from src.audio_processing.recognizer import VoiceEmotionRecognizer
from src.audio_processing.smoothing import VoiceSmoother
from src.audio_processing.vad import EnergyVAD
from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET
from src.emotion_labels import NUM_EMOTIONS
from .helpers import RATE
from .helpers import silence
from .helpers import tone


def test_vad_silence_tone_and_partial():
    vad = EnergyVAD(RATE)
    assert vad.speech_prob(silence(1000)) == 0.0
    assert vad.speech_prob(tone(1000, amp=0.1)) == 1.0
    assert vad.speech_prob(tone(1000, amp=0.001)) == 0.0
    half = np.concatenate([tone(500), silence(500)])
    assert abs(vad.speech_prob(half) - 0.5) < 0.05
    assert vad.speech_prob(np.zeros(10, np.float32)) == 0.0


def test_stub_probabilities_and_determinism():
    rec     = VoiceEmotionRecognizer(STUB_MODEL)
    windows = [tone(2000, 200), tone(2000, 3000), tone(2000, 200)]
    out     = rec.predict(windows)
    p       = np.stack([o["probs"] for o in out])
    assert p.shape == (3, NUM_EMOTIONS)
    assert np.allclose(p.sum(axis=1), 1) and (p >= 0).all()
    assert np.allclose(p[0], p[2]) and not np.allclose(p[0], p[1])
    assert all(o["logits"].shape == (NUM_EMOTIONS,) and o["emb"].shape == (256,) for o in out)
    assert rec.predict([]) == []


def test_unknown_and_missing_models():
    with pytest.raises(ValueError):
        VoiceEmotionRecognizer("no-such-model")
    with pytest.raises(ValueError, match="cpu"):
        VoiceEmotionRecognizer("crema-wavlm-int8", device="cuda")
    with pytest.raises(FileNotFoundError):
        VoiceEmotionRecognizer("onnx:missing/model.onnx")


def test_smoother_undetermined_when_uncertain():
    s = VoiceSmoother()
    s.update(np.full(NUM_EMOTIONS, 1 / NUM_EMOTIONS))
    assert s.label is None and s.probs is not None
    s.update(np.eye(NUM_EMOTIONS)[2])
    assert s.label == EMOTION_LABELS[2]


def test_smoother_first_prediction_sets_label():
    s = VoiceSmoother()
    p = np.eye(NUM_EMOTIONS)[3]
    s.update(p)
    assert s.label == EMOTION_LABELS[3] and np.allclose(s.probs, p)


def test_smoother_needs_repeated_evidence_to_switch():
    s = VoiceSmoother(ema_coef=0.5, switch_windows=2)
    s.update(np.eye(NUM_EMOTIONS)[0])
    s.update(np.eye(NUM_EMOTIONS)[1])
    assert s.label == EMOTION_LABELS[0]
    for _ in range(3):
        s.update(np.eye(NUM_EMOTIONS)[1])
    assert s.label == EMOTION_LABELS[1]


def test_contract_roundtrip():
    r = AudioAnalytics(camera_id="c", window_seq=0, pts_start_ms=0, pts_end_ms=2000,
                       capture_wall_ts=1.0, processed_wall_ts=1.1, speech_prob=0.0, is_speech=False)
    d = r.to_dict()
    assert d["label_set"] == LABEL_SET and d["emotion_labels"] == list(EMOTION_LABELS)
    assert d["scores"] is None and d["logits"] is None and d["emb"] is None
    assert d["schema_version"] == 2
    assert AudioAnalytics.from_dict({**d, "future": 1}) == r
