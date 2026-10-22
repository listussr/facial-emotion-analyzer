"""
AudioPipeline без Kafka: чанки подаются в handle_chunk напрямую.
"""

import numpy as np
import pytest

from src.audio_processing.pipeline import AudioPipeline
from src.emotion_labels import NUM_EMOTIONS
from src.session_config import AudioSettings
from src.session_config import SessionConfig
from src.session_config import SessionConfigStore
from .helpers import chunks
from .helpers import silence
from .helpers import tone

DEFAULTS = AudioSettings(model="stub", device="cpu", vad_threshold=0.3, window_ms=2000, hop_ms=1000)
NOW      = 1000.0


def pipeline(store=None, **kw):
    return AudioPipeline(None, DEFAULTS, session_store=store, **kw)


def run(p, msgs, now=None):
    out = []
    for m in msgs:
        out += p.handle_chunk(m, now=now if now is not None else m["capture_wall_ts"])
    return out


def test_defaults_must_be_complete():
    with pytest.raises(ValueError):
        AudioPipeline(None, AudioSettings(model="stub"))


def test_speech_and_silence_windows():
    sig = np.concatenate([tone(3000), silence(3000)])
    res = run(pipeline(), chunks(sig))
    assert [r.window_seq for r in res] == list(range(5))
    assert [(r.pts_start_ms, r.pts_end_ms) for r in res][:2] == [(0, 2000), (1000, 3000)]

    speech = [r for r in res if r.is_speech]
    quiet  = [r for r in res if not r.is_speech]
    assert [r.pts_start_ms for r in speech] == [0, 1000, 2000]
    assert all(len(r.scores) == NUM_EMOTIONS and r.label for r in speech)
    assert all(len(r.logits) == NUM_EMOTIONS and len(r.emb) == 256 for r in speech)
    assert all(r.scores is None and r.label is None and r.emb is None for r in quiet)
    assert quiet[-1].smoothed_label == speech[-1].smoothed_label is not None
    assert all(r.model == "stub" for r in res)


def test_cameras_are_independent():
    p   = pipeline()
    res = run(p, chunks(tone(2000), camera_id="a") + chunks(tone(3000), camera_id="b"))
    assert {(r.camera_id, r.window_seq) for r in res} == {("a", 0), ("b", 0), ("b", 1)}


def test_stale_chunks_dropped():
    p    = pipeline(max_chunk_age_ms=500)
    msgs = chunks(tone(2000))
    assert run(p, msgs, now=NOW + 10) == []
    assert p.staleness.dropped == len(msgs)


def test_stream_end_releases_session():
    p = pipeline()
    run(p, chunks(tone(1500)))
    assert "cam" in p._sessions
    p.handle_event({"event": "stream_end", "camera_id": "cam", "reason": "eof"})
    assert "cam" not in p._sessions and p.sessions_ended == 1
    assert run(p, chunks(tone(2000), start_pts=50_000))[0].window_seq == 0


def test_idle_sessions_swept():
    p = pipeline(session_idle_timeout_s=5)
    run(p, chunks(tone(500)))
    assert p.sweep_idle(now=NOW + 1) == []
    assert p.sweep_idle(now=NOW + 60) == ["cam"]


def test_session_config_window_and_threshold():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1, audio=AudioSettings(
        window_ms=1000, hop_ms=500, vad_threshold=0.9)).to_msgpack())
    p   = pipeline(store=store)
    sig = np.concatenate([tone(700), silence(1300)])
    res = run(p, chunks(sig))
    assert [(r.pts_start_ms, r.pts_end_ms) for r in res] == [(0, 1000), (500, 1500), (1000, 2000)]
    assert not any(r.is_speech for r in res)


def test_config_change_mid_session_rebuilds_buffer():
    store = SessionConfigStore("unused:1")
    p     = pipeline(store=store)
    run(p, chunks(tone(2500)))
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1,
                                       audio=AudioSettings(window_ms=500, hop_ms=500)).to_msgpack())
    res = run(p, chunks(tone(1000), start_pts=2500, start_seq=25))
    assert [(r.pts_end_ms - r.pts_start_ms) for r in res] == [500, 500]
    assert p._sessions["cam"].settings.window_ms == 500


def test_invalid_session_settings_fall_back():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1, audio=AudioSettings(
        window_ms=50, hop_ms=99999, vad_threshold=7.0, device="tpu")).to_msgpack())
    p = pipeline(store=store)
    run(p, chunks(tone(100)))
    assert p._sessions["cam"].settings == DEFAULTS


def test_unloadable_model_falls_back_to_stub():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1,
                                       audio=AudioSettings(model="onnx:missing.onnx")).to_msgpack())
    p   = pipeline(store=store)
    res = run(p, chunks(tone(2000)))
    assert res[0].is_speech and res[0].model == "stub"


def test_latency_and_rtf_are_measured():
    p = pipeline()
    run(p, chunks(tone(4000)))
    assert len(p.latency) == 3
    assert 0 < p.real_time_factor() < 1
