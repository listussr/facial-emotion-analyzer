import numpy as np
import pytest

from src.audio_processing.buffer import CameraAudioBuffer

from .helpers import RATE, chunks, to_pcm, tone


def feed(buf, msgs):
    out = []
    for m in msgs:
        out += buf.push(m['audio_data'], m['pts_ms'], m['capture_wall_ts'], seq=m['seq'])
    return out


def test_overlapping_windows_and_timing():
    buf = CameraAudioBuffer('cam', RATE, window_ms=2000, hop_ms=1000)
    windows = feed(buf, chunks(tone(5000)))
    assert [(w.pts_start_ms, w.pts_end_ms) for w in windows] == [(0, 2000), (1000, 3000), (2000, 4000), (3000, 5000)]
    assert all(w.samples.size == 2 * RATE for w in windows)
    # время захвата конца окна = wall0 + pts_end
    assert all(abs(w.capture_wall_ts - (1000 + w.pts_end_ms / 1000)) < 1e-6 for w in windows)


def test_window_content_matches_signal():
    sig = tone(3000, freq=300)
    buf = CameraAudioBuffer('cam', RATE, window_ms=1000, hop_ms=500)
    w = feed(buf, chunks(sig))[1]                 # окно 500..1500 мс
    expected = np.round(sig[RATE // 2: RATE // 2 + RATE] * 32767) / 32768
    assert np.allclose(w.samples, expected, atol=1e-4)


def test_seq_gap_resets_buffer():
    buf = CameraAudioBuffer('cam', RATE, window_ms=1000, hop_ms=1000)
    msgs = chunks(tone(3000))
    del msgs[5]                                    # потерян чанк 500..600 мс
    windows = feed(buf, msgs)
    # окно 0..1000 содержало бы дыру → не выдаётся; новое окно начинается с 600 мс
    assert [w.pts_start_ms for w in windows] == [600, 1600]
    assert buf.resets == 1 and buf.seq_gaps == 1


def test_pts_jump_resets_buffer():
    buf = CameraAudioBuffer('cam', RATE, window_ms=1000, hop_ms=1000)
    first = chunks(tone(800))
    second = chunks(tone(1200), start_pts=10_000, start_seq=len(first))
    windows = feed(buf, first + second)
    assert [w.pts_start_ms for w in windows] == [10_000]
    assert buf.resets == 1 and buf.seq_gaps == 0


def test_small_jitter_is_not_a_gap():
    buf = CameraAudioBuffer('cam', RATE, window_ms=1000, hop_ms=1000)
    msgs = chunks(tone(2000))
    for i, m in enumerate(msgs):
        m['pts_ms'] += (i % 3) * 10                # дрожание ±20 мс
    assert len(feed(buf, msgs)) == 2 and buf.resets == 0


def test_stereo_is_downmixed():
    buf = CameraAudioBuffer('cam', RATE, window_ms=100, hop_ms=100)
    x = tone(100)
    [w] = buf.push(to_pcm(x, channels=2), 0, 0.0, channels=2)
    assert w.samples.size == x.size


def test_invalid_parameters():
    with pytest.raises(ValueError):
        CameraAudioBuffer('cam', RATE, window_ms=500, hop_ms=1000)
    buf = CameraAudioBuffer('cam', RATE, window_ms=500, hop_ms=500)
    with pytest.raises(ValueError):
        buf.push(to_pcm(tone(100)), 0, 0.0, sample_rate=44100)
