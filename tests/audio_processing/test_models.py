import numpy as np
import pytest

from src.audio_processing.contracts import AudioAnalytics
from src.audio_processing.recognizer import STUB_MODEL, VoiceEmotionRecognizer
from src.audio_processing.smoothing import VoiceSmoother
from src.audio_processing.vad import EnergyVAD
from src.emotion_labels import EMOTION_LABELS, LABEL_SET, NUM_EMOTIONS

from .helpers import RATE, silence, tone


def test_vad_silence_tone_and_partial():
    vad = EnergyVAD(RATE)
    assert vad.speech_prob(silence(1000)) == 0.0
    assert vad.speech_prob(tone(1000, amp=0.1)) == 1.0
    assert vad.speech_prob(tone(1000, amp=0.001)) == 0.0          # -63 dBFS — тише порога
    half = np.concatenate([tone(500), silence(500)])
    assert abs(vad.speech_prob(half) - 0.5) < 0.05
    assert vad.speech_prob(np.zeros(10, np.float32)) == 0.0        # короче кадра


def test_stub_probabilities_and_determinism():
    rec = VoiceEmotionRecognizer(STUB_MODEL)
    windows = [tone(2000, 200), tone(2000, 3000), tone(2000, 200)]
    p = rec.predict(windows)
    assert p.shape == (3, NUM_EMOTIONS)
    assert np.allclose(p.sum(axis=1), 1) and (p >= 0).all()
    assert np.allclose(p[0], p[2]) and not np.allclose(p[0], p[1])
    assert rec.predict([]).shape == (0, NUM_EMOTIONS)


def test_unknown_and_missing_models():
    with pytest.raises(ValueError):
        VoiceEmotionRecognizer('no-such-model')
    with pytest.raises(FileNotFoundError):
        VoiceEmotionRecognizer('onnx:missing/model.onnx')


def test_smoother_first_prediction_sets_label():
    s = VoiceSmoother()
    p = np.eye(NUM_EMOTIONS)[3]
    s.update(p)
    assert s.label == EMOTION_LABELS[3] and np.allclose(s.probs, p)


def test_smoother_needs_repeated_evidence_to_switch():
    s = VoiceSmoother(ema_coef=0.5, switch_windows=2)
    s.update(np.eye(NUM_EMOTIONS)[0])
    s.update(np.eye(NUM_EMOTIONS)[1])          # одно окно — метка ещё держится
    assert s.label == EMOTION_LABELS[0]
    for _ in range(3):
        s.update(np.eye(NUM_EMOTIONS)[1])
    assert s.label == EMOTION_LABELS[1]


def test_contract_roundtrip():
    r = AudioAnalytics(camera_id='c', window_seq=0, pts_start_ms=0, pts_end_ms=2000,
                       capture_wall_ts=1.0, processed_wall_ts=1.1, speech_prob=0.0, is_speech=False)
    d = r.to_dict()
    assert d['label_set'] == LABEL_SET and d['emotion_labels'] == list(EMOTION_LABELS)
    assert d['scores'] is None and d['schema_version'] == 1
    assert AudioAnalytics.from_dict({**d, 'future': 1}) == r
