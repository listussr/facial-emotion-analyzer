"""
FusionService без Kafka: результаты видео и аудио подаются напрямую.
"""

import numpy as np
import pytest

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import NUM_EMOTIONS
from src.fusion.service import MODE_MULTI_FACE
from src.fusion.service import MODE_NO_FACE
from src.fusion.service import MODE_SINGLE_FACE
from src.fusion.service import FusionService
from src.session_config import FusionSettings
from src.session_config import SessionConfig
from src.session_config import SessionConfigStore

DEFAULTS = FusionSettings(method="stack_hidden", voice_max_age_ms=3000, min_overlap_ms=1000)
T0       = 1000.0


def onehot(i):
    return [float(x) for x in np.eye(NUM_EMOTIONS)[i]]


def logits(i, strength=6.0):
    return [float(x) for x in np.eye(NUM_EMOTIONS)[i] * strength]


def face(track_id, idx=0, window_seq=None, end=3000):
    f = {"bbox": [0, 0, 10, 10], "track_id": track_id, "face_id": f"p{track_id}",
         "emotion": EMOTION_LABELS[idx], "emotion_scores": onehot(idx)}
    if window_seq is not None:
        f["emotion_window"] = {"seq": window_seq, "start_pts_ms": end - 3000, "end_pts_ms": end,
                               "frames": 8, "model": "crema-swin-int8", "logits": logits(idx),
                               "probs": onehot(idx), "emb": [0.0] * 256}
    return f


def vision(pts, faces, cam="cam"):
    return {"schema_version": 2, "camera_id": cam, "frame_id": str(pts // 100), "seq": pts // 100,
            "pts_ms": pts, "capture_wall_ts": T0 + pts / 1000, "faces": faces}


def audio(seq, end, idx=1, speech=True, cam="cam"):
    return {"camera_id": cam, "window_seq": seq, "pts_start_ms": end - 3000, "pts_end_ms": end,
            "is_speech": speech, "speech_prob": 1.0 if speech else 0.0,
            "scores": onehot(idx) if speech else None, "label": EMOTION_LABELS[idx] if speech else None,
            "logits": logits(idx) if speech else None, "emb": [0.0] * 256 if speech else None,
            "smoothed_scores": onehot(idx), "smoothed_label": EMOTION_LABELS[idx]}


def service(store=None, method="mean_probs", **kw):
    return FusionService(None, FusionSettings(method=method, voice_max_age_ms=3000, min_overlap_ms=1000),
                         session_store=store, **kw)


def fuse(s, pts, faces, cam="cam"):
    return s.handle_vision(vision(pts, faces, cam), now=T0 + pts / 1000)


def test_defaults_must_be_complete_and_valid():
    with pytest.raises(ValueError):
        FusionService(None, FusionSettings(method="stack_hidden"))
    with pytest.raises(ValueError, match="unknown"):
        FusionService(None, FusionSettings(method="nope", voice_max_age_ms=1, min_overlap_ms=1))


def test_output_is_superset_of_vision_result():
    s   = service()
    out = fuse(s, 3000, [face(1, window_seq=1)])
    for key in ("schema_version", "camera_id", "frame_id", "seq", "pts_ms", "capture_wall_ts"):
        assert out[key] == vision(3000, [])[key]
    [f] = out["faces"]
    for key in ("bbox", "track_id", "face_id", "emotion", "emotion_scores"):
        assert key in f
    assert "emotion_window" not in f
    assert out["fusion"]["mode"] == MODE_SINGLE_FACE and out["fusion"]["method"] == "mean_probs"
    assert out["scene_voice"] is None


def test_no_face_window_yet_and_no_voice_is_undetermined():
    [f] = fuse(service(), 500, [face(1)])["faces"]
    assert f["emotion"] is None and f["emotion_scores"] == [] and f["fusion_used"] is None
    assert f["face_emotion"] == "anger"


def test_single_face_window_fused_with_overlapping_voice():
    s = service()
    s.handle_audio(audio(0, 3000, idx=1), now=T0 + 3)
    [f] = fuse(s, 3000, [face(1, idx=0, window_seq=1)])["faces"]
    assert f["voice_attributed"] and f["voice_emotion"] == "disgust" and f["voice_age_ms"] == 0
    assert f["fusion_used"] == "mean_probs"
    assert f["emotion_scores"][0] == pytest.approx(f["emotion_scores"][1])
    assert f["face_emotion"] == "anger" and f["face_scores"] == onehot(0)


def test_better_voice_window_refuses_same_face_window():
    s = service()
    s.handle_audio(audio(0, 2000, idx=3), now=T0 + 2)
    [f1] = fuse(s, 3000, [face(1, idx=0, window_seq=1)])["faces"]
    assert f1["voice_emotion"] == "happy"
    s.handle_audio(audio(1, 3000, idx=1), now=T0 + 3.2)
    [f2] = fuse(s, 3100, [face(1, idx=0)])["faces"]
    assert f2["voice_emotion"] == "disgust"
    assert f2["emotion_scores"][3] < 0.01 and f2["emotion_scores"][1] > 0.3


def test_voice_only_until_face_window_appears():
    s = service()
    s.handle_audio(audio(0, 3000, idx=4), now=T0 + 3)
    [f] = fuse(s, 3200, [face(1)])["faces"]
    assert f["emotion"] == "neutral" and f["fusion_used"] == "mean_probs -> audio_only (нет видео)"
    [f] = fuse(s, 7000, [face(1)])["faces"]
    assert f["emotion"] == "neutral"


def test_silence_means_video_only():
    s = service()
    s.handle_audio(audio(0, 3000, speech=False), now=T0 + 3)
    [f] = fuse(s, 3000, [face(1, idx=3, window_seq=1)])["faces"]
    assert not f["voice_attributed"] and f["emotion"] == "happy"
    assert f["fusion_used"] == "mean_probs -> video_only (нет аудио)"


def test_multi_face_voice_only_as_scene():
    s = service()
    s.handle_audio(audio(0, 3000), now=T0 + 3)
    out = fuse(s, 3100, [face(1, idx=0, window_seq=1), face(2, idx=3, window_seq=1)])
    assert out["fusion"]["mode"] == MODE_MULTI_FACE
    assert out["scene_voice"]["label"] == "disgust" and out["scene_voice"]["age_ms"] == 100
    assert [f["emotion"] for f in out["faces"]] == ["anger", "happy"]
    assert not any(f["voice_attributed"] for f in out["faces"])


def test_no_face_reports_scene_voice():
    s = service()
    s.handle_audio(audio(0, 3000), now=T0 + 3)
    out = fuse(s, 3500, [])
    assert out["fusion"]["mode"] == MODE_NO_FACE and out["scene_voice"]["label"] == "disgust"


def test_v1_vision_result_without_pts_is_face_only():
    s = service()
    s.handle_audio(audio(0, 3000), now=T0 + 3)
    r = vision(3000, [face(1, window_seq=1)])
    del r["pts_ms"]
    [f] = s.handle_vision(r, now=T0 + 3)["faces"]
    assert not f["voice_attributed"] and f["emotion"] == "anger"


def test_cameras_and_tracks_are_independent():
    s = service()
    s.handle_audio(audio(0, 3000, cam="a"), now=T0 + 3)
    [fa] = fuse(s, 3000, [face(1, window_seq=1)], cam="a")["faces"]
    [fb] = fuse(s, 3000, [face(1, window_seq=1)], cam="b")["faces"]
    assert fa["voice_attributed"] and not fb["voice_attributed"]
    s.sweep(now=T0 + 3 + 11)
    assert not s._cameras["a"].tracks


def test_stale_vision_dropped():
    s = service(max_frame_age_ms=500)
    assert s.handle_vision(vision(0, [face(1)]), now=T0 + 5) is None
    assert s.staleness.dropped == 1


def test_stream_end_grace_and_idle_sweep():
    s = service(end_grace_s=5, idle_timeout_s=30)
    s.handle_audio(audio(0, 3000), now=T0)
    s.handle_event({"event": "stream_end", "camera_id": "cam"}, now=T0 + 1)
    [f] = s.handle_vision(vision(3000, [face(1, window_seq=1)]), now=T0 + 2)["faces"]
    assert f["voice_attributed"]
    assert s.sweep(now=T0 + 3) == []
    assert s.sweep(now=T0 + 7) == ["cam"]

    s.handle_audio(audio(0, 3000, cam="idle"), now=T0)
    assert s.sweep(now=T0 + 60) == ["idle"]


def test_session_config_method():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1, fusion=FusionSettings(
        method="video_only")).to_msgpack())
    s = service(store=store)
    s.handle_audio(audio(0, 3000, idx=1), now=T0 + 3)
    out = fuse(s, 3000, [face(1, idx=0, window_seq=1)])
    [f] = out["faces"]
    assert out["fusion"]["method"] == "video_only" and f["fusion_used"] == "video_only"
    assert f["emotion"] == "anger"


def test_invalid_session_settings_fall_back():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1, fusion=FusionSettings(
        method="magic", voice_max_age_ms=-5, min_overlap_ms=-1)).to_msgpack())
    p = service(store=store).params("cam")
    assert (p.method, p.voice_max_age_ms, p.min_overlap_ms) == ("mean_probs", 3000, 1000)


def test_default_method_is_stack_hidden():
    s = FusionService(None, DEFAULTS)
    s.handle_audio(audio(0, 3000, idx=1), now=T0 + 3)
    [f] = fuse(s, 3000, [face(1, idx=1, window_seq=1)])["faces"]
    assert f["fusion_used"] == "stack_hidden" and len(f["emotion_scores"]) == NUM_EMOTIONS


def test_flickering_second_face_does_not_unpair_voice():
    s = service()
    s.handle_audio(audio(0, 3000, idx=1), now=T0 + 3)
    [f] = fuse(s, 3000, [face(1, idx=0, window_seq=1)])["faces"]
    seq = f["fusion_seq"]
    out = fuse(s, 3066, [face(1, idx=0), face(9, idx=3)])
    assert out["faces"][0]["voice_attributed"] and out["faces"][0]["fusion_seq"] == seq
    [f] = fuse(s, 3133, [face(1, idx=0)])["faces"]
    assert f["fusion_seq"] == seq
    out = fuse(s, 4000, [face(1, idx=0, window_seq=2, end=4000), face(9, idx=3)])
    assert not out["faces"][0]["voice_attributed"]
