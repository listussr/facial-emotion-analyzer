import numpy as np
import pytest

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import NUM_EMOTIONS
from src.frames_processing.processing.emotion_recognition.clip_model import STUB_MODEL
from src.frames_processing.processing.emotion_recognition.clip_model import available_clip_models
from src.frames_processing.processing.emotion_recognition.clip_model import load_clip_model
from src.frames_processing.processing.emotion_recognition.clip_window import BoxStabilizer
from src.frames_processing.processing.emotion_recognition.clip_window import ClipConfig
from src.frames_processing.processing.emotion_recognition.clip_window import TrackClip
from src.frames_processing.processing.emotion_recognition.clip_window import WindowSmoother
from src.frames_processing.processing.emotion_recognition.clip_window import crop_face
from src.frames_processing.processing.emotion_recognition.clip_window import frame_times


def feed(clip, pts_list):
    for pts in pts_list:
        if clip.wants_frame(pts):
            clip.add(pts, np.full(4, pts, np.float32))


def test_config_validation():
    with pytest.raises(ValueError):
        ClipConfig(n_frames=10)
    with pytest.raises(ValueError):
        ClipConfig(hop_ms=0)
    c = ClipConfig(n_frames=16)
    assert c.sample_interval_ms == 187.5 and c.min_frames == 10


def test_frame_times_match_emotion_kit():
    from src.emotion_kit.preprocess import frame_times as kit_times
    for n in (8, 16):
        assert np.allclose(frame_times(n), kit_times(n)[0])


def test_sampling_follows_pts_not_frame_rate():
    clip = TrackClip(ClipConfig(n_frames=8))
    feed(clip, range(0, 3000, 40))
    assert 7 <= len(clip) <= 9


def test_window_needs_coverage_then_runs_every_hop():
    clip = TrackClip(ClipConfig(n_frames=8, window_ms=3000, hop_ms=1000))
    got  = []
    for pts in range(0, 6000, 50):
        feed(clip, [pts])
        w = clip.window(pts)
        if w is not None:
            got.append((pts, w))
    first_pts, first = got[0]
    assert first["frames"] >= ClipConfig().min_frames and first_pts < 2000
    assert len(got) >= 4 and [p - first_pts for p, _ in got] == [1000 * i for i in range(len(got))]
    for pts, w in got:
        assert w["feats"].shape == (8, 4) and np.allclose(w["times"], frame_times(8))
        assert pts - 3000 < w["start_ms"] <= w["end_ms"] <= pts
        assert np.all(np.diff(w["feats"][:, 0]) >= 0)


def test_old_features_leave_window_and_gap_waits_for_coverage():
    clip = TrackClip(ClipConfig(n_frames=8))
    feed(clip, range(0, 3000, 100))
    assert clip.window(2900) is not None
    feed(clip, [8000])
    assert len(clip) == 1 and clip.window(8000) is None


def test_pts_going_back_restarts_clip():
    clip = TrackClip(ClipConfig(n_frames=8))
    feed(clip, range(10000, 13000, 100))
    assert clip.wants_frame(0)
    feed(clip, [0])
    assert len(clip) == 1


def test_box_stabilizer_is_running_median():
    s = BoxStabilizer(history=3)
    s.update((10, 10, 50, 50))
    s.update((12, 10, 50, 50))
    assert s.update((20, 10, 50, 50)) == (12, 10, 50, 50)
    assert s.resets == 0


def test_box_stabilizer_resets_on_jump():
    s = BoxStabilizer(history=7)
    for _ in range(5):
        s.update((10, 10, 50, 50))
    assert s.update((300, 200, 60, 60)) == (300, 200, 60, 60)
    assert s.resets == 1


def test_crop_face_matches_emotion_kit():
    from src.emotion_kit.preprocess import FaceCropper
    rng       = np.random.default_rng(0)
    frame_bgr = rng.integers(0, 255, size=(240, 320, 3), dtype=np.uint8)
    box       = (100, 60, 80, 90)
    ours      = crop_face(frame_bgr, box)
    cropper   = FaceCropper.__new__(FaceCropper)
    import cv2
    cropper.cv2 = cv2
    kit         = cropper.crop(frame_bgr[None, ..., ::-1].copy(), box)[0]
    assert ours.shape == (224, 224, 3) and np.array_equal(ours, kit)
    assert crop_face(frame_bgr, (500, 500, 10, 10)) is None


def test_smoother_ema_and_undetermined():
    s    = WindowSmoother(keep=0.5, undetermined_below=0.4)
    flat = np.full(NUM_EMOTIONS, 1 / NUM_EMOTIONS)
    assert s.update(flat)[0] is None
    happy        = np.eye(NUM_EMOTIONS)[3]
    label, probs = s.update(happy)
    assert label == "happy" and np.isclose(probs[3], 0.5 + 0.5 / NUM_EMOTIONS)


def test_stub_clip_model_contract():
    assert STUB_MODEL in available_clip_models()
    m     = load_clip_model(STUB_MODEL)
    faces = np.random.default_rng(0).integers(0, 255, size=(5, 224, 224, 3), dtype=np.uint8)
    feats = m.encode(faces)
    assert feats.shape == (5, m.feat_dim)
    out = m.predict(feats, frame_times(8)[:5])
    assert out["logits"].shape == out["probs"].shape == (NUM_EMOTIONS,) and out["emb"].shape == (256,)
    assert np.isclose(out["probs"].sum(), 1.0)
    assert len(EMOTION_LABELS) == NUM_EMOTIONS


def test_unknown_model_and_device_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        load_clip_model("resnet-18")
    with pytest.raises(ValueError, match="cpu"):
        load_clip_model("crema-swin-int8", device="cuda")
