"""Источник браузера: очередь кадров и звука, часы, завершение и фабрика."""

import threading
import cv2
import numpy as np
import pytest

from src.video_producing.config import CameraConfig
from src.video_producing.sources import AudioChunk
from src.video_producing.sources import VideoFrame
from src.video_producing.sources import browser_source
from src.video_producing.sources import open_source
from src.video_producing.sources.browser_source import BrowserSource


def jpeg() -> bytes:
    return cv2.imencode(".jpg", np.full((48, 64, 3), 128, np.uint8))[1].tobytes()


def make(**kwargs) -> BrowserSource:
    params = dict(has_video=True, has_audio=True, sample_rate=16000, channels=1, samples_per_chunk=1600)
    params.update(kwargs)
    return BrowserSource("browser_x", **params)


def test_frames_and_audio_reach_reader():
    s = make()
    info = s.open()
    assert info.is_live and info.has_video and info.has_audio

    assert s.push_video(100, jpeg())
    assert not s.push_video(100, jpeg())
    assert s.push_audio(0, b"\x01\x00" * 3200) == 2
    s.finish()

    items = list(s.read())
    frames = [i for i in items if isinstance(i, VideoFrame)]
    chunks = [i for i in items if isinstance(i, AudioChunk)]
    assert [f.pts_ms for f in frames] == [100] and frames[0].image.shape == (48, 64, 3)
    assert [c.pts_ms for c in chunks] == [0, 100]
    assert chunks[1].capture_wall_ts - frames[0].capture_wall_ts == pytest.approx(0.0, abs=1e-6)


def test_disabled_modalities_are_ignored():
    s = make(has_audio=False)
    assert s.push_audio(0, b"\x00\x00" * 1600) == 0
    assert not s.open(want_video=True, want_audio=True).has_audio


def test_full_queue_drops_frames():
    s = make(queue_size=2)
    assert [s.push_video(p, jpeg()) for p in (1, 2, 3)] == [True, True, False]
    assert s.dropped_video == 1


def test_no_media_times_out_and_cannot_reopen():
    s = make(connect_timeout_s=0.1)
    s.open()
    with pytest.raises(RuntimeError):
        list(s.read())
    with pytest.raises(RuntimeError):
        s.open()


def test_finish_unblocks_reader():
    s = make()
    s.open()
    out = []
    t = threading.Thread(target=lambda: out.extend(s.read()))
    t.start()
    s.finish()
    t.join(timeout=2)
    assert not t.is_alive() and out == []


def test_factory_returns_registered_source():
    s   = make()
    uri = browser_source.register(s)
    try:
        assert open_source(CameraConfig(camera_id="browser_x", source=uri, kafka_servers="k")) is s
    finally:
        browser_source.unregister("browser_x")
    with pytest.raises(RuntimeError):
        open_source(CameraConfig(camera_id="browser_x", source=uri, kafka_servers="k"))
