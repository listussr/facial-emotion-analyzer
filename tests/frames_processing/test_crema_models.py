"""Модели CREMA-D из models/crema; пропускаются без файлов моделей."""

import numpy as np
import pytest

from src.emotion_kit.backends import MODELS

pytestmark = pytest.mark.skipif(
    not (MODELS / "video_frame_encoder_swin_t_int8.onnx").exists(),
    reason=f"CREMA-D models are not available in {MODELS}",
)


@pytest.fixture(scope="module")
def selftest():
    return np.load(MODELS / "selftest.npz")


@pytest.mark.parametrize("precision", ["fp32", "int8"])
def test_kit_video_matches_reference(selftest, precision):
    """usage_rules.md, п. 2: fp32 совпадает с эталоном до ~1e-3, у int8 совпадает класс."""
    from src.emotion_kit.backends import VideoONNX
    out = VideoONNX(precision, threads=2).predict(selftest["frames"], selftest["times"])
    ref = selftest["video_logits"]
    assert int(out["logits"].argmax()) == int(ref.argmax())
    if precision == "fp32":
        assert np.abs(out["logits"] - ref).max() < 2e-3


@pytest.mark.parametrize("precision", ["fp32", "int8"])
def test_kit_audio_matches_reference(selftest, precision):
    from src.emotion_kit.backends import AudioONNX
    out = AudioONNX(precision, threads=2).predict(selftest["wave"])
    ref = selftest["audio_logits"]
    assert int(out["logits"].argmax()) == int(ref.argmax())
    assert out["emb"].shape == (256,)
    if precision == "fp32":
        assert np.abs(out["logits"] - ref).max() < 2e-3


def test_clip_model_equals_kit_pipeline():
    """Кодирование по кадрам + голова в обёртке == VideoONNX.predict на тех же кропах."""
    from src.emotion_kit.backends import VideoONNX
    from src.emotion_kit.preprocess import faces_to_input
    from src.frames_processing.processing.emotion_recognition.clip_model import load_clip_model
    from src.frames_processing.processing.emotion_recognition.clip_window import frame_times

    faces = np.random.default_rng(0).integers(0, 255, size=(8, 224, 224, 3), dtype=np.uint8)
    times = frame_times(8)
    for name, precision, atol in (("crema-swin", "fp32", 1e-4), ("crema-swin-int8", "int8", 5e-2)):
        m     = load_clip_model(name, num_threads=2)
        feats = np.concatenate([m.encode(faces[:3]), m.encode(faces[3:])])
        ours  = m.predict(feats, times)
        ref   = VideoONNX(precision, threads=2).predict(faces_to_input(faces), times)
        assert np.allclose(ours["logits"], ref["logits"], atol=atol), name
        assert ours["probs"].argmax() == ref["probs"].argmax()
        assert ours["emb"].shape == (256,) and np.isclose(ours["probs"].sum(), 1.0)


def test_yunet_detector_runs_and_filters():
    from src.frames_processing.processing.face_detection import FaceDetector
    det   = FaceDetector(0.6)
    blank = np.zeros((720, 1280, 3), np.uint8)
    assert det.detect(blank) == []
    assert det.detect(np.zeros((0, 0, 3), np.uint8)) == []
