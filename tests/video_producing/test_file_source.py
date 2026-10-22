import sys
import pytest

from pathlib import Path

pytest.importorskip("av")

from src.video_producing.sources import AudioChunk, VideoFrame
from src.video_producing.sources.file_source import FileSource

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from make_sync_test import make  # noqa: E402

SECONDS = 3


@pytest.fixture(scope="module")
def av_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("media") / "sync.mp4"
    make(path, seconds=SECONDS, fps=30, width=160, height=120, audio_rate=48000)
    return path


@pytest.fixture(scope="module")
def wav_file(tmp_path_factory) -> Path:
    import wave
    import numpy as np
    path = tmp_path_factory.mktemp("media") / "tone.wav"
    t    = np.arange(SECONDS * 44100) / 44100
    x    = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(x.tobytes())
    return path


def read_all(path, want_video, want_audio):
    src = FileSource(str(path), sample_rate=16000, channels=1, samples_per_chunk=1600)
    with src:
        info  = src.open(want_video, want_audio)
        items = list(src.read())
    return info, [i for i in items if isinstance(i, VideoFrame)], [i for i in items if isinstance(i, AudioChunk)]


def test_video_and_audio(av_file):
    info, frames, chunks = read_all(av_file, True, True)
    assert info.has_video and info.has_audio and not info.is_live
    assert abs(info.native_fps - 30) < 0.5
    assert abs(len(frames) - SECONDS * 30) <= 1
    assert frames[0].image.shape == (120, 160, 3)

    total = sum(c.num_samples for c in chunks)
    assert abs(total - SECONDS * 16000) <= 1600
    assert all(c.num_samples == 1600 for c in chunks[:-1])

    pts = [c.pts_ms for c in chunks]
    assert all(b > a for a, b in zip(pts, pts[1:]))
    assert all(abs((b - a) - 100) <= 1 for a, b in zip(pts[:-1], pts[1:-1]))


def test_video_only_does_not_decode_audio(av_file):
    info, frames, chunks = read_all(av_file, True, False)
    assert info.has_audio
    assert frames and not chunks


def test_audio_only(av_file):
    _, frames, chunks = read_all(av_file, False, True)
    assert chunks and not frames


def test_wav_without_video(wav_file):
    info, frames, chunks = read_all(wav_file, True, True)
    assert not info.has_video and info.has_audio
    assert not frames
    assert abs(sum(c.num_samples for c in chunks) - SECONDS * 16000) <= 1600


def test_flash_and_beep_are_aligned(av_file):
    import numpy as np
    _, frames, chunks = read_all(av_file, True, True)

    flash = next(f.pts_ms for f in frames if f.pts_ms > 500 and f.image.mean() > 128)
    beep  = None
    for c in chunks:
        if c.pts_ms < 500:
            continue
        x    = np.frombuffer(c.pcm, np.int16).astype(np.float32)
        loud = np.flatnonzero(np.abs(x) > 3000)
        if loud.size:
            beep = c.pts_ms + loud[0] * 1000 / 16000
            break
    assert beep is not None
    assert abs(beep - flash) <= 1000 / 30 + 30


def test_fit_size():
    from src.video_producing.sources.file_source import fit_size
    assert fit_size(1920, 1080, (1280, 720)) == (1280, 720)
    assert fit_size(640, 480, (1280, 720)) == (640, 480)
    assert fit_size(1080, 1920, (1280, 720)) == (404, 720)
    assert fit_size(1921, 1081, None) == (1921, 1081)


def test_frames_are_converted_lazily_and_downscaled(tmp_path):
    """Кадр переводится в BGR только при обращении и сразу вписывается в max_size."""
    path = tmp_path / "big.mp4"
    make(path, seconds=1, fps=30, width=320, height=240, audio_rate=48000)
    src = FileSource(str(path), 16000, 1, 1600, max_size=(160, 160))
    src.open(want_video=True, want_audio=False)
    frames = [x for x in src.read() if isinstance(x, VideoFrame)]
    src.close()
    assert frames and all(f._image is None for f in frames)
    assert frames[0].original_size == (320, 240)
    assert frames[0].image.shape == (120, 160, 3)
    assert frames[1]._image is None
