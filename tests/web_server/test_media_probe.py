import sys
import wave
import numpy as np
import pytest

from pathlib import Path

pytest.importorskip("av")

from src.web_server.services.media_probe import MediaProbe, check_modalities, probe_source

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from make_sync_test import make  # noqa: E402


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d       = tmp_path_factory.mktemp("probe")
    av_file = d / "av.mp4"
    make(av_file, seconds=1, fps=10, width=64, height=48, audio_rate=16000)
    wav = d / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(16000, np.int16).tobytes())
    junk = d / "junk.mp4"
    junk.write_bytes(b"not a media file")
    return av_file, wav, junk


def test_probe_files(files):
    av_file, wav, junk = files
    p                  = probe_source(str(av_file))
    assert p.has_video and p.has_audio and abs(p.duration_sec - 1.0) < 0.2
    p = probe_source(str(wav))
    assert not p.has_video and p.has_audio
    with pytest.raises(RuntimeError):
        probe_source(str(junk))


def test_probe_mic():
    assert probe_source("mic") == MediaProbe(has_video=False, has_audio=True)


def test_check_modalities():
    wav = MediaProbe(has_video=False, has_audio=True)
    check_modalities(wav, ["video", "audio"])
    with pytest.raises(ValueError):
        check_modalities(wav, ["video"])
    check_modalities(MediaProbe(has_video=True, has_audio=None), ["audio"])
