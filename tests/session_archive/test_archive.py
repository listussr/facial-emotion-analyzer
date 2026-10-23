"""Спул, рендер и сервис архивации с подменёнными хранилищем и БД."""

import json
import av
import cv2
import numpy as np
import pytest

from src.db_managing.schema import upsert_recording_sql
from src.session_archive import ArchiveService
from src.session_archive import SessionSpool
from src.session_archive import build_analytics_document
from src.session_archive import recording_keys
from src.session_archive import render_recording

SR = 16000


def jpeg(value: int = 100, size=(160, 121)) -> bytes:
    return cv2.imencode(".jpg", np.full((size[1], size[0], 3), value, np.uint8))[1].tobytes()


def pcm(ms: int) -> bytes:
    return (np.ones(SR * ms // 1000, np.int16) * 1000).tobytes()


def fused(camera_id: str, pts: int, emotion="happy", voice="sad") -> dict:
    return {"camera_id": camera_id, "pts_ms": pts, "scene_voice": {"label": voice, "scores": [0] * 6},
            "faces": [{"track_id": 1, "bbox": [10, 10, 60, 70], "emotion": emotion,
                       "emotion_scores": [0, 0, 0, 0.9, 0.1, 0], "face_emotion": emotion, "voice_emotion": voice,
                       "emotion_window": {"emb": [0.1] * 512}}]}


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def upload_file(self, path, key, content_type):
        self.objects[key] = (path.read_bytes(), content_type)


class FakeDB:
    def __init__(self, log):
        self.log = log

    def upsert_recording(self, camera_id, **fields):
        self.log.append((camera_id, fields))

    def close(self):
        pass


def test_spool_fills_audio_gaps_and_skips_old_frames(tmp_path):
    s = SessionSpool(tmp_path, "cam")
    s.add_audio(0, pcm(200), SR, 1)
    s.add_audio(1000, pcm(200), SR, 1)
    s.add_frame(100, jpeg())
    s.add_frame(100, jpeg())
    s.add_frame(50, jpeg())
    s.close_files()

    assert s.audio_samples == SR * 1200 // 1000
    assert s.audio_path.stat().st_size == s.audio_samples * 2
    assert s.frames == 1 and [p for p, _ in s.iter_frames()] == [100]


def test_spool_stops_after_max_duration(tmp_path):
    s = SessionSpool(tmp_path, "cam", max_duration_s=1)
    for pts in (0, 500, 1000, 1500):
        s.add_frame(pts, jpeg())
    assert s.frames == 3 and s.truncated


def test_spool_keeps_only_needed_analytics(tmp_path):
    s = SessionSpool(tmp_path, "cam")
    s.add_analytics(fused("cam", 40))
    s.close_files()
    (record,) = list(s.iter_analytics())
    assert "emotion_window" not in record["faces"][0]
    assert record["voice"]["label"] == "sad" and s.last_analytics_pts == 40


def test_render_produces_playable_mp4(tmp_path):
    s = SessionSpool(tmp_path, "cam")
    for i in range(20):
        s.add_frame(200 + i * 50, jpeg(i * 10))
        s.add_analytics(fused("cam", 200 + i * 50))
    s.add_audio(0, pcm(1500), SR, 1)
    s.close_files()

    out    = tmp_path / "rec.mp4"
    result = render_recording(s, out, threads=1)
    assert result.frames == 20 and result.has_audio and result.duration_sec == pytest.approx(1.5)
    with av.open(str(out)) as c:
        assert {st.type for st in c.streams} == {"video", "audio"}
        v = c.streams.video[0]
        assert (v.width, v.height) == (160, 120)
        assert sum(1 for _ in c.decode(video=0)) == 20

    doc = build_analytics_document(s, result)
    assert doc["summary"]["fused_label_counts"] == {"happy": 20}
    assert doc["summary"]["tracks"]["1"]["dominant"] == "happy"
    assert doc["timeline"][0]["t_ms"] == 200


def test_render_without_anything_fails(tmp_path):
    s = SessionSpool(tmp_path, "cam")
    s.close_files()
    with pytest.raises(ValueError):
        render_recording(s, tmp_path / "rec.mp4")


def make_service(tmp_path, **kwargs):
    storage = FakeStorage()
    log     = []
    service = ArchiveService(tmp_path / "spool", storage, db_factory=lambda: FakeDB(log), **kwargs)
    return service, storage, log


def statuses(log, camera_id):
    return [f.get("status") for c, f in log if c == camera_id]


def test_service_waits_for_analytics_then_uploads(tmp_path):
    service, storage, log = make_service(tmp_path, analytics_grace_s=30)
    service.handle_event({"camera_id": "cam", "event": "stream_start", "modalities": ["video"]})
    for i in range(5):
        service.handle_video({"camera_id": "cam", "pts_ms": i * 50, "frame_data": jpeg()})
        if i < 3:
            service.handle_analytics(fused("cam", i * 50))
    service.handle_event({"camera_id": "cam", "event": "stream_end", "reason": "eof"})

    assert service.sweep() == []
    service.handle_analytics(fused("cam", 200))
    assert service.sweep() == ["cam"]
    service._executor.shutdown(wait=True)

    keys = recording_keys("cam")
    assert set(storage.objects) == set(keys.values())
    doc = json.loads(storage.objects[keys["analytics"]][0])
    assert doc["session"]["event"] == "stream_start" and doc["recording"]["frames"] == 5
    assert statuses(log, "cam") == ["recording", "rendering", "ready"]
    assert log[-1][1]["summary"]["fused_label_counts"] == {"happy": 4}
    assert not (tmp_path / "spool" / "cam").exists()

    service.handle_video({"camera_id": "cam", "pts_ms": 999, "frame_data": jpeg()})
    assert "cam" not in service._spools


def test_service_finalizes_after_grace_and_idle(tmp_path):
    service, storage, log = make_service(tmp_path, analytics_grace_s=5, idle_timeout_s=60)
    for cam in ("ended", "idle"):
        service.handle_video({"camera_id": cam, "pts_ms": 0, "frame_data": jpeg()})
    service.handle_event({"camera_id": "ended", "event": "stream_end"})
    now = service._spools["ended"].ended_at

    assert service.due(now + 1) == []
    assert service.due(now + 6) == ["ended"]
    assert service.due(now + 61) == ["ended", "idle"]


def test_service_reports_failure(tmp_path):
    service, storage, log = make_service(tmp_path)
    service.handle_event({"camera_id": "cam", "event": "stream_start"})
    service.handle_event({"camera_id": "cam", "event": "stream_end"})
    assert service.sweep() == ["cam"]
    service._executor.shutdown(wait=True)
    assert statuses(log, "cam") == ["recording", "rendering", "failed"]
    assert storage.objects == {}


def test_upsert_sql_touches_only_given_fields():
    sql = upsert_recording_sql(("status", "frames"))
    assert sql.count("%s") == 3
    assert "status = EXCLUDED.status" in sql and "name" not in sql
    with pytest.raises(ValueError):
        upsert_recording_sql(("camera_id; DROP",))
