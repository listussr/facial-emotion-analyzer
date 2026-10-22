import uuid
import numpy as np

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import NUM_EMOTIONS
from src.fusion.recorder import FusionRecorder
from src.fusion.service import FusionService
from src.session_config import FusionSettings

PERSON = str(uuid.uuid4())


def onehot(i):
    return [float(x) for x in np.eye(NUM_EMOTIONS)[i]]


def fused(pts, face_id=PERSON, track=1, cam="cam", emotion="happy"):
    return {"camera_id": cam, "pts_ms": pts, "capture_wall_ts": 1000 + pts / 1000, "faces": [{
        "track_id": track, "face_id": face_id, "emotion": emotion, "emotion_scores": onehot(3),
        "face_emotion": "sad", "voice_emotion": "happy", "fusion_used": "stack_hidden",
        "fusion_seq": pts // 100,
    }]}


def voice(seq, speech=True, cam="cam"):
    return {"camera_id": cam, "window_seq": seq, "pts_start_ms": seq * 1000, "pts_end_ms": seq * 1000 + 2000,
            "capture_wall_ts": 1002.0 + seq, "is_speech": speech, "speech_prob": 0.9, "label": "happy",
            "scores": onehot(3), "smoothed_label": "happy", "smoothed_scores": onehot(3), "model": "stub"}


def test_track_written_after_timeout():
    r = FusionRecorder(None, track_timeout_s=3)
    for i in range(5):
        r.record_frame(fused(i * 100), now=100.0 + i * 0.1)
    assert r.flush_inactive(now=101.0) == 0
    assert r.flush_inactive(now=110.0) == 1
    [(method, payload)] = r.sink
    assert method == "insert_emotion_timeseries"
    assert payload["modality"] == "fused" and payload["labels"] == list(EMOTION_LABELS)
    assert len(payload["samples"]) == 5
    s = payload["samples"][0]
    assert s["label"] == "happy" and s["face_label"] == "sad" and s["voice_label"] == "happy"


def test_pending_id_is_replaced_by_resolved_uuid():
    r = FusionRecorder(None)
    r.record_frame(fused(0, face_id="pending-abc"), now=1.0)
    r.record_frame(fused(100, face_id=PERSON), now=1.1)
    r.flush_camera("cam", now=2.0)
    assert r.written_tracks == 1 and r.sink[0][1]["track_id"] == 1


def test_unidentified_tracks_are_not_written():
    r = FusionRecorder(None)
    r.record_frame(fused(0, face_id="anonymous"), now=1.0)
    r.record_frame(fused(0, face_id="pending-x", track=2), now=1.0)
    r.flush_camera("cam", now=2.0)
    assert r.sink == [] and r.skipped_unidentified == 2


def test_faces_without_emotion_are_skipped():
    r                        = FusionRecorder(None)
    f                        = fused(0)
    f["faces"][0]["emotion"] = None
    r.record_frame(f, now=1.0)
    r.flush_camera("cam")
    assert r.sink == []


def test_voice_written_on_camera_flush():
    r = FusionRecorder(None)
    r.record_voice(voice(0), now=1.0)
    r.record_voice(voice(1, speech=False), now=2.0)
    r.record_voice(voice(2), now=3.0)
    r.flush_camera("cam", now=4.0)
    [(method, payload)] = r.sink
    assert method == "insert_session_audio" and payload["camera_id"] == "cam"
    assert [w["window_seq"] for w in payload["windows"]] == [0, 2]


def test_writes_go_to_db_handler():
    written = []

    class FakeHandler:
        def insert_emotion_timeseries(self, user_id, payload):
            written.append(("face", user_id, payload["modality"]))

        def insert_session_audio(self, camera_id, payload):
            written.append(("voice", camera_id, len(payload["windows"])))

        def close(self):
            pass

    r = FusionRecorder(lambda: FakeHandler())
    r.record_frame(fused(0), now=1.0)
    r.record_voice(voice(0), now=1.0)
    r.close(now=5.0)
    assert sorted(written) == [("face", PERSON, "fused"), ("voice", "cam", 1)]


def test_service_feeds_recorder_and_flushes_on_release():
    r = FusionRecorder(None)
    s = FusionService(None, FusionSettings(method="mean_probs", voice_max_age_ms=3000, min_overlap_ms=1000),
                      recorder=r, end_grace_s=1)
    s.handle_audio(voice(0), now=1002.0)
    vision = {"camera_id": "cam", "frame_id": "20", "pts_ms": 2000, "capture_wall_ts": 1002.0,
              "faces": [{"track_id": 1, "face_id": PERSON, "bbox": [0, 0, 1, 1],
                         "emotion": "sad", "emotion_scores": onehot(5),
                         "emotion_window": {"seq": 1, "start_pts_ms": 0, "end_pts_ms": 2000,
                                            "logits": [0, 0, 0, 0, 0, 6.0], "emb": [0.0] * 256}}]}
    s.handle_vision(vision, now=1002.0)
    s.handle_event({"event": "stream_end", "camera_id": "cam"}, now=1003.0)
    s.sweep(now=1010.0)
    methods = sorted(m for m, _ in r.sink)
    assert methods == ["insert_emotion_timeseries", "insert_session_audio"]
