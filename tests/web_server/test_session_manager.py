"""
SessionManager с подменёнными продюсером, публикатором конфигов и проверкой источника.
"""

import pytest
import src.web_server.services.session_manager as sm

from src.web_server.schemas import SessionConfig
from src.web_server.services.media_probe import MediaProbe


class Calls(list):
    pass


@pytest.fixture
def calls(monkeypatch):
    log = Calls()

    class FakePublisher:
        def __init__(self, bootstrap, topic):
            log.append(("publisher", topic))

        def publish(self, cfg, timeout=5.0):
            log.append(("publish", cfg))

        def delete(self, camera_id, timeout=5.0):
            log.append(("delete", camera_id))

        def close(self):
            pass

    class FakeProducer:
        fail = False

        def __init__(self, cfg):
            self.config     = cfg
            self.thread     = None
            self.is_running = False
            self.frame_count = self.error_count = 0

        def start(self):
            if FakeProducer.fail:
                raise RuntimeError("kafka down")
            log.append(("start", self.config))
            self.is_running = True

        def stop(self):
            log.append(("stop", self.config.camera_id))

        def get_stats(self):
            return {"modalities": list(self.config.modalities), "degraded": False,
                    "audio": {"seconds_sent": 1.5}}

    log.producer_cls = FakeProducer
    probe            = {"value": MediaProbe(has_video=True, has_audio=True, duration_sec=10.0)}
    log.probe        = probe
    monkeypatch.setattr(sm, "SessionConfigPublisher", FakePublisher)
    monkeypatch.setattr(sm, "CameraProducer", FakeProducer)
    monkeypatch.setattr(sm, "probe_source", lambda source: probe["value"])
    return log


def kinds(calls):
    return [c[0] for c in calls]


def test_config_is_published_before_producer_starts(calls):
    m = sm.SessionManager()
    cfg = SessionConfig(tracker="deepsort", model="stub", voice_model="voice-x",
                        modalities=["video", "audio"], identification=False,
                        fusion_method="weighted_logits", clip_frames=16)
    info = m.create_camera("Cam", "rtsp://x", 15, cfg)

    assert kinds(calls) == ["publisher", "publish", "start"]
    published = calls[1][1]
    assert published.camera_id == info.id
    assert published.vision.tracker == "deepsort" and published.vision.identification is False
    assert published.audio.model == "voice-x"
    assert published.fusion.method == "weighted_logits" and published.fusion.voice_max_age_ms is None
    assert published.vision.clip_frames == 16

    producer_cfg = calls[2][1]
    assert producer_cfg.modalities == ("video", "audio") and producer_cfg.partition is None
    assert producer_cfg.audio_topic == "raw-audio-chunks" and producer_cfg.events_topic == "session-events"
    assert info.modalities == ["video", "audio"] and info.audio_seconds_sent == 1.5


def test_no_fusion_section_without_method(calls):
    sm.SessionManager().create_camera("Cam", "x.mp4", 10, SessionConfig())
    assert calls[1][1].fusion is None


def test_source_without_requested_modalities_is_rejected(calls):
    calls.probe["value"] = MediaProbe(has_video=False, has_audio=True)
    with pytest.raises(ValueError, match="модальност"):
        sm.SessionManager().create_camera("Cam", "a.wav", 10, SessionConfig(modalities=["video"]))
    assert "publish" not in kinds(calls) and "start" not in kinds(calls)


def test_unknown_mic_does_not_block_camera(calls):
    calls.probe["value"] = MediaProbe(has_video=True, has_audio=None)
    sm.SessionManager().create_camera("Cam", 0, 10, SessionConfig(modalities=["audio"]))
    assert "start" in kinds(calls)


def test_failed_start_removes_config(calls):
    calls.producer_cls.fail = True
    try:
        with pytest.raises(RuntimeError):
            sm.SessionManager().create_camera("Cam", "x.mp4", 10, SessionConfig())
    finally:
        calls.producer_cls.fail = False
    assert kinds(calls)[-1] == "delete"


def test_stop_removes_config_after_producer(calls):
    m    = sm.SessionManager()
    info = m.create_camera("Cam", "x.mp4", 10, SessionConfig())
    assert m.stop(info.id) is True
    assert kinds(calls)[-2:] == ["stop", "delete"] and calls[-1][1] == info.id
    assert m.get(info.id) is None
