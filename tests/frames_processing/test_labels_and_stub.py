import numpy as np
import pytest

from src.emotion_labels import EMOTION_LABELS, LABEL_SET, NUM_EMOTIONS, label_of
from src.frames_processing.processing.emotion_recognition.emotion_recognizer import (
    STUB_MODEL, EmotionRecognizer, _models_pathes,
)
from src.frames_processing.processing.emotion_recognition.pipeline_utils import (
    get_emotion_smoothing_strategy, idx_to_label,
)


def test_label_set_is_crema_d():
    assert EMOTION_LABELS == ('anger', 'disgust', 'fear', 'happy', 'neutral', 'sad')
    assert NUM_EMOTIONS == 6 and LABEL_SET == 'crema-d-6'
    assert label_of(0) == 'anger' and label_of(5) == 'sad'
    assert label_of(6) == '' and label_of(-1) == ''
    assert idx_to_label(3) == 'happy' and idx_to_label(7) == ''


def test_stub_outputs_valid_probabilities():
    stub = EmotionRecognizer(STUB_MODEL)
    rng = np.random.default_rng(1)
    crops = [rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8) for h, w in ((90, 70), (40, 40), (120, 80))]
    probs = stub.predict(crops)
    assert probs.shape == (3, NUM_EMOTIONS)
    assert np.allclose(probs.sum(axis=1), 1.0) and (probs >= 0).all()
    assert stub.predict(crops[0]).shape == (1, NUM_EMOTIONS)


def test_stub_is_deterministic_and_input_dependent():
    a = np.full((64, 48, 3), 80, np.uint8)
    b = a.copy()
    b[..., 2] = 220
    p1, p2 = EmotionRecognizer(STUB_MODEL), EmotionRecognizer(STUB_MODEL, device='cuda')
    assert np.allclose(p1.predict(a), p2.predict(a))       # одинаково и для «cuda»
    assert not np.allclose(p1.predict(a), p1.predict(b))


def test_stub_works_with_smoothing():
    smooth = get_emotion_smoothing_strategy('ema_hysteresis')
    stub = EmotionRecognizer(STUB_MODEL)
    cached = {"emotion_probs": np.zeros(NUM_EMOTIONS), "emotion_label": None, "emotion_switch_counter": 0}
    crop = np.full((50, 40, 3), 120, np.uint8)
    for _ in range(5):
        smooth(cached, stub.predict(crop)[0], frequency=5, ema_coef=0.8)
    assert cached["emotion_label"] in EMOTION_LABELS
    assert cached["emotion_probs"].shape == (NUM_EMOTIONS,)


def test_old_8_class_onnx_model_is_rejected():
    import os
    path = _models_pathes['cpu=resnet-18']
    if not os.path.exists(path):
        pytest.skip('resnet_18.onnx is not available locally')
    with pytest.raises(ValueError, match='classes'):
        EmotionRecognizer('resnet-18', 'cpu', num_threads=1)
